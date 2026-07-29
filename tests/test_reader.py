import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


reader = load_module("whatsapp_reader", "whatsapp_reader.py")
transcriber = load_module("whatsapp_transcribe", "whatsapp_transcribe.py")


class ReaderTests(unittest.TestCase):
    def sample(self):
        return [
            {
                "conversation_name": 'Friends & "Family"',
                "datetime": "2026-07-29 12:00:00 UTC",
                "sender_display": "You",
                "is_from_me": 1,
                "text": "Use <this> & that",
                "timestamp": 100,
            }
        ]

    def test_grouping(self):
        grouped = reader.group_by_conversation(self.sample())
        self.assertEqual(grouped[0]["name"], 'Friends & "Family"')
        self.assertEqual(grouped[0]["message_count"], 1)

    def test_xml_escapes_attributes_and_body(self):
        xml = reader.format_xml(reader.group_by_conversation(self.sample()))
        self.assertIn('name="Friends &amp; &quot;Family&quot;"', xml)
        self.assertIn("Use &lt;this&gt; &amp; that", xml)

    def test_json_is_valid(self):
        output = reader.format_json(reader.group_by_conversation(self.sample()))
        parsed = json.loads(output)
        self.assertEqual(parsed["conversation_count"], 1)

    def test_pushname_rejects_known_tokens(self):
        self.assertFalse(reader._is_real_pushname("IABIAZABAPABAQ=="))
        self.assertTrue(reader._is_real_pushname("Taylor Example"))

    def test_cache_key_does_not_expose_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-name.opus"
            path.write_bytes(b"audio")
            key = transcriber._cache_key(str(path))
            self.assertEqual(len(key), 64)
            self.assertNotIn("private-name", key)

    def test_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            transcriber.save_cache(path, {"abc": "hello"})
            self.assertEqual(transcriber.load_cache(path), {"abc": "hello"})


if __name__ == "__main__":
    unittest.main()
