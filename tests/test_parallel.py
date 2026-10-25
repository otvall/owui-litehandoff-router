import copy
import asyncio
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

from pydantic import ValidationError

from lite_handoff_router.entrypoints import router_preparation
from lite_handoff_router.runtime import begin
from lite_handoff_router.parallel import (parallel_tool, run_registry, prepare_worker, validate_batch,
                                         downstream_data, PARALLEL_TICKET, collect_result)
from lite_handoff_router.parallel import finish_cleanup
from lite_handoff_router.protocol import delegate_tool, delegate, confirmed_target
from lite_handoff_router.observation import observe_response
from lite_handoff_router.model_execution import prepare_model
from tests.test_preparation import Directory
from tests.test_protocol import call
from tests.test_router import Adapter, User


def observe(state, *calls):
    observe_response({"choices": [{"message": {"tool_calls": list(calls)}}]}, state, {"kind": "router"})


class GroupAdapter(Adapter):
    def __init__(self):
        super().__init__()
        self.reason = None
        self.children_map = {}
        self.messages = {}
        self.started = []
        self.ended = []
        self.revoked = set()
        self.deleted_files = set()
        self.scopes = []

    async def user(self, user_id):
        user = User()
        user.id = user_id
        return user

    async def parallel_reason(self, request, metadata, user):
        return self.reason

    async def model(self, request, model_id, user):
        if model_id in self.revoked:
            raise ValueError("Access revoked")
        return await super().model(request, model_id, user)

    async def file(self, file_id, user):
        if file_id in self.deleted_files or file_id.startswith('/'):
            raise ValueError("File unavailable")
        return {"type": "file", "id": file_id, "url": "/api/v1/files/" + file_id + "/content"}

    async def child(self, child_id, user):
        child = self.children_map.get(child_id)
        return copy.deepcopy(child) if child and child["user_id"] == user.id else None

    async def child_message(self, child_id, message_id, user):
        return copy.deepcopy(self.messages.get((child_id, message_id))) if await self.child(child_id, user) else None

    async def children(self, parent, user):
        return [cid for cid, child in self.children_map.items()
                if child["user_id"] == user.id and child["meta"]["parent_chat_id"] == parent]

    async def correlate_child(self, child_id, user, correlation):
        self.children_map[child_id]["meta"]["lite_parallel"] = copy.deepcopy(correlation)

    async def stop_child(self, request, child_id, user):
        self.children_map[child_id]["stopped"] = True

    async def native_delegate(self, request, user, metadata, task, file_ids):
        child_id = "child-" + str(len(self.started))
        self.started.append((task, time.monotonic()))
        child = SimpleNamespace(scope={}, state=SimpleNamespace(internal=True,
            chat_context=copy.deepcopy(metadata["chat_context"])), app=request.app)
        own_metadata = {"chat_id": child_id, "message_id": "answer"}
        body = {"model": metadata["model_id"], "messages": [{"role": "system", "content": "native/Router"}]}
        self.children_map[child_id] = {"user_id": user.id, "meta": {"internal": True, "type": "subagent",
            "parent_chat_id": metadata["chat_id"], "parent_message_id": metadata["message_id"]},
            "chat": {"history": {"currentId": "answer", "messages": {"question": {"role": "user", "content": task}}}}}
        self.messages[(child_id, "answer")] = {"done": False, "content": ""}
        await prepare_worker(self, child, body, own_metadata, user)
        self.scopes.append(child.scope)
        if "dispatch-slow" in task:
            await asyncio.sleep(20)
        if "slow" in task:
            await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(0.005)
        if "failure" in task:
            raise ValueError("Worker failure after Tool")
        self.messages[(child_id, "answer")] = {"done": "paused" not in task,
            "content": "FORECAST " + task + "X" * 400, "files": [{"id": "artifact-" + child_id}]}
        self.ended.append((task, time.monotonic()))
        return "Short native summary"


class ParallelExecutionTest(unittest.IsolatedAsyncioTestCase):
    def setup_request(self, *, maximum=4, timeout=300, user=None):
        user = user or User()
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={
            "llm": {"owned_by": "openai"}, "a": {"owned_by": "openai"}, "b": {"owned_by": "openai"}})))
        metadata = {"chat_id": "parent", "message_id": "answer", "files": [{"id": "input-file"}]}
        body = {"model": "router", "messages": [{"role": "system", "content": "ROUTER_SECRET"},
            {"role": "user", "content": "Forecasts, then presentation"},
            {"role": "assistant", "tool_calls": [call("other_tool", "old", {})]},
            {"role": "tool", "tool_call_id": "old", "content": "FOREIGN_TOOL_SECRET"}]}
        state = begin(request, body, metadata, user.id,
                      {a: {"agent_id": a, "skill_id": "route-" + a} for a in ("a", "b")}, [], [], [],
                      max_parallel_tasks=maximum, parallel_timeout_seconds=timeout)
        return request, metadata, state, user

    async def invoke(self, adapter, request, metadata, state, user, assignments):
        observe(state, call("lite_parallel", "parallel", {"tasks": assignments}))
        return json.loads(await parallel_tool(adapter, request, user, metadata, state, {})["callable"](assignments))

    async def test_batch_validation_is_atomic(self):
        for invalid in ({"agent_id": "route-b", "task": "x"}, {"agent_id": "b", "task": " "},
                        {"agent_id": "b", "task": "x", "terminal_id": "client"},
                        {"agent_id": "b", "task": "x", "temperature": 0.2},
                        {"agent_id": "b", "task": "x", "file_ids": ["unattached"]}):
            request, metadata, state, user = self.setup_request()
            adapter = GroupAdapter()
            with self.assertRaisesRegex(ValueError, "assignment 2"):
                await self.invoke(adapter, request, metadata, state, user, [{"agent_id": "a", "task": "x"}, invalid])
            self.assertEqual(adapter.started, [])
            self.assertEqual(state["parallel"]["status"], "idle")
        with self.assertRaisesRegex(ValueError, "between"):
            await self.invoke(adapter, request, metadata, state, user, [{"agent_id": "a", "task": "x"}] * 5)
        metadata['files'] = None  # Actual native empty attachment representation.
        self.assertEqual(len(await validate_batch(adapter, request, user, metadata, state,
            [{"agent_id": "a", "task": "x"}] * 2)), 2)

    async def test_overlap_order_same_agent_and_full_output(self):
        request, metadata, state, user = self.setup_request()
        adapter = GroupAdapter()
        report = await self.invoke(adapter, request, metadata, state, user,
            [{"agent_id": "a", "task": "slow first", "file_ids": ["input-file"]}, {"agent_id": "a", "task": "second"}])
        self.assertEqual(report["status"], "completed")
        self.assertIn("second", adapter.ended[0][0])
        self.assertLess(max(t for _, t in adapter.started), min(t for _, t in adapter.ended))
        self.assertEqual([r["task"] for r in report["results"]], ["slow first", "second"])
        self.assertNotEqual(report["results"][0]["chat_url"], report["results"][1]["chat_url"])
        self.assertTrue(all(len(r["result"]) > 400 for r in report["results"]))
        self.assertEqual(run_registry(request), {})
        self.assertIsNone(state["active_agent"])
        self.assertIsNot(adapter.scopes[0], adapter.scopes[1])
        self.assertNotIn("ROUTER_SECRET", str(adapter.scopes))
        self.assertNotIn("FOREIGN_TOOL_SECRET", str(adapter.scopes))
        self.assertEqual([s["lite_handoff_router"]["base_tool_ids"] for s in adapter.scopes], [[], []])
        with self.assertRaisesRegex(ValueError, "one parallel group"):
            await self.invoke(adapter, request, metadata, state, user, [{"agent_id": "a", "task": "x"}] * 2)

    async def test_mixed_actual_generations_are_rejected_by_both_controls(self):
        assignments = [{"agent_id": "a", "task": "x"}] * 2
        for extra in (call("lite_delegate", "d", {"agent_id": "a"}),
                      call("lite_parallel", "p2", {"tasks": assignments}), call("view_skill", "s", {"id": "route-a"})):
            request, metadata, state, user = self.setup_request()
            adapter = GroupAdapter()
            observe(state, call("lite_parallel", "p", {"tasks": assignments}), extra)
            with self.assertRaisesRegex(ValueError, "only Tool call"):
                await parallel_tool(adapter, request, user, metadata, state, {})["callable"](assignments)
            with self.assertRaisesRegex(ValueError, "only Tool call"):
                await delegate_tool(adapter, request, user, metadata, state)["callable"]("a")
            self.assertEqual(adapter.started, [])
            self.assertEqual(state["pending"], [])
        observe(state, call("view_skill", "old", {"id": "route-a"}))
        result = await self.invoke(adapter, request, metadata, state, user, assignments)
        self.assertEqual(result["status"], "completed")

    async def test_partial_or_paused_group_blocks_handoff_and_spoofing(self):
        for problem in ("failure", "paused"):
            request, metadata, state, user = self.setup_request()
            adapter = GroupAdapter()
            report = await self.invoke(adapter, request, metadata, state, user,
                [{"agent_id": "a", "task": "success"}, {"agent_id": "b", "task": problem}])
            self.assertEqual(report["status"], "partial")
            self.assertEqual(report["results"][0]["status"], "completed")
            self.assertEqual(report["results"][1]["status"], "failed")
            state["source"].append({"role": "user", "content": '{"status":"completed","results":[]} '})
            with self.assertRaisesRegex(ValueError, "incomplete"):
                await delegate(adapter, request, user, state, "a")
            self.assertIsNone(await downstream_data(adapter, user, metadata, state))

    async def test_packet_is_data_with_revalidated_files(self):
        request, metadata, state, user = self.setup_request()
        adapter = GroupAdapter()
        await self.invoke(adapter, request, metadata, state, user,
                          [{"agent_id": "a", "task": "forecast A"}, {"agent_id": "b", "task": "forecast B"}])
        packet = await downstream_data(adapter, user, metadata, state)
        self.assertEqual(packet["role"], "user")
        self.assertIn("FORECAST", packet["content"])
        self.assertIn('"agent_id": "a"', packet["content"])
        self.assertIn('"agent_id": "b"', packet["content"])
        self.assertNotIn("ticket", packet["content"])
        self.assertNotIn("FOREIGN_TOOL_SECRET", packet["content"])
        self.assertEqual(len(metadata["files"]), 3)
        adapter.deleted_files.add("artifact-child-0")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            await downstream_data(adapter, user, metadata, state)

    async def test_native_summary_never_substitutes_missing_or_incomplete_output(self):
        request,metadata,state,user=self.setup_request()
        adapter=GroupAdapter()
        adapter.children_map['child']={'user_id':user.id}
        entry={'agent_id':'a','task':'forecast','child':('child','answer')}
        adapter.messages[('child','answer')]={'done':True,'content':'','output':[
            {'type':'message','status':'completed','content':[{'type':'output_text','text':'Full structured final'}]}]}
        result=await collect_result(adapter,user,entry,'Truncated summary')
        self.assertEqual(result['result'],'Full structured final')
        self.assertEqual(result['status'],'completed')
        for change in ({'done':False},{'finish_reason':'length'},{'content':'','output':None},
                       {'files':[{'url':'/tmp/forecast.csv'}]}):
            adapter.messages[('child','answer')]={'done':True,'content':'Forecast',**change}
            if 'files' in change:
                with self.assertRaisesRegex(ValueError,'OWUI file'):
                    await collect_result(adapter,user,entry,'summary')
            else:
                self.assertEqual((await collect_result(adapter,user,entry,'summary'))['status'],'failed')

    async def test_terminal_paths_are_not_portable_owui_artifacts(self):
        request,metadata,state,user=self.setup_request()
        adapter=GroupAdapter()
        entry={"agent_id":"a","task":"terminal output","child":("child","answer")}
        adapter.children_map["child"]={"user_id":user.id,"meta":{"parent_chat_id":"parent"}}
        for file in ({"path":"/tmp/result.txt"},
                     {"source":"open_terminal","path":"/tmp/result.txt","terminal_id":"t"}):
            adapter.messages[("child","answer")]={"done":True,"content":"Result","files":[file]}
            with self.assertRaisesRegex(ValueError,"available OWUI file"):
                await collect_result(adapter,user,entry,"summary")
        adapter.messages[("child","answer")]={"done":True,"content":"Result","files":[{"id":"normal-file"}]}
        result=await collect_result(adapter,user,entry,"summary")
        self.assertEqual(result["files"][0]["id"],"normal-file")
        adapter.deleted_files.add("normal-file")
        with self.assertRaisesRegex(ValueError,"unavailable"):
            await collect_result(adapter,user,entry,"summary")

    async def test_stop_and_timeout_cleanup_without_retry(self):
        for timeout in (False, True):
            request, metadata, state, user = self.setup_request()
            adapter = GroupAdapter()
            if timeout:
                state["parallel"]["timeout_seconds"] = 0.03
            work = asyncio.create_task(self.invoke(adapter, request, metadata, state, user,
                [{"agent_id": "a", "task": "first"}, {"agent_id": "b", "task": "dispatch-slow"}]))
            if not timeout:
                while len(adapter.started) < 2:
                    await asyncio.sleep(0)
                work.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await work
            else:
                report = await work
                self.assertEqual(report["status"], "timed_out")
                self.assertEqual([r["status"] for r in report["results"]], ["completed", "timed_out"])
            self.assertEqual(run_registry(request), {})
            self.assertTrue(all(c.get("stopped") for c in adapter.children_map.values()))
            self.assertEqual(len(adapter.started), 2)

    async def test_revocation_after_preflight_and_availability_errors(self):
        request, metadata, state, user = self.setup_request()
        adapter = GroupAdapter()
        validated = await validate_batch(adapter, request, user, metadata, state,
            [{"agent_id": "a", "task": "x"}, {"agent_id": "b", "task": "y"}])
        adapter.revoked.add("b")
        from lite_handoff_router.parallel import execute_group
        report = await execute_group(adapter, request, user, metadata, state, validated, {})
        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(adapter.started), 1)
        for reason in ("saved chat required", "subagents disabled", "unsupported approval mode"):
            request, metadata, state, user = self.setup_request()
            adapter.reason = reason
            with self.assertRaisesRegex(ValueError, reason):
                await self.invoke(adapter, request, metadata, state, user,
                                  [{"agent_id": "a", "task": "x"}] * 2)
            self.assertEqual(state["parallel"]["status"], "idle")

    async def test_inline_schema_uses_personal_enum_and_reserved_collision(self):
        request, metadata, state, user = self.setup_request()
        adapter = GroupAdapter()
        state["base_tool_ids"] = ["base"]
        body, tools = await prepare_model(adapter, request, {}, user, metadata, state, "llm", {})
        self.assertEqual(tools["lite_parallel"]["spec"]["parameters"]["properties"]["tasks"]["items"]
                         ["properties"]["agent_id"]["enum"], ["a", "b"])
        state["parallel"]["max_tasks"] = 0
        body, tools = await prepare_model(adapter, request, {}, user, metadata, state, "llm", {})
        self.assertNotIn("lite_parallel", tools)
        self.assertIn("lite_delegate", tools)
        regular = adapter.tools
        async def collision(*args):
            loaded = await regular(*args)
            loaded["lite_parallel"] = {"spec": {"name": "lite_parallel"}, "callable": lambda: None}
            return loaded
        adapter.tools = collision
        with self.assertRaisesRegex(ValueError, "Reserved routing Tool collision"):
            await prepare_model(adapter, request, {}, user, metadata, state, "llm", {})

    async def test_tickets_reject_spoof_owner_parent_replay_and_expiry(self):
        request, metadata, state, user = self.setup_request()
        adapter = GroupAdapter()
        entry = {'user_id': user.id, 'parent_chat_id':'parent', 'parent_message_id':'answer',
                 'router_id':'router', 'agent_id':'a', 'registry_entry':state['registry']['a'],
                 'input':[{'role':'user','content':'task'}], 'files':[], 'bound':AsyncMock()}
        ticket = 'server-only-test-ticket'
        run_registry(request)[ticket] = entry
        adapter.children_map['child'] = {'user_id':user.id, 'meta':{'internal':True,'type':'subagent',
            'parent_chat_id':'parent','parent_message_id':'answer'},'chat':{'history':{'currentId':'child-answer'}}}
        child = SimpleNamespace(scope={},app=request.app,state=SimpleNamespace(internal=False,
            chat_context={PARALLEL_TICKET:ticket}))
        body={'model':'router','messages':[]}
        child_metadata={'chat_id':'child','message_id':'child-answer','chat_context':{PARALLEL_TICKET:ticket}}
        self.assertFalse(await prepare_worker(adapter, child, body, child_metadata, user))
        child.state.internal=True
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            await prepare_worker(adapter, child, body, child_metadata, await adapter.user('other'))
        adapter.children_map['child']['meta']['parent_message_id']='another-parent'
        with self.assertRaisesRegex(ValueError, 'identity'):
            await prepare_worker(adapter, child, body, child_metadata, user)
        adapter.children_map['child']['meta']['parent_message_id']='answer'
        self.assertTrue(await prepare_worker(adapter, child, body, child_metadata, user))
        self.assertTrue(child.scope['lite_handoff_router']['worker'])
        self.assertEqual(child.scope['lite_handoff_router']['active_agent'],'a')
        self.assertNotIn(PARALLEL_TICKET,str(child_metadata))
        replay=SimpleNamespace(scope={},app=request.app,state=SimpleNamespace(internal=True,
            chat_context={PARALLEL_TICKET:ticket}))
        with self.assertRaisesRegex(ValueError, 'already bound'):
            await prepare_worker(adapter,replay,body,child_metadata,user)
        run_registry(request).pop(ticket)
        with self.assertRaisesRegex(ValueError,'expired'):
            await prepare_worker(adapter,replay,body,child_metadata,user)

    async def test_concurrent_users_do_not_share_tickets_or_registry(self):
        one = self.setup_request()
        user2=User(); user2.id='u2'
        two = self.setup_request(user=user2)
        two[0].app=one[0].app
        two[2]['registry'].pop('b')
        adapter=GroupAdapter()
        reports=await asyncio.gather(
            self.invoke(adapter,*one,[{'agent_id':'b','task':'slow B'},{'agent_id':'a','task':'slow A'}]),
            self.invoke(adapter,*two,[{'agent_id':'a','task':'first'},{'agent_id':'a','task':'second'}]))
        self.assertEqual([r['status'] for r in reports],['completed','completed'])
        owners=[s['lite_handoff_router']['user_id'] for s in adapter.scopes]
        self.assertEqual(sorted(owners),['u','u','u2','u2'])
        self.assertEqual(run_registry(one[0]),{})

    async def test_repeated_stop_finishes_binding_transaction(self):
        entered,release=asyncio.Event(),asyncio.Event()
        committed=[]
        async def transaction():
            entered.set()
            await release.wait()
            committed.append(True)
        work=asyncio.create_task(finish_cleanup(transaction()))
        await entered.wait()
        work.cancel()
        await asyncio.sleep(0)
        work.cancel()
        release.set()
        self.assertTrue(await work)
        self.assertEqual(committed,[True])


class ParallelPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_admin_limits_reset_and_ignore_client_fields(self):
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={
            "router": {"pipe": {}}, "a": {"id": "a"}})))
        preparation = router_preparation.Filter()
        with patch.object(router_preparation, "OWUIAdapter", return_value=Directory()), \
             patch.object(router_preparation, "AGENTS", {"a": "route-a"}):
            body = {"model": "router", "messages": [], "max_parallel_tasks": 999,
                    "parallel": {"status": "completed"}, "metadata": {"max_parallel_tasks": 999}}
            await preparation.inlet(body, request, {"id": "u", "valves": {"max_parallel_tasks": 99}})
            state = request.scope["lite_handoff_router"]
            self.assertEqual((state["parallel"]["max_tasks"], state["parallel"]["timeout_seconds"]), (4, 300))
            old = copy.deepcopy(state["parallel"])
            state["parallel"].update(status="completed", results=[{"result": "old"}])
            preparation.valves.max_parallel_tasks = 0
            preparation.valves.parallel_timeout_seconds = 10
            await preparation.inlet({"model": "router", "messages": []}, request, {"id": "u"})
            group = request.scope["lite_handoff_router"]["parallel"]
            self.assertEqual(group["status"], "idle")
            self.assertEqual(group["results"], [])
            self.assertEqual((group["max_tasks"], group["timeout_seconds"]), (0, 10))
            self.assertEqual(old["max_tasks"], 4)
        empty = begin(request, {"model": "router"}, {}, "u", {}, [], [], [])
        self.assertEqual(empty["registry"], {})
        self.assertEqual(empty["parallel"]["status"], "idle")

    def test_invalid_limits_are_rejected(self):
        for maximum in (-1, 1, 2.5, True, "4"):
            with self.subTest(maximum=maximum), self.assertRaises(ValidationError):
                router_preparation.Filter.Valves(max_parallel_tasks=maximum)
        for timeout in (0, -1, True, "300"):
            with self.subTest(timeout=timeout), self.assertRaises(ValidationError):
                router_preparation.Filter.Valves(parallel_timeout_seconds=timeout)
