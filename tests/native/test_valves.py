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
            self.assertEqual(set(spec["properties"]), {
                "priority", "max_parallel_tasks", "parallel_timeout_seconds",
                "base_tool_ids", "base_skill_ids", "tool_result_history_limit",
                "shared_summary_model_id", "shared_summary_context_percent",
                "router_summary_trigger_tokens", "agent_summary_trigger_tokens",
            })
            self.assertEqual(spec["properties"]["shared_summary_context_percent"]["default"], 60)
            for bad in (True, "0.5", -1):
                result = await client.post(url + "/update", json={"max_parallel_tasks": bad})
                self.assertEqual(result.status_code, 400, (bad, result.text))
            self.assertEqual((await client.get(url)).json()["max_parallel_tasks"], 0)

            text_values = {
                "priority": "-2", "max_parallel_tasks": "0", "parallel_timeout_seconds": "300",
                "tool_result_history_limit": "0", "router_summary_trigger_tokens": "40000",
                "agent_summary_trigger_tokens": "30000", "shared_summary_context_percent": "75",
            }
            previous_values = {
                "tool_result_history_limit": 5, "max_parallel_tasks": 0,
                "context_input_budget_tokens": 12000, "context_model_input_budgets": {"agent": 6000},
                "context_summary_enabled": True, "context_summary_model_id": "summary",
            }
            await Functions.update_function_valves_by_id(identifier, previous_values)
            previous = module.Valves(**(await client.get(url)).json())
            self.assertEqual(previous.tool_result_history_limit, 5)
            self.assertEqual(previous.max_parallel_tasks, 0)
            self.assertNotIn("context_summary_enabled", previous.model_dump())

            result = await client.post(url + "/update", json={**text_values, "shared_summary_model_id": "summary"})
            self.assertEqual(result.status_code, 200, result.text)
            saved = (await client.get(url)).json()
            self.assertEqual(saved, {**{name: int(value) for name, value in text_values.items()},
                                     "shared_summary_model_id": "summary"})
            for bad in (True, "1.5", -1):
                result = await client.post(url + "/update", json={"tool_result_history_limit": bad})
                self.assertEqual(result.status_code, 400, (bad, result.text))
            self.assertEqual((await client.get(url)).json(), saved)
            for key in ("router_summary_trigger_tokens", "agent_summary_trigger_tokens",
                        "shared_summary_context_percent"):
                for bad in (True, "1.5", 0):
                    result = await client.post(url + "/update", json={key: bad})
                    self.assertEqual(result.status_code, 400, (key, bad, result.text))
            result = await client.post(url + "/update", json={"shared_summary_context_percent": 101})
            self.assertEqual(result.status_code, 400, result.text)
            self.assertEqual((await client.get(url)).json(), saved)
            result = await client.post(url + "/update", json={**text_values, "tool_result_history_limit": None})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertIsNone(module.Valves(**(await client.get(url)).json()).tool_result_history_limit)
