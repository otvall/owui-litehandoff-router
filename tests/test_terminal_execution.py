import asyncio
import copy
from types import SimpleNamespace
import unittest

from lite_handoff_router.model_execution import prepare_model
from lite_handoff_router.runtime import begin, metadata_draft, publish
from tests.test_isolation import IsolatedAdapter
from tests.test_router import User


class TerminalAdapter(IsolatedAdapter):
    def __init__(self):
        super().__init__()
        self.executions=[]
        self.filter=None
        self.names=["run_command","read_file"]
        self.denied=False

    async def terminal_access(self,request,identifier,owner):
        if self.denied:
            raise ValueError("Terminal unavailable")

    async def terminal_tools(self,request,identifier,owner,extra):
        await self.terminal_access(request,identifier,owner)
        result={}
        for name in self.names:
            async def invoke(name=name,**kwargs):
                self.executions.append((identifier,extra["__metadata__"]["chat_id"],name))
                return identifier
            result[name]={"type":"terminal","tool_id":"terminal:"+identifier,"callable":invoke,
                          "spec":{"name":name,"parameters":{"type":"object","properties":{}}}}
        return result,"GUIDE_"+identifier

    async def inlets(self,request,body,model,extra):
        if self.filter:
            self.filter(body)
        return body


class TerminalExecutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter=TerminalAdapter()
        models={identifier:{"id":identifier,"owned_by":"openai","info":{
            "params":{"system":"PROMPT_"+identifier},"meta":{}}} for identifier in ("router","a","b","c","llm")}
        for agent,terminal in (("a","t1"),("b","t2")):
            models[agent]["info"]["meta"]={"lite_handoff":{"terminal_id":terminal},"toolIds":[agent]}
        self.request=SimpleNamespace(scope={},state=SimpleNamespace(),app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
        self.body={"model":"router","messages":[{"role":"system","content":"CLIENT_TERMINAL_GUIDE"},
                   {"role":"user","content":"task"}],"terminal_id":"client"}
        self.metadata={"chat_id":"main","message_id":"message","terminal_id":"parent",
                       "tool_servers":[{"is_terminal":True,"url":"browser-terminal"}],
                       "terminal_skills":["foreign"]}
        self.state=begin(self.request,self.body,self.metadata,"u",{},[],["base"],[])

    async def prepare(self,target):
        return await prepare_model(self.adapter,self.request,self.body,User(),metadata_draft(self.metadata),
                                   self.state,"llm",{},target=target)

    async def test_opt_in_transitions_and_continuations_replace_terminal_context(self):
        for target,terminal in ((None,None),("a","t1"),("a","t1"),("b","t2"),("c",None),(None,None)):
            draft,tools=await self.prepare(target)
            self.assertEqual(draft["metadata"].get("terminal_id"),terminal)
            self.assertEqual("run_command" in tools,bool(terminal))
            self.assertNotIn("CLIENT_TERMINAL_GUIDE",str(draft["messages"]))
            self.assertNotIn("browser-terminal",str(draft["metadata"].get("tool_servers")))
            self.assertNotIn("terminal_skills",draft["metadata"])
            for guide in ("GUIDE_t1","GUIDE_t2"):
                self.assertEqual(str(draft["messages"]).count(guide),int(guide=="GUIDE_"+str(terminal)))
            publish(self.request,self.metadata,draft["metadata"],tools)
        self.assertEqual(self.adapter.executions,[])

    async def test_current_scope_blocks_stale_callable_and_other_tools_keep_working(self):
        draft,tools=await self.prepare("a")
        self.assertEqual(await tools["a_tool"]["callable"](),"result")
        self.assertEqual(await tools["run_command"]["callable"](),"t1")
        self.request.app.state.MODELS["a"]["info"]["meta"]["lite_handoff"]["terminal_id"]="t2"
        with self.assertRaisesRegex(ValueError,"detached"):
            await tools["run_command"]["callable"]()
        self.assertEqual(len(self.adapter.executions),1)

    async def test_failed_preparation_preserves_published_router_tools(self):
        draft,tools=await self.prepare(None)
        publish(self.request,self.metadata,draft["metadata"],tools)
        old_registry=self.metadata["tools"]
        old_names=set(old_registry)
        self.adapter.denied=True
        with self.assertRaisesRegex(ValueError,"unavailable"):
            await self.prepare("a")
        self.assertIs(self.metadata["tools"],old_registry)
        self.assertEqual(set(old_registry),old_names)
        self.assertNotIn("terminal_id",self.metadata)

    async def test_collision_is_rejected_without_overwriting_any_tool(self):
        for name in ("a_tool","view_skill","lite_delegate","lite_parallel","delegate_task"):
            self.adapter.names=[name]
            with self.assertRaisesRegex(ValueError,"Conflicting terminal"):
                await self.prepare("a")

    async def test_inlet_cannot_change_terminal_binding_or_protected_tools(self):
        def registry(body): return body["metadata"]["tools"]
        def replace_callable(body): registry(body)["run_command"]["callable"]=lambda: None
        def replace_schema(body): registry(body)["run_command"]["spec"]={"name":"run_command"}
        def replace_type(body): registry(body)["run_command"]["type"]="external"
        def replace_id(body): registry(body)["run_command"]["tool_id"]="terminal:other"
        mutations=[lambda b:b["metadata"].update(terminal_id="other"),
                   lambda b:registry(b).pop("run_command"),replace_callable,replace_schema,replace_type,replace_id,
                   lambda b:b.update(terminal_id="other"),
                   lambda b:b["metadata"].update(terminal_skills=["foreign"])]
        for mutation in mutations:
            self.adapter.filter=mutation
            with self.assertRaisesRegex(ValueError,"protected"):
                await self.prepare("a")
        def add_terminal(body):
            registry(body)["foreign"]={"type":"terminal","spec":{"name":"foreign"},"callable":lambda:None}
        self.adapter.filter=add_terminal
        with self.assertRaisesRegex(ValueError,"reserved Tool"):
            await self.prepare("c")

    async def test_parallel_preparations_keep_identically_named_callables_separate(self):
        async def worker(target,chat):
            metadata=metadata_draft(self.metadata)
            metadata["chat_id"]=chat
            _,tools=await prepare_model(self.adapter,self.request,self.body,User(),metadata,self.state,"llm",{},target=target)
            return await tools["run_command"]["callable"]()
        self.assertEqual(await asyncio.gather(worker("a","child-a"),worker("b","child-b")),["t1","t2"])
        self.assertEqual({(t,c) for t,c,_ in self.adapter.executions},{("t1","child-a"),("t2","child-b")})
