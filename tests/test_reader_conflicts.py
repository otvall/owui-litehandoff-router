from types import SimpleNamespace
import unittest

from lite_handoff_router.model_execution import prepare_model
from lite_handoff_router.runtime import begin
from tests.test_isolation import IsolatedAdapter
from tests.test_router import User


class ConflictAdapter(IsolatedAdapter):
    change = "callable"

    async def inlets(self, request, body, model, extra):
        if self.change == "callable":
            async def unrestricted(id: str):
                return "Foreign instructions"
            body["metadata"]["tools"]["view_skill"]["callable"] = unrestricted
        elif self.change == "schema":
            body["tools"][0]["function"]["parameters"] = {"changed": True}
        return body


class ReaderConflictsTest(unittest.IsolatedAsyncioTestCase):
    async def test_inlet_cannot_replace_reader_callable_or_schema(self):
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={
            "a": {"id": "a", "owned_by": "openai", "info": {"params": {"system": "A"}, "meta": {"skillIds": ["own"]}}},
        })))
        body, metadata = {"model": "router", "messages": [{"role": "user", "content": "task"}]}, {}
        state = begin(request, body, metadata, "u", {}, [], [], [])
        for change in ("callable", "schema"):
            adapter = ConflictAdapter()
            adapter.change = change
            with self.assertRaisesRegex(ValueError, "protected Tool"):
                await prepare_model(adapter, request, body, User(), {}, state, "unused", {}, target="a")
