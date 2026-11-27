import asyncio
import json
from types import SimpleNamespace
import unittest

from lite_handoff_router.registry import discover
from lite_handoff_router.routing_profiles import routing_entries


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


def shared_snapshot(index, *paths):
    return {"name": "Shared routes", "version_id": "v1", "data": {"files": [
        {"path": "SKILL.md", "content": "Routing rules"},
        {"path": "agents.json", "content": index if isinstance(index, str) else json.dumps(index)},
        *({"path": path, "content": "Profile"} for path in paths),
    ]}}


def route(path):
    return {"path": path, "description": "Forecast & review"}


class RoutingFilesTest(unittest.TestCase):
    def test_valid_and_invalid_entries_are_independent(self):
        entries, invalid = routing_entries(shared_snapshot({
            "ok": route("profiles/ok.md"),
            "missing": route("profiles/missing.md"),
            "outside": route("profiles/../SKILL.md"),
            "ambiguous": route("profiles//ok.md"),
            "shared1": route("profiles/shared.md"),
            "shared2": route("profiles/shared.md"),
        }, "profiles/ok.md", "profiles/shared.md"))
        self.assertEqual(entries, {"ok": route("profiles/ok.md")})
        self.assertEqual(set(invalid), {"missing", "outside", "ambiguous", "shared1", "shared2"})

    def test_invalid_index_does_not_create_a_partial_registry(self):
        for index in ('[]', '{', '{"a": {}, "a": {}}', '{"a": {"path": "a", "path": "b"}}'):
            with self.subTest(index=index), self.assertRaises(ValueError):
                routing_entries(shared_snapshot(index))


class RegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_shared_registry_uses_router_owner_and_scopes_simultaneous_requests(self):
        class ScopedDirectory(RegistryAdapter):
            async def routing_skill_snapshot(self, skill_id, owner):
                self.requests.append((skill_id, owner.id))
                return shared_snapshot({
                    "a": route("profiles/a.md"), "b": route("profiles/b.md"),
                    "missing": route("profiles/missing.md"), "router": route("profiles/router.md"),
                }, "profiles/a.md", "profiles/b.md", "profiles/router.md")

            async def model(self, request, model_id, user):
                if model_id in {"a", "b"} and user.id != model_id:
                    raise ValueError("Model unavailable")
                return await super().model(request, model_id, user)

        directory = ScopedDirectory()
        directory.requests = []
        models = {key: {"id": key} for key in ("router", "a", "b", "legacy", "missing")}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
        alice, bob = await asyncio.gather(*(discover(
            directory, request, SimpleNamespace(id=actor), {"legacy": "old"}, "router",
            routing_skill_id="shared") for actor in ("a", "b")))
        for actor, (entries, diagnostics) in zip(("a", "b"), (alice, bob)):
            self.assertEqual(set(entries), {actor})
            self.assertEqual(entries[actor]["path"], f"profiles/{actor}.md")
            self.assertEqual(entries[actor]["skill_version_id"], "v1")
            self.assertEqual(entries[actor]["description"], "Forecast & review")
            self.assertTrue(any(item["agent_id"] == "missing" for item in diagnostics))
            self.assertTrue(any(item["agent_id"] == "router" and "Router Pipe" in item["reason"]
                                for item in diagnostics))
        self.assertEqual(directory.requests, [("shared", "owner"), ("shared", "owner")])

    async def test_unusable_configured_skill_does_not_fall_back(self):
        class BadDirectory(RegistryAdapter):
            async def routing_skill_snapshot(self, skill_id, user):
                return shared_snapshot("invalid")

        models = {key: {"id": key} for key in ("router", "old")}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
        user = SimpleNamespace(id="u")
        entries, diagnostics = await discover(
            BadDirectory(), request, user, {"old": "old-route"}, "router", routing_skill_id="unusable")
        self.assertEqual(entries, {})
        self.assertEqual(len(diagnostics), 1)
        entries, _ = await discover(BadDirectory(), request, user, {"old": "old-route"}, "router")
        self.assertEqual(set(entries), {"old"})

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
