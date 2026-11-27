"""The registry and file layout of a shared routing Skill."""

from collections import Counter
import json
from pathlib import PurePosixPath

ROUTING_INDEX = "agents.json"
ROUTING_OVERVIEW = "SKILL.md"


def unique_routing_keys(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Routing registry contains repeated JSON keys")
        result[key] = value
    return result


def routing_index(files):
    file = files.get(ROUTING_INDEX)
    if not file or file.get("encoding"):
        raise ValueError("Routing Skill requires a text agents.json")
    entries = json.loads(file["content"], object_pairs_hook=unique_routing_keys)
    if not isinstance(entries, dict):
        raise ValueError("agents.json must be a JSON object")
    return entries


def routing_profile_path(value):
    if isinstance(value, str) and "\\" not in value and ":" not in value and not any(ord(char) < 32 for char in value):
        path = PurePosixPath(value)
        if (len(path.parts) >= 2 and path.parts[0] == "profiles"
                and ".." not in path.parts and str(path) == value and path.suffix == ".md"):
            return value
    raise ValueError("Routing profile must use a canonical Markdown path under profiles/")


def routing_file_map(snapshot):
    return {file["path"]: file for file in snapshot["data"]["files"]}


def routing_entries(snapshot):
    files = routing_file_map(snapshot)
    index = routing_index(files)
    paths = Counter(entry["path"] for entry in index.values() if isinstance(entry, dict)
                    and isinstance(entry.get("path"), str))
    entries, invalid = {}, {}
    for model, entry in index.items():
        try:
            if not model or model.strip() != model or not isinstance(entry, dict):
                raise ValueError("Routing entry requires an exact Model ID and an object")
            if set(entry) != {"path", "description"}:
                raise ValueError("Routing entry must contain path and description")
            path = routing_profile_path(entry["path"])
            description = entry["description"]
            if not isinstance(description, str) or not description.strip():
                raise ValueError("Routing entry requires a nonempty description")
            file = files.get(path)
            if not file or file.get("encoding") or not file["content"].strip():
                raise ValueError("Routing profile is missing, empty or unreadable")
            if paths[path] != 1:
                raise ValueError("Routing profile path belongs to more than one Model ID")
            entries[model] = {"path": path, "description": description}
        except ValueError as error:
            invalid[model] = str(error)
    return entries, invalid
