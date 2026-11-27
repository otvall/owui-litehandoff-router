import json
from pathlib import Path
import unittest


class NativeHistoryTest(unittest.TestCase):
    def test_grouped_fixture_matches_actual_v0123_converter(self):
        from open_webui.utils.misc import convert_output_to_messages

        fixture = json.loads((Path(__file__).parents[1] / "fixtures/owui_0123_grouped.json").read_text())
        actual = convert_output_to_messages(fixture["output"], raw=True, flatten_tool_images=True)
        for message in actual:
            if message.get("role") == "assistant" and message.get("tool_calls") and message.get("content") is None:
                message["content"] = ""
        self.assertEqual(actual, fixture["messages"][1:])
