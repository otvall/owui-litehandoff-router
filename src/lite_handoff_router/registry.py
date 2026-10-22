"""The same immutable request snapshot supplies routing discovery and execution."""

import logging

log = logging.getLogger(__name__)


def pipe_dispatcher(model, models):
    """OWUI's pipe flag is just {'type': 'pipe'}, never a unique identity."""
    if not model.get("pipe"):
        return None
    current, seen = model, set()
    while True:
        identifier = current.get("id")
        if identifier in seen:
            raise ValueError("Cyclic base-model configuration")
        seen.add(identifier)
        base = (current.get("info") or {}).get("base_model_id")
        if not base:
            return str(identifier or "").split(".")[0]
        if base not in models:
            return base.split(".")[0]
        current = models[base]


def ids(values):
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("Resource IDs must be a list of non-empty strings")
    return list(dict.fromkeys(v.strip() for v in values))


async def discover(adapter, request, user, catalog, router_id):
    if not isinstance(catalog, dict) or any(
        not isinstance(k, str) or not k.strip() or k != k.strip()
        or not isinstance(v, str) or not v.strip() or v != v.strip()
        for k, v in catalog.items()
    ):
        raise ValueError("AGENTS must map exact Workspace Model IDs to routing Skill IDs")
    entries, diagnostics = {}, []
    router = request.app.state.MODELS.get(router_id, {})
    for agent_id, skill_id in catalog.items():
        try:
            model = await adapter.model(request, agent_id, user)
            if agent_id == router_id or (
                model.get("pipe") and pipe_dispatcher(model, request.app.state.MODELS)
                == pipe_dispatcher(router, request.app.state.MODELS)
            ):
                raise ValueError("Target resolves back to the Router Pipe")
            skill = await adapter.skill(skill_id, user)
            entries[agent_id] = {
                "agent_id": agent_id, "skill_id": skill_id,
                "agent_name": model.get("info", {}).get("name") or model.get("name") or agent_id,
                "name": skill["name"], "description": skill.get("description") or "",
            }
        except Exception as error:
            reason = str(getattr(error, "detail", error))
            diagnostics.append({"agent_id": agent_id, "reason": reason})
            log.warning("Excluded agent %s: %s", agent_id, reason)
    return entries, diagnostics
