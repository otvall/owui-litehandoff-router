"""Trust boundary of the offline fixture installer, without an OWUI server."""

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('visualizer_acceptance',
    Path(__file__).resolve().parents[1] / 'tools' / 'visualizer_acceptance.py')
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


class VisualizerFixtureTests(unittest.TestCase):
    def test_pinned_assets_validate_without_network(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            manifest = acceptance.verify_assets()
        self.assertEqual(len(manifest['files']), 4)

    def test_modified_tool_is_rejected_before_disposable_server_access(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / 'assets'
            shutil.copytree(acceptance.ASSETS, assets)
            with (assets / '2.2.3' / 'tool.py').open('a') as output:
                output.write('\n# modified\n')
            with patch.object(acceptance, 'disposable', side_effect=AssertionError('server accessed')) as server:
                with self.assertRaisesRegex(AssertionError, 'SHA-256 mismatch'):
                    acceptance.install('2.2.3', assets)
                server.assert_not_called()

    def test_mutations_refuse_a_checkout_before_http(self):
        with patch.object(acceptance, 'transport', side_effect=AssertionError('HTTP accessed')) as transport:
            with self.assertRaisesRegex(AssertionError, 'disposable container directory'):
                acceptance.disposable()
            transport.assert_not_called()
