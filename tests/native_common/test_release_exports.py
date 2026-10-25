import importlib.util
from pathlib import Path
import runpy
import tempfile
import unittest

from lite_handoff_router.owui_adapter import OWUIAdapter


class NativeReleaseExportsTest(unittest.TestCase):
    def test_standalone_native_import_and_exact_guard_for_both_builds(self):
        root=Path(__file__).resolve().parents[2]
        spec=importlib.util.spec_from_file_location('native_builder',root/'tools/build_functions.py')
        builder=importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        actual=OWUIAdapter().profile.version
        with tempfile.TemporaryDirectory() as directory:
            for target in ('0.11.1','0.12.3'):
                destination=Path(directory)/target
                builder.build(destination,target)
                for path in destination.glob('*.py'):
                    loaded=runpy.run_path(str(path))
                    if target==actual:
                        self.assertEqual(loaded['OWUIAdapter']().profile.version,actual)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'requires Open WebUI '+target):
                            loaded['OWUIAdapter']()
