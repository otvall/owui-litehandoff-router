"""Handoff is a completed, server-witnessed Tool exchange, not text parsing."""

import json
from uuid import uuid4

from .runtime import require
from .parallel_state import control_generation, handoff_barrier
from .resources import routing_owner


def delegate_tool(adapter, request, user, metadata, state):
    """Build a request-local native Tool; no Workspace Tool upload is needed."""
    async def lite_delegate(agent_id: str) -> str:
        actor = await adapter.user(user.id)
        current = require(request, metadata, actor.id)
        if current is not state:
            raise ValueError("Stale delegate request context")
        control_generation(current, "lite_delegate", {"agent_id": agent_id})
        return await delegate(adapter, request, actor, current, agent_id)

    return {
        "spec": {
            "name": "lite_delegate",
            "description": "Hand off this user request to an exact advertised Workspace Model ID.",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {
                    "type": "string",
                    "description": "Workspace Model ID from the current routing manifest.",
                    "enum": list(state["registry"]),
                }},
                "required": ["agent_id"],
            },
        },
        "callable": lite_delegate,
    }


def tool_image(message):
    content = message.get("content")
    return (message.get("role") == "user" and isinstance(content, list) and len(content) > 1
            and content[0] == {"type": "text", "text": "Here are the images from the tool results above. Please analyze them."}
            and all(p.get("type") == "image_url" for p in content[1:]))


def last_user(messages):
    return next((i for i in range(len(messages) - 1, -1, -1)
                 if messages[i].get("role") == "user" and not tool_image(messages[i])), -1)


def object_value(content):
    if isinstance(content, dict):
        return content
    try:
        value = json.loads(content)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def completed_pairs(messages):
    """Pair within one batch only; repeated IDs with conflicting evidence fail closed."""
    pairs, calls, results = [], {}, {}

    def finish():
        for call_id, occurrences in calls.items():
            outputs = results.get(call_id, [])
            if len(occurrences) == 1 and outputs and all(messages[i] == messages[outputs[0]] for i in outputs):
                index, ordinal = occurrences[0]
                pairs.append((index, ordinal, outputs[0]))
        calls.clear()
        results.clear()

    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant" or (role == "user" and not tool_image(message)):
            finish()
        if role == "assistant":
            for ordinal, call in enumerate(message.get("tool_calls") or []):
                if call.get("id"):
                    calls.setdefault(call["id"], []).append((index, ordinal))
        elif role == "tool" and message.get("tool_call_id") in calls:
            results.setdefault(message["tool_call_id"], []).append(index)
    finish()
    return pairs


async def delegate(adapter, request, user, state, agent_id):
    handoff_barrier(state)
    if state["active_agent"]:
        raise ValueError("Nested delegation is disabled")
    if agent_id not in state["registry"]:
        raise ValueError("agent_id is not in the current permitted registry; use the exact Workspace Model ID")
    await adapter.model(request, agent_id, user)
    owner = await routing_owner(adapter, request, user, state["router_id"])
    await adapter.skill(state["registry"][agent_id]["skill_id"], owner)
    pending = {"__lite_delegate__": "v2", "agent_id": agent_id, "request_id": state["request_id"], "nonce": uuid4().hex,
               "router_calls": list(state.get("audit") or [])}
    state["pending"].append(pending)
    state["phase"] = "pending-handoff"
    if len(state["pending"]) != 1:
        raise ValueError("Competing handoffs in one request are not supported")
    return json.dumps(pending, ensure_ascii=False)


def confirmed_target(state, messages):
    if state["active_agent"]:
        return state["active_agent"]
    if not state["pending"]:
        return None
    current = messages[state.get("user_boundary", last_user(messages)) + 1:]
    calls = [(i, n, call) for i, m in enumerate(current) for n, call in enumerate(m.get("tool_calls") or [])
             if call.get("function", {}).get("name") == "lite_delegate"]
    if len(state["pending"]) != 1 or len(calls) != 1:
        raise ValueError("Ambiguous or competing delegate exchange")
    expected = state["pending"][0]
    matches = []
    for index, ordinal, result_index in completed_pairs(current):
        call = current[index]["tool_calls"][ordinal]
        if call.get("function", {}).get("name") != "lite_delegate":
            continue
        arguments = object_value(call["function"].get("arguments"))
        result = object_value(current[result_index].get("content"))
        if arguments.get("agent_id") == expected["agent_id"] and result == expected:
            matches.append(expected["agent_id"])
    if len(matches) != 1:
        raise ValueError("Incomplete or contradictory delegate Tool exchange")
    return matches[0]
