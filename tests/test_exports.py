import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExportsTest(unittest.TestCase):
    def test_reproducible_standalone_uploads(self):
        spec = importlib.util.spec_from_file_location("builder", ROOT / "tools/build_functions.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as folder:
            builder.build(folder)
            paths = sorted(Path(folder).glob("*.py"))
            before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
            builder.build(folder)
            self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])
            for path in paths:
                compile(path.read_text(), str(path), "exec")
                env = dict(os.environ)
                env.pop("PYTHONPATH", None)
                subprocess.run([
                    sys.executable, "-I", "-c",
                    "import runpy; d=runpy.run_path(" + repr(str(path)) + "); "
                    "assert any(k in d for k in ('Filter','Pipe','Tools'))",
                ], cwd=folder, env=env, check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
