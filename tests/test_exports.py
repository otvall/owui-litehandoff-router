import hashlib
import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExportsTest(unittest.TestCase):
    def builder(self):
        spec = importlib.util.spec_from_file_location("builder", ROOT / "tools/build_functions.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        return builder

    def test_reproducible_standalone_uploads(self):
        builder = self.builder()
        with tempfile.TemporaryDirectory() as folder:
            self.check_target(builder, Path(folder), '0.12.5')

    def check_target(self, builder, folder, target):
        builder.build(folder)
        paths = sorted(Path(folder).glob("*.py"))
        self.assertEqual([path.name for path in paths], ["lite_handoff_router.py", "router_preparation.py"])
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        builder.build(folder)
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])
        for path in paths:
            text = path.read_text()
            compile(text, str(path), "exec")
            self.assertIn('required_open_webui_version: ' + target, text)
            tree = ast.parse(text)
            definitions = [n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
            self.assertEqual(len(definitions), len(set(definitions)), 'inlined global symbols collide')
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            subprocess.run([
                sys.executable, "-I", "-c",
                "import runpy; d=runpy.run_path(" + repr(str(path)) + "); "
                "assert d['OWUI_VERSION'] == " + repr(target) + "; "
                "assert any(k in d for k in ('Filter','Pipe')); "
                "from unittest.mock import patch; "
                "p=patch.dict(d['OWUIAdapter'].__init__.__globals__, {'version': lambda _: " + repr(target) + "}); "
                "p.start(); d['OWUIAdapter']()",
            ], cwd=folder, env=env, check=True, capture_output=True)

    def test_checked_in_exports_match_source(self):
        builder = self.builder()
        with tempfile.TemporaryDirectory() as folder:
            builder.build(folder)
            for path in Path(folder).glob("*.py"):
                self.assertEqual(path.read_bytes(), (ROOT / "dist" / path.name).read_bytes())
                self.assertNotIn("ReleaseProfile", path.read_text())
                self.assertNotIn("OWUI0111Adapter", path.read_text())


if __name__ == "__main__":
    unittest.main()
