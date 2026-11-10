"""Exported Functions, native child dispatch and streaming loop; scripted LLM only."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]


class NativeParallelContextTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.config import DEFAULT_CONFIG
        from open_webui.models.auths import Auths
        from open_webui.models.config import Config
        from open_webui.models.functions import Functions, FunctionForm
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.tools import Tools, ToolForm
        from open_webui.models.files import Files, FileForm
        from open_webui.utils.plugin import (load_function_module_by_id, load_tool_module_by_id,
                                            set_function_module_in_cache, set_tool_module_in_cache)
        from open_webui.utils.tools import get_tool_specs

        await Config.seed_defaults(DEFAULT_CONFIG)
        await Config.upsert({"chat.context_compaction.enable": False, "subagents.enable": True})
        suffix = uuid4().hex[:10]
        self.user = await Auths.insert_new_auth(suffix + "@worker.example.test", "unused-test-password",
                                                "Worker test", role="admin")
        self.app = FastAPI()
        self.app.state.redis = None
        self.app.state.MODELS = {"worker-router-llm": {"id": "worker-router-llm", "owned_by": "openai"}}
        self.app.state.worker_contexts = []
        self.request = Request({"type": "http", "app": self.app, "headers": [], "method": "POST",
                                "path": "/api/chat/completions"})
        self.request.state.max_tool_call_iterations = 8
        self.router, self.agent = "worker-entry-" + suffix, "worker-agent-" + suffix
        self.skill, self.route = "work-" + suffix, "route-" + suffix
        self.file_id = str(uuid4())
        directory = TemporaryDirectory(prefix="worker-context-")
        self.addCleanup(directory.cleanup)
        upload = Path(directory.name) / "input.txt"
        upload.write_text("Source data")
        await Files.insert_new_file(self.user.id, FileForm(id=self.file_id, filename="input.txt", path=str(upload),
                                                          data={"content": "Source data"}))
        for identifier, content in ((self.skill, "WORKER_SKILL_INSTRUCTIONS"), (self.route, "Route this profile")):
            await Skills.insert_new_skill(self.user.id, SkillForm(id=identifier, name=identifier, content=content))
        tool_id = "worker_tool_" + suffix
        tool_content = '''import copy
class Tools:
    async def worker_lookup(self, __request__, __messages__: list, __files__: list) -> str:
        __request__.app.state.worker_contexts.append({"messages":copy.deepcopy(__messages__),
                                                     "files":copy.deepcopy(__files__)})
        return "WORKER_LOOKUP_RESULT"
'''
        tool, _, source = await load_tool_module_by_id(tool_id, tool_content)
        await Tools.insert_new_tool(self.user.id, ToolForm(id=tool_id, name=tool_id, content=tool_content, meta={}),
                                    get_tool_specs(tool), module=tool)
        set_tool_module_in_cache(self.request, tool_id, tool_content, tool, source)

        builder_spec = importlib.util.spec_from_file_location("worker_builder", ROOT / "tools/build_functions.py")
        builder = importlib.util.module_from_spec(builder_spec)
        builder_spec.loader.exec_module(builder)
        builder.build()
        pipe_id = "worker_pipe_" + suffix
        for identifier, name in ((pipe_id, "lite_handoff_router"), ("worker_prep_" + suffix, "router_preparation")):
            content = (ROOT / "dist" / (name + ".py")).read_text()
            self.assertNotIn("from lite_handoff_router", content)
            module, kind, _, source = await load_function_module_by_id(identifier, content)
            await Functions.insert_new_function(self.user.id, kind, FunctionForm(
                id=identifier, name=identifier, content=content, meta={}), module=module)
            await Functions.update_function_by_id(identifier, {"is_active": True})
            set_function_module_in_cache(self.request, identifier, content, module, source)
            if name == "router_preparation":
                source.AGENTS = {self.agent: self.route}
                self.preparation = module
            else:
                await Functions.update_function_valves_by_id(identifier, {"orchestrator_model_id": "worker-router-llm"})
        for identifier, base, system, meta in (
            (self.router, pipe_id, "PARENT_SYSTEM", {"toolIds": [], "skillIds": []}),
            (self.agent, "worker-base", "AGENT_OWN_SYSTEM", {"toolIds": [tool_id], "skillIds": [self.skill]}),
        ):
            row = await Models.insert_new_model(ModelForm(id=identifier, name=identifier, base_model_id=base,
                                                          params={"system": system}, meta=meta), self.user.id)
            self.app.state.MODELS[identifier] = {"id": identifier, "owned_by": "openai", "info": row.model_dump()}
        self.app.state.MODELS[self.router]["pipe"] = {"type": "pipe"}
        self.app.state.MODELS[pipe_id] = {"id": pipe_id, "pipe": {"type": "pipe"}}
        self.seen, self.children, self.events = [], [], []
        self.app.state.CHAT_COMPLETION_HANDLER = self.handle_child

    async def handle_child(self, request, form_data, user):
        """Bridge native delegate to the same exported Preparation/Pipe and native response loop."""
        metadata = {"user_id": user.id, "chat_id": form_data["chat_id"], "message_id": form_data["id"],
                    "session_id": form_data["session_id"], "params": form_data["params"],
                    "files": form_data["files"], "internal": True}
        request.state.metadata = metadata
        form_data["metadata"] = metadata
        await self.preparation.inlet(form_data, request, user.model_dump(), metadata)
        self.children.append(request)
        await self.run_loop(request, form_data, user, metadata)

    async def run_loop(self, request, body, user, metadata):
        from open_webui.utils import chat, middleware
        from open_webui.socket.main import get_event_emitter

        native_emit = await get_event_emitter(metadata)

        async def emit(event):
            self.events.append(copy.deepcopy(event))
            await native_emit(event)

        ctx = {"request": request, "form_data": body, "user": user, "model": self.app.state.MODELS[self.router],
               "metadata": metadata, "tasks": {}, "events": [], "event_emitter": emit, "event_caller": None}
        response = await chat.generate_chat_completion(request, body, user)
        await middleware.process_chat_response(response, ctx)
        return ctx

    async def parent(self, question):
        from open_webui.models.chats import Chats, ChatForm

        chat_id, mid, uid = str(uuid4()), str(uuid4()), str(uuid4())
        messages = [{"role": "user", "content": "PARENT_EARLIER: FTP-42, 2026-11-01–2026-11-30"},
                    {"role": "assistant", "content": "PARENT_VISIBLE_ANSWER"},
                    {"role": "user", "content": question}]
        await Chats.insert_new_chat(chat_id, self.user.id, ChatForm(chat={"history": {"currentId": mid, "messages": {
            uid: {"id": uid, "role": "user", "content": question, "childrenIds": [mid]},
            mid: {"id": mid, "role": "assistant", "content": "", "done": False, "parentId": uid},
        }}}))
        metadata = {"user_id": self.user.id, "chat_id": chat_id, "message_id": mid,
                    "session_id": "worker-test", "params": {"tool_approval_mode": "full"},
                    "files": [{"id": self.file_id}]}
        self.request.state.metadata = metadata
        body = {"model": self.router, "stream": True, "messages": messages, "metadata": metadata}
        await self.preparation.inlet(body, self.request, self.user.model_dump(), metadata)
        return body, metadata

    async def provider(self, request, form_data, user):
        from fastapi.responses import StreamingResponse, JSONResponse

        payload = copy.deepcopy({k: v for k, v in form_data.items() if k != "metadata"})
        self.seen.append({**payload, "chat_id": form_data["metadata"]["chat_id"]})
        messages = payload["messages"]
        results = [m for m in messages if m.get("role") == "tool"]
        name, args, answer = None, {}, "PARENT_FINAL"
        if payload["model"] == "worker-router-llm":
            if not results:
                name, args = "delegate_task", self.assignments[0]
            elif getattr(self, "final_handoff", False):
                name, args = "lite_delegate", {"agent_id": self.agent}
        elif payload["model"] == self.agent:
            question = next(m["content"] for m in messages if m["role"] == "user")
            if "OVERFLOW_ASSIGNMENT" in question:
                return JSONResponse({"error": {"message": "Worker context length exceeded"}}, status_code=400)
            if not results:
                name, args = "view_skill", {"id": self.skill}
            elif len(results) == 1:
                name = "worker_lookup"
            else:
                answer = "ONE_FORECAST: " + question
        else:
            self.fail("Unexpected model " + payload["model"])
        message = {"role": "assistant", "content": None if name else answer}
        if name:
            message["tool_calls"] = [{"index": 0, "id": "call_" + uuid4().hex, "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(args)}}]
            if name == "delegate_task":
                message["tool_calls"] = [{"index": i, "id": "call_" + uuid4().hex, "type": "function",
                    "function": {"name": name, "arguments": json.dumps(item)}}
                    for i, item in enumerate(self.assignments)]

        async def stream():
            for delta, finish in ((message, None), ({}, "tool_calls" if name else "stop")):
                yield "data: " + json.dumps({"id": "worker-test", "model": payload["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")

    async def test_same_agent_exports_keep_assignment_across_native_child_and_tool_loop(self):
        from open_webui.utils import chat
        from open_webui.utils.tools import get_updated_tool_function
        from open_webui.models.chats import Chats

        task = "Покажи прогноз ФТП FTP-42 за 2026-11-01–2026-11-30. Верни краткую таблицу."
        self.assignments = [{"agent_id": self.agent, "task": task, "file_ids": [self.file_id]},
                            {"agent_id": self.agent, "task": task}]
        body, metadata = await self.parent("PARENT_REQUEST: сделай 2 параллельных вызова в этот агент")
        original = copy.deepcopy(body["messages"])
        with patch.object(chat, "generate_openai_chat_completion", self.provider):
            await self.run_loop(self.request, body, self.user, metadata)
        state = self.request.scope["lite_handoff_router"]
        self.assertEqual(state["parallel"]["status"], "completed", state["parallel"])
        self.assertEqual(len(self.children), 2)
        self.assertEqual(state["original_source"], original)
        results = state["parallel"]["results"]
        self.assertEqual(len({r["chat_url"] for r in results}), 2)
        for result in results:
            self.assertEqual(result["result"].count("ONE_FORECAST:"), 1)
        self.assertEqual(len(self.app.state.worker_contexts), 2)
        for child in self.children:
            worker = child.scope["lite_handoff_router"]
            seen = [p for p in self.seen if p["chat_id"] == worker["chat_id"]]
            self.assertEqual(len(seen), 3)
            for payload in seen:
                self.assertNotIn("PARENT_", str(payload))
                systems = [m["content"] for m in payload["messages"] if m["role"] == "system"]
                self.assertIn("AGENT_OWN_SYSTEM", systems)
                self.assertEqual(str(systems).count("Router already manages the group"), 1)
                self.assertNotIn(task, str(systems))
                self.assertEqual([m["content"] for m in payload["messages"] if m["role"] == "user"],
                                 [worker["original_source"][0]["content"]])
                self.assertIn(task, str(payload["messages"]))
                names = {t["function"]["name"] for t in payload["tools"]}
                self.assertFalse(names & {"lite_delegate", "lite_parallel", "delegate_task"})
            self.assertIn("WORKER_SKILL_INSTRUCTIONS", str(seen[1]["messages"]))
            self.assertIn("WORKER_LOOKUP_RESULT", str(seen[2]["messages"]))
            tool = child.scope["lite_handoff_metadata"]["tools"]["worker_lookup"]
            refreshed = await get_updated_tool_function(tool["callable"], {
                "__messages__": [{"role": "user", "content": "PARENT_REFRESH"}],
                "__files__": [{"id": "refreshed-file"}],
            })
            await refreshed()
            effective = self.app.state.worker_contexts[-1]
            self.assertNotIn("PARENT_", str(effective["messages"]))
            self.assertIn(task, str(effective["messages"]))
            self.assertEqual(effective["files"], [{"id": "refreshed-file"}])
            saved = await Chats.get_chat_by_id(worker["chat_id"])
            self.assertTrue(saved.meta["internal"])
        self.assertCountEqual([[f["id"] for f in item["files"]] for item in self.app.state.worker_contexts[:2]],
                              [[self.file_id], []])

    async def test_worker_overflow_is_explicit_and_does_not_repeat_the_task(self):
        from open_webui.utils import chat

        self.assignments = [{"agent_id": self.agent, "task": "OVERFLOW_ASSIGNMENT"},
                            {"agent_id": self.agent, "task": "Покажи прогноз ФТП FTP-42"}]
        body, metadata = await self.parent("PARENT_REQUEST: two forecasts")
        with patch.object(chat, "generate_openai_chat_completion", self.provider):
            await self.run_loop(self.request, body, self.user, metadata)
        results = self.request.scope["lite_handoff_router"]["parallel"]["results"]
        self.assertEqual(results[0]["status"], "failed", results)
        self.assertIn("context length", str(results[0]).lower())
        self.assertEqual(results[1]["status"], "completed", results)
        overflow = [p for p in self.seen if p["model"] == self.agent and "OVERFLOW_ASSIGNMENT" in str(p)]
        self.assertEqual(len(overflow), 1)

    async def test_final_handoff_keeps_parent_dialogue_and_complete_results(self):
        from open_webui.utils import chat

        self.final_handoff = True
        self.assignments = [{"agent_id": self.agent, "task": "FIRST_FORECAST: FTP-42"},
                            {"agent_id": self.agent, "task": "SECOND_FORECAST: FTP-99"}]
        body, metadata = await self.parent("PARENT_REQUEST: forecasts followed by final handoff")
        with patch.object(chat, "generate_openai_chat_completion", self.provider):
            await self.run_loop(self.request, body, self.user, metadata)
        state = self.request.scope["lite_handoff_router"]
        self.assertEqual(state["active_agent"], self.agent)
        self.assertEqual(state["parallel"]["status"], "completed")
        final = [p for p in self.seen if p["chat_id"] == metadata["chat_id"] and p["model"] == self.agent]
        self.assertEqual(len(final), 3)
        for payload in final:
            self.assertIn("PARENT_REQUEST", str(payload["messages"]))
            self.assertIn("PARENT_EARLIER", str(payload["messages"]))
            self.assertIn("PARENT_VISIBLE_ANSWER", str(payload["messages"]))
            self.assertNotIn("Router already manages the group", str(payload["messages"]))
            packet = next(m["content"] for m in payload["messages"]
                          if str(m.get("content", "")).startswith("Confirmed parallel task results."))
            for result in state["parallel"]["results"]:
                self.assertIn(result["result"], packet)

    async def test_native_delegate_dispatch_owns_parallelism_and_call_ids(self):
        from fastapi.responses import StreamingResponse
        from open_webui.models.chats import Chats
        from open_webui.utils import chat

        body, metadata = await self.parent("Native dispatch probe")
        entered = []
        barrier = asyncio.Event()

        async def delegate_task(agent_id: str, task: str):
            entered.append((agent_id, task))
            if len(entered) == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 3)
            return "PROBE_RESULT"

        metadata["tools"] = {"delegate_task": {
            "spec": {"name": "delegate_task", "parameters": {"type": "object", "properties": {
                "agent_id": {"type": "string"}, "task": {"type": "string"}},
                "required": ["agent_id", "task"]}}, "callable": delegate_task}}
        count = 0

        async def provider(request, form_data, user, **kwargs):
            nonlocal count
            count += 1
            message = {"role": "assistant", "content": "PROBE_FINAL"}
            if count == 1:
                message = {"role": "assistant", "content": None, "tool_calls": [
                    {"index": i, "id": "probe-" + str(i), "type": "function", "function": {
                        "name": "delegate_task", "arguments": json.dumps({"agent_id": "same", "task": "same"})}}
                    for i in range(2)]}

            async def stream():
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": message,
                    "finish_reason": None}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {},
                    "finish_reason": "tool_calls" if count == 1 else "stop"}]}) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(stream(), media_type="text/event-stream")

        with patch.object(chat, "generate_chat_completion", provider):
            await self.run_loop(self.request, body, self.user, metadata)
        self.assertEqual(entered, [("same", "same")] * 2)
        saved = await Chats.get_chat_by_id(metadata["chat_id"])
        message = saved.chat["history"]["messages"][metadata["message_id"]]
        outputs = [item for item in message["output"] if item["type"] == "function_call_output"]
        self.assertEqual([item["call_id"] for item in outputs], ["probe-0", "probe-1"])
        self.assertTrue(all("PROBE_RESULT" in str(item["output"]) for item in outputs), outputs)

    async def test_four_native_calls_return_four_worker_results(self):
        from open_webui.models.chats import Chats
        from open_webui.utils import chat
        self.assignments = [{"agent_id": self.agent, "task": "FOUR_FORECAST_" + str(i)} for i in range(4)]
        body, metadata = await self.parent("PARENT_REQUEST: four forecasts")
        with patch.object(chat, "generate_openai_chat_completion", self.provider):
            await self.run_loop(self.request, body, self.user, metadata)
        group = self.request.scope["lite_handoff_router"]["parallel"]
        self.assertEqual(group["status"], "completed")
        self.assertEqual(len(self.children), 4)
        saved = await Chats.get_chat_by_id(metadata["chat_id"])
        output = saved.chat["history"]["messages"][metadata["message_id"]]["output"]
        calls = [o for o in output if o.get("name") == "delegate_task"]
        self.assertEqual(len(calls), 4)
        for i, call in enumerate(calls):
            result = next(o for o in output if o["type"] == "function_call_output" and o["call_id"] == call["call_id"])
            content = json.loads(''.join(p.get("text", "") for p in result["output"]))
            self.assertEqual(content["task"], self.assignments[i]["task"])
            self.assertEqual(content["status"], "completed")
            self.assertNotIn("results", content)

    async def test_second_group_rejection_blocks_native_handoff(self):
        from fastapi.responses import StreamingResponse
        from open_webui.models.chats import Chats
        from open_webui.utils import chat
        self.assignments = [{"agent_id": self.agent, "task": "FIRST_FORECAST"}]
        body, metadata = await self.parent("PARENT_REQUEST: two independent forecasts")
        parent_count = 0

        async def provider(request, form_data, user):
            nonlocal parent_count
            if form_data["model"] != "worker-router-llm":
                return await self.provider(request, form_data, user)
            parent_count += 1
            if parent_count == 1:
                return await self.provider(request, form_data, user)
            name = "delegate_task" if parent_count == 2 else "lite_delegate" if parent_count == 3 else None
            args = self.assignments[0] if parent_count == 2 else {"agent_id": self.agent}
            message = {"role": "assistant", "content": "Only one forecast completed; additional group and handoff rejected."}
            if name:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "index": 0, "id": "rejected-" + str(parent_count), "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}
            async def stream():
                for delta, finish in ((message, None), ({}, "tool_calls" if name else "stop")):
                    yield "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(stream(), media_type="text/event-stream")

        with patch.object(chat, "generate_openai_chat_completion", provider):
            await self.run_loop(self.request, body, self.user, metadata)
        state = self.request.scope["lite_handoff_router"]
        self.assertEqual(len(self.children), 1)
        self.assertEqual(len(state["parallel"]["results"]), 1)
        self.assertEqual(state["parallel"]["results"][0]["status"], "completed")
        self.assertTrue(state["parallel"]["rejected_followup"])
        self.assertIsNone(state["active_agent"])
        self.assertEqual(state["pending"], [])
        saved = await Chats.get_chat_by_id(metadata["chat_id"])
        message = saved.chat["history"]["messages"][metadata["message_id"]]
        self.assertIn("Only one forecast completed", message["content"])
        outputs = [o for o in message["output"] if o["type"] == "function_call_output"]
        rejected = next(o for o in outputs if o["call_id"] == "rejected-3")
        self.assertIn("additional parallel group was rejected", str(rejected))
