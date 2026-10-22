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
    if (len(calls) != 1 or calls[0].get("name") != name
            or calls[0].get("executor") != {"kind": "router"}
            or calls[0].get("arguments") != arguments):
        raise ValueError(name + " must be the only Tool call in its actual generation")


def handoff_barrier(state):
    status = (state.get("parallel") or {}).get("status", "idle")
    if status not in {"idle", "completed"}:
        raise ValueError("Cannot hand off: parallel group is incomplete (" + status + ")")
