import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


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


class OutputDestinationTests(unittest.TestCase):
    """Default ~/Downloads output, --save, --stdout, and their exclusion."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = {"HOME": str(self.home)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("WHATSAPP_WHISPER_PYTHON", None)
        self.db = self.tmp / "ChatStorage.sqlite"
        self._make_db(self.db)

    def _make_db(self, path):
        nsdate = (
            datetime.now(timezone.utc) - datetime(2001, 1, 1, tzinfo=timezone.utc)
        ).total_seconds() - 60
        with contextlib.closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, "
                "ZMESSAGEDATE REAL, ZTEXT TEXT, ZISFROMME INTEGER)"
            )
            connection.execute(
                "INSERT INTO ZWAMESSAGE VALUES (1, ?, 'hello example', 1)", (nsdate,)
            )
            connection.commit()

    def run_main(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = reader.main(["--db", str(self.db), *argv])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_default_path_uses_expanduser_downloads(self):
        self.assertEqual(
            reader.default_output_path("text"),
            self.home / "Downloads" / "whatsapp_messages.txt",
        )

    def test_extension_matches_output_format(self):
        expected = {"text": "txt", "json": "json", "xml": "xml", "claude": "xml"}
        for output, extension in expected.items():
            with self.subTest(output=output):
                self.assertEqual(
                    reader.default_output_path(output).name,
                    f"whatsapp_messages.{extension}",
                )

    def test_default_writes_to_downloads_and_creates_folder(self):
        downloads = self.home / "Downloads"
        self.assertFalse(downloads.exists())
        for output, extension in (("text", "txt"), ("json", "json"), ("xml", "xml")):
            with self.subTest(output=output):
                code, out, _ = self.run_main("--output", output)
                self.assertEqual(code, 0)
                written = downloads / f"whatsapp_messages.{extension}"
                self.assertTrue(written.is_file())
                self.assertIn("hello example", written.read_text(encoding="utf-8"))
                self.assertEqual(out.strip(), f"Saved to {written.resolve()}")
                self.assertNotIn("hello example", out)
                self.assertEqual(stat.S_IMODE(written.stat().st_mode), 0o600)
        json.loads((downloads / "whatsapp_messages.json").read_text(encoding="utf-8"))

    def test_save_overrides_default_location(self):
        target = self.tmp / "elsewhere" / "custom.json"
        code, out, _ = self.run_main("--output", "json", "--save", str(target))
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())
        self.assertFalse((self.home / "Downloads").exists())
        self.assertEqual(out.strip(), f"Saved to {target.resolve()}")

    def test_existing_file_is_tightened_to_owner_only(self):
        target = self.tmp / "existing.txt"
        target.write_text("old", encoding="utf-8")
        os.chmod(target, 0o644)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        reader.write_output("new", target)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

    def test_stdout_prints_and_writes_no_file(self):
        code, out, _ = self.run_main("--stdout")
        self.assertEqual(code, 0)
        self.assertIn("hello example", out)
        self.assertNotIn("Saved to", out)
        self.assertFalse((self.home / "Downloads").exists())

    def test_stdout_and_save_are_mutually_exclusive(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            reader.parse_args(["--stdout", "--save", str(self.tmp / "x.txt")])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("not allowed with argument", stderr.getvalue())

    def test_database_is_not_modified(self):
        before = self.db.read_bytes()
        self.run_main("--output", "json")
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
