import json
import unittest
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.skills import READERS, scoped_reader


class NativeSkillsTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_reader_scopes_then_authorizes_and_preserves_files_and_pages(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.users import Users
        from open_webui.models.skills import Skills, SkillForm

        suffix = uuid4().hex[:10]
        user = await Users.insert_new_user("skill-user-" + suffix, "Reader", suffix + "@skills.example.test", role="admin")
        own, foreign = "own-" + suffix, "foreign-" + suffix
        for skill_id in (own, foreign):
            await Skills.insert_new_skill(user.id, SkillForm(id=skill_id, name=skill_id, description="Files.", files=[
                {"path": "SKILL.md", "content": "# Guide\n\nOwn instructions."},
                {"path": "references/data.txt", "content": "alpha\nneedle content\nomega"},
            ]))
        request = Request({"type": "http", "app": FastAPI(), "headers": []})
        metadata = {"chat_context": {"skill_versions": {}}}
        adapter = OWUIAdapter()
        model = {"id": "a", "info": {"meta": {"skillIds": [own]}}}
        builtins = await adapter.builtins(request, model, {
            "__request__": request, "__user__": user.model_dump(), "__metadata__": metadata,
            "__skill_ids__": [own], "__model__": model,
        })
        tools = {name: await scoped_reader(adapter, builtins[name], [own], user, []) for name in READERS}
        arguments = {"view_skill": {}, "read_skill_file": {"path": "references/data.txt", "max_chars": 5},
                     "grep_skill_files": {"pattern": "needle"}}
        for name, tool in tools.items():
            # The same helper the native loop uses must not recover an unguarded builtin.
            refreshed = await adapter.refresh(tool["callable"], {
                "__messages__": [{"role": "system", "content": "FOREIGN_CONTEXT"}],
                "__files__": [{"id": "updated-file"}],
            })
            tool = {**tool, "callable": refreshed}
            # Foreign skill is readable by the execution user but is not attached to A.
            with self.assertRaisesRegex(ValueError, "model scope"):
                await tool["callable"](id=foreign, **arguments[name])
            result = json.loads(await tool["callable"](id=own, **arguments[name]))
            self.assertNotIn("error", result, (name, result))
            if name == "read_skill_file":
                self.assertEqual(result["content"], "alpha")
                next_page = json.loads(await tool["callable"](id=own, path="references/data.txt", offset=result["next_offset"], max_chars=100))
                self.assertIn("needle content", next_page["content"])
            if name == "grep_skill_files":
                self.assertIn("needle content", str(result))
        self.assertIn(own, metadata["chat_context"]["skill_versions"])

    async def test_detached_skill_disappears_on_fresh_workspace_preparation(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.users import Users
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.skills import Skills, SkillForm
        from lite_handoff_router.model_execution import prepare_model
        from lite_handoff_router.runtime import begin, metadata_draft

        suffix = uuid4().hex[:10]
        user = await Users.insert_new_user("detach-" + suffix, "Reader", suffix + "@detach.example.test", role="admin")
        skill_id, model_id = "guide-" + suffix, "model-" + suffix
        await Skills.insert_new_skill(user.id, SkillForm(id=skill_id, name=skill_id, content="Instructions"))
        form = ModelForm(id=model_id, name=model_id, params={"system": "A"}, meta={"skillIds": [skill_id]})
        row = await Models.insert_new_model(form, user.id)
        app = FastAPI()
        app.state.MODELS = {model_id: {"id": model_id, "owned_by": "openai", "info": row.model_dump()}}
        request = Request({"type": "http", "app": app, "headers": []})
        body, metadata = {"model": "router", "messages": [{"role": "user", "content": "task"}]}, {}
        state = begin(request, body, metadata, user.id, {}, [], [], [])
        adapter = OWUIAdapter()
        first, tools = await prepare_model(adapter, request, body, user, metadata_draft(metadata), state, "unused", {}, target=model_id)
        self.assertEqual(set(tools), set(READERS))
        form.meta = type(form.meta)(skillIds=[])
        await Models.update_model_by_id(model_id, form, user_id=user.id)
        second, tools = await prepare_model(adapter, request, body, user, metadata_draft(metadata), state, "unused", {}, target=model_id)
        self.assertEqual(tools, {})
        self.assertNotIn(skill_id, str(second["messages"]))
