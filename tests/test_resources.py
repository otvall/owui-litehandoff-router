import copy
from types import SimpleNamespace
import unittest

from lite_handoff_router.resources import ResourceScope


class Directory:
    def __init__(self):
        self.model_row = {"id":"agent", "info":{"user_id":"owner", "meta":{
            "toolIds":["private-tool"], "skillIds":["private-skill"]}}}
        self.calls = []
        self.denied = False

    async def model(self, request, identifier, user):
        self.calls.append(("model",identifier,user.id))
        if self.denied:
            raise ValueError("Model denied to user")
        return copy.deepcopy(self.model_row)

    async def owner(self, model):
        return SimpleNamespace(id=model["info"]["user_id"])

    async def skill(self, identifier, user):
        self.calls.append(("skill",identifier,user.id))

    async def tool_access(self, request, identifier, user):
        self.calls.append(("tool",identifier,user.id))

    async def terminal_access(self, request, identifier, user):
        self.calls.append(("terminal",identifier,user.id))


class ResourceScopeTest(unittest.IsolatedAsyncioTestCase):
    def scope(self, adapter):
        return ResourceScope(adapter,None,SimpleNamespace(id="actor"),adapter.model_row,
            SimpleNamespace(id="owner"),["private-tool"],["private-skill"])

    async def test_model_is_authorized_by_actor_and_resources_by_owner(self):
        adapter=Directory()
        scope=self.scope(adapter)
        await scope.tool("private-tool")
        await scope.skill("private-skill")
        self.assertEqual(adapter.calls,[("model","agent","actor"),("tool","private-tool","owner"),
                                        ("model","agent","actor"),("skill","private-skill","owner")])
        before=list(adapter.calls)
        with self.assertRaisesRegex(ValueError,"model scope"):
            await scope.skill("owner-readable-but-unattached")
        self.assertEqual(adapter.calls,before)

    async def test_owner_changes_detachment_and_model_revocation_stop_stale_calls(self):
        adapter=Directory()
        scope=self.scope(adapter)
        adapter.model_row["info"]["user_id"]="new-owner"
        with self.assertRaisesRegex(ValueError,"owner changed"):
            await scope.tool("private-tool")
        adapter.model_row["info"]["user_id"]="owner"
        adapter.model_row["info"]["meta"]["toolIds"]=[]
        with self.assertRaisesRegex(ValueError,"detached"):
            await scope.tool("private-tool")
        adapter.denied=True
        with self.assertRaisesRegex(ValueError,"denied"):
            await scope.skill("private-skill")
        self.assertFalse(any(c[0] in {"tool","skill"} for c in adapter.calls))

    async def test_router_uses_trusted_valves_instead_of_card_attachments(self):
        adapter=Directory()
        adapter.model_row["info"]["meta"]={"toolIds":["card-tool"],"skillIds":["card-skill"]}
        scope=ResourceScope(adapter,None,SimpleNamespace(id="actor"),adapter.model_row,
                            SimpleNamespace(id="owner"),["base-tool"],["routing-skill"],router=True)
        await scope.tool("base-tool")
        await scope.skill("routing-skill")
        with self.assertRaisesRegex(ValueError,"model scope"):
            await scope.tool("card-tool")

    async def test_terminal_scope_keeps_owner_authority_and_rechecks_binding(self):
        adapter=Directory()
        meta=adapter.model_row["info"]["meta"]
        meta["lite_handoff"]={"terminal_id":"term-a"}
        scope=ResourceScope(adapter,None,SimpleNamespace(id="actor"),adapter.model_row,
                            SimpleNamespace(id="owner"),[],[],terminal_id="term-a")
        await scope.terminal("term-a")
        self.assertEqual(adapter.calls,[("model","agent","actor"),("terminal","term-a","owner")])
        for identifier in ("term-b",None):
            meta["lite_handoff"]["terminal_id"]=identifier
            with self.assertRaisesRegex(ValueError,"detached"):
                await scope.terminal("term-a")
        meta["lite_handoff"]["terminal_id"]="term-a"
        adapter.model_row["info"]["user_id"]="other-owner"
        with self.assertRaisesRegex(ValueError,"owner changed"):
            await scope.terminal("term-a")
        adapter.model_row["info"]["user_id"]="owner"
        adapter.denied=True
        with self.assertRaisesRegex(ValueError,"denied"):
            await scope.terminal("term-a")
        self.assertEqual(len([c for c in adapter.calls if c[0]=="terminal"]),1)

    async def test_no_binding_or_router_cannot_gain_terminal_from_tool_ids(self):
        adapter=Directory()
        for router,terminal_id in ((False,None),(True,"term-a")):
            scope=ResourceScope(adapter,None,SimpleNamespace(id="actor"),adapter.model_row,
                SimpleNamespace(id="owner"),["terminal:term-a"],[],router=router,terminal_id=terminal_id)
            with self.assertRaisesRegex(ValueError,"model scope"):
                await scope.terminal("term-a")
        self.assertEqual(adapter.calls,[])
