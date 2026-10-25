"""Build-time release capabilities. Request data never selects a profile."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ReleaseProfile:
    version: str
    skill_readers: frozenset[str]
    parallel: bool


RELEASE_PROFILES = MappingProxyType({
    "0.11.1": ReleaseProfile("0.11.1", frozenset({"view_skill"}), False),
    "0.12.3": ReleaseProfile("0.12.3", frozenset({"view_skill", "read_skill_file", "grep_skill_files"}), True),
})
# The standalone builder replaces this assignment before inlining.
ROUTER_TARGET_VERSION = "0.12.3"
ROUTER_PROFILE = RELEASE_PROFILES[ROUTER_TARGET_VERSION]
