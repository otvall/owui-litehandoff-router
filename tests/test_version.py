from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.entrypoints import lite_handoff_router, router_preparation


class VersionTest(unittest.IsolatedAsyncioTestCase):
    def test_adapter_does_not_inspect_installed_version(self):
        with patch("importlib.metadata.version", side_effect=AssertionError("Version lookup forbidden")):
            OWUIAdapter()

    async def test_both_functions_reach_user_loading_without_version_gate(self):
        with patch("importlib.metadata.version", side_effect=AssertionError("Version lookup forbidden")), \
             patch.object(OWUIAdapter, "user", AsyncMock(side_effect=LookupError("User lookup reached"))) as user:
            request = SimpleNamespace(scope={})
            for execute in (
                lambda: router_preparation.Filter().inlet({}, request, {"id": "actor"}),
                lambda: lite_handoff_router.Pipe().pipe({}, request, {"id": "actor"}, {}),
            ):
                with self.assertRaisesRegex(LookupError, "User lookup reached"):
                    await execute()
            self.assertEqual(user.await_count, 2)
