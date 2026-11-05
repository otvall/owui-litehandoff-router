from importlib.metadata import PackageNotFoundError
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lite_handoff_router import SUPPORTED_OWUI, owui_adapter
from lite_handoff_router.entrypoints import lite_handoff_router, router_preparation


class VersionTest(unittest.IsolatedAsyncioTestCase):
    def test_exact_guard_and_source_image_fallback(self):
        self.assertEqual(SUPPORTED_OWUI, "0.12.5")
        with patch.object(owui_adapter, "version", return_value="0.12.5"):
            self.assertIs(type(owui_adapter.OWUIAdapter()), owui_adapter.OWUIAdapter)
        for installed in ("0.11.1", "0.12.3", "0.12.4", "0.12.6", "unknown"):
            with self.subTest(installed=installed), patch.object(owui_adapter, "version", return_value=installed):
                with self.assertRaisesRegex(RuntimeError, "requires Open WebUI 0.12.5"):
                    owui_adapter.OWUIAdapter()
        for installed in ("0.12.5", "0.12.3"):
            with patch.object(owui_adapter, "version", side_effect=PackageNotFoundError), \
                 patch.dict(sys.modules, {"open_webui.env": SimpleNamespace(VERSION=installed)}):
                if installed == "0.12.5":
                    owui_adapter.OWUIAdapter()
                else:
                    with self.assertRaisesRegex(RuntimeError, "requires Open WebUI 0.12.5"):
                        owui_adapter.OWUIAdapter()

    async def test_both_functions_reject_before_user_or_resource_loading(self):
        with patch.object(owui_adapter, "version", return_value="0.12.3"):
            request = SimpleNamespace(scope={})
            body = {"model": "router", "owui_version": "0.12.5", "metadata": {"profile": "0.12.5"}}
            user = {"id": "actor", "valves": {"owui_version": "0.12.5", "profile": "0.12.5"}}
            with self.assertRaisesRegex(RuntimeError, "requires Open WebUI 0.12.5"):
                await router_preparation.Filter().inlet(body, request, user)
            with self.assertRaisesRegex(RuntimeError, "requires Open WebUI 0.12.5"):
                await lite_handoff_router.Pipe().pipe(body, request, user, body["metadata"])
