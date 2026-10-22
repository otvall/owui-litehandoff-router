"""Observe provider arguments, then execute through OWUI's real argument filter."""

import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from lite_handoff_router.observation import observe_response
from lite_handoff_router.protocol import confirmed_target, delegate_tool
from lite_handoff_router.runtime import begin


class NativeDelegateArgumentsTest(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, arguments, *, extra_call=False, observed_agent=None):
        from open_webui.utils.middleware import execute_tool_call

        user = SimpleNamespace(id="execution-user")
        owner = SimpleNamespace(id="router-owner")
        adapter = SimpleNamespace(
            user=AsyncMock(return_value=user),
            model=AsyncMock(return_value={"id": "router"}),
            owner=AsyncMock(return_value=owner),
            skill=AsyncMock(return_value={"id": "route-a"}),
        )
        request = SimpleNamespace(scope={})
        metadata = {"chat_id": "local:delegate-test", "message_id": "answer",
                    "params": {"tool_approval_mode": "full"}}
        body = {"model": "router", "messages": [{"role": "user", "content": "Original user task"}]}
        state = begin(request, body, metadata, user.id, {"a": {"skill_id": "route-a"}}, [], [], [])
        tool = delegate_tool(adapter, request, user, metadata, state)
        metadata["tools"]["lite_delegate"] = tool
        call = {"id": "delegate-call", "type": "function", "function": {
            "name": "lite_delegate", "arguments": json.dumps(arguments)}}
        observed = copy.deepcopy(call)
        if observed_agent is not None:
            observed["function"]["arguments"] = json.dumps({**arguments, "agent_id": observed_agent})
        calls = [observed]
        if extra_call:
            calls.append({"id": "other-call", "type": "function", "function": {
                "name": "view_skill", "arguments": '{"id":"route-a"}'}})
        observe_response({"choices": [{"message": {"tool_calls": calls}}]}, state, {"kind": "router"})
        params, result, *_ = await execute_tool_call(body, metadata, None, call)
        return params, result, state, body, call, tool

    async def test_native_filter_ignores_extra_task_and_handoff_uses_original_chat(self):
        for extras in ({}, {"task": "Invented replacement task"}, {"task": {"nested": "ignored"}}):
            with self.subTest(extras=extras):
                params, result, state, body, call, _ = await self.invoke({"agent_id": "a", **extras})
                self.assertEqual(params, {"agent_id": "a"})
                self.assertIsInstance(result, str, result)
                marker = json.loads(result)
                self.assertEqual(marker["agent_id"], "a")
                self.assertEqual(state["source"], body["messages"])
                self.assertEqual(state["audit"][0]["arguments"], {"agent_id": "a", **extras})
                messages = body["messages"] + [
                    {"role": "assistant", "tool_calls": [call]},
                    {"role": "tool", "tool_call_id": call["id"], "content": result},
                ]
                self.assertEqual(confirmed_target(state, messages), "a")

    async def test_extra_task_cannot_hide_another_tool_call(self):
        _, result, state, *_ = await self.invoke({"agent_id": "a", "task": "ignored"}, extra_call=True)
        self.assertIn("only Tool call", result["error"])
        self.assertEqual(state["pending"], [])

    async def test_extra_task_cannot_change_observed_destination(self):
        _, result, state, *_ = await self.invoke({"agent_id": "a", "task": "ignored"}, observed_agent="b")
        self.assertIn("arguments", result["error"])
        self.assertEqual(state["pending"], [])

    async def test_extra_task_cannot_bypass_agent_registry(self):
        _, result, state, *_ = await self.invoke({"agent_id": "unlisted", "task": "ignored"})
        self.assertIn("exact Workspace Model ID", result["error"])
        self.assertEqual(state["pending"], [])
