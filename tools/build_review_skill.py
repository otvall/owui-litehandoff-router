"""Build the importable routing Skill review package without downloads."""

import argparse
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "docs/skills/routing-skill-review"


def build(destination=None):
    destination = Path(destination or ROOT / "dist")
    destination.mkdir(parents=True, exist_ok=True)
    package = destination / "routing-skill-review.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("routing-skill-review/SKILL.md", date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, (SKILL / "SKILL.md").read_bytes())
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path)
    build(parser.parse_args().destination)
