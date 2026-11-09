"""Request-local evidence for context created after native preprocessing."""

from copy import deepcopy
import json
import hashlib
import xml.etree.ElementTree as ET

from .protocol import completed_pairs

NATIVE_CONTEXT_NAMES = frozenset({"recall_memories", "list_attached_knowledge", "list_available_skills"})


def context_pair_key(call, result):
    function = call.get("function") or {}
    arguments = function.get("arguments", "{}")
    try:
        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(arguments, dict):
            return None
        value = json.dumps([call.get("id"), function.get("name"), arguments,
                            result.get("content")], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        return None


def capture_native_context(request, metadata, state, converted):
    """Converted messages must come from the post-Preparation server registry."""
    identity = tuple(state.get(k) for k in ("request_id", "user_id", "chat_id", "message_id"))
    if state.get("native_context") is not None:
        if state["native_context"]["identity"] != identity:
            raise ValueError("Native context request identity changed")
        return
    records = []
    source = state["source"]
    candidates = completed_pairs(converted)
    incoming = completed_pairs(source)
    for i, n, r in candidates:
        call, result = converted[i]["tool_calls"][n], converted[r]
        name = call.get("function", {}).get("name")
        key = context_pair_key(call, result)
        matches = [(a, b, c) for a, b, c in incoming if a > state["user_boundary"]
                   and context_pair_key(source[a]["tool_calls"][b], source[c]) == key]
        if name not in NATIVE_CONTEXT_NAMES or key is None or len(matches) != 1:
            continue
        if sum(c.get("id") == call.get("id") for m in source[state["user_boundary"] + 1:]
               for c in m.get("tool_calls", [])) != 1:
            continue
        if any(record["key"] == key for record in records):
            continue
        records.append({"key": key, "name": name, "call": deepcopy(call), "result": deepcopy(result)})
    state["native_context"] = {"identity": identity, "records": records}


def native_record(state, call, result):
    key = context_pair_key(call, result)
    context = state.get("native_context") or {}
    identity = tuple(state.get(k) for k in ("request_id", "user_id", "chat_id", "message_id"))
    if context.get("identity") != identity:
        return None
    return next((record for record in context.get("records", [])
                 if record["key"] == key or key in record.get("projections", [])), None)


async def scoped_knowledge_result(record, eligible, check):
    """Filter structured native catalogs, without treating their text as authority."""
    content = record["result"].get("content")
    if not isinstance(content, str) or len(content) > 1_000_000:
        return None
    try:
        if "<!" in content:
            return None
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError):
        return None
    if root.tag != "attached_knowledge" or root.attrib:
        return None
    selected = []
    for item in root:
        key = (item.get("type"), item.get("id"), item.get("source"))
        if (item.tag != "knowledge" or list(item) or (item.text or "").strip()
                or key not in eligible or key in selected):
            continue
        if await check(key[0], key[1]):
            selected.append(key)
    if not selected:
        return None
    output = ET.Element("attached_knowledge")
    for kind, identifier, source in selected:
        # Rebuild attributes from validated identities; discard untrusted names.
        ET.SubElement(output, "knowledge", {"type": kind, "id": identifier, "source": source})
    return {**deepcopy(record["result"]), "content": ET.tostring(output, encoding="unicode")}


def project_native_results(messages, state):
    for i, n, r in completed_pairs(messages):
        record = native_record(state, messages[i]["tool_calls"][n], messages[r])
        if record and record["key"] in state.get("native_context_allowed", {}):
            messages[r] = deepcopy(state["native_context_allowed"][record["key"]])
            key = context_pair_key(messages[i]["tool_calls"][n], messages[r])
            projections = record.setdefault("projections", [])
            if key != record["key"] and key not in projections:
                projections.append(key)
    return messages
