import importlib.util
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
import zipfile

from lite_handoff_router.authoring import authoring_page
from lite_handoff_router.routing_skill_renderer import render
from tests.test_routing_skill_renderer import fields, TEMPLATE


ROOT = Path(__file__).resolve().parents[1]


class AuthoringBuildTest(unittest.TestCase):
    def test_standalone_and_importable_package_are_reproducible_and_match_sources(self):
        spec = importlib.util.spec_from_file_location("authoring_builder", ROOT / "tools/build_authoring_agent.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            builder.build(destination)
            before = {p.name: p.read_bytes() for p in destination.iterdir()}
            builder.build(destination)
            self.assertEqual(before, {p.name: p.read_bytes() for p in destination.iterdir()})
            for name, content in before.items():
                self.assertEqual(content, (ROOT / "dist" / name).read_bytes())
            tool = destination / "routing_skill_authoring.py"
            subprocess.run([sys.executable, "-I", "-c", "import runpy; d=runpy.run_path(" + repr(str(tool)) +
                            "); assert d['Tools'](); assert d['authoring_template']().startswith('---')"],
                           cwd=directory, capture_output=True, check=True)
            loaded = runpy.run_path(str(tool))
            self.assertEqual(loaded["render"](fields(), loaded["authoring_template"]()), render(fields(), TEMPLATE))
            with zipfile.ZipFile(destination / "routing-skill-authoring.zip") as archive:
                files = archive.namelist()
                self.assertIn("routing-skill-authoring/SKILL.md", files)
                self.assertIn("routing-skill-authoring/references/router-contract.md", files)
                self.assertFalse(any("__pycache__" in name for name in files))
                self.assertEqual(archive.read("routing-skill-authoring/scripts/render_routing_skill.py"),
                                 (ROOT / "docs/skills/routing-skill-authoring/scripts/render_routing_skill.py").read_bytes())

    def test_pages_cover_full_file_without_silently_truncating(self):
        self.assertEqual(authoring_page("abcdef", 0, 3), {"content": "abc", "next_offset": 3})
        self.assertEqual(authoring_page("abcdef", 3, 3), {"content": "def", "next_offset": None})
        for offset, size in ((-1, 3), (0, 0), (0, 20001)):
            with self.assertRaises(ValueError):
                authoring_page("text", offset, size)
