"""Trusted lifecycle state shared by independent uploads through the ASGI scope.

Only a serializable receipt is published in metadata. A client-replayed receipt
cannot establish the server binding in a different HTTP request.
"""

from copy import deepcopy
from uuid import uuid4

from .parallel_state import group_state

SCOPE_KEY = "lite_handoff_router"
RECEIPT = "lite_router"


def begin(request, body, metadata, user_id, registry, diagnostics, tool_ids, skill_ids,
          *, max_parallel_tasks=4, parallel_timeout_seconds=300):
    state = {
        "request_id": uuid4().hex, "user_id": user_id,
        "chat_id": metadata.get("chat_id"), "message_id": metadata.get("message_id"),
        "router_id": body["model"], "registry": deepcopy(registry),
        "diagnostics": diagnostics, "base_tool_ids": list(tool_ids),
        "base_skill_ids": list(skill_ids), "source": deepcopy(body.get("messages", [])),
        "phase": "router", "pending": [], "active_agent": None,
        "skill_scope": None,
        "original_source": deepcopy(body.get("messages", [])),
        "user_boundary": next((i for i in range(len(body.get("messages", [])) - 1, -1, -1)
                               if body["messages"][i].get("role") == "user"), -1),
        "generation": 0, "audit": [],
        "parallel": group_state(max_parallel_tasks, parallel_timeout_seconds),
    }
    request.scope[SCOPE_KEY] = state
    request.scope["lite_handoff_metadata"] = metadata
    # chat.py/functions.py shallow-copy metadata. Establish the shared Tool
    # registry before that happens, so the outer native loop observes updates.
    metadata["tools"] = {}
    for field in ("tool_ids", "skill_ids", "base_tool_ids", "base_skill_ids"):
        metadata.pop(field, None)
        body.pop(field, None)
    metadata.pop("lite_skill_scope", None)
    metadata[RECEIPT] = {k: deepcopy(state[k]) for k in (
        "request_id", "user_id", "chat_id", "message_id", "router_id", "registry",
        "diagnostics", "base_tool_ids", "base_skill_ids",
    )}
    metadata.setdefault("params", {})["function_calling"] = "native"
    body.setdefault("params", {})["function_calling"] = "native"
    body["metadata"] = metadata
    return state


def require(request, metadata, user_id):
    state = request.scope.get(SCOPE_KEY)
    receipt = metadata.get(RECEIPT) or {}
    if not isinstance(state, dict) or any((
        state.get("user_id") != user_id,
        receipt.get("request_id") != state.get("request_id"),
        metadata.get("chat_id") != state.get("chat_id"),
        metadata.get("message_id") != state.get("message_id"),
    )):
        raise ValueError("Missing or stale Router Preparation context; attach the mandatory Filter to Router")
    return state


def metadata_draft(metadata):
    # Native Tool entries contain bound callables/clients; deepcopy only data.
    draft = deepcopy({key: value for key, value in metadata.items() if key not in ("tools", "mcp_clients")})
    draft["tools"] = {name: {**tool, "spec": deepcopy(tool["spec"])}
                      for name, tool in (metadata.get("tools") or {}).items()}
    return draft


def publish(request, current, draft, tools):
    root = request.scope["lite_handoff_metadata"]
    live = root.setdefault("tools", {})
    live.clear()
    live.update(tools)
    draft["tools"] = live
    draft.setdefault("params", {})["function_calling"] = "native"
    for metadata in (root, current):
        if metadata is not draft:
            metadata.clear()
            metadata.update(draft)
    request.state.metadata = draft
    request.scope[SCOPE_KEY]["skill_scope"] = draft.get("lite_skill_scope")
