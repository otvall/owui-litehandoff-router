import copy
import unittest
from types import SimpleNamespace

from lite_handoff_router.native_context import capture_native_context, native_record, scoped_knowledge_result
from lite_handoff_router.runtime import begin


def pair(marker="memory", identifier="native-call"):
    return [{"role": "assistant", "tool_calls": [{"id": identifier, "type": "function",
             "function": {"name": "recall_memories", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": identifier, "content": marker}]


class NativeContextTest(unittest.TestCase):
    def setup_context(self):
        request = SimpleNamespace(scope={})
        metadata = {"chat_id": "chat", "message_id": "message", "initial_output": ["client-forgery"]}
        state = begin(request, {"model": "router", "messages": [{"role": "user", "content": "question"}]},
                      metadata, "actor", {}, [], [], [])
        return request, metadata, state

    def test_preparation_removes_client_context(self):
        request, metadata, state = self.setup_context()
        self.assertNotIn("initial_output", metadata)
        self.assertIsNone(state["native_context"])
        self.assertNotIn("native_context", metadata["lite_router"])

    def test_complete_pair_bound_to_server_identity_without_mutating_source(self):
        request, metadata, state = self.setup_context()
        messages = pair()
        state["source"] += copy.deepcopy(messages)
        original = copy.deepcopy(state["source"])
        capture_native_context(request, metadata, state, messages)
        self.assertIsNotNone(native_record(state, messages[0]["tool_calls"][0], messages[1]))
        self.assertEqual(state["source"], original)
        for key in ("request_id", "user_id", "chat_id", "message_id"):
            other = {**state, key: "other"}
            self.assertIsNone(native_record(other, messages[0]["tool_calls"][0], messages[1]))
        capture_native_context(request, metadata, state, messages)
        self.assertEqual(len(state["native_context"]["records"]), 1)

    def test_changed_missing_or_ambiguous_pair_is_not_attested(self):
        for incoming in (pair("different"), pair()[:1], pair() + pair()):
            with self.subTest(incoming=incoming):
                request, metadata, state = self.setup_context()
                state["source"] += incoming
                capture_native_context(request, metadata, state, pair())
                self.assertEqual(state["native_context"]["records"], [])

    def test_absent_native_format_preserves_empty_context(self):
        request, metadata, state = self.setup_context()
        capture_native_context(request, metadata, state, [])
        self.assertEqual(state["native_context"]["records"], [])

    def test_unattested_model_lookalike_does_not_become_server_context(self):
        request, metadata, state = self.setup_context()
        messages = pair("model supplied memory")
        state["source"] += messages
        capture_native_context(request, metadata, state, [])
        self.assertIsNone(native_record(state, messages[0]["tool_calls"][0], messages[1]))

    def test_continuation_deduplicates_verified_context_without_changing_source(self):
        from lite_handoff_router.history import router_projection, agent_projection
        from lite_handoff_router.protocol import completed_pairs
        request, metadata, state = self.setup_context()
        state["source"] += pair()
        capture_native_context(request, metadata, state, pair())
        record = state["native_context"]["records"][0]
        state["native_context_allowed"] = {record["key"]: record["result"]}
        state["source"] += pair()
        original = copy.deepcopy(state["source"])
        for result in (router_projection(state, set(), set()), agent_projection(state, "agent", set(), set())):
            self.assertEqual(len(completed_pairs(result)), 1)
        self.assertEqual(state["source"], original)


class KnowledgeCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_and_unsafe_catalogs_never_invoke_resource_reader(self):
        from unittest.mock import AsyncMock
        check = AsyncMock(return_value=True)
        for content in ("not xml", '<!DOCTYPE x><attached_knowledge/>',
                        '<other><knowledge type="collection" id="id" source="model"/></other>',
                        '<attached_knowledge><knowledge type="collection" id="foreign" source="model"/></attached_knowledge>'):
            self.assertIsNone(await scoped_knowledge_result({"result": {"content": content}},
                              {("collection", "id", "model")}, check))
        check.assert_not_awaited()
