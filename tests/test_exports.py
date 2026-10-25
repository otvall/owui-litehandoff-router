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
            for target in ('0.12.3', '0.11.1'):
                self.check_target(builder, Path(folder) / target, target)

    def check_target(self, builder, folder, target):
        builder.build(folder, target)
        paths = sorted(Path(folder).glob("*.py"))
        self.assertEqual([path.name for path in paths], ["lite_handoff_router.py", "router_preparation.py"])
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        builder.build(folder, target)
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
                "assert d['ROUTER_PROFILE'].version == " + repr(target) + "; "
                "assert any(k in d for k in ('Filter','Pipe')); "
                "from unittest.mock import patch; "
                "p=patch.dict(d['OWUIAdapter'].__init__.__globals__, {'version': lambda _: " + repr(target) + "}); "
                "p.start(); assert d['OWUIAdapter']().profile.version == " + repr(target),
            ], cwd=folder, env=env, check=True, capture_output=True)

    def test_unknown_target_does_not_overwrite_or_create_output(self):
        builder = self.builder()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'output'
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                builder.build(output, '0.11.2')
            self.assertFalse(output.exists())
            builder.build(output)  # Preserve the original Python API default.
            before = {p.name:p.read_bytes() for p in output.iterdir()}
            with self.assertRaises(ValueError): builder.build(output, 'unknown')
            self.assertEqual(before, {p.name:p.read_bytes() for p in output.iterdir()})


if __name__ == "__main__":
    unittest.main()
