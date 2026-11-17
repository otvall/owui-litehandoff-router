"""Exercise the real Function Valves endpoint with OWUI's text-input payload."""

from pathlib import Path
import unittest
from uuid import uuid4


class NativeValvesTest(unittest.IsolatedAsyncioTestCase):
    async def test_ui_integer_text_saves_zero_and_preserves_runtime_disable(self):
        import httpx
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.users import Users
        from open_webui.models.functions import Functions, FunctionForm
        from open_webui.routers import functions
        from open_webui.utils.auth import get_admin_user
        from open_webui.utils.plugin import load_function_module_by_id, set_function_module_in_cache

        identifier = "valves_" + uuid4().hex
        user = await Users.insert_new_user(identifier, "Valves tester", identifier + "@example.test", role="admin")
        app = FastAPI()
        app.state.redis = None
        app.include_router(functions.router, prefix="/api/v1/functions")
        app.dependency_overrides[get_admin_user] = lambda: user
        request = Request({"type": "http", "app": app, "headers": []})
        content = (Path(__file__).resolve().parents[2] / "dist/router_preparation.py").read_text()
        module, kind, _, source = await load_function_module_by_id(identifier, content)
        await Functions.insert_new_function(user.id, kind, FunctionForm(
            id=identifier, name="Router Preparation", content=content, meta={}), module=module)
        set_function_module_in_cache(request, identifier, content, module, source)
        url = "/api/v1/functions/id/" + identifier + "/valves"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post(url + "/update", json={
                "max_parallel_tasks": "0", "parallel_timeout_seconds": "300"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json(), {"max_parallel_tasks": 0, "parallel_timeout_seconds": 300})
            saved = await client.get(url)
            self.assertEqual(saved.json()["max_parallel_tasks"], 0)
            state = source.group_state(module.Valves(**saved.json()).max_parallel_tasks)
            self.assertEqual(state["max_tasks"], 0)
            spec = (await client.get(url + "/spec")).json()
            self.assertEqual(spec["properties"]["max_parallel_tasks"]["minimum"], 0)
            for bad in (True, "0.5", -1):
                result = await client.post(url + "/update", json={"max_parallel_tasks": bad})
                self.assertEqual(result.status_code, 400, (bad, result.text))
            self.assertEqual((await client.get(url)).json()["max_parallel_tasks"], 0)

            text_values = {
                "priority": "-2", "max_parallel_tasks": "0", "parallel_timeout_seconds": "300",
                "tool_result_history_limit": "0", "context_input_budget_tokens": "12000",
                "context_safety_margin_tokens": "0", "context_model_input_budgets": {"agent": "6000"},
                "context_summary_trigger_ratio": "0.9", "context_summary_target_ratio": "0.7",
                "context_summary_input_budget_tokens": "8192", "context_summary_max_tokens": "1024",
                "context_summary_timeout_seconds": "60", "context_summary_max_chunks": "8",
            }
            result = await client.post(url + "/update", json=text_values)
            self.assertEqual(result.status_code, 200, result.text)
            saved = (await client.get(url)).json()
            for name, value in text_values.items():
                expected = ({"agent": 6000} if isinstance(value, dict) else
                            float(value) if name.endswith("_ratio") else int(value))
                self.assertEqual(saved[name], expected)
                self.assertIs(type(saved[name]), type(expected))
            policy = module.Valves(**saved).snapshot()
            self.assertEqual(policy["tool_result_history_limit"], 0)
            self.assertEqual(policy["context_safety_margin_tokens"], 0)
            self.assertEqual(policy["context_model_input_budgets"], {"agent": 6000})
            for invalid in ({"tool_result_history_limit": "1.5"}, {"context_summary_target_ratio": "nan"},
                            {"context_safety_margin_tokens": True}, {"context_summary_timeout_seconds": "0"},
                            {"context_safety_margin_tokens": 0, "context_model_input_budgets": {"a": True}}):
                result = await client.post(url + "/update", json=invalid)
                self.assertEqual(result.status_code, 400, (invalid, result.text))
            self.assertEqual((await client.get(url)).json(), saved)
            result = await client.post(url + "/update", json={
                **text_values, "tool_result_history_limit": None, "context_input_budget_tokens": None})
            self.assertEqual(result.status_code, 200, result.text)
            policy = module.Valves(**(await client.get(url)).json()).snapshot()
            self.assertIsNone(policy["tool_result_history_limit"])
            self.assertIsNone(policy["context_input_budget_tokens"])
