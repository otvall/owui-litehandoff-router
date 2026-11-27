from io import BytesIO
import json
from pathlib import Path
import unittest
from uuid import uuid4
from zipfile import ZipFile

from lite_handoff_router.model_execution import prepare_model
from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.registry import discover
from lite_handoff_router.routing_profiles import routing_entries
from lite_handoff_router.runtime import begin, metadata_draft
from tests.context_helpers import catalog_result
from tests.test_protocol import call


class SharedRoutingImportTest(unittest.TestCase):
    def test_example_imports_as_a_single_workspace_skill(self):
        from open_webui.utils.skill_files import parse_import

        example = Path(__file__).resolve().parents[2] / "docs/skills/routing-profiles"
        archive = BytesIO()
        with ZipFile(archive, "w") as package:
            for path in example.rglob("*"):
                if path.is_file():
                    package.write(path, arcname=path.relative_to(example.parent))
        packages = parse_import(archive.getvalue(), "routing-profiles.zip")
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["name"], "routing-profiles")
        entries, invalid = routing_entries({"data": {"files": packages[0]["files"]}})
        self.assertEqual(invalid, {})
        self.assertEqual(entries["forecast-agent"]["path"], "profiles/forecast.md")


class NativeSharedReadersTest(unittest.IsolatedAsyncioTestCase):
    async def test_personal_catalog_readers_are_versioned_and_checked_again_after_refresh(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.users import Users

        tag = uuid4().hex[:10]
        owner, alice, bob = [await Users.insert_new_user(f"{name}-{tag}", name, f"{name}-{tag}@example.test",
                                                         role="user") for name in ("owner", "alice", "bob")]
        router, a, b, shared = (f"{name}-{tag}" for name in ("router", "agent-a", "agent-b", "shared"))
        app = FastAPI()
        app.state.MODELS = {"llm": {"id": "llm", "owned_by": "openai"}}
        request = Request({"type": "http", "app": app, "headers": []})
        request.state.max_tool_call_iterations = 8
        for name in (router, a, b):
            row = await Models.insert_new_model(ModelForm(
                id=name, name=name, params={}, meta={"skillIds": [shared]}), owner.id)
            app.state.MODELS[name] = {"id": name, "owned_by": "openai", "info": row.model_dump()}
        for actor, target in ((alice, a), (bob, b)):
            await AccessGrants.grant_access("model", router, "user", actor.id, "read")
            await AccessGrants.grant_access("model", target, "user", actor.id, "read")
        files = [{"path": p, "content": c} for p, c in (
            ("SKILL.md", "Guide"),
            ("agents.json", json.dumps({a: {"path": "profiles/a.md", "description": "A description"},
                                       b: {"path": "profiles/b.md", "description": "B description"}})),
            ("profiles/a.md", "alpha needle allowed"),
            ("profiles/b.md", "needle PRIVATE_B"),
        )]
        skill = await Skills.insert_new_skill(owner.id, SkillForm(id=shared, name=shared, files=files))
        adapter = OWUIAdapter()
        with self.assertRaises(Exception):
            await adapter.skill(shared, alice)

        async def prepare(actor):
            registry, _ = await discover(adapter, request, actor, {}, router, routing_skill_id=shared)
            body, metadata = {"model": router, "messages": [{"role": "user", "content": "Question"}]}, {}
            state = begin(request, body, metadata, actor.id, registry, [], [], [shared],
                          chat_skill_ids=[shared], routing_skill_id=shared)
            draft, tools = await prepare_model(adapter, request, body, actor, metadata_draft(metadata), state, "llm", {})
            return body, metadata, state, draft, tools

        _, _, _, draft_b, tools_b = await prepare(bob)
        self.assertIn("profiles/b.md", catalog_result(draft_b["messages"]))
        self.assertNotIn("profiles/a.md", catalog_result(draft_b["messages"]))
        body, metadata, state, draft, tools = await prepare(alice)
        self.assertIn("profiles/a.md", catalog_result(draft["messages"]))
        self.assertNotIn("profiles/b.md", catalog_result(draft["messages"]))
        read = await adapter.refresh(tools["read_skill_file"]["callable"], {"__metadata__": {}})
        listing, grep = (tools[name]["callable"] for name in ("view_skill", "grep_skill_files"))
        overview = json.loads(await listing(id=shared))
        self.assertIn("profiles/a.md", str(overview))
        self.assertNotIn("profiles/b.md", str(overview))
        self.assertNotIn("agents.json", str(overview))
        search = json.loads(await grep(id=shared, pattern="needle"))
        self.assertEqual(search["total_matches"], 1)
        self.assertNotIn("PRIVATE_B", str(search))
        for path in ("agents.json", "profiles/b.md", "profiles/../SKILL.md"):
            with self.assertRaisesRegex(ValueError, "current model scope"):
                await read(id=shared, path=path)
            with self.assertRaisesRegex(ValueError, "current model scope"):
                await grep(id=shared, pattern="needle", path=path)
        first = json.loads(await read(id=shared, path="profiles/a.md", max_chars=5))
        self.assertEqual(first, {"path": "profiles/a.md", "content": "alpha", "next_offset": 5})
        files[2]["content"] = "new version of allowed profile"
        await Skills.update_skill_by_id(shared, SkillForm(
            id=shared, name=shared, files=files, expected_version_id=skill.version_id).model_dump(exclude_none=True),
            user_id=owner.id)
        second = await read(id=shared, path="profiles/a.md", offset=first["next_offset"])
        self.assertIn("needle allowed", second)
        state["source"] += [
            {"role": "assistant", "tool_calls": [call("read_skill_file", "page", {"id": shared, "path": "profiles/a.md"})]},
            {"role": "tool", "tool_call_id": "page", "content": second},
            {"role": "assistant", "tool_calls": [call("view_skill", "overview", {"id": shared, "path": "SKILL.md"})]},
            {"role": "tool", "tool_call_id": "overview", "content": json.dumps(overview)},
        ]
        state["generation"] = 1
        state["audit"] = [
            {"id": "page", "name": "read_skill_file", "executor": {"kind": "router"},
             "generation": 1, "arguments": {"id": shared, "path": "profiles/a.md"}},
            {"id": "overview", "name": "view_skill", "executor": {"kind": "router"},
             "generation": 2, "arguments": {"id": shared, "path": "SKILL.md"}},
        ]
        continuation, _ = await prepare_model(adapter, request, body, alice, metadata_draft(metadata), state, "llm", {})
        self.assertIn("needle allowed", str(continuation["messages"]))
        agent, agent_tools = await prepare_model(
            adapter, request, body, alice, metadata_draft(metadata), state, "llm", {}, target=a)
        self.assertNotIn("profiles/a.md", str(agent["messages"]))
        self.assertNotIn("read_skill_file", agent_tools)
        await AccessGrants.revoke_access("model", a, "user", alice.id, "read")
        with self.assertRaisesRegex(ValueError, "current model scope"):
            await read(id=shared, path="profiles/a.md", offset=first["next_offset"])
        self.assertNotIn("profiles/a.md", await listing(id=shared))
        self.assertIn("PRIVATE_B", await tools_b["read_skill_file"]["callable"](id=shared, path="profiles/b.md"))
        revoked, _ = await prepare_model(adapter, request, body, alice, metadata_draft(metadata), state, "llm", {})
        self.assertNotIn("needle allowed", str(revoked["messages"]))
        self.assertFalse(any(message.get("tool_call_id") == "overview" for message in revoked["messages"]))
        await AccessGrants.grant_access("model", a, "user", alice.id, "read")
        _, _, _, _, fresh = await prepare(alice)
        self.assertIn("new version", await fresh["read_skill_file"]["callable"](id=shared, path="profiles/a.md"))
