"""Request-owned group policy and routing control guards."""


def group_state(maximum=4, timeout=300):
    if type(maximum) is not int or maximum < 0 or maximum == 1:
        raise ValueError("max_parallel_tasks must be 0 or an integer >= 2")
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


def handoff_barrier(state):
    status = (state.get("parallel") or {}).get("status", "idle")
    if status not in {"idle", "completed"}:
        raise ValueError("Cannot hand off: parallel group is incomplete (" + status + ")")
