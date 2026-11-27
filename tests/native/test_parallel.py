"""Native boundary checks; full execution is covered by Docker acceptance."""

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter


class NativeParallelTest(unittest.IsolatedAsyncioTestCase):
    async def test_native_contracts_and_no_other_runtime_native_imports(self):
        from open_webui.utils.subagents import delegate
        from open_webui.models.chats import Chats
        from open_webui.tasks import stop_task

        for operation in (delegate, Chats.get_internal_chat_ids_by_parent_id,
                          Chats.get_message_by_id_and_message_id, stop_task):
            self.assertTrue(inspect.iscoroutinefunction(operation))
        from open_webui.env import VERSION

        if VERSION == '0.13.3':
            self.assertIn('background', inspect.signature(delegate).parameters)
        elif VERSION == '0.13.5':
            self.assertNotIn('background', inspect.signature(delegate).parameters)
        root=Path(__file__).resolve().parents[2]/'src/lite_handoff_router'
        for path in root.rglob('*.py'):
            # authoring.py belongs to the separate Skill-author Tool, not the
            # Router/Preparation runtime and its adapter boundary.
            if path.name in {'owui_adapter.py', 'authoring.py'}:
                continue
            tree=ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    self.assertFalse((node.module or '').startswith('open_webui'),str(path))

    async def test_saved_chat_and_approval_policy_are_checked_natively(self):
        from open_webui.models.chats import Chats, ChatForm
        from open_webui.models.users import Users
        from open_webui.models.config import Config

        adapter=OWUIAdapter()
        suffix=uuid4().hex
        user=await Users.insert_new_user('parallel-'+suffix,'Parallel native',suffix+'@example.test',role='user')
        cid,mid=str(uuid4()),str(uuid4())
        await Chats.insert_new_chat(cid,user.id,ChatForm(chat={'history':{'currentId':mid,
            'messages':{mid:{'id':mid,'role':'assistant','content':'','done':False}}}}))
        before=await Config.get('subagents.enable',False)
        try:
            await Config.upsert({'subagents.enable':True})
            metadata={'chat_id':cid,'message_id':mid,'params':{'tool_approval_mode':'full'}}
            self.assertIsNone(await adapter.parallel_reason(None,metadata,user))
            self.assertIn('saved chat',await adapter.parallel_reason(None,{'chat_id':'local:temp'},user))
            self.assertIn('approval',await adapter.parallel_reason(None,{**metadata,'params':{'tool_approval_mode':'ask'}},user))
            await Config.upsert({'subagents.enable':False})
            self.assertIn('subagents.enable',await adapter.parallel_reason(None,metadata,user))
        finally:
            await Config.upsert({'subagents.enable':before})

    async def test_0135_subagent_permission_is_rechecked(self):
        from copy import deepcopy
        from open_webui.config import DEFAULT_CONFIG
        from open_webui.env import VERSION
        from open_webui.models.chats import ChatForm, Chats
        from open_webui.models.config import Config
        from open_webui.models.users import Users

        if VERSION != "0.13.5":
            return

        suffix = uuid4().hex
        user = await Users.insert_new_user(suffix, "Sub-agent permission", suffix + "@example.test", role="user")
        chat_id, message_id = str(uuid4()), str(uuid4())
        await Chats.insert_new_chat(chat_id, user.id, ChatForm(chat={"history": {
            "currentId": message_id, "messages": {message_id: {
                "id": message_id, "role": "assistant", "content": "", "done": False,
            }},
        }}))
        metadata = {"chat_id": chat_id, "message_id": message_id}
        previous = await Config.get_many("subagents.enable", "user.permissions")
        permissions = deepcopy(DEFAULT_CONFIG["user.permissions"])
        adapter = OWUIAdapter()
        try:
            permissions["features"]["subagents"] = True
            await Config.upsert({"subagents.enable": True, "user.permissions": permissions})
            self.assertIsNone(await adapter.parallel_reason(None, metadata, user))
            permissions["features"]["subagents"] = False
            await Config.upsert({"user.permissions": permissions})
            self.assertIn("permission", await adapter.parallel_reason(None, metadata, user))
        finally:
            await Config.upsert(previous)

    async def test_internal_chats_stay_hidden_from_native_folder_queries(self):
        from open_webui.models.chats import Chats, ChatForm
        from open_webui.models.folders import Folders, FolderForm
        from open_webui.models.users import Users

        suffix=uuid4().hex
        user=await Users.insert_new_user('folder-'+suffix,'Folder native',suffix+'@example.test',role='user')
        folder=await Folders.insert_new_folder(user.id,FolderForm(name='Parallel inspection'))
        parent_id,child_id=str(uuid4()),str(uuid4())
        await Chats.insert_new_chat(parent_id,user.id,ChatForm(chat={'title':'Main'},folder_id=folder.id))
        await Chats.insert_new_chat(child_id,user.id,ChatForm(chat={'title':'Internal task'},folder_id=folder.id),
            internal_meta={'internal':True,'type':'subagent','parent_chat_id':parent_id})
        self.assertEqual([c.id for c in await Chats.get_chats_by_folder_ids_and_user_id([folder.id],user.id)],[parent_id])
        self.assertEqual(await Chats.count_all_chats_by_folder_id(folder.id),1)
        self.assertTrue((await Chats.get_accessible_chat_by_id(child_id,user)).meta['internal'])
        self.assertEqual([c.id for c in await Chats.get_chats_by_folder_ids_and_user_id([folder.id],user.id)],[parent_id])

    async def test_saved_chat_builtin_dispatches_in_background_on_0135(self):
        import asyncio
        import json
        from fastapi import FastAPI, Request
        from open_webui.env import VERSION
        from open_webui.models.auths import Auths
        from open_webui.models.chats import ChatForm, Chats
        from open_webui.utils.subagents import delegate

        if VERSION != "0.13.5":
            return

        app = FastAPI()
        app.state.redis = None
        request = Request({"type": "http", "app": app, "headers": []})
        parent, reply = str(uuid4()), str(uuid4())
        user = await Auths.insert_new_auth(parent + "@example.test", "unused", "Probe", role="admin")
        await Chats.insert_new_chat(parent, user.id, ChatForm(chat={"models": ["probe"],
            "history": {"currentId": reply, "messages": {reply: {"id": reply, "model": "probe",
                "role": "assistant", "done": True, "content": "Started"}}}}))
        released = asyncio.Event()
        reports = asyncio.Queue()

        async def handler(child_request, body, user):
            if body["chat_id"] == parent:
                await Chats.upsert_message_to_chat_by_id_and_message_id(parent, body["id"], {"done": True})
                reports.put_nowait(body["user_message"])
            else:
                await released.wait()
                await Chats.upsert_message_to_chat_by_id_and_message_id(
                    body["chat_id"], body["id"], {"content": "Synthetic child result", "done": True})

        app.state.CHAT_COMPLETION_HANDLER = handler
        try:
            dispatch = json.loads(await asyncio.wait_for(delegate(
                "Synthetic probe", "", request=request, user_data=user.model_dump(),
                metadata={"model_id": "probe"}, parent_chat_id=parent, parent_message_id=reply,
            ), timeout=8))
            self.assertEqual(dispatch["status"], "dispatched")
            self.assertTrue(reports.empty())
            released.set()
            report = await asyncio.wait_for(reports.get(), timeout=8)
            self.assertEqual(report["role"], "user")
            self.assertEqual(report["meta"]["type"], "subagent")
            self.assertEqual(report["meta"]["subagent_chat_id"], dispatch["subagent_chat_id"])
        finally:
            released.set()

    async def test_0135_threads_stop_only_the_selected_router_child(self):
        import asyncio
        from fastapi import FastAPI, Request
        from open_webui.env import VERSION
        from open_webui.models.auths import Auths
        from open_webui.models.chats import ChatForm, Chats
        if VERSION != "0.13.5":
            return

        from open_webui.routers.chats import get_chat_children

        app = FastAPI()
        app.state.redis = None
        request = Request({"type": "http", "app": app, "headers": []})
        parent, reply = str(uuid4()), str(uuid4())
        user = await Auths.insert_new_auth(parent + "@example.test", "unused", "Threads", role="admin")
        await Chats.insert_new_chat(parent, user.id, ChatForm(chat={"models": ["probe"],
            "history": {"currentId": reply, "messages": {reply: {"id": reply, "model": "probe",
                "role": "assistant", "done": False, "content": ""}}}}))
        started = asyncio.Queue()
        finish = asyncio.Event()

        async def handler(child_request, body, user):
            self.assertTrue(child_request.state.internal)
            started.put_nowait(body)
            await finish.wait()
            await Chats.upsert_message_to_chat_by_id_and_message_id(
                body["chat_id"], body["id"], {"content": "Saved result", "done": True})

        app.state.CHAT_COMPLETION_HANDLER = handler
        metadata = {"chat_id": parent, "message_id": reply, "model_id": "probe",
                    "chat_context": {"params": {}}, "files": []}
        adapter = OWUIAdapter()
        workers = [asyncio.create_task(adapter.native_delegate(request, user, metadata, name, []))
                   for name in ("First", "Second")]
        try:
            running = {body["messages"][0]["content"]: body["chat_id"]
                       for body in [await asyncio.wait_for(started.get(), 8) for _ in workers]}
            children = await get_chat_children(request, parent, user)
            self.assertEqual({row.id: row.status for row in children}, dict.fromkeys(running.values(), "running"))
            await adapter.stop_child(request, running["Second"], user)
            self.assertEqual(await asyncio.wait_for(workers[1], 8), {"status": "cancelled"})
            self.assertFalse(workers[0].done())
            finish.set()
            self.assertEqual(await asyncio.wait_for(workers[0], 8), {"status": "finished"})
            saved = await Chats.get_chat_by_id(parent)
            self.assertEqual(list(saved.chat["history"]["messages"]), [reply])
        finally:
            finish.set()
            await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True), 8)
