import json
from pathlib import Path
import runpy
import unittest
from uuid import uuid4

from lite_handoff_router.authoring import Tools


class NativeAuthoringTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.users import Users

        self.suffix = uuid4().hex[:10]
        self.owner, self.writer, self.reader = [
            await Users.insert_new_user(f"author-{role}-{self.suffix}", role,
                                        f"{role}-{self.suffix}@authoring.example.test", role="user")
            for role in ("owner", "writer", "reader")
        ]
        self.model_id = "authoring-model-" + self.suffix
        await Models.insert_new_model(ModelForm(
            id=self.model_id, name=self.model_id, params={"system": "SECRET_AGENT_PROMPT"},
            meta={"skillIds": [], "toolIds": []}), self.owner.id)
        from open_webui.models.access_grants import AccessGrants
        await AccessGrants.grant_access("model", self.model_id, "user", self.writer.id, "write")
        await AccessGrants.grant_access("model", self.model_id, "user", self.reader.id, "read")
        self.request = Request({"type": "http", "app": FastAPI(), "headers": []})
        self.tools = Tools()

    def profile(self):
        return {
            "agent_model_id": self.model_id, "agent_name": "Прогнозист",
            "skill_name": "route-authoring-" + self.suffix,
            "routing_description": "Прогноз по временному ряду.",
            "routing_criteria": "Рассчитать прогноз.",
            "other_route_criteria": "Объяснение терминов без расчёта прогноза вне профиля.",
            "required_inputs": "Временной ряд, горизонт, единицы.",
            "independent_task_scope": "Один прогноз одного ряда.",
            "final_result_scope": "Сравнение готовых прогнозов.",
            "expected_result": "Прогноз с допущениями.",
            "positive_examples": ["Рассчитай продажи на декабрь."],
            "negative_examples": ["Что такое сезонность?"],
        }

    async def test_read_only_actor_cannot_inspect_or_render_even_with_forged_admin_role(self):
        from open_webui.models.access_grants import AccessGrants

        actor = {**self.reader.model_dump(), "role": "admin"}
        listed = json.loads(await self.tools.list_agent_models(self.model_id, __user__=actor))
        self.assertEqual(listed["items"], [])
        with self.assertRaisesRegex(ValueError, "Write access"):
            await self.tools.inspect_agent_model(self.model_id, __user__=actor)
        with self.assertRaisesRegex(ValueError, "Write access"):
            await self.tools.render_routing_skill(self.profile(), __user__=actor)
        result = json.loads(await self.tools.inspect_agent_model(self.model_id, __user__=self.writer.model_dump()))
        self.assertEqual(result["system_prompt"], "SECRET_AGENT_PROMPT")
        await AccessGrants.revoke_access("model", self.model_id, "user", self.writer.id, "write")
        with self.assertRaises(Exception):
            await self.tools.inspect_agent_model(self.model_id, __user__=self.writer.model_dump())

    async def test_private_skill_is_not_opened_through_model_write_grant_and_detachment_is_rechecked(self):
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.access_grants import AccessGrants

        skill_id = "private-authoring-" + self.suffix
        await Skills.insert_new_skill(self.owner.id, SkillForm(id=skill_id, name=skill_id, content="PRIVATE_SKILL"))
        form = ModelForm(id=self.model_id, name=self.model_id, params={"system": "Prompt"},
                         meta={"skillIds": [skill_id]})
        await Models.update_model_by_id(self.model_id, form, user_id=self.owner.id)
        actor = self.writer.model_dump()
        result = json.loads(await self.tools.inspect_agent_model(self.model_id, __user__=actor))
        self.assertEqual(result["skills"][0]["status"], "unavailable")
        with self.assertRaises(Exception):
            await self.tools.read_agent_skill_file(self.model_id, skill_id, __user__=actor)
        await AccessGrants.grant_access("skill", skill_id, "user", self.writer.id, "read")
        page = json.loads(await self.tools.read_agent_skill_file(self.model_id, skill_id, max_chars=7, __user__=actor))
        self.assertEqual(page["content"], "PRIVATE")
        self.assertEqual(page["next_offset"], 7)
        form.meta = type(form.meta)(skillIds=[])
        await Models.update_model_by_id(self.model_id, form, user_id=self.owner.id)
        with self.assertRaisesRegex(ValueError, "not attached"):
            await self.tools.read_agent_skill_file(self.model_id, skill_id, __user__=actor)

    async def test_save_creates_private_skill_and_update_preserves_files_grants_and_activity(self):
        from fastapi import HTTPException
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.groups import Groups, GroupForm
        from open_webui.utils.skill_files import frontmatter

        group = await Groups.insert_new_group(self.owner.id, GroupForm(
            name="authors-" + self.suffix, description="Skill authors",
            permissions={"workspace": {"skills": True}}))
        await Groups.add_users_to_group(group.id, [self.writer.id])
        actor = self.writer.model_dump()
        skill_id = "route-authoring-" + self.suffix
        saved = json.loads(await self.tools.save_routing_skill(self.profile(), skill_id,
                          __user__=actor, __request__=self.request))
        first = await Skills.get_skill_by_id(skill_id)
        self.assertEqual(first.user_id, self.writer.id)
        self.assertEqual(first.access_grants, [])
        self.assertTrue(first.is_active)
        self.assertEqual(frontmatter(first.content), {
            "name": self.profile()["skill_name"], "description": self.profile()["routing_description"],
        })
        self.assertEqual(saved["registry_entry"], {self.model_id: skill_id})
        self.assertFalse(saved["registry_updated"])
        # Empty version means create only, not an implicit overwrite.
        with self.assertRaises(HTTPException):
            await self.tools.save_routing_skill(self.profile(), skill_id, __user__=actor, __request__=self.request)
        await AccessGrants.grant_access("skill", skill_id, "user", self.reader.id, "read")
        current = await Skills.update_skill_by_id(skill_id, {
            "files": first.data["files"] + [{"path": "notes.txt", "content": "Keep me"}],
            "is_active": False, "expected_version_id": first.version_id,
        }, user_id=self.writer.id)
        inspected = json.loads(await self.tools.inspect_routing_skill(skill_id, __user__=actor))
        self.assertEqual(inspected["version_id"], current.version_id)
        updated_fields = {**self.profile(), "expected_result": "Updated forecast with sources"}
        updated = json.loads(await self.tools.save_routing_skill(updated_fields, skill_id, current.version_id,
                            __user__=actor, __request__=self.request))
        row = await Skills.get_skill_by_id(skill_id)
        self.assertFalse(updated["is_active"])
        self.assertNotEqual(row.version_id, current.version_id)
        self.assertIn("Updated forecast with sources", row.content)
        self.assertEqual(set(frontmatter(row.content)), {"name", "description"})
        self.assertIn({"path": "notes.txt", "content": "Keep me"}, row.data["files"])
        self.assertTrue(await AccessGrants.has_access(user_id=self.reader.id, resource_type="skill",
                                                    resource_id=skill_id, permission="read"))
        with self.assertRaises(HTTPException) as error:
            await self.tools.save_routing_skill(self.profile(), skill_id, current.version_id,
                                               __user__=actor, __request__=self.request)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual((await Skills.get_skill_by_id(skill_id)).version_id, row.version_id)
        # A writer of the agent still cannot overwrite another user's private Skill.
        private_id = "private-route-" + self.suffix
        private = await Skills.insert_new_skill(self.owner.id, SkillForm(id=private_id, name=private_id, content="Keep"))
        with self.assertRaises(HTTPException):
            await self.tools.save_routing_skill(updated_fields, private_id, private.version_id,
                                               __user__=actor, __request__=self.request)
        self.assertEqual((await Skills.get_skill_by_id(private_id)).content, "Keep")

    async def test_standalone_tool_schemas_and_skill_zip_are_accepted_by_native_parsers(self):
        from open_webui.utils.tools import get_tool_specs
        from open_webui.utils.skill_files import parse_import

        root = Path(__file__).resolve().parents[2]
        loaded = runpy.run_path(str(root / "dist/routing_skill_authoring.py"))
        instance = loaded["Tools"]()
        specs = get_tool_specs(instance)
        self.assertEqual({s["name"] for s in specs}, {
            "list_agent_models", "inspect_agent_model", "read_agent_skill_file",
            "inspect_routing_skill", "render_routing_skill", "save_routing_skill",
        })
        for spec in specs:
            self.assertFalse(any(name.startswith("__") for name in spec["parameters"]["properties"]))
        packages = parse_import((root / "dist/routing-skill-authoring.zip").read_bytes(), "routing-skill-authoring.zip")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["name"], "routing-skill-authoring")
        result = json.loads(await instance.inspect_agent_model(self.model_id, __user__=self.writer.model_dump()))
        self.assertEqual(result["system_prompt"], "SECRET_AGENT_PROMPT")

    async def test_local_tool_inspection_never_loads_source_and_respects_resource_grants(self):
        from open_webui.models.tools import Tools as ToolRows, ToolForm
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.access_grants import AccessGrants

        tool_id = "authoring-tool-" + self.suffix
        schema = {"name": "forecast", "description": "Forecast one series",
                  "parameters": {"type": "object", "properties": {}}}
        await ToolRows.insert_new_tool(self.owner.id, ToolForm(
            id=tool_id, name=tool_id, meta={},
            content="raise RuntimeError('INSPECTION_MUST_NOT_EXECUTE_SOURCE')"), [schema])
        await Models.update_model_by_id(self.model_id, ModelForm(
            id=self.model_id, name=self.model_id, params={}, meta={"toolIds": [tool_id, "server:mcp:external"]}),
            user_id=self.owner.id)
        actor = self.writer.model_dump()
        result = json.loads(await self.tools.inspect_agent_model(self.model_id, __user__=actor))
        self.assertEqual(result["tools"][0]["status"], "unavailable")
        await AccessGrants.grant_access("tool", tool_id, "user", self.writer.id, "read")
        result = json.loads(await self.tools.inspect_agent_model(self.model_id, __user__=actor))
        self.assertEqual(result["tools"][0]["specs"], [schema])
        self.assertNotIn("INSPECTION_MUST_NOT_EXECUTE_SOURCE", json.dumps(result))
        self.assertEqual(result["tools"][1]["status"], "not_inspected")
