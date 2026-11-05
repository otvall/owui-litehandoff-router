"""Isolated contracts for the supported Open WebUI 0.12.5 release."""

import argparse
from pathlib import Path
import sys
import unittest
from native_environment import configure

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("smoke", "full"), default="full")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    configure(data_root=args.data_root)
    sys.path.insert(0, str(ROOT / "src"))
    from lite_handoff_router.owui_adapter import OWUIAdapter, OWUI_VERSION
    OWUIAdapter()  # Fail before discovery if the environment is wrong.
    # Native startup/config owns migrations. Initialize it before fixture writes.
    from open_webui import config as native_config

    print(f"Native target={OWUI_VERSION} suite={args.suite}", flush=True)
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().discover(str(ROOT / "tests/native_common")))
    if args.suite == "full":
        suite.addTests(unittest.TestLoader().discover(str(ROOT / "tests/native")))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print("Skipped native cases do not establish release compatibility", file=sys.stderr)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
