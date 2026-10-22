import asyncio
from types import SimpleNamespace
import unittest
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.registry import discover
from lite_handoff_router.protocol import delegate


class NativeRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_groups_multiple_memberships_individual_base_access_and_revocation(self):
        from open_webui.models.users import Users
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.groups import Groups, GroupForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.access_grants import AccessGrants

        suffix = uuid4().hex[:10]
        people = [await Users.insert_new_user(f"{role}-{suffix}", role, f"{role}-{suffix}@example.test", role="admin" if role == "owner" else "user")
                  for role in ("owner", "one", "two", "multi", "individual")]
        owner, one, two, multi, individual = people
        ga = await Groups.insert_new_group(owner.id, GroupForm(name="A-" + suffix, description="A"))
        gb = await Groups.insert_new_group(owner.id, GroupForm(name="B-" + suffix, description="B"))
        child = await Groups.insert_new_group(owner.id, GroupForm(name="child-" + suffix, description="inherited", parent_group_id=ga.id))
        await Groups.add_users_to_group(child.id, [one.id])
        await Groups.add_users_to_group(ga.id, [multi.id])
        await Groups.add_users_to_group(gb.id, [two.id, multi.id])
        models, catalog = {"router": {"pipe": {"type": "pipe", "id": "router"}}}, {}
        grants = []
        for letter, group in (("a", ga), ("b", gb)):
            model_id, base_id, skill_id = (f"{prefix}-{letter}-{suffix}" for prefix in ("agent", "base", "route"))
            for identifier, base in ((base_id, None), (model_id, base_id)):
                row = await Models.insert_new_model(ModelForm(id=identifier, name=identifier, base_model_id=base, params={}, meta={"hidden": True}), owner.id)
                models[identifier] = {"id": identifier, "info": row.model_dump(), "owned_by": "openai"}
                await AccessGrants.grant_access("model", identifier, "group", group.id, "read")
                grants.append(("model", identifier, "group", group.id, "read"))
            await Skills.insert_new_skill(owner.id, SkillForm(id=skill_id, name=skill_id, description="Route.", content="Instructions", meta={}))
            await AccessGrants.grant_access("skill", skill_id, "group", group.id, "read")
            catalog[model_id] = skill_id
            if letter == "a":
                for kind, identifier in (("model", model_id), ("model", base_id), ("skill", skill_id)):
                    await AccessGrants.grant_access(kind, identifier, "user", individual.id, "read")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
        adapter = OWUIAdapter()

        async def available(user):
            entries, _ = await discover(adapter, request, user, catalog, "router")
            return set(entries)

        a, b = list(catalog)
        actual = await asyncio.gather(*(available(user) for user in (one, two, multi, individual)))
        self.assertEqual(actual, [{a}, {b}, {a, b}, {a}])
        snapshot, _ = await discover(adapter, request, one, catalog, "router")
        state = {"request_id": "current", "active_agent": None, "pending": [], "registry": snapshot, "phase": "router"}
        # A model grant alone cannot bypass a revoked base-model grant.
        await AccessGrants.revoke_access(*grants[0])
        with self.assertRaises(Exception):
            await delegate(adapter, request, one, state, a)
        self.assertEqual(state["pending"], [])
        self.assertIsNone(state["active_agent"])
        self.assertEqual(await available(one), set())
        self.assertEqual(await available(multi), {b})
        self.assertEqual(await available(individual), {a})
        await AccessGrants.revoke_access("skill", catalog[a], "user", individual.id, "read")
        self.assertEqual(await available(individual), set())
        await Models.toggle_model_by_id(b)
        self.assertEqual(await available(two), set())
