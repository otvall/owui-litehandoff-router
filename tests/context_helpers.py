"""Read service catalogs separately from ordinary execution results in tests."""


def catalog_ids(messages):
    return {call.get("id") for message in messages for call in message.get("tool_calls", [])
            if call.get("function", {}).get("name") == "list_available_skills"}


def catalog_result(messages):
    ids = catalog_ids(messages)
    found = [m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") in ids]
    if len(found) != 1:
        raise AssertionError("Expected exactly one current Skill catalog: " + str(len(found)))
    return found[0]["content"]


def work_results(messages):
    ids = catalog_ids(messages)
    return [m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") not in ids]


def last_question(messages):
    return next(m for m in reversed(messages) if m.get("role") == "user")
