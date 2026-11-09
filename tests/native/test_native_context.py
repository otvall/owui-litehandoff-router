"""Native hidden context through standalone exports; scripted provider only."""

import copy
import unittest
from unittest.mock import patch

import test_parallel_context as context


class NativeContextTest(context.NativeParallelContextTest):
    async def handle_child(self, request, form_data, user):
        if not getattr(self, "child_native", False):
            return await super().handle_child(request, form_data, user)
        from open_webui.utils.middleware import hidden_tool_result
        from open_webui.utils.misc import convert_output_to_messages
        metadata = {"user_id": user.id, "chat_id": form_data["chat_id"], "message_id": form_data["id"],
                    "session_id": form_data["session_id"], "params": form_data["params"],
                    "files": form_data["files"], "internal": True, "features": {"memory": True}}
        request.state.metadata = metadata
        form_data["metadata"] = metadata
        await self.preparation.inlet(form_data, request, user.model_dump(), metadata)
        output = hidden_tool_result("recall_memories", "CHILD_NATIVE_MEMORY_" + metadata["chat_id"])
        metadata["initial_output"] = output
        form_data["messages"] += convert_output_to_messages(output, raw=True)
        self.children.append(request)
        await self.run_loop(request, form_data, user, metadata)

    async def attest(self, name, content, actor=None):
        from open_webui.utils import middleware
        from open_webui.utils.misc import convert_output_to_messages
        from lite_handoff_router.history import refresh_source
        from lite_handoff_router.native_context import capture_native_context
        from lite_handoff_router.owui_adapter import OWUIAdapter
        body, metadata = await self.parent("PARENT_REQUEST: context policy")
        if actor is not None:
            from lite_handoff_router.runtime import begin
            metadata["user_id"] = actor.id
            begin(self.request, body, metadata, actor.id, {}, [], [], [])
        metadata["features"] = {"memory": True}
        output = middleware.hidden_tool_result(name, content)
        metadata["initial_output"] = output
        converted = convert_output_to_messages(output, raw=True)
        body["messages"] += converted
        state = self.request.scope["lite_handoff_router"]
        refresh_source(state, body["messages"])
        adapter = OWUIAdapter()
        capture_native_context(self.request, metadata, state,
                               adapter.native_context_messages(self.request, metadata, state))
        return adapter, metadata, state

    async def provider(self, request, form_data, user):
        # Keep native context in the measured payload. The scripted decision
        # engine counts only actual agent tool results, never automatic recall.
        draft = copy.deepcopy(form_data)
        hidden_ids = {c.get("id") for m in draft["messages"] for c in m.get("tool_calls", [])
                      if c.get("function", {}).get("name") in {
                          "recall_memories", "list_attached_knowledge", "list_available_skills"}}
        messages = []
        for message in draft["messages"]:
            if message.get("role") == "tool" and message.get("tool_call_id") in hidden_ids:
                continue
            if message.get("tool_calls"):
                message["tool_calls"] = [c for c in message["tool_calls"] if c.get("id") not in hidden_ids]
                if not message["tool_calls"] and not message.get("content"):
                    continue
            messages.append(message)
        draft["messages"] = messages
        index = len(self.seen)
        response = await super().provider(request, draft, user)
        self.seen[index] = {**copy.deepcopy({k: v for k, v in form_data.items() if k != "metadata"}),
                            "chat_id": form_data["metadata"]["chat_id"]}
        return response

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from open_webui.models.config import Config
        await Config.upsert({"memories.enable": True, "memories.system_context.enable": True})

    async def test_current_memory_reaches_final_handoff(self):
        from open_webui.utils import chat, middleware
        from open_webui.utils.misc import convert_output_to_messages

        self.final_handoff = True
        self.child_native = True
        self.assignments = [{"agent_id": self.agent, "task": "First independent task"},
                            {"agent_id": self.agent, "task": "Second independent task"}]
        body, metadata = await self.parent("PARENT_REQUEST: use my current preferences")
        metadata["features"] = {"memory": True}
        output = middleware.hidden_tool_result("recall_memories", "NATIVE_CURRENT_USER_MEMORY")
        # Model the server preprocessing boundary, never a provider tool call.
        metadata["initial_output"] = copy.deepcopy(output)
        body["messages"] += convert_output_to_messages(output, raw=True)
        self.assertEqual(body["messages"][-1]["role"], "tool")
        with patch.object(chat, "generate_openai_chat_completion", self.provider):
            await self.run_loop(self.request, body, self.user, metadata)
        destination = next(p for p in self.seen
                           if p["chat_id"] == metadata["chat_id"] and p["model"] == self.agent)
        self.assertEqual(len(self.children), 2)
        for child in self.children:
            child_state = child.scope["lite_handoff_router"]
            child_id = child_state["chat_id"]
            self.assertEqual(child_state["native_context"]["identity"][2], child_id)
            own = [p for p in self.seen if p["chat_id"] == child_id]
            self.assertTrue(own)
            for payload in own:
                self.assertIn("CHILD_NATIVE_MEMORY_" + child_id, str(payload["messages"]))
                self.assertNotIn("NATIVE_CURRENT_USER_MEMORY", str(payload["messages"]))
                for other in self.children:
                    other_id = other.scope["lite_handoff_router"]["chat_id"]
                    if other_id != child_id:
                        self.assertNotIn("CHILD_NATIVE_MEMORY_" + other_id, str(payload["messages"]))
        self.assertNotIn("CHILD_NATIVE_MEMORY_", str(destination["messages"]))
        self.assertIn("NATIVE_CURRENT_USER_MEMORY", str(destination["messages"]),
                      "Current native user memory disappeared at final handoff")
        self.assertIn("AGENT_OWN_SYSTEM", str(destination["messages"]))
        self.assertNotIn("PARENT_SYSTEM", str(destination["messages"]))
        self.assertNotIn("Historical completed Tool data", str(destination["messages"]))
        self.assertNotIn("recall_memories", {t["function"]["name"] for t in destination["tools"]})
        from lite_handoff_router.protocol import completed_pairs
        native_pairs = [(i, n, r) for i, n, r in completed_pairs(destination["messages"])
                        if destination["messages"][i]["tool_calls"][n]["function"]["name"] == "recall_memories"]
        self.assertEqual(len(native_pairs), 1)
        effective = [item for item in self.app.state.worker_contexts
                     if "NATIVE_CURRENT_USER_MEMORY" in str(item["messages"])]
        self.assertEqual(len(effective), 1)
        from open_webui.utils.tools import get_updated_tool_function
        tool = self.request.scope["lite_handoff_metadata"]["tools"]["worker_lookup"]
        refreshed = await get_updated_tool_function(tool["callable"], {
            "__messages__": [{"role": "user", "content": "OUTER_REFRESH_WITHOUT_MEMORY"}],
            "__files__": []})
        await refreshed()
        self.assertIn("NATIVE_CURRENT_USER_MEMORY", str(self.app.state.worker_contexts[-1]["messages"]))
        self.assertNotIn("OUTER_REFRESH_WITHOUT_MEMORY", str(self.app.state.worker_contexts[-1]["messages"]))

    async def test_minimal_native_pair_is_not_an_agent_tool_execution(self):
        from open_webui.utils.middleware import hidden_tool_result
        from open_webui.utils.misc import convert_output_to_messages
        from lite_handoff_router.history import analyze

        messages = [{"role": "user", "content": "Current question"}]
        messages += convert_output_to_messages(
            hidden_tool_result("recall_memories", "NATIVE_CURRENT_USER_MEMORY"), raw=True)
        original = copy.deepcopy(messages)
        exchanges = analyze(messages, current_boundary=0)
        self.assertEqual(len(exchanges), 1)
        self.assertEqual(exchanges[0].executor, {"kind": "unknown"})
        self.assertEqual(messages, original)

    async def test_memory_policy_rechecks_features_model_and_global_permission(self):
        from open_webui.models.config import Config
        adapter, metadata, state = await self.attest("recall_memories", "ACTOR_MEMORY")
        model = copy.deepcopy(self.app.state.MODELS[self.agent])
        self.assertEqual(len(await adapter.native_context_policy(self.request, metadata, state, model, self.user)), 1)
        model["info"]["meta"]["capabilities"] = {"memory": False}
        self.assertEqual(await adapter.native_context_policy(self.request, metadata, state, model, self.user), {})
        model["info"]["meta"]["capabilities"] = {"memory": True}
        metadata["features"]["memory"] = False
        self.assertEqual(await adapter.native_context_policy(self.request, metadata, state, model, self.user), {})
        metadata["features"]["memory"] = True
        await Config.upsert({"memories.enable": False})
        self.assertEqual(await adapter.native_context_policy(self.request, metadata, state, model, self.user), {})

    async def test_native_skills_catalog_does_not_expand_effective_scope(self):
        from open_webui.utils import chat, middleware
        from open_webui.utils.misc import convert_output_to_messages
        self.final_handoff = True
        self.assignments = [{"agent_id": self.agent, "task": "one"}, {"agent_id": self.agent, "task": "two"}]
        body, metadata = await self.parent("PARENT_REQUEST: native skill catalog")
        output = middleware.hidden_tool_result("list_available_skills", "FOREIGN_SKILL_DESCRIPTION_AND_INSTRUCTIONS")
        metadata["initial_output"] = output
        body["messages"] += convert_output_to_messages(output, raw=True)
        with patch.object(chat, "generate_openai_chat_completion", self.provider):
            await self.run_loop(self.request, body, self.user, metadata)
        for payload in self.seen:
            self.assertNotIn("FOREIGN_SKILL_DESCRIPTION_AND_INSTRUCTIONS", str(payload["messages"]))
            if payload["model"] == self.agent:
                self.assertIn(self.skill, str(payload["messages"]))
                self.assertIn("view_skill", {t["function"]["name"] for t in payload["tools"]})
    async def test_knowledge_uses_actor_grants_and_destination_attachments(self):
        from open_webui.models.auths import Auths
        from open_webui.models.knowledge import Knowledges, KnowledgeForm
        from open_webui.models.access_grants import AccessGrants
        from uuid import uuid4
        actor = await Auths.insert_new_auth(uuid4().hex + "@context.test", "synthetic", "Actor", role="user")
        own = await Knowledges.insert_new_knowledge(actor.id, KnowledgeForm(name="Own", description="", access_grants=[]))
        shared = await Knowledges.insert_new_knowledge(self.user.id, KnowledgeForm(name="Shared", description="",
            access_grants=[{"principal_type": "user", "principal_id": actor.id, "permission": "read"}]))
        unrelated = await Knowledges.insert_new_knowledge(actor.id, KnowledgeForm(name="Unattached", description=""))
        text = '<attached_knowledge>' + ''.join(
            '<knowledge type="collection" id="' + row.id + '" source="model" name="PRIVATE_DESCRIPTION"/>'
            for row in (own, shared, unrelated)) + '</attached_knowledge>'
        adapter, metadata, state = await self.attest("list_attached_knowledge", text, actor)
        model = {"info": {"meta": {"knowledge": [{"type": "collection", "id": row.id} for row in (own, shared)]}}}
        allowed = await adapter.native_context_policy(self.request, metadata, state, model, actor)
        value = str(list(allowed.values()))
        self.assertIn(own.id, value)
        self.assertIn(shared.id, value)
        self.assertNotIn(unrelated.id, value)
        self.assertNotIn("PRIVATE_DESCRIPTION", value)
        from lite_handoff_router.history import agent_projection, refresh_source
        state["native_context_allowed"] = allowed
        projected = agent_projection(state, self.agent, set(), set())
        self.assertIn(own.id, str(projected))
        self.assertNotIn(unrelated.id, str(projected))
        self.assertNotIn("PRIVATE_DESCRIPTION", str(projected))
        refresh_source(state, projected)
        await AccessGrants.set_access_grants("knowledge", shared.id, [])
        allowed = await adapter.native_context_policy(self.request, metadata, state, model, actor)
        self.assertIn(own.id, str(list(allowed.values())))
        self.assertNotIn(shared.id, str(list(allowed.values())))
        state["native_context_allowed"] = allowed
        refreshed = agent_projection(state, self.agent, set(), set())
        self.assertIn(own.id, str(refreshed))
        self.assertNotIn(shared.id, str(refreshed))
        model["info"]["meta"]["knowledge"] = []
        self.assertEqual(await adapter.native_context_policy(self.request, metadata, state, model, actor), {})

    async def test_user_memory_permission_is_rechecked_without_owner_fallback(self):
        from open_webui.models.config import Config
        from open_webui.models.auths import Auths
        from open_webui.models.memories import Memories
        from uuid import uuid4
        actor = await Auths.insert_new_auth(uuid4().hex + "@context.test", "synthetic", "Actor", role="user")
        adapter, metadata, state = await self.attest("recall_memories", "ONLY_ACTOR_MEMORY", actor)
        before = await Config.get("user.permissions")
        try:
            enabled = copy.deepcopy(before)
            enabled.setdefault("features", {})["memories"] = True
            await Config.upsert({"user.permissions": enabled})
            with patch.object(Memories, "get_memories_by_user_id", side_effect=AssertionError("Policy must not read owner memory")):
                allowed = await adapter.native_context_policy(self.request, metadata, state, {}, actor)
                self.assertIn("ONLY_ACTOR_MEMORY", str(list(allowed.values())))
                self.assertEqual(await adapter.native_context_policy(self.request, metadata, state, {}, self.user), {})
                enabled["features"]["memories"] = False
                await Config.upsert({"user.permissions": enabled})
                self.assertEqual(await adapter.native_context_policy(self.request, metadata, state, {}, actor), {})
        finally:
            await Config.upsert({"user.permissions": before})

    async def test_resume_reads_fresh_memory_only_for_server_verified_approval(self):
        from open_webui.utils import middleware
        from lite_handoff_router.owui_adapter import OWUIAdapter
        from unittest.mock import AsyncMock
        body, metadata = await self.parent("PARENT_REQUEST: approval resume")
        state = self.request.scope["lite_handoff_router"]
        metadata.update(features={"memory": True}, assistant_message_id=state["message_id"],
                        tool_approval_resume="tools", model=self.app.state.MODELS[self.router])
        output = middleware.hidden_tool_result("recall_memories", "FRESH_RESUME_MEMORY")
        reader = AsyncMock(return_value=output)
        adapter = OWUIAdapter()
        with patch.object(middleware, "preload_memory", reader):
            self.request.state.tool_approval_resume = ("wrong-chat", state["message_id"], "tools")
            self.assertEqual(await adapter.native_resume_context(self.request, metadata, state, self.user), [])
            reader.assert_not_awaited()
            self.request.state.tool_approval_resume = (state["chat_id"], state["message_id"], "tools")
            converted = await adapter.native_resume_context(self.request, metadata, state, self.user)
            self.assertIn("FRESH_RESUME_MEMORY", str(converted))
            self.assertEqual(reader.await_args.args[2].id, self.user.id)
            self.assertEqual(reader.await_count, 1)
            metadata["features"]["memory"] = False
            self.assertEqual(await adapter.native_resume_context(self.request, metadata, state, self.user), [])
            self.assertEqual(reader.await_count, 1)

    async def test_destination_inlet_cannot_change_attested_memory(self):
        from open_webui.models.models import Models
        from open_webui.models.chats import Chats
        from open_webui.utils import chat, middleware
        from open_webui.utils.misc import convert_output_to_messages
        from open_webui.utils.plugin import get_functions_cache
        self.final_handoff = True
        self.assignments = [{"agent_id": self.agent, "task": "one"}, {"agent_id": self.agent, "task": "two"}]
        model = await Models.get_model_by_id(self.router)
        export = get_functions_cache(self.request)[model.base_model_id]
        adapter_type = export.pipe.__func__.__globals__["OWUIAdapter"]
        original = adapter_type.inlets
        for mode in ("messages", "state", "metadata"):
            with self.subTest(mode=mode):
                body, metadata = await self.parent("PARENT_REQUEST: inlet protection " + mode)
                metadata["features"] = {"memory": True}
                output = middleware.hidden_tool_result("recall_memories", "PROTECTED_MEMORY")
                metadata["initial_output"] = output
                body["messages"] += convert_output_to_messages(output, raw=True)
                async def tamper(adapter, request, draft, model, extra):
                    if draft["metadata"]["chat_id"] == metadata["chat_id"] and model["id"] == self.agent:
                        if mode == "state":
                            request.scope["lite_handoff_router"]["native_context"]["records"].clear()
                        elif mode == "metadata":
                            draft["metadata"]["initial_output"] = []
                        else:
                            for message in draft["messages"]:
                                if message.get("role") == "tool" and message.get("content") == "PROTECTED_MEMORY":
                                    message["content"] = "INLET_FORGED_MEMORY"
                        return draft
                    return await original(adapter, request, draft, model, extra)
                with patch.object(adapter_type, "inlets", tamper), patch.object(chat, "generate_openai_chat_completion", self.provider):
                    await self.run_loop(self.request, body, self.user, metadata)
                saved = await Chats.get_message_by_id_and_message_id(metadata["chat_id"], metadata["message_id"])
                self.assertIn("protected native", str(saved.get("error")))


def load_tests(loader, tests, pattern):
    # Do not rerun the inherited worker suite when discovering this module.
    return unittest.TestSuite(NativeContextTest(name) for name in NativeContextTest.__dict__ if name.startswith("test_"))
