"""Prepare one model's payload and executable capabilities before publication."""

from copy import deepcopy

from .registry import ids
from .skills import READERS, manifest, prepare_skill_scope, scoped_reader
from .capabilities import load_capabilities
from .binding import bind_tool_context
from .history import router_projection, agent_projection
from .protocol import delegate_tool
from .parallel import parallel_tool, ROUTING_TOOLS, downstream_data
from .resources import ResourceScope
from .terminals import terminal_binding, prepare_terminal_scope, is_terminal_tool
from .profiles import ROUTER_PROFILE

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
    owner = await adapter.owner(model)
    selected = deepcopy(model)
    selected["owned_by"] = inference.get("owned_by")
    meta = model.get("info", {}).get("meta") or {}
    tool_ids = ids(meta.get("toolIds") or []) if target else state["base_tool_ids"]
    skill_ids = ids(meta.get("skillIds") or []) if target else ids(
        state["base_skill_ids"] + [entry["skill_id"] for entry in state["registry"].values()]
    )
    terminal_id = terminal_binding(model) if target else None
    prepare_terminal_scope(metadata, terminal_id)
    resources = ResourceScope(adapter, request, user, model, owner, tool_ids, skill_ids,
                              router=not target, terminal_id=terminal_id)
    prepare_skill_scope(metadata, state, model["id"], skill_ids)
    draft = {key: deepcopy(value) for key, value in body.items() if key in ENVELOPE}
    draft.update(model=llm_id, messages=[
        deepcopy(m) for m in state["source"] if m.get("role") != "system"
    ], metadata=metadata)
    draft = await adapter.prompt_and_params(draft, selected, user, metadata)
    packet = await downstream_data(adapter, user, metadata, state) if target and ROUTER_PROFILE.parallel else None
    if packet:
        draft["files"] = deepcopy(metadata["files"])
    prompt = await manifest(adapter, skill_ids, owner, None if target else state["registry"])
    if prompt:
        draft["messages"].insert(0, {"role": "system", "content": prompt})
    extra = injections(request, user, metadata, model, draft["messages"], events)
    tools, mcp_instructions = await load_capabilities(adapter, request, tool_ids, owner, extra)
    terminal_names = set()
    if any(is_terminal_tool(tool) for tool in tools.values()):
        raise ValueError("Terminal Tools must come from the model terminal binding")
    if terminal_id:
        terminal_tools, terminal_prompt = await adapter.terminal_tools(request, terminal_id, owner, extra)
        collisions = set(terminal_tools).intersection(set(tools) | READERS | ROUTING_TOOLS)
        if collisions:
            raise ValueError("Conflicting terminal Tool name: " + ", ".join(sorted(collisions)))
        terminal_names = set(terminal_tools)
        tools.update(terminal_tools)
        if terminal_prompt:
            draft["messages"].insert(0, {"role": "system", "content": terminal_prompt})
    if READERS.intersection(tools):
        raise ValueError("Reserved Skill reader collision: " + ", ".join(sorted(READERS.intersection(tools))))
    if "lite_parallel" in tools or "delegate_task" in tools:
        raise ValueError("Reserved routing Tool collision: " + ", ".join(sorted(ROUTING_TOOLS.intersection(tools))))
    for instruction in mcp_instructions:
        draft["messages"].insert(0, {"role": "system", "content": instruction})
    if state["registry"] and not target:
        if "lite_delegate" in tools:
            raise ValueError("Reserved delegate Tool collision: lite_delegate; remove the separate delegate Tool from base_tool_ids")
        tools["lite_delegate"] = delegate_tool(adapter, request, user, metadata, state)
        group = state.get("parallel") or {}
        if ROUTER_PROFILE.parallel and group.get("max_tasks"):
            reason = (await adapter.parallel_reason(request, metadata, user) if metadata.get("chat_id")
                      else "Parallel tasks require a saved chat")
            group["unavailable_reason"] = reason
            if reason:
                draft["messages"].insert(0, {"role": "system", "content": reason})
            else:
                tools["lite_parallel"] = parallel_tool(adapter, request, user, metadata, state, events)
    else:
        for name in ROUTING_TOOLS:
            tools.pop(name, None)
    if skill_ids:
        builtins = await adapter.builtins(request, model, {
            **extra, "__user__": owner.model_dump(), "__skill_ids__": skill_ids})
        for name in ROUTER_PROFILE.skill_readers:
            if name not in builtins:
                raise ValueError("Required builtin Skill reader unavailable: " + name)
            if name in tools:
                raise ValueError("Reserved Skill reader collision: " + name)
            tools[name] = await scoped_reader(adapter, builtins[name], skill_ids, owner,
                                               draft["messages"], check_scope=resources.skill)
    if target:
        history = agent_projection(state, target, set(tools), skill_ids)
    else:
        history = router_projection(state, set(tools), skill_ids)
    systems = [m for m in draft["messages"] if m.get("role") == "system"]
    if packet:
        # Preserve the actual current user message as the last question.
        index = next((i for i in range(len(history) - 1, -1, -1) if history[i].get("role") == "user"), len(history))
        history.insert(index, packet)
    draft["messages"][:] = systems + history
    draft["tools"] = [{"type": "function", "function": tool["spec"]} for tool in tools.values()]
    protected = {name: (tool["callable"], deepcopy(tool["spec"]), tool.get("tool_id"), tool.get("type"))
                 for name, tool in tools.items() if name in READERS | ROUTING_TOOLS | terminal_names}
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
    if (final_metadata.get("terminal_id") != terminal_id or draft.get("terminal_id") is not None
            or final_metadata.get("terminal_skills")
            or any(isinstance(s, dict) and s.get("is_terminal") is True
                   for s in final_metadata.get("tool_servers") or [])):
        raise ValueError("Destination inlet changed protected terminal context")
    schemas = [schema.get("function") for schema in (draft.get("tools") or []) if isinstance(schema, dict)]
    terminal_found = {name for name, tool in tools.items() if isinstance(tool, dict) and is_terminal_tool(tool)}
    for name in READERS | ROUTING_TOOLS | terminal_names | terminal_found:
        expected = protected.get(name)
        found = tools.get(name)
        public = [schema for schema in schemas if isinstance(schema, dict) and schema.get("name") == name]
        if expected is None:
            if found is not None or public:
                raise ValueError("Destination inlet added a reserved Tool: " + name)
        elif (not isinstance(found, dict) or found.get("callable") is not expected[0]
              or found.get("spec") != expected[1] or public != [expected[1]]
              or (name in terminal_names and (found.get("tool_id"), found.get("type")) != expected[2:])):
            raise ValueError("Destination inlet replaced a protected Tool: " + name)
    for name, tool in list(tools.items()):
        identifier = tool.get("tool_id")
        check = None
        if name in terminal_names:
            async def check(arguments, terminal_id=terminal_id):
                await resources.terminal(terminal_id)
        elif identifier and name not in READERS | ROUTING_TOOLS:
            async def check(arguments, identifier=identifier):
                await resources.tool(identifier)
        tools[name] = await bind_tool_context(adapter, tool, draft["messages"], check=check)
    draft["tools"] = [{"type": "function", "function": tool["spec"]} for tool in tools.values()]
    return draft, tools
