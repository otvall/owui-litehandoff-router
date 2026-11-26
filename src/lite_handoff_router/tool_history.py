"""A request-local result window, applied after destination filters."""

from copy import deepcopy
import json

from .protocol import completed_pairs, object_value, tool_image

def call_key(call):
    function = call.get("function") or {}
    return call.get("id"), function.get("name"), function.get("arguments")


def result_status(content):
    value = object_value(content)
    failed = bool(value.get("error") or value.get("success") is False or value.get("ok") is False
                  or value.get("status") in ("error", "failed")
                  or isinstance(content, str) and content.startswith("Error:"))
    return "error" if failed else "completed"


def result_placeholder(content):
    return "[Tool result hidden by history limit; original retained in chat; status=" + result_status(content) + "]"


def split_tool_result_window(entries, limit):
    ordered = sorted(entries)
    return (ordered[:-limit], ordered[-limit:]) if limit else (ordered, [])


class ToolResultWindow:
    def __init__(self, limit):
        self.limit = limit
        self.entries = []
        self.native_keys = []
        self.packets = {}
        self.hidden_count = 0

    def capture(self, messages, native_origins, nested_origins=None):
        """Origins are trusted projection positions, never provider fields."""
        for ordinal, (i, n, r) in enumerate(completed_pairs(messages), start=len(self.native_keys)):
            self.native_keys.append(deepcopy(call_key(messages[i]["tool_calls"][n])))
            origin = native_origins.get(r)
            if origin is not None:
                self.entries.append((origin, ordinal, -1))
            if r in (nested_origins or {}):
                self.packets[ordinal] = object_value(messages[r]["content"])
                for child, child_origin in enumerate(nested_origins[r]):
                    self.entries.append((child_origin, ordinal, child))

    def apply(self, messages):
        if self.limit is None:
            return
        pairs = completed_pairs(messages)
        keys = [call_key(messages[i]["tool_calls"][n]) for i, n, _ in pairs]
        call_count = sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")
        result_count = sum(m.get("role") == "tool" for m in messages)
        if keys != self.native_keys or call_count != len(pairs) or result_count != len(pairs):
            raise ValueError("Destination inlet changed Tool history structure under the result limit")
        packets = {}
        for ordinal, expected in self.packets.items():
            found = object_value(messages[pairs[ordinal][2]].get("content"))
            children = found.get("tool_results")
            if (not isinstance(children, list) or len(children) != len(expected["tool_results"])
                    or {k: v for k, v in found.items() if k != "tool_results"}
                    != {k: v for k, v in expected.items() if k != "tool_results"}
                    or any(not isinstance(child, dict) or set(child) != set(original)
                           or {k: v for k, v in child.items() if k != "result"}
                           != {k: v for k, v in original.items() if k != "result"}
                           for child, original in zip(children, expected["tool_results"]))):
                raise ValueError("Destination inlet changed historical handoff packet under the result limit")
            packets[ordinal] = deepcopy(found)
        hidden, _ = split_tool_result_window(self.entries, self.limit)
        self.hidden_count = len(hidden)
        hidden_batches = set()
        for _, ordinal, child in hidden:
            if child >= 0:
                record = packets[ordinal]["tool_results"][child]
                record["result"] = result_placeholder(record.get("result"))
                continue
            i, _, r = pairs[ordinal]
            result = messages[r]
            # Do not leave output/files/embeds as an alternate copy of hidden HTML.
            messages[r] = {key: deepcopy(result[key]) for key in ("role", "tool_call_id", "name") if key in result}
            messages[r]["content"] = result_placeholder(result.get("content"))
            hidden_batches.add(i)
        for ordinal, packet in packets.items():
            r = pairs[ordinal][2]
            messages[r] = {"role": "tool", "tool_call_id": messages[r]["tool_call_id"],
                           "content": json.dumps(packet, ensure_ascii=False)}
        # Synthetic images are grouped by OWUI; never remove user attachments.
        previous = None
        remove = []
        for index, message in enumerate(messages):
            if message.get("role") == "assistant":
                previous = index
            elif tool_image(message) and previous in hidden_batches:
                remove.append(index)
        for index in reversed(remove):
            del messages[index]
