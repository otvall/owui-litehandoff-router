"""The same immutable request snapshot supplies routing discovery and execution."""

import logging

from .routing_profiles import routing_entries

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


async def discover(adapter, request, user, catalog, router_id, *, routing_skill_id="", source=None):
    if not routing_skill_id and (not isinstance(catalog, dict) or any(
        not isinstance(k, str) or not k.strip() or k != k.strip()
        or not isinstance(v, str) or not v.strip() or v != v.strip()
        for k, v in catalog.items()
    )):
        raise ValueError("AGENTS must map exact Workspace Model IDs to routing Skill IDs")
    entries, diagnostics = {}, []
    router = await adapter.model(request, router_id, user)
    owner = await adapter.owner(router)
    if source is not None:
        source.update(kind="shared_skill" if routing_skill_id else "AGENTS",
                      skill_id=routing_skill_id or None, version_id=None)
    if routing_skill_id:
        try:
            snapshot = await adapter.routing_skill_snapshot(routing_skill_id, owner)
        except Exception as error:
            return entries, [routing_diagnostic(error, category="skill_unavailable")]
        if source is not None:
            source["version_id"] = snapshot["version_id"]
        try:
            profiles, invalid = routing_entries(snapshot)
        except Exception as error:
            return entries, [routing_diagnostic(error, category="invalid_registry")]
        diagnostics.extend(routing_diagnostic(reason, agent_id, "invalid_profile")
                           for agent_id, reason in invalid.items())
        catalog = dict.fromkeys(profiles, routing_skill_id)
    for agent_id, skill_id in catalog.items():
        category = "model_unavailable"
        try:
            model = await adapter.model(request, agent_id, user)
            if agent_id == router_id or (
                model.get("pipe") and pipe_dispatcher(model, request.app.state.MODELS)
                == pipe_dispatcher(router, request.app.state.MODELS)
            ):
                raise ValueError("Target resolves back to the Router Pipe")
            category = "skill_unavailable"
            skill = snapshot if routing_skill_id else await adapter.skill(skill_id, owner)
            entry = {
                "agent_id": agent_id, "skill_id": skill_id,
                "agent_name": model.get("info", {}).get("name") or model.get("name") or agent_id,
                "name": skill["name"], "description": skill.get("description") or "",
            }
            if routing_skill_id:
                entry.update(profiles[agent_id], skill_version_id=snapshot["version_id"])
            entries[agent_id] = entry
        except Exception as error:
            diagnostics.append(routing_diagnostic(error, agent_id, category))
    return entries, diagnostics


def routing_diagnostic(error, agent_id=None, category="unknown"):
    reason = str(getattr(error, "detail", error))
    log.warning("Excluded routing entry %s: %s", agent_id or "registry", reason)
    return {"agent_id": agent_id, "reason": reason, "category": category}
