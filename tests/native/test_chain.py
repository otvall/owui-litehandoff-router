"""Real OWUI loaders, permissions and streaming Tool loop with a test provider.

Only provider transport and browser-event delivery are replaced. No route,
Tool execution, callable-refresh or completion-loop helper is mocked.
"""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="


class NativeChainTest(unittest.IsolatedAsyncioTestCase):
    async def test_two_exports_inline_delegate_agent_tool_and_final_same_message(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.config import DEFAULT_CONFIG
        from open_webui.models.config import Config
        from open_webui.models.users import Users
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.functions import Functions, FunctionForm
        from open_webui.models.tools import Tools, ToolForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.utils.plugin import (
            load_function_module_by_id, load_tool_module_by_id, set_function_module_in_cache,
            set_tool_module_in_cache,
        )
        from open_webui.utils.tools import get_tool_specs
        from open_webui.utils import chat, middleware
        from open_webui import functions

        await Config.seed_defaults(DEFAULT_CONFIG)
        await Config.upsert({"chat.context_compaction.enable": False})
        suffix = uuid4().hex[:10]
        pipe_id, tool_id = "router_" + suffix, "lookup_" + suffix
        router_id, agent_id, skill_id = "entry-" + suffix, "agent-a-" + suffix, "route-a-" + suffix
        user = await Users.insert_new_user("user-" + suffix, "Native User", suffix + "@example.test", role="admin")
        app = FastAPI()
        app.state.redis = None
        app.state.MODELS = {}
        request = Request({"type": "http", "app": app, "headers": [], "path": "/api/chat/completions", "method": "POST"})
        request.state.max_tool_call_iterations = 8

        builder_spec = importlib.util.spec_from_file_location("native_builder", ROOT / "tools/build_functions.py")
        builder = importlib.util.module_from_spec(builder_spec)
        builder_spec.loader.exec_module(builder)
        builder.build()
        pipe_content = (ROOT / "dist/lite_handoff_router.py").read_text()
        pipe, kind, _, pipe_source = await load_function_module_by_id(pipe_id, pipe_content)
        await Functions.insert_new_function(user.id, kind, FunctionForm(id=pipe_id, name="Router", content=pipe_content, meta={}), module=pipe)
        await Functions.update_function_by_id(pipe_id, {"is_active": True})
        await Functions.update_function_valves_by_id(pipe_id, {"orchestrator_model_id": "router-llm"})
        set_function_module_in_cache(request, pipe_id, pipe_content, pipe, pipe_source)

        for identifier, content in (
            (tool_id, f'''class Tools:
    async def profile_lookup(self, query: str, __user__: dict, __messages__: list, __metadata__: dict) -> str:
        assert "INLET_A" in str(__messages__)
        assert "ROUTER_PROMPT" not in str(__messages__)
        __metadata__["files"].append({{"id": "tool-file-update"}})
        return {PNG!r}
    async def profile_finish(self, __user__: dict, __messages__: list, __files__: list) -> str:
        assert "INLET_A" in str(__messages__)
        assert "ROUTER_PROMPT" not in str(__messages__)
        assert "Image file read successfully" in str(__messages__)
        assert "data:image/png;base64" in str(__messages__)
        assert any(item.get("id") == "user-file" for item in __files__)
        assert any(item.get("id") == "tool-file-update" for item in __files__)
        return "TOOL_RESULT_42:" + __user__["id"]
'''),
        ):
            module, _, source = await load_tool_module_by_id(identifier, content)
            await Tools.insert_new_tool(user.id, ToolForm(id=identifier, name=identifier, content=content, meta={}), get_tool_specs(module), module=module)
            set_tool_module_in_cache(request, identifier, content, module, source)
        inlet_id = "inlet_" + suffix
        inlet_content = '''class Filter:
    async def inlet(self, body: dict, __model__: dict, __metadata__: dict):
        assert body["model"] == __model__["id"]
        assert "AGENT_PROMPT" in str(body["messages"])
        assert "ROUTER_PROMPT" not in str(body["messages"])
        assert "profile_lookup" in __metadata__["tools"]
        body["messages"] = body["messages"] + [{"role": "system", "content": "INLET_A"}]
        return body
'''
        inlet, kind, _, inlet_source = await load_function_module_by_id(inlet_id, inlet_content)
        await Functions.insert_new_function(user.id, kind, FunctionForm(id=inlet_id, name="A inlet", content=inlet_content, meta={}), module=inlet)
        await Functions.update_function_by_id(inlet_id, {"is_active": True})
        set_function_module_in_cache(request, inlet_id, inlet_content, inlet, inlet_source)
        await Skills.insert_new_skill(user.id, SkillForm(
            id=skill_id, name="route-" + suffix, description="Profile A questions.",
            content="Call lite_delegate with exact agent_id " + agent_id, meta={},
        ))
        for identifier, base, params, meta in (
            (router_id, pipe_id, {"system": "ROUTER_PROMPT", "temperature": 0.8, "top_p": 0.9, "top_k": 17,
                                 "custom_params": {"seed": "123", "router_only": "true"}}, {"toolIds": [], "skillIds": []}),
            (agent_id, "specialist-a", {"system": "AGENT_PROMPT {{USER_NAME}} {{ticket}}", "temperature": 0.2,
                                       "top_p": 0.6, "top_k": 3, "custom_params": {"seed": "321", "agent_only": "true"}},
             {"toolIds": [tool_id], "skillIds": [], "hidden": True, "filterIds": [inlet_id]}),
        ):
            row = await Models.insert_new_model(ModelForm(id=identifier, name=identifier, base_model_id=base, params=params, meta=meta), user.id)
            app.state.MODELS[identifier] = {"id": identifier, "name": identifier, "owned_by": "openai", "info": row.model_dump()}
        app.state.MODELS[router_id]["pipe"] = {"type": "pipe"}
        app.state.MODELS["router-llm"] = {"id": "router-llm", "owned_by": "openai"}
        app.state.MODELS[pipe_id] = {"id": pipe_id, "pipe": {"type": "pipe"}}

        preparation, _, _, prep_source = await load_function_module_by_id("prep_" + suffix, (ROOT / "dist/router_preparation.py").read_text())
        prep_source.AGENTS = {agent_id: skill_id}
        self.assertEqual(preparation.valves.base_tool_ids, [])
        metadata = {"chat_id": "local:" + suffix, "message_id": "answer-" + suffix, "session_id": "test-session-" + suffix,
                    "params": {"function_calling": "native", "tool_approval_mode": "full"}, "files": [{"id": "user-file"}],
                    "variables": {"{{ticket}}": "T42"}}
        request.state.metadata = metadata
        body = {"model": router_id, "stream": True, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "profile a question"}, {"type": "image_url", "image_url": {"url": PNG}},
        ]}], "metadata": metadata}
        await preparation.inlet(body, request, user.model_dump(), metadata)

        provider_spec = importlib.util.spec_from_file_location("native_provider", ROOT / "tools/acceptance_provider.py")
        provider = importlib.util.module_from_spec(provider_spec)
        provider_spec.loader.exec_module(provider)
        seen, events = [], []

        async def transport(request, form_data, user):
            payload = copy.deepcopy({k: v for k, v in form_data.items() if k != "metadata"})
            seen.append(copy.deepcopy(payload))
            if payload["model"] == agent_id:
                payload["model"] = "specialist-a"
                if len([item for item in seen if item["model"] == agent_id]) == 1:
                    # Change the card between A's first inference and native continuation.
                    current = await Models.get_model_by_id(agent_id)
                    params = current.params.model_dump()
                    params.update(system="AGENT_PROMPT_UPDATED {{USER_NAME}} {{ticket}}", temperature=0.15)
                    await Models.update_model_by_id(agent_id, ModelForm(
                        id=agent_id, name=current.name, base_model_id=current.base_model_id,
                        params=params, meta=current.meta,
                    ), user_id=user.id)

            async def json_body():
                return payload
            return await provider.completion(SimpleNamespace(json=json_body))

        async def emit(event):
            events.append(copy.deepcopy(event))

        ctx = {"request": request, "form_data": body, "user": user, "model": app.state.MODELS[router_id],
               "metadata": metadata, "tasks": {}, "events": [], "event_emitter": emit, "event_caller": None}
        with patch.object(chat, "generate_openai_chat_completion", transport), \
             patch.object(functions, "get_event_emitter", AsyncMock(return_value=emit)), \
             patch.object(functions, "get_event_call", AsyncMock(return_value=None)):
            response = await chat.generate_chat_completion(request, body, user)
            await middleware.process_chat_response(response, ctx)

        self.assertEqual([p["model"] for p in seen], ["router-llm", "router-llm", agent_id, agent_id, agent_id], events)
        self.assertEqual([p["temperature"] for p in seen], [0.8, 0.8, 0.2, 0.15, 0.15])
        self.assertEqual([p["top_p"] for p in seen], [0.9, 0.9, 0.6, 0.6, 0.6])
        self.assertEqual([p["top_k"] for p in seen], [17, 17, 3, 3, 3])
        self.assertEqual([p["seed"] for p in seen], [123, 123, 321, 321, 321])
        for payload in seen[2:]:
            systems = "\n".join(str(m.get("content")) for m in payload["messages"] if m.get("role") == "system")
            self.assertEqual(systems.count("AGENT_PROMPT"), 1)
            self.assertNotIn("ROUTER_PROMPT", systems)
            self.assertIn("Native User", systems)
            self.assertIn("T42", systems)
            self.assertNotIn("router_only", payload)
            self.assertTrue(payload["agent_only"])
        self.assertIn("AGENT_PROMPT_UPDATED", str(seen[-1]["messages"]))
        self.assertEqual(app.state.MODELS[agent_id]["info"]["params"]["temperature"], 0.2)
        finals = [event["data"] for event in events if event.get("type") == "chat:completion" and event.get("data", {}).get("done")]
        self.assertTrue(finals, events)
        self.assertIn("TOOL_RESULT_42", str(finals[-1]))
        output = ctx["assistant_message"]["output"]
        self.assertFalse([item for item in output if item.get("status") in {"failed", "incomplete", "rejected"}], output)
        self.assertTrue(any("TOOL_RESULT_42:" + user.id in str(item.get("output"))
                            for item in output if item.get("type") == "function_call_output"), output)
        import json
        delegate_call = next(item for item in output if item.get("type") == "function_call" and item["name"] == "lite_delegate")
        self.assertEqual(delegate_call["status"], "completed")
        delegate_result = next(item for item in output if item.get("type") == "function_call_output" and item.get("call_id") == delegate_call["call_id"])
        marker = json.loads("".join(part.get("text", "") for part in delegate_result["output"]))
        self.assertEqual(marker, request.scope["lite_handoff_router"]["pending"][0])
        self.assertEqual(marker["agent_id"], agent_id)
        self.assertEqual(marker["request_id"], request.scope["lite_handoff_router"]["request_id"])
        self.assertEqual(metadata["chat_id"], "local:" + suffix)
        self.assertEqual(metadata["message_id"], "answer-" + suffix)
        self.assertEqual(request.scope["lite_handoff_router"]["active_agent"], agent_id)
        statuses = [event["data"]["description"] for event in events
                    if event.get("type") == "status" and event.get("data", {}).get("action") == "handoff"]
        self.assertEqual(statuses, ["Передача: " + agent_id])
        audit = request.scope["lite_handoff_router"]["audit"]
        self.assertEqual([record["executor"]["kind"] for record in audit], ["router", "router", "agent", "agent"])
        self.assertEqual(len(request.scope["lite_handoff_router"]["pending"][0]["router_calls"]), 2)

        # Replay the real grouped output as OWUI does for a subsequent message.
        from open_webui.utils.misc import convert_output_to_messages

        history = [copy.deepcopy(request.scope["lite_handoff_router"]["original_source"][-1])]
        history += convert_output_to_messages(output, raw=True, flatten_tool_images=True)
        b_id, b_skill, b_tool = "agent-b-" + suffix, "route-b-" + suffix, "lookup_b_" + suffix
        b_content = '''class Tools:
    async def profile_lookup(self, query: str, __messages__: list) -> str:
        assert "B_PROMPT" in str(__messages__)
        assert "AGENT_PROMPT" not in str(__messages__)
        assert "ROUTER_PROMPT" not in str(__messages__)
        assert not any(m.get("role") == "tool" for m in __messages__)
        return "B_PRIVATE_RESULT_42"
'''
        b_module, _, b_source = await load_tool_module_by_id(b_tool, b_content)
        await Tools.insert_new_tool(user.id, ToolForm(id=b_tool, name="B Tool", content=b_content, meta={}),
                                    get_tool_specs(b_module), module=b_module)
        set_tool_module_in_cache(request, b_tool, b_content, b_module, b_source)
        await Skills.insert_new_skill(user.id, SkillForm(
            id=b_skill, name="B routing " + suffix, description="Profile B questions.",
            content="Call lite_delegate with exact agent_id " + b_id, meta={},
        ))
        b_row = await Models.insert_new_model(ModelForm(
            id=b_id, name="Specialist B", base_model_id="specialist-b",
            params={"system": "B_PROMPT", "temperature": 0.4},
            meta={"toolIds": [b_tool], "skillIds": [], "hidden": True},
        ), user.id)
        app.state.MODELS[b_id] = {"id": b_id, "name": "Specialist B", "owned_by": "openai", "info": b_row.model_dump()}
        prep_source.AGENTS[b_id] = b_skill

        async def next_turn(question):
            next_request = Request({"type": "http", "app": app, "headers": [], "path": "/api/chat/completions", "method": "POST"})
            next_request.state.max_tool_call_iterations = 8
            next_metadata = {"chat_id": "local:" + suffix, "message_id": "answer-" + uuid4().hex,
                             "session_id": "test-session-" + suffix, "files": [],
                             "params": {"function_calling": "native", "tool_approval_mode": "full"}}
            next_request.state.metadata = next_metadata
            next_body = {"model": router_id, "stream": True, "messages": copy.deepcopy(history) + [
                {"role": "user", "content": question}], "metadata": next_metadata}
            await preparation.inlet(next_body, next_request, user.model_dump(), next_metadata)
            self.assertIsNone(next_request.scope["lite_handoff_router"]["active_agent"])
            start, event_start = len(seen), len(events)

            async def next_transport(request, form_data, user):
                if form_data["model"] == b_id:
                    payload = copy.deepcopy({k: v for k, v in form_data.items() if k != "metadata"})
                    seen.append(copy.deepcopy(payload))
                    payload["model"] = "specialist-b"

                    async def json_body():
                        return payload
                    return await provider.completion(SimpleNamespace(json=json_body))
                return await transport(request, form_data, user)

            next_ctx = {"request": next_request, "form_data": next_body, "user": user, "model": app.state.MODELS[router_id],
                        "metadata": next_metadata, "tasks": {}, "events": [], "event_emitter": emit, "event_caller": None}
            with patch.object(chat, "generate_openai_chat_completion", next_transport), \
                 patch.object(functions, "get_event_emitter", AsyncMock(return_value=emit)), \
                 patch.object(functions, "get_event_call", AsyncMock(return_value=None)):
                response = await chat.generate_chat_completion(next_request, next_body, user)
                await middleware.process_chat_response(response, next_ctx)
            return next_request, next_ctx, seen[start:], events[event_start:]

        b_request, b_ctx, b_seen, b_events = await next_turn("profile b question")
        self.assertEqual([p["model"] for p in b_seen], ["router-llm", "router-llm", b_id, b_id], b_events)
        self.assertEqual([p["temperature"] for p in b_seen], [0.8, 0.8, 0.4, 0.4])
        self.assertEqual(b_request.scope["lite_handoff_router"]["active_agent"], b_id)
        self.assertIn("B_PRIVATE_RESULT_42", str(b_ctx["assistant_message"]["output"]))
        self.assertIn("Specialist B", str(b_events))
        history += [{"role": "user", "content": "profile b question"}]
        history += convert_output_to_messages(b_ctx["assistant_message"]["output"], raw=True, flatten_tool_images=True)

        general_request, general_ctx, general_seen, general_events = await next_turn("general question")
        self.assertEqual([p["model"] for p in general_seen], ["router-llm"], general_events)
        self.assertIsNone(general_request.scope["lite_handoff_router"]["active_agent"])
        self.assertIn("General answer", str(general_ctx["assistant_message"]["output"]))
        self.assertIn("B_PRIVATE_RESULT_42", str(general_seen[0]["messages"]))
        self.assertNotIn("profile_lookup", [t["function"]["name"] for t in general_seen[0]["tools"]])

        for question, detail in (("provider error", "Test provider failure"), ("overflow", "Context length exceeded")):
            with self.subTest(question=question):
                _, error_ctx, error_seen, error_events = await next_turn(question)
                self.assertEqual(len(error_seen), 1, error_events)
                self.assertIn(detail, str(error_events))
                self.assertFalse([item for item in error_ctx["assistant_message"].get("output", [])
                                  if item.get("type") == "function_call"])

        # Failure after an action must not repeat it or fall back to Router.
        _, failed_ctx, failed_seen, failed_events = await next_turn("profile a fail after lookup")
        self.assertEqual([p["model"] for p in failed_seen], ["router-llm", "router-llm", agent_id, agent_id], failed_events)
        self.assertIn("Test provider failure after Tool", str(failed_events))
        failed_calls = [item["name"] for item in failed_ctx["assistant_message"]["output"] if item.get("type") == "function_call"]
        self.assertEqual(failed_calls.count("profile_lookup"), 1)
        self.assertNotIn("profile_finish", failed_calls)

        saved_tools = preparation.valves.base_tool_ids
        try:
            preparation.valves.base_tool_ids = ["missing-required-tool-" + suffix]
            _, resource_ctx, resource_seen, resource_events = await next_turn("general question")
            self.assertEqual(resource_seen, [], resource_events)
            self.assertIn("Required model Tools unavailable", str(resource_events))
            self.assertFalse([item for item in resource_ctx["assistant_message"].get("output", [])
                              if item.get("type") == "function_call"])
        finally:
            preparation.valves.base_tool_ids = saved_tools
