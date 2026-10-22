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
        self.assertIn('background', inspect.signature(delegate).parameters)
        root=Path(__file__).resolve().parents[2]/'src/lite_handoff_router'
        for path in root.rglob('*.py'):
            if path.name=='owui_adapter.py':
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
