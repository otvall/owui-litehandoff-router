import io
import json
from pathlib import Path
import runpy
import unittest
from uuid import uuid4
from zipfile import ZipFile
from unittest.mock import AsyncMock, patch

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
            id=self.model_id, name=self.model_id, base_model_id="base-llm", params={"system": "SECRET_AGENT_PROMPT"},
            meta={"skillIds": [], "toolIds": []}), self.owner.id)
        from open_webui.models.access_grants import AccessGrants
        await AccessGrants.grant_access("model", self.model_id, "user", self.writer.id, "write")
        await AccessGrants.grant_access("model", self.model_id, "user", self.reader.id, "read")
        self.request = Request({"type": "http", "app": FastAPI(), "headers": [], "path": "/api/chat/completions"})
        self.tools = Tools()

    def profile(self):
        return {
            "agent_model_id": self.model_id, "agent_name": "Прогнозист",
            "skill_name": "route-" + self.model_id,
            "routing_description": "Прогноз по временному ряду.",
            "routing_criteria": "Рассчитать прогноз.",
            "exclusion_criteria": "Объяснение терминов без расчёта прогноза вне профиля.",
            "expected_result": "Прогноз с допущениями.",
            "positive_examples": ["Рассчитай продажи на декабрь."],
            "negative_examples": ["Что такое сезонность?"],
        }

    async def configure_consultation_models(self, provider):
        from open_webui.models.models import Models, ModelForm
        base_id = "consultation-base-" + self.suffix
        await Models.insert_new_model(ModelForm(id=base_id, name=base_id, params={}, meta={}), self.owner.id)
        await Models.update_model_by_id(self.model_id, ModelForm(
            id=self.model_id, name=self.model_id, base_model_id=base_id,
            params={"system": "SECRET_AGENT_PROMPT"}, meta={"toolIds": ["agent-tool"]}), user_id=self.owner.id)
        self.request.app.state.MODELS = {
            self.model_id: {"id": self.model_id, "owned_by": provider},
            base_id: {"id": base_id, "owned_by": provider},
        }
        return base_id

    async def test_ask_agent_uses_native_prompt_and_isolated_chat_context(self):
        from open_webui.utils.tools import get_tool_specs

        base_id = await self.configure_consultation_models("ollama")
        self.request.state.metadata = {"chat_id": "author-chat", "tool_ids": ["author-tool"]}
        specs = {spec["name"]: spec for spec in get_tool_specs(self.tools)}
        self.assertEqual(set(specs["ask_agent"]["parameters"]["properties"]), {"model_id", "question"})

        async def provider(request, form_data, user):
            self.assertIsNot(request, self.request)
            self.assertEqual(user.id, self.owner.id)
            self.assertEqual(form_data["messages"], [
                {"role": "system", "content": "SECRET_AGENT_PROMPT"},
                {"role": "user", "content": "What do you do?"},
            ])
            self.assertFalse({"tools", "tool_ids", "files", "chat_id"} & form_data.keys())
            self.assertNotIn("chat_id", form_data.get("metadata", {}))
            return {"model": base_id, "message": {"role": "assistant", "content": "Forecasts."},
                    "done": True}

        with patch("open_webui.utils.chat.generate_ollama_chat_completion", side_effect=provider) as dispatch:
            result = json.loads(await self.tools.ask_agent(
                self.model_id, "What do you do?", __user__=self.owner.model_dump(),
                __model__={"id": "author"}, __request__=self.request))
        dispatch.assert_awaited_once()
        self.assertEqual(result, {"agent_id": self.model_id, "status": "ok",
                                  "answer": "Forecasts.", "truncated": False})

    async def test_ask_agent_rechecks_access_and_handles_unsupported_models_and_failures(self):
        from fastapi import HTTPException

        from open_webui.models.access_grants import AccessGrants

        await self.configure_consultation_models("openai")
        runtime = self.request.app.state.MODELS[self.model_id]

        async def ask(user=None, author_id="author"):
            return json.loads(await self.tools.ask_agent(
                self.model_id, "What do you do?", __user__=(user or self.owner).model_dump(),
                __model__={"id": author_id}, __request__=self.request))

        with patch("open_webui.utils.chat.generate_chat_completion", new_callable=AsyncMock) as dispatch:
            with self.assertRaises(ValueError):
                await ask(author_id=self.model_id)
            runtime["pipe"] = True
            with self.assertRaises(ValueError):
                await ask()
            del runtime["pipe"]
            with self.assertRaises(HTTPException):
                await ask(self.writer)
            await AccessGrants.revoke_access("model", self.model_id, "user", self.writer.id, "write")
            with self.assertRaises(HTTPException):
                await ask(self.writer)
            dispatch.assert_not_awaited()
            for error, status in ((TimeoutError(), "timeout"), (RuntimeError("private"), "generation_failed")):
                dispatch.side_effect = error
                self.assertEqual(await ask(), {"agent_id": self.model_id, "status": status})

    async def test_list_includes_model_shared_with_write_only_grant(self):
        from open_webui.models.models import Models, ModelForm

        # A real Workspace Model with a base model and a display name unlike its ID.
        await Models.update_model_by_id(self.model_id, ModelForm(
            id=self.model_id, name="Forecast specialist", base_model_id="base-llm",
            params={"system": "Prompt"}, meta={}), user_id=self.owner.id)
        for query in ("", "Forecast specialist"):
            with self.subTest(query=query):
                result = json.loads(await self.tools.list_agent_models(
                    query, __user__=self.writer.model_dump()))
                self.assertIn(self.model_id, {item["id"] for item in result["items"]})

    async def test_list_matches_model_id_when_display_name_differs(self):
        from open_webui.models.models import Models, ModelForm

        await Models.update_model_by_id(self.model_id, ModelForm(
            id=self.model_id, name="Forecast specialist", base_model_id="base-llm",
            params={}, meta={}), user_id=self.owner.id)
        result = json.loads(await self.tools.list_agent_models(
            self.model_id, __user__=self.owner.model_dump()))
        self.assertEqual([item["id"] for item in result["items"]], [self.model_id])

    async def test_list_includes_inherited_group_write_and_rechecks_revocation(self):
        from open_webui.models.groups import Groups, GroupForm
        from open_webui.models.access_grants import AccessGrants

        parent = await Groups.insert_new_group(self.owner.id, GroupForm(
            name="authors-" + self.suffix, description="Authors"))
        child = await Groups.insert_new_group(self.owner.id, GroupForm(
            name="child-" + self.suffix, description="Inherited authors", parent_group_id=parent.id))
        await Groups.add_users_to_group(child.id, [self.writer.id])
        await AccessGrants.revoke_access("model", self.model_id, "user", self.writer.id, "write")
        await AccessGrants.grant_access("model", self.model_id, "group", parent.id, "write")
        result = json.loads(await self.tools.list_agent_models(self.model_id, __user__=self.writer.model_dump()))
        self.assertEqual([m["id"] for m in result["items"]], [self.model_id])
        await AccessGrants.revoke_access("model", self.model_id, "group", parent.id, "write")
        result = json.loads(await self.tools.list_agent_models(self.model_id, __user__=self.writer.model_dump()))
        self.assertEqual(result["items"], [])

    async def test_list_pages_matching_writable_models_without_read_only_entries(self):
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.access_grants import AccessGrants

        prefix = "Page " + self.suffix
        expected = []
        for label in ("0-read-only", "A", "B", "C"):
            identifier = "page-" + label.lower() + "-" + self.suffix
            await Models.insert_new_model(ModelForm(
                id=identifier, name=prefix + " " + label, base_model_id="base-llm", params={}, meta={}), self.owner.id)
            permission = "read" if label == "0-read-only" else "write"
            await AccessGrants.grant_access("model", identifier, "user", self.writer.id, permission)
            if permission == "write":
                expected.append(identifier)
        with patch("open_webui.routers.models.PAGE_ITEM_COUNT", 2):
            first = json.loads(await self.tools.list_agent_models(prefix.upper(), __user__=self.writer.model_dump()))
            second = json.loads(await self.tools.list_agent_models(prefix, page=first["next_page"],
                                                                  __user__=self.writer.model_dump()))
        self.assertEqual([m["id"] for m in first["items"]], expected[:2])
        self.assertEqual([m["id"] for m in second["items"]], expected[2:])
        self.assertIsNone(second["next_page"])

    async def test_list_admin_bypass_respects_platform_setting(self):
        from open_webui.models.users import Users

        admin = await Users.insert_new_user("admin-author-" + self.suffix, "Admin",
                                           self.suffix + "@admin-author.example.test", role="admin")
        for bypass in (False, True):
            with self.subTest(bypass=bypass), patch("open_webui.routers.models.BYPASS_ADMIN_ACCESS_CONTROL", bypass):
                result = json.loads(await self.tools.list_agent_models(self.model_id, __user__=admin.model_dump()))
                self.assertEqual([m["id"] for m in result["items"]], [self.model_id] if bypass else [])

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
        skill_id = "route-" + self.model_id
        saved = json.loads(await self.tools.save_routing_skill(self.profile(), skill_id,
                          __user__=actor, __request__=self.request))
        first = await Skills.get_skill_by_id(skill_id)
        self.assertEqual(first.user_id, self.writer.id)
        self.assertEqual(first.access_grants, [])
        self.assertTrue(first.is_active)
        self.assertNotIn(self.model_id, first.content.split("---", 2)[2])
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
        legacy_id = "legacy-route-" + self.suffix
        legacy = await Skills.insert_new_skill(self.writer.id, SkillForm(id=legacy_id, name=legacy_id, content="Old"))
        # The canonical name is occupied by the earlier route. A legacy ID still keeps its link.
        preview = json.loads(await self.tools.preview_routing_change(
            updated_fields, legacy_id, legacy.version_id, shared=False, __user__=actor))
        renamed = json.loads(await self.tools.save_routing_skill(
            updated_fields, legacy_id, legacy.version_id, __user__=actor, __request__=self.request))
        self.assertEqual(renamed["id"], legacy_id)
        self.assertEqual(renamed["name"], skill_id + "-2")
        self.assertEqual(preview["name"], renamed["name"])
        self.assertEqual(preview["content"], (await Skills.get_skill_by_id(legacy_id)).content)
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
            "inspect_shared_routing_profile", "save_shared_routing_profile", "ask_agent",
            "list_shared_routing_catalog", "read_shared_routing_profile", "preview_routing_change",
            "inspect_router_catalog", "inspect_routing_turn", "resolve_routing_test_group", "export_routing_test_job",
        })
        for spec in specs:
            self.assertFalse(any(name.startswith("__") for name in spec["parameters"]["properties"]))
        packages = parse_import((root / "dist/routing-skill-authoring.zip").read_bytes(), "routing-skill-authoring.zip")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["name"], "routing-skill-authoring")
        with ZipFile(root / "dist/routing-skill-authoring-agent.zip") as bundle:
            review_bytes = bundle.read("routing-skill-review.zip")
            diagnostic_bytes = bundle.read("routing-diagnostics.zip")
        diagnostic = parse_import(diagnostic_bytes, "routing-diagnostics.zip")[0]
        self.assertEqual(diagnostic["name"], "routing-diagnostics")
        self.assertIn("references/test-groups.json", {file["path"] for file in diagnostic["files"]})
        review = parse_import(review_bytes, "routing-skill-review.zip")
        self.assertEqual([package["name"] for package in review], ["routing-skill-review"])
        with ZipFile(io.BytesIO(review_bytes)) as skill:
            self.assertEqual(skill.read("routing-skill-review/SKILL.md"),
                             (root / "docs/skills/routing-skill-review/SKILL.md").read_bytes())
        result = json.loads(await instance.inspect_agent_model(self.model_id, __user__=self.writer.model_dump()))
        self.assertEqual(result["system_prompt"], "SECRET_AGENT_PROMPT")
        catalog = json.loads(await instance.list_agent_models(self.model_id, __user__=self.writer.model_dump()))
        self.assertEqual([m["id"] for m in catalog["items"]], [self.model_id])

    async def test_shared_save_updates_profile_and_index_in_one_version(self):
        from fastapi import HTTPException
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.skills import Skills, SkillForm, get_skill_snapshot

        skill_id = "shared-authoring-" + self.suffix
        unrelated = {"path": f"profiles/route-{self.model_id}.md", "description": "Other route"}
        own_path = f'profiles/{self.profile()["skill_name"]}-2.md'
        original = await Skills.insert_new_skill(self.owner.id, SkillForm(
            id=skill_id, name=skill_id, is_active=False, files=[
                {"path": "SKILL.md", "content": "Shared rules"},
                {"path": "agents.json", "content": json.dumps({"other": unrelated})},
                {"path": unrelated["path"], "content": "Other profile"},
                {"path": "notes.md", "content": "Unrelated notes"},
            ]))
        author = self.writer.model_dump()
        with self.assertRaises(HTTPException):
            await self.tools.save_shared_routing_profile(
                self.profile(), skill_id, original.version_id, __user__=author, __request__=self.request)
        await AccessGrants.grant_access("skill", skill_id, "user", self.writer.id, "write")
        await AccessGrants.grant_access("skill", skill_id, "user", self.reader.id, "write")
        with self.assertRaisesRegex(ValueError, "Write access"):
            await self.tools.save_shared_routing_profile(
                self.profile(), skill_id, original.version_id, __user__=self.reader.model_dump(), __request__=self.request)
        inspected = json.loads(await self.tools.inspect_shared_routing_profile(skill_id, self.model_id, __user__=author))
        self.assertEqual(inspected["version_id"], original.version_id)
        self.assertIsNone(inspected["path"])
        preview = json.loads(await self.tools.preview_routing_change(
            self.profile(), skill_id, original.version_id, __user__=author))
        self.assertEqual((await Skills.get_skill_by_id(skill_id)).version_id, original.version_id)
        self.assertEqual(preview["path"], own_path)
        self.assertIn("+", preview["profile_diff"])
        self.assertNotIn("Other route", preview["registry_diff"])
        saved = json.loads(await self.tools.save_shared_routing_profile(
            self.profile(), skill_id, original.version_id, __user__=author, __request__=self.request))
        self.assertEqual(saved["path"], own_path)
        self.assertTrue(saved["registry_updated"])
        row = await Skills.get_skill_by_id(skill_id)
        self.assertEqual(row.user_id, self.owner.id)
        self.assertFalse(row.is_active)
        self.assertEqual(row.version_id, saved["version_id"])
        files = {f["path"]: f["content"] for f in row.data["files"]}
        self.assertEqual(preview["content"], files[own_path])
        self.assertEqual(preview["registry_entry"], {self.model_id: json.loads(files["agents.json"])[self.model_id]})
        self.assertEqual(files["SKILL.md"], "Shared rules")
        self.assertEqual(files["notes.md"], "Unrelated notes")
        self.assertEqual(files[unrelated["path"]], "Other profile")
        self.assertIn(self.profile()["expected_result"], files[own_path])
        self.assertIn(f'name: "route-{self.model_id}-2"', files[own_path])
        self.assertEqual(json.loads(files["agents.json"]), {
            "other": unrelated,
            self.model_id: {"path": own_path, "description": self.profile()["routing_description"]},
        })
        old = await get_skill_snapshot(row, original.version_id)
        self.assertNotIn(own_path, {f["path"] for f in old["data"]["files"]})
        with self.assertRaises(HTTPException) as stale:
            await self.tools.save_shared_routing_profile(
                self.profile(), skill_id, original.version_id, __user__=author, __request__=self.request)
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException) as stale_preview:
            await self.tools.preview_routing_change(self.profile(), skill_id, original.version_id, __user__=author)
        self.assertEqual(stale_preview.exception.status_code, 409)
        self.assertEqual((await Skills.get_skill_by_id(skill_id)).version_id, row.version_id)
        updated = json.loads(await self.tools.save_shared_routing_profile(
            {**self.profile(), "routing_description": "Updated description", "skill_name": "new-name"},
            skill_id, row.version_id, __user__=author, __request__=self.request))
        self.assertEqual(updated["path"], own_path)
        self.assertTrue(await AccessGrants.has_access(
            resource_type="skill", resource_id=skill_id, user_id=self.reader.id, permission="write"))

    async def test_read_catalog_versions_and_permissions(self):
        from fastapi import HTTPException
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.routers.skills import update_skill_by_id

        skill_id = "catalog-" + self.suffix
        skill = await Skills.insert_new_skill(self.owner.id, SkillForm(
            id=skill_id, name=skill_id, files=[
                {"path": "SKILL.md", "content": "Shared rules"},
                {"path": "agents.json", "content": json.dumps({
                    "external-model": {"path": "profiles/external.md", "description": "External"},
                    "invalid-model": {"path": "notes.md", "description": "Invalid"},
                })},
                {"path": "profiles/external.md", "content": "External profile"},
                {"path": "notes.md", "content": "Private note"},
            ]))
        actor = self.reader.model_dump()
        with self.assertRaises(HTTPException):
            await self.tools.list_shared_routing_catalog(skill_id, __user__=actor)
        await AccessGrants.grant_access("skill", skill_id, "user", self.reader.id, "read")
        first = json.loads(await self.tools.list_shared_routing_catalog(skill_id, limit=1, __user__=actor))
        self.assertEqual(first["version_id"], skill.version_id)
        self.assertEqual(first["items"], [{"agent_id": "external-model", "path": "profiles/external.md",
                                          "description": "External"}])
        second = json.loads(await self.tools.list_shared_routing_catalog(
            skill_id, offset=first["next_offset"], expected_version_id=skill.version_id, __user__=actor))
        self.assertEqual(second["items"][0]["agent_id"], "invalid-model")
        self.assertIn("error", second["items"][0])
        self.assertIsNone(second["next_offset"])
        profile = json.loads(await self.tools.read_shared_routing_profile(
            skill_id, "external-model", skill.version_id, max_chars=8, __user__=actor))
        self.assertEqual((profile["content"], profile["next_offset"]), ("External", 8))
        with self.assertRaises(ValueError):
            await self.tools.read_shared_routing_profile(skill_id, "invalid-model", skill.version_id, __user__=actor)
        updated = await update_skill_by_id(self.request, skill_id, SkillForm(
            id=skill_id, name=skill_id, expected_version_id=skill.version_id,
            operations=[{"op": "put", "path": "profiles/external.md", "content": "Updated"}]),
            user=self.owner, db=None)
        with self.assertRaises(HTTPException) as stale:
            await self.tools.read_shared_routing_profile(
                skill_id, "external-model", skill.version_id, offset=profile["next_offset"], __user__=actor)
        self.assertEqual(stale.exception.status_code, 409)
        await AccessGrants.revoke_access("skill", skill_id, "user", self.reader.id, "read")
        with self.assertRaises(HTTPException):
            await self.tools.list_shared_routing_catalog(
                skill_id, offset=1, expected_version_id=updated.version_id, __user__=actor)

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

    async def test_admin_catalog_uses_router_preparation_in_both_modes(self):
        from fastapi import HTTPException
        from types import SimpleNamespace
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.users import Users
        from lite_handoff_router.entrypoints import router_preparation

        admin = await Users.insert_new_user("diagnostic-admin-" + self.suffix, "Admin",
                                            f"diagnostic-{self.suffix}@example.test", role="admin")
        router_id = "diagnostic-router-" + self.suffix
        agent_id = "diagnostic-agent-" + self.suffix
        inactive = "inactive-" + self.suffix
        shared = "diagnostic-profiles-" + self.suffix
        legacy = "diagnostic-legacy-" + self.suffix
        self.request.app.state.MODELS = {}
        for identifier, active in ((router_id, True), (agent_id, True), (inactive, False)):
            row = await Models.insert_new_model(ModelForm(
                id=identifier, name=identifier, is_active=active, params={}, meta={}), admin.id)
            self.request.app.state.MODELS[identifier] = {"id": identifier, "info": row.model_dump()}
        self.request.app.state.MODELS[router_id]["pipe"] = {"type": "pipe"}
        await Skills.insert_new_skill(admin.id, SkillForm(id=legacy, name=legacy, content="Route"))
        skill = await Skills.insert_new_skill(admin.id, SkillForm(id=shared, name=shared, files=[
            {"path": "SKILL.md", "content": "Guide"},
            {"path": "profiles/a.md", "content": "Route"},
            {"path": "agents.json", "content": json.dumps({
                agent_id: {"path": "profiles/a.md", "description": "Route"},
                inactive: {"path": "profiles/absent.md", "description": "Bad"},
            })},
        ]))
        preparation = router_preparation.Filter()
        get_valves = AsyncMock(side_effect=lambda *_: preparation.valves)
        fixture = SimpleNamespace(id="preparation")
        with patch.object(router_preparation, "AGENTS", {agent_id: legacy, inactive: legacy,
                                                         router_id: "missing-skill"}), \
             patch("open_webui.utils.filter.get_filter_functions", return_value=[fixture]), \
             patch("open_webui.utils.filter.get_function_module", return_value=preparation), \
             patch("open_webui.utils.filter.get_filter_context",
                   return_value=SimpleNamespace(get_function_valves=get_valves)):
            with self.assertRaises(HTTPException):
                await self.tools.inspect_router_catalog(router_id, __user__=self.owner.model_dump(),
                                                        __request__=self.request)
            for configured, kind, version, category in (
                    ("", "AGENTS", None, "model_unavailable"),
                    (shared, "shared_skill", skill.version_id, "invalid_profile"),
                    ("missing-shared", "shared_skill", None, "skill_unavailable")):
                preparation.valves.routing_skill_id = configured
                result = json.loads(await self.tools.inspect_router_catalog(
                    router_id, inactive, __user__=admin.model_dump(), __request__=self.request))
                await preparation.inlet({"model": router_id}, self.request, admin.model_dump())
                snapshot = self.request.scope["lite_handoff_router"]["registry"]
                self.assertEqual(result["entries"], list(snapshot.values()))
                self.assertEqual((result["source"]["kind"], result["source"]["version_id"]), (kind, version))
                self.assertEqual(result["requested_agent"]["diagnostics"][0]["category"], category)

    async def test_admin_turn_reports_signed_trace_and_acl_bound_history(self):
        from fastapi import HTTPException
        from open_webui.models.chats import Chats, ChatForm
        from open_webui.models.users import Users
        from lite_handoff_router.routing_trace import record_routing_event, ROUTING_TRACE
        from lite_handoff_router.skills import observe_routing_reader
        from lite_handoff_router.owui_adapter import OWUIAdapter

        admin = await Users.insert_new_user("trace-admin-" + self.suffix, "Admin",
                                            f"trace-{self.suffix}@example.test", role="admin")
        chat_id, other_id = str(uuid4()), str(uuid4())
        for identifier, actor in ((chat_id, admin), (other_id, self.owner)):
            await Chats.insert_new_chat(identifier, actor.id, ChatForm(chat={"title": "Trace"}))
            await Chats.upsert_message_to_chat_by_id_and_message_id(identifier, "a", {
                "role": "assistant", "output": [
                    {"type": "function_call", "name": "lite_delegate", "call_id": "call", "arguments": "PRIVATE"},
                    {"type": "function_call_output", "call_id": "call", "status": "completed", "output": "PRIVATE"},
                ], "content": "Private reply", "meta": {ROUTING_TRACE: {"trace": {"private": True}, "signature": "fake"}},
            })
        actor = admin.model_dump()
        with patch("open_webui.models.chats.ENABLE_ADMIN_CHAT_ACCESS", False):
            with self.assertRaises(HTTPException):
                await self.tools.inspect_routing_turn(other_id, "a", __user__=actor)
        history = json.loads(await self.tools.inspect_routing_turn(chat_id, "a", __user__=actor))
        self.assertEqual(history["evidence"], "saved_history")
        self.assertEqual(history["events"], [{"tool": "lite_delegate", "result_recorded": True}])

        state = {"chat_id": chat_id, "message_id": "a", "generation": 2,
                 "registry": {"agent": {"agent_id": "agent", "skill_id": "route", "path": "profiles/a.md"}},
                 "routing_trace": {"source": {"version_id": "historic"}, "events": []}}
        adapter = OWUIAdapter()
        observe = observe_routing_reader(adapter, state, admin, "read_skill_file")
        arguments = {"id": "route", "path": "profiles/a.md"}
        await observe(arguments, '{"error":"PRIVATE"}')
        await observe(arguments, '{"content":"PRIVATE","next_offset":null}')
        await record_routing_event(adapter, state, admin, "handoff", "accepted", "agent")
        await record_routing_event(adapter, state, admin, "execution", "failed", "agent")
        await record_routing_event(adapter, state, admin, "parallel_handoff", "attempted", "agent")
        await record_routing_event(adapter, state, admin, "parallel_execution", "completed", "agent")
        result = await self.tools.inspect_routing_turn(chat_id, "a", __user__=actor)
        self.assertNotIn("PRIVATE", result)
        trace = json.loads(result)
        self.assertEqual(trace["evidence"], "trace")
        self.assertEqual(trace["source"]["version_id"], "historic")
        self.assertEqual([event["status"] for event in trace["events"]],
                         ["failed", "completed", "accepted", "failed", "attempted", "completed"])
        await Chats.delete_chat_by_id(chat_id)
        with self.assertRaises(HTTPException):
            await self.tools.inspect_routing_turn(chat_id, "a", __user__=actor)

    async def test_diagnostic_groups_live_in_skill_and_resolve_one_catalog_version(self):
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.users import Users
        from open_webui.tools.builtin import read_skill_file

        admin = await Users.insert_new_user("group-admin-" + self.suffix, "Admin",
                                            f"group-admin-{self.suffix}@example.test", role="admin")
        groups = {
            "first": {"description": "First", "agents": ["known"]},
            "second": {"description": "Second", "agents": ["known", "gone"]},
        }
        diagnostic = await Skills.insert_new_skill(admin.id, SkillForm(
            id="groups-" + self.suffix, name="routing-diagnostics-" + self.suffix, files=[
                {"path": "SKILL.md", "content": "Choose a group"},
                {"path": "references/test-groups.json", "content": json.dumps(groups)},
            ]))
        shared = await Skills.insert_new_skill(admin.id, SkillForm(
            id="group-catalog-" + self.suffix, name="Catalog " + self.suffix, files=[
                {"path": "SKILL.md", "content": "Shared"},
                {"path": "agents.json", "content": '{"known":{"path":"profiles/a.md","description":"Known"}}'},
                {"path": "profiles/a.md", "content": "Known"},
            ]))
        actor = admin.model_dump()
        file = json.loads(await read_skill_file(diagnostic.id, "references/test-groups.json",
                                               __request__=self.request, __user__=actor))
        self.assertEqual(json.loads(file["content"]), groups)
        first = json.loads(await self.tools.resolve_routing_test_group(
            diagnostic.id, "first", shared.id, shared.version_id, __user__=actor))
        self.assertEqual((first["status"], first["agents"]), ("ready", ["known"]))
        second = json.loads(await self.tools.resolve_routing_test_group(
            diagnostic.id, "second", shared.id, shared.version_id, __user__=actor))
        self.assertEqual((second["status"], second["agents"]), ("blocked", ["known", "gone"]))
        self.assertEqual(second["invalid_agents"], {"gone": "Not registered"})
        cases = [{"id": "known-query", "actor": "member", "entrypoint": "router",
                  "turns": ["Read the catalog"], "expected": {"agent_id": "known"}}]
        job = json.loads(await self.tools.export_routing_test_job(
            diagnostic.id, "first", shared.id, shared.version_id, cases, __user__=actor))
        self.assertEqual((job["version_id"], job["agents"], job["cases"]),
                         (shared.version_id, ["known"], cases))
        blocked = json.loads(await self.tools.export_routing_test_job(
            diagnostic.id, "second", shared.id, shared.version_id, cases, __user__=actor))
        self.assertEqual(blocked["status"], "blocked")
        self.assertNotIn("references/test-groups.json", {file["path"] for file in shared.data["files"]})
