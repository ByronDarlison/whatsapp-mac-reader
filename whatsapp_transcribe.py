#!/usr/bin/env python3
"""Transcribe WhatsApp .opus voice notes locally with MLX Whisper.

Reads a JSON array of absolute audio paths from stdin and writes a JSON object
mapping each path to its transcript on stdout. Transcripts are cached locally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

DEFAULT_CACHE = Path.home() / ".cache" / "whatsapp-mac-reader" / "transcripts.json"
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


def _cache_key(path: str) -> str:
    """Use a non-identifying cache key derived from path and file metadata."""
    file_path = Path(path)
    try:
        stat = file_path.stat()
        material = f"{file_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        material = str(file_path)
    return hashlib.sha256(material.encode()).hexdigest()


def load_cache(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache), encoding="utf-8")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--language", default="en", help="Language code, or 'auto'")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for directory in ("/opt/homebrew/bin", "/usr/local/bin"):
        if directory not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    try:
        paths = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 2
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        print("{}")
        return 2

    cache_path = args.cache.expanduser()
    cache = load_cache(cache_path)
    results: dict[str, str] = {}
    pending: list[str] = []
    for path in paths:
        key = _cache_key(path)
        if key in cache:
            results[path] = cache[key]
        elif Path(path).is_file():
            pending.append(path)

    if pending:
        try:
            import mlx_whisper
        except ImportError as exc:
            print(f"mlx-whisper is unavailable: {exc}", file=sys.stderr)
            print(json.dumps(results))
            return 1

        language = None if args.language == "auto" else args.language
        for path in pending:
            try:
                response = mlx_whisper.transcribe(
                    path,
                    path_or_hf_repo=args.model,
                    language=language,
                )
                transcript = str(response.get("text") or "").strip()
                results[path] = transcript
                cache[_cache_key(path)] = transcript
            except Exception as exc:
                print(f"Could not transcribe {Path(path).name}: {exc}", file=sys.stderr)
        save_cache(cache_path, cache)

    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
