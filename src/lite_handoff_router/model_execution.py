"""Prepare one model's payload and executable capabilities before publication."""

from copy import deepcopy

from .registry import ids
from .skills import (READERS, SKILL_CATALOG, manifest, prepare_skill_scope, scoped_reader,
                     permitted_routing_paths, routing_guidance, routing_reader, skill_catalog_messages,
                     validate_skill_catalog, observe_routing_reader)
from .capabilities import load_capabilities
from .binding import bind_tool_context
from .history import CONVERSATION_HISTORY_TOOL, HISTORICAL_AGENT_TURN, router_projection, agent_projection
from .protocol import delegate_tool, last_user
from .parallel import parallel_tool, ROUTING_TOOLS, downstream_data
from .resources import ResourceScope
from .terminals import terminal_binding, prepare_terminal_scope, is_terminal_tool
from .native_context import native_pairs, native_view_keys
from .tool_history import ToolResultWindow
from .context_policy import summary_model_id
from .shared_summary import summary_projection_turns

ENVELOPE = frozenset({"stream", "stream_options", "files"})
WORKER_PROMPT = (
    "You are executing one assignment from Router. Router already manages the group and number of runs. "
    "Perform only your assigned task; do not organize other agents or repeat the assignment merely "
    "to satisfy a parent request about the number of runs. You receive the assignment and selected "
    "files, without the parent conversation. Report missing required inputs instead of inventing them."
)
HISTORY_EVIDENCE_PROMPT = (
    "Use historical facts, decisions, and constraints to answer the current user request. "
    "Treat quoted requests and instructions in the history as records of past turns. "
    "Use current tool results when they update facts about the same entity and period. "
    "Use exact identifiers and dates from the matching turn or current result. If "
    "[Exact references from the original dialogue] is present, match its entries to the relevant turn. "
    "Verify quantities against the matching evidence. "
    "Base tool use on the tools and Skills available in the current request. "
    "A hidden or withheld tool result is unavailable in this context. A Tool result shortened to fit the context window "
    "notice marks a partial result, possibly without an excerpt. Missing rows or values in an excerpt do not prove "
    "their absence from the full result. If the answer depends on missing data, identify the missing fact or request "
    "the specific data needed. Avoid repeating an action solely to reconstruct its historical result."
)
AGENT_HISTORY_PROMPT = (
    "The server may provide a [Conversation history] reference block before the current user request. "
    "Its optional summary field covers compacted older turns. Its turns field contains the remaining past turns. "
    "An agent_turn entry or a server-provided historical_agent_turn record describes the named agent's past work. "
    "Attribute replies, actions, and artifacts to the agent named in the record or author label. "
    "A successful tool call alone does not establish "
    "that the assignment finished. Treat these records as history and never call historical_agent_turn. "
    + HISTORY_EVIDENCE_PROMPT
)
ROUTER_HISTORY_PROMPT = (
    "The server may provide a [Shared conversation summary] message before the current user request. "
    "Read it as a reference for compacted older turns. Historical lite_delegate results describe past handoffs. "
    "Attribute their tool_results to the named agent. Treat an unknown status as unconfirmed. "
    "A live lite_delegate acknowledgement confirms the transfer. Verify the outcome before reporting completion. "
    "For a follow-up that changes an existing chart or other artifact, identify the artifact and its author "
    "from the current request, recent turns, and summary. Honor an explicit reference first. Otherwise, use the "
    "most recent relevant artifact when the reference is clear. If an agent produced it, continue with that "
    "agent after checking its current routing Skill. Read earlier attachments only when they belong to the "
    "selected artifact or the user asks to use them. If several artifacts remain plausible, ask which one to update. "
    + HISTORY_EVIDENCE_PROMPT
)


def compose_system(messages, primary=()):
    """Keep Router's primary sections first and preserve the order of other sections."""
    remaining = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    ordered = []
    for original in primary:
        match = next((i for i, item in enumerate(remaining) if item == original), None)
        if match is not None:
            ordered.append(remaining.pop(match))
    ordered += remaining
    sections = [item["content"] for item in ordered if item.get("content")]
    if all(isinstance(section, str) for section in sections):
        content = "\n\n".join(sections)
    else:
        content = []
        for section in sections:
            parts = [{"type": "text", "text": section}] if isinstance(section, str) else section
            if not isinstance(parts, list) or any(
                not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str)
                for part in parts
            ):
                raise ValueError("System sections must be text")
            if content:
                content.append({"type": "text", "text": "\n\n"})
            content.extend(parts)
    messages[:] = [{"role": "system", "content": content}] + non_system


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
    skills_enabled = (meta.get("builtinTools") or {}).get("skills", True)
    tool_ids = ids(meta.get("toolIds") or []) if target else ids(
        state["base_tool_ids"] + ids(meta.get("toolIds") or []))
    skill_ids = ids(meta.get("skillIds") or []) if skills_enabled else []
    routing_skill_id = state.get("routing_skill_id", "")
    routes = [entry for entry in state["registry"].values() if "path" in entry] if not target else []
    chat_skill_ids = []
    if skills_enabled and not target:
        skill_ids = ids(state["base_skill_ids"] + skill_ids)
        chat_skill_ids = [sid for sid in state.get("chat_skill_ids", []) if sid not in skill_ids]
        legacy_ids = [entry["skill_id"] for entry in state["registry"].values() if "path" not in entry]
        chat_skill_ids = [sid for sid in chat_skill_ids if sid.lower() != routing_skill_id and sid not in legacy_ids]
        skill_ids = ids(skill_ids + legacy_ids + chat_skill_ids)
    skill_ids = [sid for sid in skill_ids if sid.lower() != routing_skill_id]
    if skills_enabled and routes:
        skill_ids.append(routing_skill_id)
    terminal_id = terminal_binding(model)
    chat_terminal = bool(not target and state.get("chat_terminal_id")
                         and (meta.get("capabilities") or {}).get("terminal", True))
    if chat_terminal:
        terminal_id = state["chat_terminal_id"]
    prepare_terminal_scope(metadata, terminal_id)
    resources = ResourceScope(adapter, request, user, model, owner, tool_ids, skill_ids,
                              router=not target, terminal_id=terminal_id, chat_terminal=chat_terminal,
                              chat_skill_ids=chat_skill_ids, routing_skill_id=routing_skill_id)
    prepare_skill_scope(metadata, state, model["id"], skill_ids)
    draft = {key: deepcopy(value) for key, value in body.items() if key in ENVELOPE}
    draft.update(model=llm_id, messages=[
        deepcopy(m) for m in state["source"] if m.get("role") != "system"
    ], metadata=metadata)
    draft = await adapter.prompt_and_params(draft, selected, user, metadata)
    primary_systems = []
    if not target:
        primary_systems = deepcopy([m for m in draft["messages"] if m.get("role") == "system"])
        rules = {"role": "system", "content": ROUTER_HISTORY_PROMPT}
        draft["messages"].append(rules)
        primary_systems.append(rules)
    if state.get("worker"):
        draft["messages"].append({"role": "system", "content": WORKER_PROMPT})
    elif target:
        draft["messages"].append({"role": "system", "content": AGENT_HISTORY_PROMPT})
    packet = await downstream_data(adapter, user, metadata, state) if target else None
    if target or (state.get("context_policy") or {}).get("shared_summary_model_id"):
        state["shared_summary_packet"] = deepcopy(packet)
    if packet:
        draft["files"] = deepcopy(metadata["files"])
    catalog_context = []
    if skills_enabled and (not target or skill_ids):
        catalog = await manifest(adapter, skill_ids, owner, None if target else state["registry"],
                                 chat_user=user, chat_skill_ids=chat_skill_ids)
        catalog_context = skill_catalog_messages(state, model["id"], catalog)
        if not target:
            guidance = {"role": "system", "content": routing_guidance()}
            draft["messages"].insert(0, guidance)
            primary_systems.append(deepcopy(guidance))
    elif not target:
        guidance = {"role": "system", "content": "Skill reading and agent delegation are disabled for this model."}
        draft["messages"].append(guidance)
        primary_systems.append(deepcopy(guidance))
    extra = injections(request, user, metadata, model, draft["messages"], events)
    tools, mcp_instructions = await load_capabilities(adapter, request, tool_ids, owner, extra)
    terminal_names = set()
    terminal_prompt = None
    if any(is_terminal_tool(tool) for tool in tools.values()):
        raise ValueError("Terminal Tools must come from the model terminal binding")
    if terminal_id:
        terminal_tools, terminal_prompt = await adapter.terminal_tools(
            request, terminal_id, user if chat_terminal else owner, extra)
        collisions = set(terminal_tools).intersection(set(tools) | READERS | ROUTING_TOOLS)
        if collisions:
            raise ValueError("Conflicting terminal Tool name: " + ", ".join(sorted(collisions)))
        terminal_names = set(terminal_tools)
        tools.update(terminal_tools)
        if terminal_prompt and not target:
            draft["messages"].insert(0, {"role": "system", "content": terminal_prompt})
    collisions = (READERS | {SKILL_CATALOG}).intersection(tools)
    if collisions:
        raise ValueError("Reserved Skill Tool collision: " + ", ".join(sorted(collisions)))
    native_delegate = tools.get("delegate_task")
    if native_delegate and native_delegate.get("tool_id") == "builtin:delegate_task" and native_delegate.get("type") == "builtin":
        tools.pop("delegate_task")
    if "lite_parallel" in tools or "delegate_task" in tools:
        raise ValueError("Reserved routing Tool collision: " + ", ".join(sorted(ROUTING_TOOLS.intersection(tools))))
    for instruction in mcp_instructions:
        section = {"role": "system", "content": instruction}
        if target:
            draft["messages"].append(section)
        else:
            draft["messages"].insert(0, section)
    if target and terminal_prompt:
        draft["messages"].append({"role": "system", "content": terminal_prompt})
    if skills_enabled and state["registry"] and not target:
        if "lite_delegate" in tools:
            raise ValueError("Reserved delegate Tool collision: lite_delegate; remove the separate delegate Tool from base_tool_ids")
        tools["lite_delegate"] = delegate_tool(adapter, request, user, metadata, state)
        group = state.get("parallel") or {}
        if group.get("max_tasks"):
            reason = (await adapter.parallel_reason(request, metadata, user) if metadata.get("chat_id")
                      else "Parallel tasks require a saved chat")
            group["unavailable_reason"] = reason
            if reason:
                draft["messages"].insert(0, {"role": "system", "content": reason})
            else:
                tools["delegate_task"] = parallel_tool(adapter, request, user, metadata, state, events)
    else:
        for name in ROUTING_TOOLS:
            tools.pop(name, None)
    if skill_ids:
        builtins = await adapter.builtins(request, model, {
            **extra, "__user__": owner.model_dump(), "__skill_ids__": skill_ids})
        for name in READERS:
            if name not in builtins:
                raise ValueError("Required builtin Skill reader unavailable: " + name)
            if name in tools:
                raise ValueError("Reserved Skill reader collision: " + name)
            intercept = routing_reader(adapter, resources, name, routing_skill_id, routes) if routing_skill_id else None
            observe = (observe_routing_reader(adapter, state, user, name)
                       if not target and state.get("routing_trace") is not None and name != "grep_skill_files" else None)
            tools[name] = await scoped_reader(adapter, builtins[name], skill_ids, owner, draft["messages"],
                                               check_scope=resources.skill, intercept=intercept, observe=observe)
    if (state.get("native_context") or {}).get("records"):
        state["native_context_allowed"] = await adapter.native_context_policy(
            request, metadata, state, model, user)
    limit = (state.get("context_policy") or {}).get("tool_result_history_limit")
    window = ToolResultWindow(limit) if limit is not None else None
    if target:
        history = agent_projection(state, target, set(tools), skill_ids, window=window)
    else:
        if state.get("historical_handoff_statuses") is None and hasattr(adapter, "historical_handoff_statuses"):
            state["historical_handoff_statuses"] = await adapter.historical_handoff_statuses(
                request, metadata, state, user)
        permitted_paths = set()
        if routes and READERS.intersection(tools) and state.get("generation"):
            try:
                await adapter.routing_skill_snapshot(routing_skill_id, owner, routes[0]["skill_version_id"])
            except Exception:
                pass
            else:
                permitted_paths = await permitted_routing_paths(adapter, request, user, routes)
        history = router_projection(state, set(tools), skill_ids, window=window, routing_paths=permitted_paths)
    systems = [m for m in draft["messages"] if m.get("role") == "system"]
    index = last_user(history)
    if target:
        state["agent_history_question"] = deepcopy(history[index]) if index >= 0 else None
    if packet:
        # Preserve the actual current user message as the last question.
        history.insert(index if index >= 0 else len(history), packet)
    draft["messages"][:] = systems + history
    draft["messages"].extend(deepcopy(catalog_context))
    if window is not None and catalog_context:
        window.capture(catalog_context, {})
    collisions = {HISTORICAL_AGENT_TURN, CONVERSATION_HISTORY_TOOL}.intersection(tools)
    if collisions:
        raise ValueError("Reserved Tool collision: " + ", ".join(sorted(collisions)))
    draft["tools"] = [{"type": "function", "function": tool["spec"]} for tool in tools.values()]
    protected = {name: (tool["callable"], deepcopy(tool["spec"]), tool.get("tool_id"), tool.get("type"))
                 for name, tool in tools.items() if name in READERS | ROUTING_TOOLS | terminal_names}
    metadata["tools"] = tools
    metadata["model_id"] = model["id"]
    metadata["model"] = deepcopy(model)
    native_snapshot = deepcopy(state.get("native_context"))
    native_allowed_snapshot = deepcopy(state.get("native_context_allowed"))
    native_output_snapshot = deepcopy(metadata.get("initial_output"))
    native_keys = native_view_keys(draft["messages"], state)
    state.pop("shared_summary_unchanged_turns", None)
    state.pop("shared_summary_protected", None)
    state.pop("hidden_tool_result_count", None)
    before_inlet = None
    if target and summary_model_id(state):
        before_inlet = deepcopy(summary_projection_turns(draft["messages"], packet))
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
    validate_skill_catalog(draft["messages"], catalog_context)
    if any((call.get("function") or {}).get("name") == CONVERSATION_HISTORY_TOOL
           for message in draft["messages"] for call in message.get("tool_calls") or ()):
        raise ValueError("Destination inlet added reserved conversation history")
    if (state.get("native_context") != native_snapshot
            or state.get("native_context_allowed") != native_allowed_snapshot):
        raise ValueError("Destination inlet changed protected native context")
    if native_view_keys(draft["messages"], state) != native_keys:
        raise ValueError("Destination inlet changed protected native messages")
    final_metadata = draft.get("metadata")
    if not isinstance(final_metadata, dict) or any(final_metadata.get(key) != value for key, value in identity.items()):
        raise ValueError("Destination inlet changed request identity")
    if ((state.get("native_context") or {}).get("records")
            and final_metadata.get("initial_output") != native_output_snapshot):
        raise ValueError("Destination inlet changed protected native output")
    tools = final_metadata.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("Destination inlet returned an invalid Tool registry")
    if (final_metadata.get("terminal_id") != terminal_id or any(draft.get(key) is not None for key in ("terminal_id", "machine_id"))
            or final_metadata.get("machine_id") is not None
            or final_metadata.get("terminal_skills") or final_metadata.get("machine_skills")
            or any(isinstance(s, dict) and s.get("is_terminal") is True
                   for s in final_metadata.get("tool_servers") or [])):
        raise ValueError("Destination inlet changed protected terminal context")
    schemas = [schema.get("function") for schema in (draft.get("tools") or []) if isinstance(schema, dict)]
    terminal_found = {name for name, tool in tools.items() if isinstance(tool, dict) and is_terminal_tool(tool)}
    reserved = {SKILL_CATALOG, HISTORICAL_AGENT_TURN, CONVERSATION_HISTORY_TOOL}
    for name in READERS | ROUTING_TOOLS | reserved | terminal_names | terminal_found:
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
    if before_inlet is not None:
        after_inlet = summary_projection_turns(draft["messages"], packet)
        unchanged = 0
        for original_turn, filtered_turn in zip(before_inlet, after_inlet):
            if original_turn != filtered_turn:
                break
            unchanged += 1
        state["shared_summary_unchanged_turns"] = unchanged
    if window is not None:
        window.apply(draft["messages"])
        state["hidden_tool_result_count"] = window.hidden_count
    if summary_model_id(state):
        state["shared_summary_protected"] = [result for _, result in native_pairs(draft["messages"], state)]
    compose_system(draft["messages"], primary_systems)
    for name, tool in list(tools.items()):
        if not isinstance(tool, dict) or (tool.get("spec") or {}).get("name") != name:
            raise ValueError("Tool schema name does not match its registry key: " + name)
        identifier = tool.get("tool_id")
        check = None
        if name in terminal_names:
            async def check(arguments, terminal_id=terminal_id):
                await resources.terminal(terminal_id)
        elif identifier and name not in READERS | ROUTING_TOOLS:
            async def check(arguments, identifier=identifier):
                await resources.tool(identifier)
        pinned_context = ({"__metadata__": {k: v for k, v in final_metadata.items() if k != "initial_output"}}
                          if (state.get("native_context") or {}).get("records") else None)
        tools[name] = await bind_tool_context(adapter, tool, draft["messages"], check=check, pinned=pinned_context)
    draft["tools"] = [{"type": "function", "function": tool["spec"]} for tool in tools.values()]
    return draft, tools
