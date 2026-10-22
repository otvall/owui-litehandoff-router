import copy
import inspect
from importlib.metadata import PackageNotFoundError, version
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from lite_handoff_router.owui_adapter import OWUIAdapter


class NativeAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_official_source_image_version_without_distribution_metadata(self):
        from open_webui import env
        from lite_handoff_router import owui_adapter

        with patch.object(owui_adapter, "version", side_effect=PackageNotFoundError("open-webui")):
            with patch.object(env, "VERSION", "0.12.3"):
                OWUIAdapter()
            with patch.object(env, "VERSION", "0.12.4"):
                with self.assertRaisesRegex(RuntimeError, "requires Open WebUI 0.12.3"):
                    OWUIAdapter()

    async def test_async_native_boundaries(self):
        from open_webui.utils import tools, models, filter, chat
        from open_webui.routers.skills import authorized_skill

        self.assertEqual(version("open-webui"), "0.12.3")
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


if __name__ == "__main__":
    unittest.main()
