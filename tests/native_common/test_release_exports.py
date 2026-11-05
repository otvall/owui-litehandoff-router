import importlib.util
from pathlib import Path
import runpy
import tempfile
import unittest

from lite_handoff_router.owui_adapter import OWUIAdapter, OWUI_VERSION


class NativeReleaseExportsTest(unittest.TestCase):
    def test_standalone_native_import_and_exact_guard(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location('native_builder', root / 'tools/build_functions.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        OWUIAdapter()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            builder.build(destination)
            for path in destination.glob('*.py'):
                loaded = runpy.run_path(str(path))
                self.assertEqual(loaded['OWUI_VERSION'], OWUI_VERSION)
                loaded['OWUIAdapter']()
