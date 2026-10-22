"""Configure only the named disposable OWUI acceptance instance via its HTTP API.

Run inside the test container after copying dist/ and these tools. Credentials
and original provider configuration remain in private files there, never in git.
"""

import argparse
import json
import os
from pathlib import Path
import secrets
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "private-state.json"
TOKEN = ROOT / "admin-token"
BASE = "http://127.0.0.1:8080"


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(value, output)


def api(path, data=None, token=None, method=None):
    token = token or TOKEN.read_text().strip()
    request = urllib.request.Request(
        BASE + path, data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{path}: HTTP {error.code}: {error.read().decode()}") from None


def setup():
    assert api("/api/version")["version"] == "0.12.3"
    assert api("/api/v1/auths/")["role"] == "admin"
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if "original_config" not in state:
        state["original_config"] = api("/api/v1/configs/export")
        state["original_openai"] = api("/openai/config")
        state["original_servers"] = api("/api/v1/configs/tool_servers")
        state["users"], state["groups"] = {}, {}
        private_json(STATE, state)
    for letter in ("a", "b"):
        if letter not in state["users"]:
            password = secrets.token_urlsafe(24)
            user = api("/api/v1/auths/add", {
                "name": "Router Test " + letter.upper(), "email": f"lhr-{letter}@example.com",
                "password": password, "role": "user",
            })
            user["password"] = password
            state["users"][letter] = user
            private_json(STATE, state)
        if letter not in state["groups"]:
            group = api("/api/v1/groups/create", {"name": "LHR Test " + letter.upper(), "description": "Disposable router acceptance"})
            state["groups"][letter] = group["id"]
            private_json(STATE, state)
        api(f"/api/v1/groups/id/{state['groups'][letter]}/users/add", {"user_ids": [state["users"][letter]["id"]]})

    def grants(letters="ab"):
        return [{"principal_type": "group", "principal_id": state["groups"][letter], "permission": "read"} for letter in letters]

    def function(identifier, filename, name):
        content = (ROOT / "dist" / filename).read_text()
        if filename == "router_preparation.py":
            content = content.replace("AGENTS: dict[str, str] = {}", "AGENTS: dict[str, str] = {'agent-a-lhr': 'route-a-lhr', 'agent-b-lhr': 'route-b-lhr'}")
        form = {"id": identifier, "name": name, "content": content, "meta": {}}
        existing = next((item for item in api("/api/v1/functions/") if item["id"] == identifier), None)
        result = api(f"/api/v1/functions/id/{identifier}/update" if existing else "/api/v1/functions/create", form)
        if not result["is_active"]:
            api(f"/api/v1/functions/id/{identifier}/toggle", {})

    def tool(identifier, name, content, letters):
        form = {"id": identifier, "name": name, "content": content, "meta": {}, "access_grants": grants(letters)}
        existing = next((item for item in api("/api/v1/tools/") if item["id"] == identifier), None)
        api(f"/api/v1/tools/id/{identifier}/update" if existing else "/api/v1/tools/create", form)

    function("lhr_router", "lite_handoff_router.py", "LHR Router Pipe")
    function("lhr_preparation", "router_preparation.py", "LHR Router Preparation")
    api("/api/v1/functions/id/lhr_router/valves/update", {"orchestrator_model_id": "router-llm"})
    api("/api/v1/functions/id/lhr_preparation/valves/update", {"base_tool_ids": [], "base_skill_ids": []})
    if any(item["id"] == "lhr_delegate" for item in api("/api/v1/tools/")):
        api("/api/v1/tools/id/lhr_delegate/delete", method="DELETE")
    for letter in "ab":
        tool("lhr_lookup_" + letter, "LHR Lookup " + letter.upper(), f'''import json
class Tools:
    async def profile_lookup(self, query: str, __user__: dict, __messages__: list) -> str:
        assert "LHR_{letter.upper()}_PROMPT" in str(__messages__)
        assert "LHR_ROUTER_PROMPT" not in str(__messages__)
        return json.dumps({{"result": "TOOL_RESULT_42", "agent": "{letter}", "actor": __user__["id"]}})
''', letter)
        for prefix in ("route", "work"):
            identifier = f"{prefix}-{letter}-lhr"
            form = {"id": identifier, "name": identifier, "description": f"LHR {prefix} {letter.upper()}",
                    "content": (f'For profile {letter} call lite_delegate(agent_id="agent-{letter}-lhr").'
                                if prefix == "route" else f"Use your profile_lookup Tool for profile {letter}."),
                    "access_grants": grants(letter), "meta": {}}
            existing = next((item for item in api("/api/v1/skills/") if item["id"] == identifier), None)
            api(f"/api/v1/skills/id/{identifier}/update" if existing else "/api/v1/skills/create", form)

    def model(identifier, name, base, prompt, temperature, meta, letters="ab"):
        form = {"id": identifier, "name": name, "base_model_id": base, "params": {
            "system": prompt, "temperature": temperature, "function_calling": "native",
        }, "meta": meta, "access_grants": grants(letters)}
        existing = next((item for item in api("/api/v1/models/all") if item["id"] == identifier), None)
        api("/api/v1/models/model/update" if existing else "/api/v1/models/create", form)

    for identifier, letters in (("router-llm", "ab"), ("specialist-a", "a"), ("specialist-b", "b"), ("lhr_router", "ab")):
        model(identifier, identifier, None, "", 0.5, {"hidden": True}, letters)
    for letter in "ab":
        model(f"agent-{letter}-lhr", "LHR Agent " + letter.upper(), "specialist-" + letter,
              f"LHR_{letter.upper()}_PROMPT. Read your working Skill, use profile_lookup and MCP when available.",
              0.2 if letter == "a" else 0.4,
              {"hidden": True, "toolIds": ["lhr_lookup_" + letter, "server:mcp:lhr_" + letter],
               "skillIds": [f"work-{letter}-lhr"]}, letter)
    model("lhr-router", "LHR Router", "lhr_router", "LHR_ROUTER_PROMPT. Answer general questions. For profile questions read the routing Skill and call lite_delegate.",
          0.8, {"filterIds": ["lhr_preparation"], "toolIds": [], "skillIds": []})

    api("/openai/config/update", {"ENABLE_OPENAI_API": True, "OPENAI_API_BASE_URLS": ["http://127.0.0.1:8766/v1"],
                                   "OPENAI_API_KEYS": ["local-test"], "OPENAI_API_CONFIGS": {}})
    api("/api/v1/configs/import", {"config": {"ollama.enable": False, "chat.context_compaction.enable": False}})
    connections = [item for item in state["original_servers"]["TOOL_SERVER_CONNECTIONS"]
                   if (item.get("info") or {}).get("id") not in {"lhr_a", "lhr_b"}]
    for letter in "ab":
        connections.append({"url": "http://127.0.0.1:8766/mcp/", "path": "", "type": "mcp", "auth_type": "none", "key": "",
                            "headers": {"X-Execution-User": "{{USER_ID}}"},
                            "config": {"enable": True, "access_grants": grants(letter)},
                            "info": {"id": "lhr_" + letter, "name": "LHR MCP " + letter.upper()}})
    api("/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": connections})
    print(json.dumps({"configured": True, "router": "lhr-router", "groups": state["groups"],
                      "users": {letter: {"id": user["id"], "email": user["email"]} for letter, user in state["users"].items()}}))


def snapshot():
    functions = [api("/api/v1/functions/id/" + identifier) for identifier in ("lhr_router", "lhr_preparation")]
    private_json(ROOT / "installation-backup.json", {
        "functions": functions,
        "tool": (api("/api/v1/tools/id/lhr_delegate")
                 if any(item["id"] == "lhr_delegate" for item in api("/api/v1/tools/")) else None),
        "valves": {item["id"]: api("/api/v1/functions/id/" + item["id"] + "/valves") for item in functions},
    })
    print("Functions, administrative Valves and optional legacy delegate Tool saved")


def restore():
    backup = json.loads((ROOT / "installation-backup.json").read_text())
    for item in backup["functions"]:
        api("/api/v1/functions/id/" + item["id"] + "/update", item)
        api("/api/v1/functions/id/" + item["id"] + "/valves/update", backup["valves"][item["id"]])
    exists = any(item["id"] == "lhr_delegate" for item in api("/api/v1/tools/"))
    if backup.get("tool"):
        api("/api/v1/tools/id/lhr_delegate/update" if exists else "/api/v1/tools/create", backup["tool"])
    elif exists:
        api("/api/v1/tools/id/lhr_delegate/delete", method="DELETE")
    print("Functions, administrative Valves and optional legacy delegate Tool restored")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "snapshot", "restore"])
    args = parser.parse_args()
    {"setup": setup, "snapshot": snapshot, "restore": restore}[args.action]()
