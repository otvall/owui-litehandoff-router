"""Optional server terminal binding; never inherit a client's selection."""


def terminal_binding(model):
    meta = model.get("info", {}).get("meta") or {}
    settings = meta.get("lite_handoff", {})
    if not isinstance(settings, dict):
        raise ValueError("Model lite_handoff settings must be an object")
    identifier = settings.get("terminal_id")
    if identifier is None:
        return None
    if not isinstance(identifier, str):
        raise ValueError("Model lite_handoff.terminal_id must be a string or null")
    identifier = identifier.strip()
    if not identifier:
        return None
    if (meta.get("capabilities") or {}).get("terminal") is False:
        raise ValueError("Model terminal binding conflicts with disabled Terminal capability")
    return identifier


def is_terminal_tool(tool):
    return (tool.get("type") == "terminal" or str(tool.get("tool_id", "")).startswith("terminal:")
            or (tool.get("server") or {}).get("is_terminal") is True)


def prepare_terminal_scope(metadata, identifier):
    for key in ("terminal_id", "terminal_skills"):
        metadata.pop(key, None)
    if isinstance(metadata.get("tool_servers"), list):
        metadata["tool_servers"] = [s for s in metadata["tool_servers"]
                                    if not isinstance(s, dict) or s.get("is_terminal") is not True]
    context = dict(metadata.get("chat_context") or {})
    for key in ("terminal_id", "terminal_skills"):
        context.pop(key, None)
    if "chat_context" in metadata:
        metadata["chat_context"] = context
    if identifier:
        metadata["terminal_id"] = identifier
