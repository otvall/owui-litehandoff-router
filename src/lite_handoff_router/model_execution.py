"""Prepare one model's payload and executable capabilities before publication."""

from copy import deepcopy

from .registry import ids
from .skills import READERS, manifest, prepare_skill_scope, scoped_reader
from .capabilities import load_capabilities
from .binding import bind_tool_context
from .history import router_projection, agent_projection

ENVELOPE = frozenset({"stream", "stream_options", "files"})


def injections(request, user, metadata, model, messages, events):
    return {
        "__request__": request, "__user__": user.model_dump(),
        "__metadata__": metadata, "__model__": model, "__messages__": messages,
        "__files__": metadata.get("files", []), "__features__": {},
        "__chat_id__": metadata.get("chat_id"), "__message_id__": metadata.get("message_id"),
        "__session_id__": metadata.get("session_id"), **events,
    }


async def prepare_model(adapter, request, body, user, metadata, state, llm_id, events, target=None):
    identity = deepcopy({key: metadata.get(key) for key in ("chat_id", "message_id", "lite_router")})
    if target is None:
        if not llm_id or llm_id == state["router_id"]:
            raise ValueError("Configure orchestrator_model_id with a real Router LLM")
        inference = request.app.state.MODELS.get(llm_id)
        if not inference or inference.get("pipe"):
            raise ValueError("Router LLM must exist and must not resolve to a Pipe")
    else:
        llm_id = target
        inference = request.app.state.MODELS.get(target)
    model = await adapter.model(request, target or state["router_id"], user)
    selected = deepcopy(model)
    selected["owned_by"] = inference.get("owned_by")
    meta = model.get("info", {}).get("meta") or {}
    tool_ids = ids(meta.get("toolIds") or []) if target else state["base_tool_ids"]
    skill_ids = ids(meta.get("skillIds") or []) if target else ids(
        state["base_skill_ids"] + [entry["skill_id"] for entry in state["registry"].values()]
    )
    prepare_skill_scope(metadata, state, model["id"], skill_ids)
    draft = {key: deepcopy(value) for key, value in body.items() if key in ENVELOPE}
    draft.update(model=llm_id, messages=[
        deepcopy(m) for m in state["source"] if m.get("role") != "system"
    ], metadata=metadata)
    draft = await adapter.prompt_and_params(draft, selected, user, metadata)
    prompt = await manifest(adapter, skill_ids, user, None if target else state["registry"])
    if prompt:
        draft["messages"].insert(0, {"role": "system", "content": prompt})
    extra = injections(request, user, metadata, model, draft["messages"], events)
    tools, mcp_instructions = await load_capabilities(adapter, request, tool_ids, user, extra)
    if READERS.intersection(tools):
        raise ValueError("Reserved Skill reader collision: " + ", ".join(sorted(READERS.intersection(tools))))
    for instruction in mcp_instructions:
        draft["messages"].insert(0, {"role": "system", "content": instruction})
    if state["registry"] and not target:
        if "lite_delegate" not in tools:
            raise ValueError("base_tool_ids must contain a Tool providing lite_delegate")
        tools["lite_delegate"]["spec"]["parameters"]["properties"]["agent_id"]["enum"] = list(state["registry"])
    else:
        tools.pop("lite_delegate", None)
    if skill_ids:
        builtins = await adapter.builtins(request, model, {**extra, "__skill_ids__": skill_ids})
        for name in READERS:
            if name not in builtins:
                raise ValueError("Required builtin Skill reader unavailable: " + name)
            if name in tools:
                raise ValueError("Reserved Skill reader collision: " + name)
            tools[name] = await scoped_reader(adapter, builtins[name], skill_ids, user, draft["messages"])
    if target:
        history = agent_projection(state, target, set(tools), skill_ids)
    else:
        history = router_projection(state, set(tools), skill_ids)
    systems = [m for m in draft["messages"] if m.get("role") == "system"]
    draft["messages"][:] = systems + history
    draft["tools"] = [{"type": "function", "function": tool["spec"]} for tool in tools.values()]
    protected = {name: (tool["callable"], deepcopy(tool["spec"]))
                 for name, tool in tools.items() if name in READERS or name == "lite_delegate"}
    metadata["tools"] = tools
    metadata["model_id"] = model["id"]
    metadata["model"] = deepcopy(model)
    if target:
        previous_metadata = getattr(request.state, "metadata", None)
        request.state.metadata = metadata
        request.scope["lite_handoff_preparing"] = True
        try:
            draft = await adapter.inlets(request, draft, model, extra)
        finally:
            request.scope.pop("lite_handoff_preparing", None)
            request.state.metadata = previous_metadata
    if not isinstance(draft, dict) or not isinstance(draft.get("messages"), list):
        raise ValueError("Destination inlet returned an invalid request")
    final_metadata = draft.get("metadata")
    if not isinstance(final_metadata, dict) or any(final_metadata.get(key) != value for key, value in identity.items()):
        raise ValueError("Destination inlet changed request identity")
    tools = final_metadata.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("Destination inlet returned an invalid Tool registry")
    schemas = [schema.get("function") for schema in (draft.get("tools") or []) if isinstance(schema, dict)]
    for name in READERS | {"lite_delegate"}:
        expected = protected.get(name)
        found = tools.get(name)
        public = [schema for schema in schemas if isinstance(schema, dict) and schema.get("name") == name]
        if expected is None:
            if found is not None or public:
                raise ValueError("Destination inlet added a reserved Tool: " + name)
        elif (not isinstance(found, dict) or found.get("callable") is not expected[0]
              or found.get("spec") != expected[1] or public != [expected[1]]):
            raise ValueError("Destination inlet replaced a protected Tool: " + name)
    for name, tool in list(tools.items()):
        tools[name] = await bind_tool_context(adapter, tool, draft["messages"])
    draft["tools"] = [{"type": "function", "function": tool["spec"]} for tool in tools.values()]
    return draft, tools
