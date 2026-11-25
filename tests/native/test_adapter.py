import copy
import inspect
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from lite_handoff_router.owui_adapter import OWUIAdapter


class NativeAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_native_boundaries(self):
        from open_webui.utils import tools, models, filter, chat
        from open_webui.routers.skills import authorized_skill

        for function in (
            models.check_model_access, models.get_filtered_models, tools.get_tools,
            tools.connect_mcp_server, tools.get_builtin_tools,
            tools.get_updated_tool_function, filter.get_filter_functions,
            filter.process_filter_functions, chat.generate_chat_completion, authorized_skill,
        ):
            self.assertTrue(inspect.iscoroutinefunction(function), function.__name__)

    async def test_real_refresh_rebinds_injections(self):
        adapter = OWUIAdapter()

        async def operation(value: int, __messages__=None, __files__=None):
            return value, __messages__, __files__

        bound = await adapter.bind(operation, {"__messages__": ["old"], "__files__": []})
        updated = await adapter.refresh(bound, {"__messages__": ["new"], "__files__": ["file"]})
        self.assertEqual(await updated(value="3"), (3, ["new"], ["file"]))
        self.assertIs(updated.__function__, operation)

    async def test_pinned_context_survives_real_refresh_while_files_are_updated(self):
        from lite_handoff_router.binding import bind_tool_context

        adapter = OWUIAdapter()
        own = [{"role": "system", "content": "A own inlet context"}]

        async def operation(__messages__=None, __files__=None):
            return __messages__, __files__

        initial = await adapter.bind(operation, {"__messages__": own, "__files__": ["old"]})
        tool = await bind_tool_context(adapter, {"callable": initial, "spec": {}}, own)
        refreshed = await adapter.refresh(tool["callable"], {
            "__messages__": [{"role": "system", "content": "Outer Router"}], "__files__": ["new-file"],
        })
        self.assertEqual(await refreshed(), (own, ["new-file"]))

    async def test_selected_prompt_and_params_on_native_continuation(self):
        adapter = OWUIAdapter()
        model = {"owned_by": "openai", "info": {"params": {
            "system": "Agent {{USER_NAME}} {{ticket}}", "temperature": 0.2,
            "top_p": 0.7, "top_k": 12, "custom_params": {"seed": "23"},
        }}}
        from open_webui.models.users import UserModel

        user = UserModel(id="u", name="Test User", email="u@example.test", role="user",
                         created_at=1, updated_at=1, last_active_at=1)
        metadata = {"variables": {"{{ticket}}": "T42"}}
        source = [{"role": "user", "content": "question"}]
        for _ in range(2):
            body = {"messages": copy.deepcopy(source)}
            result = await adapter.prompt_and_params(body, model, user, metadata)
            self.assertEqual(result["temperature"], 0.2)
            self.assertEqual(result["seed"], 23)
            systems = [m for m in result["messages"] if m["role"] == "system"]
            self.assertEqual(len(systems), 1)
            self.assertIn("Test User", systems[0]["content"])
            self.assertIn("T42", systems[0]["content"])

    async def test_native_completion_explicitly_preserves_prepared_prompt(self):
        from open_webui.utils import chat

        adapter = OWUIAdapter()
        request = SimpleNamespace(
            state=SimpleNamespace(bypass_system_prompt=True, metadata={}),
            app=SimpleNamespace(state=SimpleNamespace(MODELS={"llm": {"id": "llm", "owned_by": "openai"}})),
        )
        body = {"model": "llm", "messages": [{"role": "system", "content": "Agent"}]}
        provider = AsyncMock(return_value={"choices": []})
        with patch.object(chat, "generate_openai_chat_completion", provider):
            await adapter.complete(request, body, SimpleNamespace(role="admin"))
        self.assertTrue(request.state.bypass_system_prompt)
        self.assertEqual(provider.call_args.kwargs["form_data"]["messages"], body["messages"])

    async def test_compaction_is_rejected_before_calling_provider(self):
        from open_webui.models.config import Config
        from open_webui.utils import chat

        original = await Config.get("chat.context_compaction.enable", False)
        try:
            await Config.upsert({"chat.context_compaction.enable": True})
            provider = AsyncMock()
            with patch.object(chat, "generate_chat_completion", provider):
                with self.assertRaisesRegex(RuntimeError, "requires full history"):
                    await OWUIAdapter().complete(None, {"messages": []}, None)
                provider.assert_not_awaited()
        finally:
            await Config.upsert({"chat.context_compaction.enable": original})

    async def test_summary_requests_json_and_accepts_only_completed_text(self):
        from open_webui.models.config import Config
        from open_webui.utils import chat, models
        from starlette.responses import JSONResponse, StreamingResponse
        from lite_handoff_router.summary_response import SummaryGenerationError

        request = SimpleNamespace(
            url=SimpleNamespace(path="/api/chat/completions"),
            app=SimpleNamespace(state=SimpleNamespace(MODELS={"llm": {"id": "llm", "owned_by": "openai"}})),
        )
        messages = [{"role": "user", "content": "Old question"}]
        template = "Custom {{PREVIOUS_SUMMARY}} / {{COMPACTED_MESSAGES}} / {{RECENT_MESSAGES}}"
        for mode in ("json", "json_text", "json_bytes", "response_json", "stream", "stream_final_line", "interrupted", "stream_eof", "stream_non_sse",
                     "stream_non_sse_incomplete", "stream_non_sse_truncated", "stream_response_json", "stream_html",
                     "stream_length", "length", "http_error", "empty", "tool_call"):
            with self.subTest(mode=mode):
                choice = {"message": {"content": "Result"}, "finish_reason": "stop"}
                response = {"choices": [choice]}
                if mode == "json_text":
                    response = json.dumps(response)
                elif mode == "json_bytes":
                    response = json.dumps(response).encode()
                elif mode in ("stream", "stream_final_line", "interrupted", "stream_eof", "stream_length"):
                    chunks = ['data: {"choices":[{"delta":{"content":"Result"}}]}\n\n']
                    if mode == "stream":
                        chunks.append('data: {"choices":[{"finish_reason":"stop"}]}\n\n')
                    if mode in ("stream", "interrupted"):
                        chunks.append('data: [DONE]\n\n')
                    if mode == "stream_final_line":
                        chunks.extend(('data: {"choices":[{"finish_', 'reason":"stop"}]}'))
                    if mode == "stream_length":
                        chunks.append('data: {"choices":[{"finish_reason":"length"}]}')
                    response = StreamingResponse(iter(chunks), media_type="text/event-stream")
                elif mode.startswith("stream_non_sse"):
                    if mode == "stream_non_sse_incomplete":
                        choice["finish_reason"] = None
                    raw = json.dumps({"choices": [choice]}, indent=2)
                    if mode == "stream_non_sse_truncated":
                        raw = raw[:-1]
                    response = StreamingResponse(iter((raw[:85], raw[85:])), media_type="text/event-stream")
                elif mode in ("response_json", "stream_response_json"):
                    document = {"object": "response", "status": "completed", "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "Result"}]}
                    ]}
                    response = (StreamingResponse(iter([json.dumps(document)]), media_type="text/event-stream")
                                if mode == "stream_response_json" else document)
                elif mode == "stream_html":
                    response = StreamingResponse(iter(["<html><title>502 Bad Gateway</title>Result</html>"]),
                                                 media_type="text/event-stream")
                elif mode == "length":
                    choice["finish_reason"] = "length"
                elif mode == "http_error":
                    response = JSONResponse({"error": {"message": "Fixture unavailable"}}, status_code=503)
                elif mode == "empty":
                    choice["message"]["content"] = " "
                elif mode == "tool_call":
                    choice["message"]["tool_calls"] = [{"type": "function"}]
                with (
                    patch.object(chat, "generate_openai_chat_completion", AsyncMock(return_value=response)) as provider,
                    patch.object(Config, "get_many", AsyncMock(return_value={
                        "chat.context_compaction.prompt_template": template,
                        "task.model.params": {"temperature": 0.2, "stream": True,
                                              "stream_options": {"include_usage": True}},
                    })),
                    patch.object(Config, "get", AsyncMock(return_value=False)),
                    patch.object(models, "check_model_access", AsyncMock()) as access,
                ):
                    operation = OWUIAdapter().generate_shared_summary(
                        request, SimpleNamespace(id="u", role="admin"), "llm", messages, "Earlier")
                    if mode in ("json", "json_text", "json_bytes", "response_json", "stream", "stream_final_line",
                                "stream_non_sse", "stream_response_json", "empty"):
                        self.assertEqual(await operation, "" if mode == "empty" else "Result")
                    else:
                        expected = {
                            "interrupted": r"\[DONE\].*finish_reason.*SSE-кадров: 1.*фрагментов текста: 1",
                            "stream_eof": r"без завершающего события.*SSE-кадров: 1.*фрагментов текста: 1",
                            "stream_non_sse_incomplete": r"без завершающего события.*JSON-событий без SSE: 1",
                            "stream_non_sse_truncated": r"без завершающего события.*SSE-кадров: 0",
                            "stream_html": r"без завершающего события.*SSE-кадров: 0.*HTML/XML, код 502",
                            "length": "finish_reason=length", "stream_length": "finish_reason=length",
                            "http_error": "Fixture unavailable", "tool_call": "вызов инструмента",
                        }
                        with self.assertRaisesRegex(RuntimeError if mode == "http_error" else SummaryGenerationError,
                                                    expected[mode]) as failure:
                            await operation
                        if mode in ("interrupted", "stream_eof", "stream_non_sse_truncated", "stream_html"):
                            self.assertNotIn("Result", str(failure.exception))
                access.assert_awaited_once()
                body = provider.await_args.kwargs["form_data"]
                self.assertIs(body["stream"], False)
                self.assertNotIn("stream_options", body)
                self.assertEqual(body["messages"][0]["content"],
                                 "Custom Earlier / USER: Old question / ")
                self.assertEqual(body["temperature"], 0.2)
                self.assertEqual(body["metadata"]["task"], "context_compaction")


if __name__ == "__main__":
    unittest.main()
