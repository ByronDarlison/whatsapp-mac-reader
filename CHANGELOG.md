# Changelog

## 1.1.0 - 2026-11-02

- Save output to `~/Downloads/whatsapp_messages.<ext>` by default, where the
  extension matches `--output` (`txt`, `json`, or `xml`). The Downloads folder
  is created if missing, and the full path written is printed when done.
- Keep `--save PATH` to write somewhere else.
- Add `--stdout` to print to the terminal instead, as 1.0.0 did by default.
- Reject `--save` and `--stdout` together with a clear error.
- Write saved files readable only by the current user, including when
  replacing an existing file.
- Print the `Saved to <path>` message to stdout instead of stderr.
- Keep `--discover` printing to the terminal unless `--save` is given.
- The chat database is still opened read-only, and nothing uses the clipboard.

## 1.0.0 - 2026-07-29

- Publish a generalized, read-only macOS WhatsApp message reader.
- Add text, JSON, and XML output.
- Add optional local voice-note transcription with MLX Whisper.
- Make message export opt-in.
- Remove personal identity and private-directory assumptions.
- Escape structured output and hash transcript-cache keys.
- Add complete privacy, security, installation, and troubleshooting guidance.
