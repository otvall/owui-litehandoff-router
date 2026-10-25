import inspect
from importlib.metadata import PackageNotFoundError, version
import unittest
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter


class ReleaseSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_native_version_model_payload_filter_and_binder(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.env import VERSION
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.users import Users
        from open_webui.utils import tools, models, payload, filter

        adapter = OWUIAdapter()
        try:
            installed = version("open-webui")
        except PackageNotFoundError:
            installed = VERSION
        self.assertEqual(installed, adapter.profile.version)
        for function in (models.check_model_access, tools.get_updated_tool_function,
                         tools.get_builtin_tools, filter.get_filter_functions,
                         filter.process_filter_functions, payload.apply_system_prompt_to_body):
            self.assertTrue(inspect.iscoroutinefunction(function), function.__name__)
        suffix = uuid4().hex
        owner = await Users.insert_new_user("smoke-" + suffix, "Native Owner", suffix + "@smoke.test", role="user")
        row = await Models.insert_new_model(ModelForm(id="smoke-" + suffix, name="Smoke", meta={},
            params={"system": "Native {{USER_NAME}}", "temperature": 0.2}), owner.id)
        app = FastAPI()
        app.state.MODELS = {row.id: {"id": row.id, "owned_by": "openai", "info": row.model_dump()}}
        app.state.redis = None
        request = Request({"type": "http", "app": app, "headers": []})
        try:
            model = await adapter.model(request, row.id, owner)
            draft = await adapter.prompt_and_params({"messages": [{"role": "user", "content": "Question"}]},
                                                    model, owner, {})
            self.assertEqual(draft["temperature"], 0.2)
            self.assertIn(owner.name, draft["messages"][0]["content"])
            self.assertEqual(await adapter.inlets(request, draft, model, {}), draft)

            async def operation(value: int, __user__=None):
                return value, __user__["id"]

            bound = await adapter.bind(operation, {"__user__": owner.model_dump()})
            refreshed = await adapter.refresh(bound, {"__user__": {"id": "fresh"}})
            self.assertEqual(await refreshed(value="7"), (7, "fresh"))
        finally:
            await Models.delete_model_by_id(row.id)
            await Users.delete_user_by_id(owner.id)
