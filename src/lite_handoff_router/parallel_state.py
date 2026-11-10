"""Request-owned group policy and routing control guards."""


def group_state(maximum=4, timeout=300):
    if type(maximum) is not int or maximum < 0:
        raise ValueError("max_parallel_tasks must be 0 or an integer >= 1")
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("parallel_timeout_seconds must be a positive integer")
    return {"status": "idle", "max_tasks": maximum, "timeout_seconds": timeout,
            "results": [], "unavailable_reason": None}


def control_generation(state, name, arguments):
    if state.get("active_agent") or state.get("worker"):
        raise ValueError("Nested delegation is disabled")
    calls = [c for c in state.get("audit", []) if c.get("generation") == state.get("generation")]
    if not calls:
        raise ValueError("Cannot verify " + name + ": no Tool calls were observed in its actual generation")
    if len(calls) != 1:
        raise ValueError(name + " must be the only Tool call in its actual generation")
    if calls[0].get("name") != name or calls[0].get("executor") != {"kind": "router"}:
        raise ValueError(name + " does not match the observed Router Tool call")
    observed = calls[0].get("arguments")
    # OWUI execute_tool_call drops properties outside the Tool schema before
    # calling us. Compare that same projection, preserving nested values and
    # the unmodified provider arguments in the audit trail.
    if (not isinstance(observed, dict)
            or {key: value for key, value in observed.items() if key in arguments} != arguments):
        raise ValueError(name + " arguments do not match the observed Tool call")


def rejected_followup(state):
    group = state.get("parallel") or {}
    generation = group.get("generation")
    # Native dispatch may reject arguments before our callable is entered.
    # The server-observed generation still proves the additional routing attempt.
    return bool(group.get("rejected_followup") or generation is not None and any(
        call.get("generation", -1) > generation and call.get("name") == "delegate_task"
        and call.get("executor") == {"kind": "router"} for call in state.get("audit", [])))


def handoff_barrier(state):
    group = state.get("parallel") or {}
    if rejected_followup(state):
        raise ValueError("Cannot hand off: additional parallel group was rejected. "
                         "Report the actual completed results and the missing work.")
    status = group.get("status", "idle")
    if status not in {"idle", "completed"}:
        raise ValueError("Cannot hand off: parallel group is incomplete (" + status + ")")


def group_calls(state):
    if state.get("active_agent") or state.get("worker"):
        raise ValueError("Nested delegation is disabled")
    calls = [c for c in state.get("audit", []) if c.get("generation") == state.get("generation")]
    if not calls or any(c.get("name") != "delegate_task" or c.get("executor") != {"kind": "router"}
                        for c in calls):
        raise ValueError("delegate_task must be the only Tool kind in its actual generation")
    ids = [c.get("id") for c in calls]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("Ambiguous parallel Tool call IDs")
    for index, call in enumerate(calls, 1):
        arguments = call.get("arguments")
        if (not isinstance(arguments, dict) or not {"agent_id", "task"} <= set(arguments)
                or set(arguments) - {"agent_id", "task", "file_ids"}):
            raise ValueError("Invalid parallel assignment " + str(index) + ": only agent_id, task and file_ids are accepted")
    return calls
