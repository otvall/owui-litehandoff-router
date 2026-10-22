"""Run contracts against an installed, unmodified OWUI 0.12.3 package."""

from pathlib import Path
import sys
import unittest
from native_environment import configure

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
configure()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests/native"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
