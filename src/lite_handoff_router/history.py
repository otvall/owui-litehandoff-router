"""Canonical branch facts; model-specific projections never mutate the source."""

from copy import deepcopy
from dataclasses import dataclass
import json
import re

from .protocol import actual_user, completed_pairs, object_value, tool_image, last_user
from .skills import READERS, SKILL_CATALOG
from .native_context import NATIVE_CONTEXT_NAMES, native_record, project_native_results
from .tool_history import result_status

HISTORICAL_AGENT_TURN = "historical_agent_turn"
CONVERSATION_HISTORY_TOOL = "load_conversation_history"


def fingerprint(call):
    function = call.get("function") or {}
    arguments = function.get("arguments", "{}")
    try:
        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (TypeError, ValueError):
        return None
    if not isinstance(arguments, dict):
        return None
    return json.dumps([call.get("id"), function.get("name"), arguments], sort_keys=True, ensure_ascii=False)


def audit_fingerprint(record):
    return fingerprint({"id": record.get("id"), "function": {"name": record.get("name"), "arguments": record.get("arguments")}})


def handoff_evidence(call, result):
    marker = object_value(result.get("content"))
    arguments = object_value((call.get("function") or {}).get("arguments"))
    if ((call.get("function") or {}).get("name") == "lite_delegate" and marker.get("__lite_delegate__") == "v2"
            and isinstance(marker.get("agent_id"), str) and marker["agent_id"]
            and isinstance(marker.get("request_id"), str) and marker["request_id"]
            and isinstance(marker.get("nonce"), str) and marker["nonce"]
            and arguments.get("agent_id") == marker["agent_id"]):
        return marker
    return None


@dataclass(frozen=True)
class Exchange:
    turn: int
    batch: int
    call_index: int
    result_index: int
    executor: dict
    generation: int | None


def analyze(messages, *, current_boundary=None, audit=()):
    """IDs are scoped by request and assistant batch, never matched globally.

    Fresh provenance comes from observed provider calls. Past handoff results
    retain Router generation evidence; destination ownership does not depend on
    today's registry or Tool names. Incomplete/conflicting evidence stays unknown.
    """
    turns, turn = [], -1
    for i, message in enumerate(messages):
        if (i == current_boundary and message.get("role") == "user") or actual_user(message):
            turn += 1
        turns.append(turn)
    pairs = completed_pairs(messages)
    paired = {(i, n) for i, n, _ in pairs}
    incomplete_handoffs = {
        turns[i] for i, message in enumerate(messages) for n, call in enumerate(message.get("tool_calls") or [])
        if (i, n) not in paired and call.get("function", {}).get("name") == "lite_delegate"
    }
    by_turn = {}
    for pair in pairs:
        by_turn.setdefault(turns[pair[0]], []).append(pair)
    exchanges = []
    for turn, group in by_turn.items():
        evidence = []
        for i, n, r in group:
            marker = handoff_evidence(messages[i]["tool_calls"][n], messages[r])
            if marker:
                evidence.append((r, marker))
        marker = evidence[0][1] if len(evidence) == 1 else None
        transition = evidence[0][0] if marker else None
        ambiguous_handoff = turn in incomplete_handoffs or len(evidence) > 1
        records = list(marker.get("router_calls") or []) if marker else []
        if current_boundary is not None and any(i > current_boundary for i, _, _ in group):
            records += list(audit)
        by_fingerprint = {}
        for record in records:
            if isinstance(record, dict) and (key := audit_fingerprint(record)) is not None:
                by_fingerprint.setdefault(key, []).append(record)
        for i, n, r in group:
            call = messages[i]["tool_calls"][n]
            matching = by_fingerprint.get(fingerprint(call), ())
            # A persisted Router record and live copy describe the same observation.
            unique = {json.dumps(record, sort_keys=True): record for record in matching}
            owner, generation = {"kind": "unknown"}, None
            if len(unique) == 1:
                record = next(iter(unique.values()))
                owner, generation = deepcopy(record.get("executor") or owner), record.get("generation")
            elif not matching and marker and not ambiguous_handoff:
                owner = {"kind": "router"} if r <= transition else {"kind": "agent", "agent_id": marker["agent_id"]}
            exchanges.append(Exchange(turn, i, n, r, owner, generation))
    return sorted(exchanges, key=lambda exchange: exchange.result_index)


def refresh_source(state, incoming):
    """Merge only this request's native continuation into its original prefix."""
    current = last_user(incoming)
    original = state["original_source"][state["user_boundary"]] if state["user_boundary"] >= 0 else None
    if original and tool_image(original):
        matching = [i for i, message in enumerate(incoming) if message.get("role") == "user" and message.get("content") == original.get("content")]
        if matching:
            current = matching[-1]
    if current < 0:
        raise ValueError("Native continuation has no current user message")
    state["source"] = deepcopy(state["original_source"][:state["user_boundary"]]) + deepcopy(incoming[current:])


def exchange_name(messages, exchange):
    return messages[exchange.batch]["tool_calls"][exchange.call_index].get("function", {}).get("name", "")


def skill_is_current(messages, exchange, skill_ids):
    call = messages[exchange.batch]["tool_calls"][exchange.call_index]
    return object_value(call.get("function", {}).get("arguments")).get("id") in skill_ids


def reconstruct(messages, selected, *, images=False, positions=None, assistant_content=None,
                historical_turns=None):
    calls = {(e.batch, e.call_index) for e in selected}
    results = {e.result_index for e in selected}
    historical_turns = historical_turns or {}
    output = []
    for i, message in enumerate(messages):
        role = message.get("role")
        if i in historical_turns:
            output.extend(historical_turns[i])
        if role == "system" or role == "tool" and i not in results:
            continue
        if tool_image(message):
            if images is True or images and i in images:
                output.append(deepcopy(message))
            continue
        if role == "assistant":
            original_calls = message.get("tool_calls") or []
            kept = [deepcopy(call) for n, call in enumerate(original_calls) if (i, n) in calls]
            content = deepcopy((assistant_content or {}).get(i, message.get("content")) or "")
            if not kept and not content:
                continue
            clean = {"role": "assistant", "content": content}
            if kept:
                clean["tool_calls"] = kept
                if len(kept) == len(original_calls):
                    for key in ("reasoning_content", "thinking", "reasoning_details", "reasoning_items"):
                        if key in message:
                            clean[key] = deepcopy(message[key])
            output.append(clean)
        elif role in {"user", "tool"}:
            if positions is not None:
                positions[i] = len(output)
            output.append(deepcopy(message))
    return output


def router_projection(state, tool_names, skill_ids, *, window=None):
    messages = state["source"]
    boundary = state.get("user_boundary", last_user(messages))
    exchanges = analyze(messages, current_boundary=boundary, audit=state.get("audit", []))
    exchanges.sort(key=lambda e: (e.batch, e.call_index))
    selected, overrides, packets = [], {}, {}
    handoffs = historical_handoffs(messages, exchanges, boundary)
    by_turn = {e.turn: (e, marker) for e, marker in handoffs}
    for exchange, marker in handoffs:
        packets[exchange.result_index] = {
            "agent_id": marker["agent_id"],
            "status": (state.get("historical_handoff_statuses") or {}).get(
                context_handoff_key(marker), "unknown"),
            "tool_results": [],
        }
    nested_origins = {e.result_index: [] for e, _ in handoffs}
    native_seen = set()
    for exchange in exchanges:
        name = exchange_name(messages, exchange)
        if name in {SKILL_CATALOG, HISTORICAL_AGENT_TURN, CONVERSATION_HISTORY_TOOL}:
            continue
        record = native_record(state, messages[exchange.batch]["tool_calls"][exchange.call_index],
                               messages[exchange.result_index])
        if record:
            if record["key"] not in native_seen and record["key"] in state.get("native_context_allowed", {}):
                selected.append(exchange)
            native_seen.add(record["key"])
            continue
        call = messages[exchange.batch]["tool_calls"][exchange.call_index]
        content = messages[exchange.result_index].get("content")
        destination = by_turn.get(exchange.turn)
        belongs = (destination is not None and exchange.executor == {
            "kind": "agent", "agent_id": destination[1]["agent_id"]})
        if name in READERS:
            if (exchange.batch > boundary and exchange.executor.get("kind") == "router"
                    and name in tool_names and skill_is_current(messages, exchange, skill_ids)):
                selected.append(exchange)
                continue
            fact = {"tool": name, "skill_id": object_value(call.get("function", {}).get("arguments")).get("id"),
                    "status": result_status(content)}
            if belongs:
                packets[destination[0].result_index].setdefault("skill_reads", []).append(fact)
                continue
            overrides[exchange.result_index] = fact
        elif belongs:
            packet_index = destination[0].result_index
            packets[packet_index]["tool_results"].append({
                "tool": name, "arguments": object_value(call.get("function", {}).get("arguments")),
                "status": result_status(content), "result": deepcopy(content),
            })
            nested_origins[packet_index].append((exchange.batch, exchange.call_index))
            continue
        selected.append(exchange)
    positions = {}
    output = reconstruct(messages, selected, images=True, positions=positions)
    for source_index, value in {**overrides, **packets}.items():
        original = output[positions[source_index]]
        output[positions[source_index]] = {"role": "tool", "tool_call_id": original["tool_call_id"],
                                          "content": json.dumps(value, ensure_ascii=False)}
    output = project_native_results(output, state)
    if window is not None:
        window.capture(output, ordinary_origins(state, selected, positions),
                       {positions[i]: origins for i, origins in nested_origins.items()})
    return output


def context_handoff_key(marker):
    """Request provenance, not a call ID which may repeat in another turn."""
    return marker["request_id"], marker["nonce"], marker["agent_id"]


def historical_handoffs(messages, exchanges, boundary):
    candidates = {}
    for exchange in exchanges:
        if exchange.batch >= boundary:
            continue
        marker = handoff_evidence(messages[exchange.batch]["tool_calls"][exchange.call_index],
                                  messages[exchange.result_index])
        if marker:
            candidates.setdefault(exchange.turn, []).append((exchange, marker))
    return [group[0] for group in candidates.values() if len(group) == 1
            and group[0][0].executor == {"kind": "router"}]


VIZ_START = "@@@VIZ-START"
VIZ_END = "@@@VIZ-END"
CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def visualizer_ranges(text):
    """Find complete standalone blocks, preserving quoted and ambiguous markers."""
    if VIZ_START not in text:
        return [], True
    blocks, fence, depth, start, offset = [], "", 0, 0, 0
    unambiguous, nested = True, False
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if depth:
            if line == VIZ_START:
                depth += 1
                nested = True
                unambiguous = False
            elif line == VIZ_END:
                depth -= 1
                if depth == 0 and not nested:
                    blocks.append((start, offset + len(line)))
        else:
            marker = CODE_FENCE.match(line)
            if fence:
                if marker and marker[1].startswith(fence) and not marker[2].strip():
                    fence = ""
            elif marker:
                fence = marker[1]
            elif line == VIZ_START:
                start, depth, nested = offset, 1, False
            elif line == VIZ_END:
                unambiguous = False
        offset += len(raw_line)
    return blocks, unambiguous and depth == 0


def describe_visualizations(content, titles):
    if isinstance(content, str):
        text_parts = [(None, content)]
    elif isinstance(content, list):
        text_parts = [(i, part["text"]) for i, part in enumerate(content)
                      if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
                      and isinstance(part.get("text"), str)]
    else:
        return content
    ranges = [(i, text, *visualizer_ranges(text)) for i, text in text_parts]
    count = sum(len(blocks) for _, _, blocks, _ in ranges)
    if not count:
        return content
    use_titles = (len(titles) == count and None not in titles
                  and all(valid for _, _, _, valid in ranges))
    labels = iter(titles if use_titles else [""] * count)
    parts = deepcopy(content) if isinstance(content, list) else None
    for index, text, blocks, _ in ranges:
        chunks, start = [], 0
        for left, right in blocks:
            label = next(labels)
            name = f" «{label}»" if label else ""
            chunks.extend((text[start:left], f"[Визуализация{name} доступна в исходном чате.]"))
            start = right
        replaced = "".join(chunks) + text[start:]
        if index is None:
            return replaced
        parts[index]["text"] = replaced
    return parts


def visualizer_title(call, exchange, messages, owner):
    if exchange is None or exchange.executor != owner:
        return None
    if result_status(messages[exchange.result_index].get("content")) != "completed":
        return None
    title = object_value(call["function"].get("arguments")).get("title")
    return " ".join(title.split())[:160] if isinstance(title, str) else ""


def agent_dialogue(state, agent_id, exchanges, boundary):
    """Derive text ownership from the same turn and handoff evidence as Tool history."""
    messages = state["source"]
    batches = {}
    for exchange in exchanges:
        batches.setdefault(exchange.batch, []).append(exchange)
    overrides = {}
    historical_turns = {}
    starts = [i for i, m in enumerate(messages)
              if (i == boundary and m.get("role") == "user") or actual_user(m)]
    for start, end in zip(starts, starts[1:] + [len(messages)]):
        calls = [(i, n) for i in range(start + 1, end)
                 for n, call in enumerate(messages[i].get("tool_calls") or [])
                 if call.get("function", {}).get("name") == "lite_delegate"]
        if len(calls) != 1:
            continue
        batch, ordinal = calls[0]
        handoff = next((e for e in batches.get(batch, ()) if e.call_index == ordinal
                        and e.executor == {"kind": "router"}), None)
        if handoff is None:
            continue
        marker = handoff_evidence(messages[batch]["tool_calls"][ordinal], messages[handoff.result_index])
        if not marker or (start >= boundary and state.get("pending") != [marker]):
            continue
        author = marker["agent_id"]
        owner = {"kind": "agent", "agent_id": author}
        terminal = messages[end - 1]
        status = (state.get("historical_handoff_statuses") or {}).get(context_handoff_key(marker))
        replace_viz = (start < boundary and author != agent_id
                       and terminal.get("role") == "assistant" and not terminal.get("tool_calls")
                       and status in (None, "completed"))
        titles = []
        for i in range(start + 1, end):
            message = messages[i]
            if message.get("role") != "assistant":
                continue
            exchanges_in_batch = batches.get(i, ())
            if i <= batch:
                if all(e.executor.get("kind") == "router" for e in exchanges_in_batch):
                    overrides[i] = ""
            elif replace_viz and i > handoff.result_index:
                content = message.get("content")
                if content:
                    if all(e.executor == owner for e in exchanges_in_batch):
                        overrides[i] = describe_visualizations(content, titles)
                    titles = []
                by_index = {e.call_index: e for e in exchanges_in_batch}
                for ordinal, call in enumerate(message.get("tool_calls") or []):
                    if call.get("function", {}).get("name") == "visualize":
                        titles.append(visualizer_title(call, by_index.get(ordinal), messages, owner))
        if start < boundary and author != agent_id:
            payload, moved = foreign_turn(state, handoff, end, owner, batches, overrides)
            if payload:
                call_id = f"lhr_turn_{start}"
                anchor = max(exchange.result_index for exchange in batches[batch]) + 1
                historical_turns[anchor] = [
                    {"role": "assistant", "content": "", "tool_calls": [{
                        "id": call_id, "type": "function",
                        "function": {"name": HISTORICAL_AGENT_TURN, "arguments": "{}"},
                    }]},
                    {"role": "tool", "tool_call_id": call_id,
                     "content": json.dumps(payload, ensure_ascii=False)},
                ]
                overrides.update(dict.fromkeys(moved, ""))
    return overrides, historical_turns


def foreign_turn(state, handoff, end, owner, batches, overrides):
    messages = state["source"]
    events, moved = [], []
    reply = None
    excluded = READERS | NATIVE_CONTEXT_NAMES | {
        HISTORICAL_AGENT_TURN, CONVERSATION_HISTORY_TOOL, "lite_delegate", "delegate_task"}
    for i in range(handoff.batch, end):
        message = messages[i]
        if message.get("role") != "assistant":
            continue
        group = batches.get(i, ())
        tool_calls = message.get("tool_calls") or []
        content = overrides.get(i, message.get("content"))
        if i > handoff.result_index and content:
            if (len(group) != len(tool_calls) or any(exchange.executor != owner for exchange in group)
                    or isinstance(content, list) and any(
                        not isinstance(part, dict) or part.get("type") != "text" for part in content)):
                # Keep native dialogue when its attribution or media cannot be represented as text.
                return None, []
            moved.append(i)
            if i == end - 1 and not tool_calls:
                reply = content
            else:
                events.append({"type": "assistant", "content": content})
        calls = []
        for exchange in sorted(group, key=lambda item: item.call_index):
            name = exchange_name(messages, exchange)
            if exchange.executor == owner and exchange.result_index > handoff.result_index and name not in excluded:
                result = messages[exchange.result_index]
                if not native_record(state, tool_calls[exchange.call_index], result):
                    calls.append({"tool": name, "status": result_status(result.get("content")),
                                  "result": "[Tool result withheld from other agents]"})
        if calls:
            events.append({"type": "tool_calls", "calls": calls})
    if not events and reply is None:
        return None, []
    return {"agent_id": owner["agent_id"], "events": events, "reply": reply}, moved


def agent_projection(state, agent_id, tool_names, skill_ids, *, window=None):
    messages = state["source"]
    boundary = state.get("user_boundary", last_user(messages))
    exchanges = analyze(messages, current_boundary=boundary, audit=state.get("audit", []))
    selected = [e for e in exchanges if e.executor == {"kind": "agent", "agent_id": agent_id}
                and exchange_name(messages, e) in tool_names
                and exchange_name(messages, e) not in {SKILL_CATALOG, HISTORICAL_AGENT_TURN, CONVERSATION_HISTORY_TOOL}
                and (exchange_name(messages, e) not in READERS
                     or e.batch > boundary and skill_is_current(messages, e, skill_ids))]
    selected = [e for e in selected if not native_record(
        state, messages[e.batch]["tool_calls"][e.call_index], messages[e.result_index])]
    native_seen = set()
    for exchange in exchanges:
        if exchange_name(messages, exchange) in {SKILL_CATALOG, HISTORICAL_AGENT_TURN, CONVERSATION_HISTORY_TOOL}:
            continue
        record = native_record(state, messages[exchange.batch]["tool_calls"][exchange.call_index],
                               messages[exchange.result_index])
        if record:
            if record["key"] not in native_seen and record["key"] in state.get("native_context_allowed", {}):
                selected.append(exchange)
            native_seen.add(record["key"])
    selected_results = {e.result_index for e in selected}
    image_indices = set()
    for index, message in enumerate(messages):
        if not tool_image(message):
            continue
        previous = next((i for i in range(index - 1, -1, -1) if messages[i].get("role") == "assistant"), -1)
        group = [e for e in exchanges if e.batch == previous and e.result_index < index]
        if any(e.result_index in selected_results for e in group) and all(
            e.result_index in selected_results or exchange_name(messages, e) in READERS | {"lite_delegate"} for e in group
        ):
            image_indices.add(index)
    positions = {}
    assistant_content, historical_turns = agent_dialogue(state, agent_id, exchanges, boundary)
    output = project_native_results(reconstruct(
        messages, selected, images=image_indices, positions=positions, assistant_content=assistant_content,
        historical_turns=historical_turns), state)
    if window is not None:
        window.capture(output, ordinary_origins(state, selected, positions))
    return output


def ordinary_origins(state, selected, positions):
    return {positions[e.result_index]: (e.batch, e.call_index) for e in selected
            if ordinary_exchange(state, e)}


def ordinary_exchange(state, exchange):
    messages = state["source"]
    return (exchange_name(messages, exchange) not in READERS | {"lite_delegate"}
            and not native_record(state, messages[exchange.batch]["tool_calls"][exchange.call_index],
                                  messages[exchange.result_index]))
