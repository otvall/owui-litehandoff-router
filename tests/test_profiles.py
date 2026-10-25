from dataclasses import FrozenInstanceError
from importlib.metadata import PackageNotFoundError
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lite_handoff_router import owui_adapter
from lite_handoff_router.profiles import RELEASE_PROFILES, ROUTER_PROFILE
from lite_handoff_router.entrypoints import lite_handoff_router, router_preparation


class ProfilesTest(unittest.IsolatedAsyncioTestCase):
    def test_fixed_default_and_immutable_capabilities(self):
        self.assertEqual(ROUTER_PROFILE.version, "0.12.3")
        self.assertTrue(ROUTER_PROFILE.parallel)
        self.assertEqual(len(ROUTER_PROFILE.skill_readers), 3)
        self.assertFalse(RELEASE_PROFILES["0.11.1"].parallel)
        with self.assertRaises(FrozenInstanceError):
            ROUTER_PROFILE.version = "0.11.1"
        with self.assertRaises(TypeError):
            RELEASE_PROFILES["0.11.1"] = ROUTER_PROFILE

    def test_exact_guard_and_source_image_fallback(self):
        for target in RELEASE_PROFILES:
            with self.subTest(target=target), patch.object(owui_adapter, "ROUTER_PROFILE", RELEASE_PROFILES[target]):
                with patch.object(owui_adapter, "version", return_value=target):
                    adapter = owui_adapter.OWUIAdapter()
                    self.assertEqual(type(adapter).__name__, "OWUI" + target.replace(".", "") + "Adapter")
                    with self.assertRaises(AttributeError):
                        adapter.profile = ROUTER_PROFILE
                for installed in ("0.11.2", "0.12.4", "unknown", next(v for v in RELEASE_PROFILES if v != target)):
                    with patch.object(owui_adapter, "version", return_value=installed):
                        with self.assertRaisesRegex(RuntimeError, "requires Open WebUI " + target):
                            owui_adapter.OWUIAdapter()
                with patch.object(owui_adapter, "version", side_effect=PackageNotFoundError), \
                     patch.dict(sys.modules, {"open_webui.env": SimpleNamespace(VERSION=target)}):
                    self.assertEqual(owui_adapter.OWUIAdapter().profile.version, target)

    async def test_both_functions_reject_before_user_or_resource_loading(self):
        for target in RELEASE_PROFILES:
            with self.subTest(target=target), patch.object(owui_adapter, "ROUTER_PROFILE", RELEASE_PROFILES[target]), \
                 patch.object(owui_adapter, "version", return_value="unsupported"):
                request = SimpleNamespace(scope={})
                body = {"model": "router", "owui_version": target, "metadata": {"profile": target}}
                user = {"id": "actor", "valves": {"owui_version": target, "profile": target}}
                with self.assertRaisesRegex(RuntimeError, "requires Open WebUI " + target):
                    await router_preparation.Filter().inlet(body, request, user)
                with self.assertRaisesRegex(RuntimeError, "requires Open WebUI " + target):
                    await lite_handoff_router.Pipe().pipe(body, request, user, body["metadata"])
