"""Explicit, isolated contracts for each unmodified OWUI release."""

import argparse
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from native_environment import configure

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owui-version", required=True, choices=("0.11.1", "0.12.3"))
    parser.add_argument("--suite", choices=("smoke", "full"), default="full")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    configure(owui_version=args.owui_version, data_root=args.data_root)
    # A test build has the same fixed selection as an export. Never add an
    # environment/request override to the production profile to run tests.
    with tempfile.TemporaryDirectory(prefix="lhr-native-source-") as directory:
        source = Path(directory) / "lite_handoff_router"
        shutil.copytree(ROOT / "src/lite_handoff_router", source, ignore=shutil.ignore_patterns("__pycache__"))
        profile = source / "profiles.py"
        text = profile.read_text()
        assert text.count('ROUTER_TARGET_VERSION = "0.12.3"') == 1
        profile.write_text(text.replace('ROUTER_TARGET_VERSION = "0.12.3"',
                                       'ROUTER_TARGET_VERSION = ' + repr(args.owui_version)))
        sys.path.insert(0, directory)
        from lite_handoff_router.owui_adapter import OWUIAdapter
        adapter = OWUIAdapter()  # Fail before discovery if the environment is wrong.
        # Native startup/config owns migrations. Models can be imported before
        # config, so initialize it explicitly before the first fixture writes.
        from open_webui import config as native_config

        print(f"Native target={adapter.profile.version} suite={args.suite}", flush=True)
        suite = unittest.TestSuite()
        suite.addTests(unittest.TestLoader().discover(str(ROOT / "tests/native_common")))
        if args.suite == "full":
            folder = "native" if args.owui_version == "0.12.3" else "native_0111"
            suite.addTests(unittest.TestLoader().discover(str(ROOT / "tests" / folder)))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if result.skipped:
            print("Skipped native cases do not establish release compatibility", file=sys.stderr)
        return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
