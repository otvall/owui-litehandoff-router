"""Full HTTP/native-loop acceptance against the disposable configured instance."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from uuid import uuid4

from acceptance_setup import api, STATE, ROOT


def turn(question, token=None, chat_id=None, parent=None, tools=None):
    user_id, answer_id = str(uuid4()), str(uuid4())
    form = {"model": "lhr-router", "stream": True, "session_id": "lhr-acceptance-" + uuid4().hex,
            "id": answer_id, "parent_id": parent, "messages": [{"role": "user", "content": question}],
            "user_message": {"id": user_id, "parentId": parent, "childrenIds": [answer_id], "role": "user",
                             "content": question, "timestamp": int(time.time()), "models": ["lhr-router"]}}
    if chat_id:
        form["chat_id"] = chat_id
    if tools is not None:
        form["tools"] = tools
    started = api("/api/chat/completions", form, token=token)
    chat_id = started["chat_id"]
    for _ in range(300):
        chat = api("/api/v1/chats/" + chat_id, token=token)
        message = chat["chat"]["history"]["messages"].get(answer_id, {})
        if message.get("done"):
            return chat_id, answer_id, message
        time.sleep(0.1)
    raise AssertionError("Completion did not finish: " + chat_id)


def calls(message):
    return [item for item in message.get("output", []) if item.get("type") == "function_call"]


def completed(message, agent, actor):
    assert not message.get("error"), message.get("error")
    assert f"specialist-{agent} final" in message.get("content", ""), message
    output = message["output"]
    assert all(item.get("status") == "completed" for item in output), output
    assert [item["name"] for item in calls(message)] == ["view_skill", "lite_delegate", "view_skill", "profile_lookup", f"lhr_{agent}_mcp_echo"]
    results = [item for item in output if item.get("type") == "function_call_output"]
    handoff = next(item for item in calls(message) if item["name"] == "lite_delegate")
    result = next(item for item in results if item["call_id"] == handoff["call_id"])
    marker = json.loads("".join(part.get("text", "") for part in result["output"]))
    assert marker["__lite_delegate__"] == "v2" and marker["agent_id"] == f"agent-{agent}-lhr"
    assert marker["request_id"] and marker["nonce"]
    assert actor in str(results[-1]["output"]), results[-1]
    assert actor in str(results[-2]["output"]), results[-2]
    assert any(item.get("description") == "Передача: LHR Agent " + agent.upper() for item in message.get("statusHistory", []))


def check():
    state = json.loads(STATE.read_text())
    result = {}
    assert not any(item["id"] == "lhr_delegate" for item in api("/api/v1/tools/"))
    assert api("/api/v1/functions/id/lhr_preparation/valves")["base_tool_ids"] == []
    result["no_workspace_delegate_tool"] = True
    with ThreadPoolExecutor(2) as pool:
        pending = {letter: pool.submit(turn, "profile " + letter + " parallel acceptance", state["users"][letter]["token"])
                   for letter in "ab"}
        for letter, future in pending.items():
            chat_id, answer_id, message = future.result()
            completed(message, letter, state["users"][letter]["id"])
            result["parallel_" + letter] = {"chat_id": chat_id, "answer_id": answer_id, "passed": True}
    # A lacks B but still has A: the Router's enum must never publish B to A.
    a = state["users"]["a"]
    _, _, message = turn("profile b requested by A", a["token"])
    completed(message, "a", a["id"])
    result["personal_registry"] = True
    _, _, message = turn("profile a explicit empty tools", a["token"], tools=[])
    completed(message, "a", a["id"])
    result["explicit_empty_tools"] = True

    chat_id, answer_id, message = turn("profile a sequential acceptance")
    completed(message, "a", api("/api/v1/auths/")["id"])
    same_chat, answer_id, message = turn("profile b sequential acceptance", chat_id=chat_id, parent=answer_id)
    assert same_chat == chat_id
    completed(message, "b", api("/api/v1/auths/")["id"])
    same_chat, _, message = turn("general question sequential acceptance", chat_id=chat_id, parent=answer_id)
    assert same_chat == chat_id
    assert message["content"] == "General answer" and not calls(message), message
    result["a_b_router"] = {"chat_id": chat_id, "passed": True}

    for question, detail in (("provider error", "Test provider failure"), ("overflow", "Context length exceeded")):
        _, _, message = turn(question, a["token"])
        assert detail in str(message.get("error")), message
        assert not calls(message), message
        result[question] = True
    _, _, message = turn("profile a fail after lookup", a["token"])
    assert "Test provider failure after Tool" in str(message.get("error")), message
    assert sum(item["name"] == "profile_lookup" for item in calls(message)) == 1
    assert len([item for item in calls(message) if item["name"].endswith("_mcp_echo")]) == 0
    result["no_action_replay"] = True

    valves = api("/api/v1/functions/id/lhr_preparation/valves")
    try:
        api("/api/v1/functions/id/lhr_preparation/valves/update", {**valves, "base_tool_ids": ["lhr_missing_required"]})
        _, _, message = turn("missing resource acceptance", a["token"])
        assert "Required model Tools unavailable" in str(message.get("error")), message
        assert not calls(message)
        result["required_resource_error"] = True
    finally:
        api("/api/v1/functions/id/lhr_preparation/valves/update", valves)

    # Revoke after the Router received its registry but before delegate executes.
    ready, release = ROOT / "handoff-ready", ROOT / "handoff-continue"
    ready.unlink(missing_ok=True)
    release.unlink(missing_ok=True)
    saved_model = api("/api/v1/models/model?id=agent-a-lhr")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(turn, "profile a revoke access acceptance", a["token"])
        try:
            for _ in range(200):
                if ready.exists():
                    break
                if future.done():
                    raise AssertionError("Request finished before the handoff gate: " + str(future.result()))
                time.sleep(0.05)
            else:
                raise AssertionError("Handoff gate was not reached")
            api("/api/v1/models/model/update", {**saved_model, "access_grants": []})
            release.touch()
            revoked_chat, _, message = future.result()
            assert "Tool execution failed" in str(message.get("error")), message
            assert not any(item["name"] == "profile_lookup" or item["name"].endswith("_mcp_echo") for item in calls(message))
            assert any(item["name"] == "lite_delegate" and item["status"] == "failed" for item in calls(message))
            result["revoked_before_handoff"] = {"chat_id": revoked_chat, "passed": True}
        finally:
            release.touch()
            api("/api/v1/models/model/update", saved_model)

    Path(ROOT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    check()
