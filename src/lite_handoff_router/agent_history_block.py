"""Render an agent's already-filtered past turns as a separate reference message."""

import json

from .history import HISTORICAL_AGENT_TURN, analyze, historical_handoffs
from .native_context import native_record
from .protocol import AGENT_HISTORY_HEADING, SHARED_SUMMARY_PREFIX, actual_user, completed_pairs, object_value
from .tool_history import result_placeholder, result_status

HIDDEN_ERROR = result_placeholder("Error:")


def history_content(content, media):
    if not isinstance(content, list):
        return content
    parts = []
    for part in content:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind in {"text", "input_text"}:
            parts.append(part.get("text", ""))
        elif kind in {"image_url", "video_url", "audio_url", "input_audio", "input_image"}:
            if kind == "input_image":
                url = part.get("image_url")
                part = {"type": "image_url", "image_url": url if isinstance(url, dict) else {"url": url}}
            media.append(part)
            parts.append({"media_index": len(media)})
        else:
            parts.append(part)
    return parts


def pack_agent_history(messages, state, current_question, *, covered_turns=0, packet=None):
    if current_question is None:
        return
    question_id = current_question.get("id")
    current = next((i for i in range(len(messages) - 1, -1, -1)
                    if messages[i].get("role") == "user" and
                    (messages[i].get("id") == question_id if question_id
                     else messages[i].get("content") == current_question.get("content"))), None)
    if current is None:
        return
    first = 1 if messages[0].get("role") == "system" else 0
    section = messages[first:current]
    past = [m for m in section if m != packet]
    if not past:
        return

    source = state["source"]
    boundary = state["user_boundary"]
    source_questions = [m for m in source[:boundary] if actual_user(m)]
    if not source_questions:
        return
    pairs = completed_pairs(past)
    if any(native_record(state, past[i]["tool_calls"][n], past[r]) for i, n, r in pairs):
        return
    exchanges = analyze(source[:boundary], current_boundary=boundary, audit=state.get("audit", ()))
    authors = {exchange.turn: marker["agent_id"]
               for exchange, marker in historical_handoffs(source, exchanges, boundary)}
    historical_users = [m for m in past if actual_user(m)]
    summary = historical_users[0] if covered_turns and historical_users else None
    projected_questions = historical_users[1:] if summary is not None else historical_users
    aligned = [m.get("content") for m in projected_questions] == [
        m.get("content") for m in source_questions[covered_turns:]]

    results = {(i, ordinal): past[r] for i, ordinal, r in pairs}
    media, turns, attachments = [], [], []
    history = {"turns": turns}
    if summary is not None:
        history["summary"] = summary["content"].removeprefix(SHARED_SUMMARY_PREFIX)
    turn = None
    turn_number = covered_turns - 1
    for index, message in enumerate(past):
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        if message is summary:
            continue
        content = history_content(message.get("content"), media)
        attachments.extend(message.get("files") or ())
        if actual_user(message):
            turn_number += 1
            turn = {"question": content, "events": []}
            if aligned and turn_number in authors:
                turn["agent_id"] = authors[turn_number]
            turns.append(turn)
            continue
        if turn is None:
            turn = {"events": []}
            turns.append(turn)
        events = turn["events"]
        if content:
            events.append({"type": "tool_media" if role == "user" else "assistant", "content": content})
        calls = []
        for ordinal, call in enumerate(message.get("tool_calls") or ()):
            result = results.get((index, ordinal))
            if result is None:
                continue
            function = call["function"]
            if function["name"] == HISTORICAL_AGENT_TURN:
                foreign = object_value(result["content"])
                events.append({"type": "agent_turn", **foreign})
            else:
                raw_result = result.get("content")
                calls.append({"tool": function["name"], "arguments": object_value(function.get("arguments")),
                              "status": "error" if raw_result == HIDDEN_ERROR else result_status(raw_result),
                              "result": history_content(raw_result, media)})
                attachments.extend(result.get("files") or ())
        if calls:
            events.append({"type": "tool_calls", "calls": calls})

    text = AGENT_HISTORY_HEADING + "\n" + json.dumps(history, ensure_ascii=False, separators=(",", ":"))
    block = []
    if media or attachments:
        media_label = "[Conversation history media] Media are numbered in the order they appear here."
        media_message = {"role": "user",
                         "content": [{"type": "text", "text": media_label}, *media] if media else media_label}
        if attachments:
            media_message["files"] = attachments
        block.append(media_message)
    retained = [m for m in section if m == packet]
    messages[first:current] = [*block, *retained, {"role": "user", "content": text}]
