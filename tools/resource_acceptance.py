"""Private-resource acceptance. Only the disposable pgate fixtures are changed."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import time

import parallel_gate as gate
import parallel_acceptance as product

ROOT=Path(__file__).resolve().parent
TOOLS={"pgate_lookup_a","pgate_lookup_b","pgate_presentation"}
SKILLS={"route-a-pgate","route-b-pgate","route-c-pgate","work-a-pgate","work-b-pgate","work-c-pgate","other-pgate"}


def refresh_exports():
    assert ROOT==Path("/tmp/lhr-parallel-gate")
    for identifier, filename in (("lhr_parallel_preparation","router_preparation.py"),
                                 ("lhr_parallel_pipe","lite_handoff_router.py")):
        content=(ROOT/"dist"/filename).read_text()
        if "preparation" in identifier:
            content=content.replace('AGENTS: dict[str, str] = {}',
                "AGENTS: dict[str, str] = {'agent-a-pgate':'route-a-pgate','agent-b-pgate':'route-b-pgate','agent-c-pgate':'route-c-pgate'}")
        product.upsert("functions",identifier,{"id":identifier,"name":identifier,"content":content,"meta":{}})
    gate.api("/api/models")
    print("Both product exports refreshed; fixture grants and owners preserved")


def install():
    assert ROOT==Path("/tmp/lhr-parallel-gate")
    product.install()
    state=json.loads(gate.STATE.read_text())
    if "resource_owner" not in state:
        password=secrets.token_urlsafe(24)
        owner=gate.api("/api/v1/auths/add",{"name":"Private Agent Owner","email":"lhr-resource-owner@example.com",
                                             "password":password,"role":"user"})
        state["resource_owner"]={**owner,"password":password}
        gate.private_json(gate.STATE,state)
    for collection, identifiers in (("tools",TOOLS),("skills",SKILLS)):
        for identifier in identifiers:
            row=gate.api("/api/v1/"+collection+"/id/"+identifier)
            row["access_grants"]=[]
            if collection=="tools":
                content=row["content"]
                if "class UserValves" not in content:
                    content=content.replace("class Tools:",'''from pydantic import BaseModel
class Tools:
    class UserValves(BaseModel):
        label: str = "missing"
''')
                lines=content.splitlines()
                for index in range(len(lines)-1,-1,-1):
                    if (lines[index].startswith("    async def ") and lines[index].rstrip().endswith(":")
                            and (index+1==len(lines) or "__user__['valves'].label" not in lines[index+1])):
                        lines.insert(index+1,"        assert __user__['role']=='user' and __user__['valves'].label=='actor:'+__user__['id']")
                row["content"]="\n".join(lines)+"\n"
            product.upsert(collection,identifier,row)
    servers=gate.api("/api/v1/configs/tool_servers")
    for connection in servers["TOOL_SERVER_CONNECTIONS"]:
        if (connection.get("info") or {}).get("id") in {"pgate_a","pgate_b"}:
            connection["auth_type"]="oauth_2.1"
            connection["config"]["access_grants"]=[{"principal_type":"user","principal_id":state["resource_owner"]["id"],"permission":"read"}]
    gate.api("/api/v1/configs/tool_servers",servers)
    asyncio.run(prepare_private_records(state))
    gate.api("/api/models")
    print("Private resources installed: two execution users, distinct non-admin agent owner, private routing Skills, per-user MCP OAuth")


async def prepare_private_records(state):
    secret=Path("/app/backend/.webui_secret_key")
    if secret.exists():
        os.environ["WEBUI_SECRET_KEY"]=secret.read_text().strip()
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.models import Model
    from open_webui.models.tools import Tool, Tools
    from open_webui.models.skills import Skill
    from open_webui.models.oauth_sessions import OAuthSessions

    owner=state["resource_owner"]["id"]
    async with get_async_db_context() as session:
        for table, identifiers in ((Model,{"agent-a-pgate","agent-b-pgate","agent-c-pgate"}),
                                   (Tool,TOOLS),(Skill,{"work-a-pgate","work-b-pgate","work-c-pgate","other-pgate"})):
            for identifier in identifiers:
                row=await session.get(table,identifier)
                assert row is not None
                row.user_id=owner
        await session.commit()
    for identifier in TOOLS:
        await Tools.update_user_valves_by_id_and_user_id(identifier,owner,{"label":"owner-private-setting"})
        for key in ("user","user2"):
            actor=state[key]["id"]
            await Tools.update_user_valves_by_id_and_user_id(identifier,actor,{"label":"actor:"+actor})
    for user_id in (owner,state["user"]["id"],state["user2"]["id"]):
        for server_id in ("pgate_a","pgate_b"):
            provider="mcp:"+server_id
            await OAuthSessions.delete_sessions_by_user_id_and_provider(user_id,provider)
            result=await OAuthSessions.create_session(user_id,provider,{"access_token":"resource-fixture-"+user_id,
                                                                        "expires_at":int(time.time())+7200})
            assert result is not None


def assert_private():
    state=json.loads(gate.STATE.read_text())
    for key in ("user","user2"):
        token=state[key]["token"]
        for collection, identifiers in (("tools",TOOLS),("skills",SKILLS)):
            rows=gate.api("/api/v1/"+collection+"/",token=token)
            rows=rows.get(collection,rows.get("items",[])) if isinstance(rows,dict) else rows
            assert not identifiers & {r["id"] for r in rows}
            for identifier in identifiers:
                try:
                    gate.api("/api/v1/"+collection+"/id/"+identifier,token=token)
                except RuntimeError as error:
                    assert any(": "+str(code)+":" in str(error) for code in (401,403,404)), str(error)
                else:
                    raise AssertionError("Private resource is directly accessible")
    print("Direct Tools/Skills APIs and catalogs deny both execution users")


def check():
    assert_private()
    product.check()
    state=json.loads(gate.STATE.read_text())
    results=[]
    for key in ("user","user2"):
        report=None
        for question in (("handoff a","handoff b","general question") if key=="user"
                         else ("handoff a","handoff a","general question")):
            report=product.finish(product.start(question,user_key=key,
                chat_id=report["chat_id"] if report else None,parent_id=report["message_id"] if report else None))
            assert not report["message"].get("error") and report["children"]==[]
            if question=="general question":
                assert "General answer" in report["message"]["content"]
            else:
                assert "FORECAST_"+question[-1].upper() in report["message"]["content"]
                calls=[o for o in report["message"]["output"] if o["type"]=="function_call"]
                assert [o["status"] for o in calls[-9:]]==["completed"]*3+["failed"]*3+["completed"]*3
        results.append({k:report[k] for k in ("chat_id","message_id","user_key")})
    trace=[json.loads(line) for line in Path("/tmp/lhr-acceptance/mcp-trace.jsonl").read_text().splitlines()]
    for key in ("user","user2"):
        own=[r for r in trace if r["actor"]==state[key]["id"] and r.get("oauth_matches_actor")]
        assert own, "No MCP proof of execution-user OAuth"
    assert_private()
    evidence=json.loads((ROOT/"product-evidence.json").read_text())
    verify_run_oauth(evidence["runs"])
    gate.private_json(ROOT/"resource-evidence.json",{"passed":True,"parallel":evidence,"same_chat":results,
                                                   "private_api_denied":True,"user_valves_and_oauth":True})
    print("Private-resource product acceptance passed")


def verify_run_oauth(runs):
    def objects(value):
        if isinstance(value,dict):
            yield value
            for child in value.values(): yield from objects(child)
        elif isinstance(value,list):
            for child in value: yield from objects(child)
        elif isinstance(value,str):
            try: decoded=json.loads(value)
            except (ValueError,TypeError): return
            if decoded!=value: yield from objects(decoded)

    state=json.loads(gate.STATE.read_text())
    for run in runs:
        user=state[run["user_key"]]
        parent=gate.api("/api/v1/chats/"+run["chat_id"],token=user["token"])
        message=parent["chat"]["history"]["messages"][run["message_id"]]
        for result in product.group_result(message)["results"]:
            child=gate.api(result["chat_url"].replace("/c/","/api/v1/chats/"),token=user["token"])
            saved=child["chat"]["history"]["messages"][child["meta"]["lite_parallel"]["message_id"]]
            call=next(o for o in saved["output"] if o.get("name","").endswith("_mcp_echo"))
            output=next(o for o in saved["output"] if o["type"]=="function_call_output" and o["call_id"]==call["call_id"])
            assert any(o.get("actor")==user["id"] and o.get("oauth_matches_actor") is True for o in objects(output))
    print("Each saved worker MCP result confirms execution-user OAuth and identity")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["install","exports","check","lifecycle","controls"])
    args=parser.parse_args()
    if args.action=="install": install()
    elif args.action=="exports": refresh_exports()
    elif args.action=="check": check()
    elif args.action=="lifecycle": product.lifecycle()
    else: product.controls()
