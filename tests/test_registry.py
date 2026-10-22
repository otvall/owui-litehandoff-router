from types import SimpleNamespace
import unittest

from lite_handoff_router.registry import discover


class RegistryAdapter:
    async def owner(self, model):
        return SimpleNamespace(id="owner")

    async def model(self, request, model_id, user):
        model = request.app.state.MODELS.get(model_id)
        if not model or model.get("denied"):
            raise ValueError("Model unavailable")
        return model

    async def skill(self, skill_id, user):
        if skill_id in ("missing", "inactive", "unreadable"):
            raise ValueError(skill_id)
        return {"id": skill_id, "name": skill_id, "description": "Purpose"}


class RegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_resources_and_recursive_pipe_targets_have_diagnostics(self):
        models = {
            "router": {"id": "router", "pipe": {"type": "pipe"}, "info": {"base_model_id": "router-pipe"}},
            "same-pipe": {"id": "same-pipe", "pipe": {"type": "pipe"}, "info": {"base_model_id": "router-pipe"}},
            "other-pipe": {"id": "other-pipe", "pipe": {"type": "pipe"}, "info": {"base_model_id": "another-pipe"}},
            "ok": {"id": "ok", "name": "Cached name", "info": {"name": "Specialist A"}},
            "denied": {"id": "denied", "denied": True},
            **{key: {"id": key} for key in ("missing-skill", "inactive-skill", "unreadable-skill")},
        }
        catalog = {"router": "route", "same-pipe": "route", "other-pipe": "route-other", "ok": "route-ok",
                   "denied": "route", "missing-skill": "missing", "inactive-skill": "inactive", "unreadable-skill": "unreadable"}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
        entries, diagnostics = await discover(RegistryAdapter(), request, SimpleNamespace(id="u"), catalog, "router")
        self.assertEqual(set(entries), {"ok", "other-pipe"})
        self.assertEqual(len(diagnostics), 6)
        reasons = {item["agent_id"]: item["reason"] for item in diagnostics}
        self.assertIn("Router Pipe", reasons["same-pipe"])
        self.assertEqual(reasons["inactive-skill"], "inactive")
        self.assertEqual(entries["ok"]["skill_id"], "route-ok")
        self.assertEqual(entries["ok"]["agent_name"], "Specialist A")
        self.assertEqual(entries["ok"]["name"], "route-ok")
