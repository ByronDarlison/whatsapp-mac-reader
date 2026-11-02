#!/usr/bin/env python3
"""Read recent messages from WhatsApp Desktop's local macOS database.

The database is opened in SQLite read-only mode. Nothing is sent to WhatsApp,
Meta, an AI provider, or any other network service.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

SKIP_DIRS = {"Media", "Message", "Logs", "Caches", "tmp", "StickerThumbs", "Stickers"}
DEFAULT_OUTPUT_STEM = "whatsapp_messages"
OUTPUT_EXTENSIONS = {"text": "txt", "json": "json", "xml": "xml", "claude": "xml"}
KNOWN_DB_PATH = (
    Path.home()
    / "Library"
    / "Group Containers"
    / "group.net.whatsapp.WhatsApp.shared"
    / "ChatStorage.sqlite"
)


def _safe_glob(root: Path, pattern: str) -> list[Path]:
    results: list[Path] = []
    try:
        for item in root.iterdir():
            if item.is_file() and item.match(pattern):
                results.append(item)
            elif item.is_dir() and item.name not in SKIP_DIRS:
                results.extend(_safe_glob(item, pattern))
    except (PermissionError, OSError):
        pass
    return results


def find_whatsapp_databases() -> list[dict[str, Any]]:
    candidates: list[Path] = []
    if KNOWN_DB_PATH.exists():
        candidates.append(KNOWN_DB_PATH)

    group_containers = Path.home() / "Library" / "Group Containers"
    if group_containers.exists():
        try:
            containers = list(group_containers.iterdir())
        except (PermissionError, OSError):
            containers = []
        for container in containers:
            if "whatsapp" not in container.name.lower():
                continue
            for path in _safe_glob(container, "*.sqlite"):
                if path not in candidates:
                    candidates.append(path)

    found = []
    for path in candidates:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if path.is_file() and size > 1000:
            found.append({"path": str(path), "size_mb": round(size / 1_048_576, 2)})
    return found


def _table_names(cursor: sqlite3.Cursor) -> set[str]:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cursor.fetchall()}


def explore_schema(db_path: str) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            cursor = connection.cursor()
            for table in sorted(_table_names(cursor)):
                safe_table = table.replace('"', '""')
                cursor.execute(f'PRAGMA table_info("{safe_table}")')
                columns = [{"name": row[1], "type": row[2]} for row in cursor.fetchall()]
                cursor.execute(f'SELECT COUNT(*) FROM "{safe_table}"')
                schema[table] = {"columns": columns, "row_count": cursor.fetchone()[0]}
    except sqlite3.OperationalError as exc:
        return {"error": str(exc), "hint": "Grant Full Disk Access to your terminal or Python."}
    return schema


def _is_real_pushname(value: str | None) -> bool:
    if not value or value in {"IAA=", "IABIAZABAPABAQ=="}:
        return False
    if re.fullmatch(r"[A-Za-z0-9+/]+=*", value) and len(value) > 10:
        try:
            decoded = base64.b64decode(value)
            if decoded and decoded[0] < 0x20:
                return False
        except Exception:
            pass
    return True


def _pushname_lookup(cursor: sqlite3.Cursor) -> dict[str, str]:
    if "ZWAPROFILEPUSHNAME" not in _table_names(cursor):
        return {}
    try:
        cursor.execute(
            "SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME "
            "WHERE ZPUSHNAME IS NOT NULL"
        )
        return {
            jid: name
            for jid, name in cursor.fetchall()
            if jid and name and _is_real_pushname(name)
        }
    except sqlite3.Error:
        return {}


def _group_name_lookup(cursor: sqlite3.Cursor) -> dict[int, str]:
    if "ZWAGROUPINFO" not in _table_names(cursor):
        return {}
    try:
        cursor.execute("PRAGMA table_info(ZWAGROUPINFO)")
        columns = {row[1] for row in cursor.fetchall()}
        subject = next((c for c in ("ZSUBJECT", "ZGROUPNAME", "ZNAME", "ZTITLE") if c in columns), None)
        chat_fk = next((c for c in ("ZCHATSESSION", "ZCHAT") if c in columns), None)
        if not subject or not chat_fk:
            return {}
        cursor.execute(
            f"SELECT {chat_fk}, {subject} FROM ZWAGROUPINFO "
            f"WHERE {subject} IS NOT NULL"
        )
        return {pk: name for pk, name in cursor.fetchall() if pk and name}
    except sqlite3.Error:
        return {}


def extract_messages(db_path: str, hours: int) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()
        if "ZWAMESSAGE" not in _table_names(cursor):
            raise RuntimeError("ZWAMESSAGE table not found; this WhatsApp schema is unsupported.")
        return _extract_core_data(cursor, hours)


def _extract_core_data(cursor: sqlite3.Cursor, hours: int) -> list[dict[str, Any]]:
    nsdate_epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_nsdate = (cutoff - nsdate_epoch).total_seconds()
    pushnames = _pushname_lookup(cursor)
    group_names = _group_name_lookup(cursor)
    tables = _table_names(cursor)

    cursor.execute("PRAGMA table_info(ZWAMESSAGE)")
    msg_columns = {row[1] for row in cursor.fetchall()}
    text_col = next((c for c in ("ZTEXT", "ZBODY", "ZCONTENT") if c in msg_columns), None)
    date_col = next((c for c in ("ZMESSAGEDATE", "ZSENTDATE", "ZTIMESTAMP", "ZDATE") if c in msg_columns), None)
    if not date_col:
        raise RuntimeError("No supported message timestamp column was found.")

    sender_col = next((c for c in ("ZFROMJID", "ZSENDER", "ZFROM") if c in msg_columns), None)
    from_me_col = next((c for c in ("ZISFROMME", "ZFROMME", "ZISOUTGOING") if c in msg_columns), None)
    type_col = next((c for c in ("ZMESSAGETYPE", "ZMEDIATYPE", "ZTYPE") if c in msg_columns), None)
    chat_fk = next((c for c in ("ZCHATSESSION", "ZCHAT", "ZCONVERSATION") if c in msg_columns), None)
    chat_table = next((t for t in ("ZWACHATSESSION", "ZWACHAT", "ZCHAT") if t in tables), None)

    select = ["m.Z_PK AS id", f"m.{date_col} AS timestamp"]
    optional = {
        "text": text_col,
        "sender": sender_col,
        "is_from_me": from_me_col,
        "media_type": type_col,
        "push_name": "ZPUSHNAME" if "ZPUSHNAME" in msg_columns else None,
    }
    select.extend(f"m.{column} AS {alias}" for alias, column in optional.items() if column)
    joins: list[str] = []

    if "ZWAMEDIAITEM" in tables:
        select.extend(["mi.ZMEDIALOCALPATH AS media_local_path", "mi.ZMOVIEDURATION AS media_duration"])
        joins.append("LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK")

    if chat_table and chat_fk:
        cursor.execute(f"PRAGMA table_info({chat_table})")
        chat_columns = {row[1] for row in cursor.fetchall()}
        jid_col = next((c for c in ("ZCONTACTJID", "ZJID") if c in chat_columns), None)
        partner_col = next((c for c in ("ZPARTNERNAME", "ZDISPLAYNAME", "ZNAME") if c in chat_columns), None)
        select.append("c.Z_PK AS chat_session_pk")
        if jid_col:
            select.append(f"c.{jid_col} AS chat_jid")
        if partner_col:
            select.append(f"c.{partner_col} AS partner_name")
        joins.insert(0, f"LEFT JOIN {chat_table} c ON m.{chat_fk} = c.Z_PK")

        if "ZGROUPMEMBER" in msg_columns and "ZWAGROUPMEMBER" in tables:
            select.extend(["gm.ZMEMBERJID AS group_member_jid", "pn.ZPUSHNAME AS group_member_name"])
            joins.extend(
                [
                    "LEFT JOIN ZWAGROUPMEMBER gm ON m.ZGROUPMEMBER = gm.Z_PK",
                    "LEFT JOIN ZWAPROFILEPUSHNAME pn ON gm.ZMEMBERJID = pn.ZJID",
                ]
            )

    query = (
        f"SELECT {', '.join(select)} FROM ZWAMESSAGE m "
        f"{' '.join(joins)} WHERE m.{date_col} > ? ORDER BY m.{date_col} ASC"
    )
    cursor.execute(query, (cutoff_nsdate,))

    messages: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        message = dict(raw)
        timestamp = message.get("timestamp")
        if timestamp is not None:
            message["datetime"] = (nsdate_epoch + timedelta(seconds=timestamp)).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )

        chat_jid = str(message.get("chat_jid") or "")
        chat_pk = message.get("chat_session_pk")
        partner = message.get("partner_name")
        if chat_pk in group_names:
            conversation = group_names[chat_pk]
        elif partner:
            conversation = partner
        elif chat_jid in pushnames:
            conversation = pushnames[chat_jid]
        elif chat_jid:
            conversation = chat_jid.split("@")[0]
        else:
            conversation = "unknown"
        message["conversation_name"] = conversation

        sender_jid = str(message.get("sender") or "")
        if message.get("is_from_me") == 1:
            sender = "You"
        elif "@g.us" in chat_jid and _is_real_pushname(message.get("group_member_name")):
            sender = message["group_member_name"]
        elif "@g.us" not in chat_jid and partner:
            sender = partner
        elif _is_real_pushname(message.get("push_name")):
            sender = message["push_name"]
        elif sender_jid in pushnames:
            sender = pushnames[sender_jid]
        elif "@" in sender_jid and "@lid" not in sender_jid and "@g.us" not in sender_jid:
            sender = sender_jid.split("@")[0]
        else:
            sender = "unknown"
        message["sender_display"] = sender
        messages.append(message)
    return messages


def transcribe_voice_notes(
    messages: list[dict[str, Any]], db_path: str, whisper_python: str | None
) -> None:
    if not whisper_python:
        return
    helper = Path(__file__).with_name("whatsapp_transcribe.py")
    python_path = Path(whisper_python).expanduser()
    if not python_path.exists() or not helper.exists():
        print("Voice transcription skipped: helper or Python environment not found.", file=sys.stderr)
        return

    media_base = Path(db_path).parent / "Message"
    by_path: dict[str, list[dict[str, Any]]] = {}
    for message in messages:
        relative = message.get("media_local_path")
        if (
            message.get("text")
            or message.get("media_type") != 3
            or not relative
            or not str(relative).lower().endswith(".opus")
        ):
            continue
        absolute = str(media_base / str(relative))
        by_path.setdefault(absolute, []).append(message)

    if not by_path:
        return
    try:
        process = subprocess.run(
            [str(python_path), str(helper)],
            input=json.dumps(list(by_path)),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if process.stderr.strip():
            print(process.stderr.strip(), file=sys.stderr)
        result = json.loads(process.stdout) if process.stdout.strip() else {}
    except Exception as exc:
        print(f"Voice transcription failed; continuing without it: {exc}", file=sys.stderr)
        return

    for path, related_messages in by_path.items():
        transcript = str(result.get(path) or "").strip()
        for message in related_messages:
            if transcript:
                message["transcript"] = transcript
            if message.get("media_duration"):
                message["voice_dur"] = round(float(message["media_duration"]))


def group_by_conversation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for message in messages:
        name = str(message.get("conversation_name") or "unknown")
        grouped.setdefault(name, {"name": name, "messages": [], "message_count": 0})
        grouped[name]["messages"].append(message)
        grouped[name]["message_count"] += 1
    return sorted(
        grouped.values(),
        key=lambda item: item["messages"][-1].get("timestamp", 0),
        reverse=True,
    )


def _message_body(message: dict[str, Any]) -> str:
    if message.get("text"):
        return str(message["text"])
    if message.get("transcript"):
        duration = f" ({message['voice_dur']}s)" if message.get("voice_dur") else ""
        return f"[voice{duration}] {message['transcript']}"
    return f"[media:{message.get('media_type', 'unknown')}]"


def format_text(conversations: list[dict[str, Any]]) -> str:
    lines = [
        f"WhatsApp messages — scan time: {datetime.now().astimezone().isoformat()}",
        f"Conversations with activity: {len(conversations)}",
    ]
    for conversation in conversations:
        lines.extend(["", f"## {conversation['name']}"])
        for message in conversation["messages"]:
            lines.append(
                f"[{message.get('datetime', 'unknown')}] "
                f"{message.get('sender_display', 'unknown')}: {_message_body(message)}"
            )
    return "\n".join(lines)


def format_json(conversations: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "scan_time": datetime.now().astimezone().isoformat(),
            "conversation_count": len(conversations),
            "conversations": conversations,
        },
        indent=2,
        default=str,
    )


def format_xml(conversations: list[dict[str, Any]]) -> str:
    scan_time = escape(datetime.now().astimezone().isoformat(), quote=True)
    lines = [f'<whatsapp_messages scan_time="{scan_time}" conversations="{len(conversations)}">']
    for conversation in conversations:
        name = escape(str(conversation["name"]), quote=True)
        lines.append(f'<conversation name="{name}" messages="{conversation["message_count"]}">')
        for message in conversation["messages"]:
            timestamp = escape(str(message.get("datetime", "")), quote=True)
            sender = escape(str(message.get("sender_display", "unknown")), quote=True)
            direction = "sent" if message.get("is_from_me") == 1 else "received"
            body = escape(_message_body(message))
            lines.append(f'<msg time="{timestamp}" from="{sender}" dir="{direction}">{body}</msg>')
        lines.append("</conversation>")
    lines.append("</whatsapp_messages>")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="History window in hours")
    parser.add_argument("--db", help="Explicit path to ChatStorage.sqlite")
    parser.add_argument("--discover", action="store_true", help="Print database candidates and schema")
    parser.add_argument("--output", choices=("text", "json", "xml", "claude"), default="text")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "--save",
        metavar="PATH",
        help="Write output to PATH instead of the default file in ~/Downloads",
    )
    destination.add_argument(
        "--stdout",
        action="store_true",
        help="Print output to the terminal instead of writing a file",
    )
    parser.add_argument(
        "--whisper-python",
        default=os.environ.get("WHATSAPP_WHISPER_PYTHON"),
        help="Python executable with mlx-whisper installed; enables voice transcription",
    )
    return parser.parse_args(argv)


def default_output_path(output: str) -> Path:
    """Return ~/Downloads/whatsapp_messages.<ext> for the chosen --output."""
    extension = OUTPUT_EXTENSIONS[output]
    return Path(os.path.expanduser("~/Downloads")) / f"{DEFAULT_OUTPUT_STEM}.{extension}"


def write_output(output: str, destination: Path) -> Path:
    """Write output to destination, readable only by the current user."""
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(output)
        if not output.endswith("\n"):
            handle.write("\n")
    return destination.resolve()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.hours <= 0:
        print("ERROR: --hours must be greater than zero.", file=sys.stderr)
        return 2

    if args.db:
        db_path = str(Path(args.db).expanduser())
        if not Path(db_path).is_file():
            print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
            return 1
    else:
        found = find_whatsapp_databases()
        if not found:
            print(
                "ERROR: ChatStorage.sqlite was not found. Install and sign in to "
                "WhatsApp for Mac, then grant Full Disk Access.",
                file=sys.stderr,
            )
            return 1
        db_path = max(found, key=lambda item: item["size_mb"])["path"]

    if args.discover:
        output = json.dumps(
            {"selected_database": db_path, "schema": explore_schema(db_path)},
            indent=2,
        )
    else:
        try:
            messages = extract_messages(db_path, args.hours)
        except (sqlite3.Error, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        transcribe_voice_notes(messages, db_path, args.whisper_python)
        conversations = group_by_conversation(messages)
        output_format = "xml" if args.output == "claude" else args.output
        output = {
            "text": format_text,
            "json": format_json,
            "xml": format_xml,
        }[output_format](conversations)

    if args.stdout or (args.discover and not args.save):
        print(output)
        return 0

    destination = Path(args.save) if args.save else default_output_path(args.output)
    written = write_output(output, destination)
    print(f"Saved to {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
