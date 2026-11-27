"""Persist signed routing evidence alongside the assistant message."""

import hashlib
import hmac
import json

ROUTING_TRACE = "lite_routing_trace"


def routing_trace_signature(trace, chat_id, message_id, secret):
    value = json.dumps([ROUTING_TRACE, chat_id, message_id, trace], sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def trusted_routing_trace(message, chat_id, message_id, secret):
    meta = message.get("meta")
    record = meta.get(ROUTING_TRACE) if isinstance(meta, dict) else None
    if isinstance(record, dict) and isinstance(record.get("trace"), dict) and isinstance(record.get("signature"), str):
        signature = routing_trace_signature(record["trace"], chat_id, message_id, secret)
        if hmac.compare_digest(signature, record["signature"]):
            return record["trace"]
    return None


async def record_routing_event(adapter, state, user, stage=None, status=None, agent_id=None):
    trace = state.get("routing_trace")
    if trace is None:
        return
    if stage:
        if len(trace["events"]) < 256:
            trace["events"].append({"stage": stage, "status": status, "agent_id": agent_id,
                                    "generation": state["generation"]})
        else:
            trace["events_truncated"] = True
    if state.get("chat_id") and state.get("message_id"):
        await adapter.save_routing_trace(state, user)
