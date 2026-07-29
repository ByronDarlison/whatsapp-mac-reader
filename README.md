# WhatsApp Mac Reader

A local, read-only command-line tool for exporting recent messages from the
WhatsApp Desktop database on macOS. It can optionally transcribe downloaded
WhatsApp voice notes on Apple Silicon using MLX Whisper.

The tool does **not** log in to WhatsApp, connect to WhatsApp's servers, send
messages, or upload message data. WhatsApp Desktop performs the account
connection and synchronization. This tool reads the resulting local
`ChatStorage.sqlite` database.

> [!IMPORTANT]
> This is an independent community project. It is not affiliated with,
> endorsed by, or supported by WhatsApp or Meta. WhatsApp's private database
> schema can change without notice and may break the reader.

## How it works

```text
Phone ↔ WhatsApp service ↔ WhatsApp Desktop
                              │
                              ▼
                    ChatStorage.sqlite
                              │ read-only SQLite connection
                              ▼
                    whatsapp_reader.py
                       │             │
                       │             └─ downloaded .opus voice notes
                       │                         │
                       │                         ▼
                       │              whatsapp_transcribe.py
                       │                    MLX Whisper
                       ▼
                 text / JSON / XML
```

WhatsApp Desktop must already be installed, paired with your account, and
allowed to synchronize messages. The reader discovers `ChatStorage.sqlite`,
opens it with SQLite's `mode=ro`, detects supported table and column names, and
groups recent messages by conversation.

Voice transcription is optional. When enabled, the reader identifies locally
downloaded `.opus` voice notes and invokes `whatsapp_transcribe.py` using a
separate Python environment containing `mlx-whisper`. Audio and transcripts
remain on the Mac.

## Requirements

### Message export

- macOS
- WhatsApp Desktop from the Mac App Store, installed and signed in
- Python 3.9 or newer
- Full Disk Access for the application that launches Python

### Optional voice transcription

- Apple Silicon Mac
- Homebrew
- `ffmpeg`
- A Python virtual environment containing `mlx-whisper`
- Enough disk space to download the selected Whisper model

The default model is `mlx-community/whisper-large-v3-turbo`.

## Installation

```bash
git clone https://github.com/ByronDarlison/whatsapp-mac-reader.git
cd whatsapp-mac-reader
```

The basic reader uses only Python's standard library; no package installation is
required.

Make the scripts executable if desired:

```bash
chmod +x whatsapp_reader.py whatsapp_transcribe.py
```

## Grant Full Disk Access

The database lives inside the protected `~/Library/Group Containers` directory.
macOS will normally block access until you grant permission.

1. Open **System Settings**.
2. Select **Privacy & Security**.
3. Select **Full Disk Access**.
4. Enable the terminal application you use, such as Terminal, iTerm, or your
   automation host.
5. Quit and reopen that application.

Grant access only to software you trust. Full Disk Access is a powerful macOS
permission.

## Basic usage

Print the last 24 hours as readable text:

```bash
python3 whatsapp_reader.py
```

Print the last 72 hours:

```bash
python3 whatsapp_reader.py --hours 72
```

Generate JSON:

```bash
python3 whatsapp_reader.py --hours 72 --output json
```

Generate XML suitable for structured downstream processing:

```bash
python3 whatsapp_reader.py --hours 72 --output xml
```

Save explicitly to a file:

```bash
python3 whatsapp_reader.py \
  --hours 72 \
  --output json \
  --save ~/Downloads/whatsapp_output.json
```

Saving is deliberately opt-in because exports contain private message content.
Without `--save`, output is written to the terminal.

### Database discovery

Show the selected database and its schema:

```bash
python3 whatsapp_reader.py --discover
```

Supply a database path manually:

```bash
python3 whatsapp_reader.py \
  --db "/path/to/ChatStorage.sqlite" \
  --hours 24
```

The expected App Store path is currently:

```text
~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
```

## Voice-note transcription

Install Homebrew's `ffmpeg`:

```bash
brew install ffmpeg
```

Create a dedicated environment and install the optional dependency:

```bash
python3 -m venv ~/.whatsapp-reader-venv
~/.whatsapp-reader-venv/bin/pip install -r requirements-voice.txt
```

Run the reader with transcription:

```bash
python3 whatsapp_reader.py \
  --hours 72 \
  --whisper-python ~/.whatsapp-reader-venv/bin/python
```

Or set the executable once in your shell:

```bash
export WHATSAPP_WHISPER_PYTHON="$HOME/.whatsapp-reader-venv/bin/python"
python3 whatsapp_reader.py --hours 72
```

The first run downloads the Whisper model. Subsequent runs reuse it. Successful
transcriptions are cached at:

```text
~/.cache/whatsapp-mac-reader/transcripts.json
```

The cache uses hashed keys rather than saving media paths as JSON keys, but the
values are still private message transcripts. Protect and delete that file as
you would any message export.

### Transcription language

The integrated reader currently uses the helper's default language, English.
To invoke the helper directly with automatic language detection:

```bash
printf '%s' '["/absolute/path/to/note.opus"]' |
  ~/.whatsapp-reader-venv/bin/python whatsapp_transcribe.py --language auto
```

The helper accepts JSON paths on standard input and returns a JSON
`path → transcript` mapping on standard output. This interface keeps diagnostic
messages on standard error and makes the helper easy to automate.

## Output formats

- `text`: human-readable conversations and messages
- `json`: complete structured data for software processing
- `xml`: compact structured context for AI or automation pipelines
- `claude`: backward-compatible alias for `xml`

Each message includes the best available timestamp, sender display name,
direction, and text or media marker. Sender resolution is best-effort because
WhatsApp has changed how identifiers and push names are stored over time.

## Privacy and security

- The database is opened using SQLite read-only mode.
- The scripts contain no network requests.
- Voice recognition runs locally through MLX Whisper.
- No database, message export, audio file, transcript cache, or credential
  should be committed to Git.
- Output can contain personal names, phone numbers, private conversations,
  legal matters, financial information, and other sensitive data.
- Review an export before providing it to another person, service, or AI model.
- Use the tool only on an account and computer you are authorized to access.

The included `.gitignore` excludes common database, media, export, cache,
environment, and credential files. It is a safety net, not a substitute for
reviewing staged changes before committing.

## Database details

WhatsApp Desktop currently uses an Apple Core Data SQLite store. The reader
primarily works with:

- `ZWAMESSAGE` for messages
- `ZWACHATSESSION` for conversations
- `ZWAGROUPINFO` for group names
- `ZWAGROUPMEMBER` and `ZWAPROFILEPUSHNAME` for sender names
- `ZWAMEDIAITEM` for local media paths and voice-note duration

WhatsApp dates are converted from Apple's Core Data epoch, which begins at
2001-01-01 UTC.

This schema is undocumented and version-dependent. The reader probes for
several observed table and column variants, but it cannot guarantee
compatibility with future WhatsApp releases.

## Troubleshooting

### `ChatStorage.sqlite was not found`

- Confirm the App Store version of WhatsApp is installed and signed in.
- Open WhatsApp and allow it to finish synchronizing.
- Grant Full Disk Access to the terminal or automation host.
- Quit and reopen the terminal after changing permission.
- Run `python3 whatsapp_reader.py --discover`.

### `operation not permitted` or `unable to open database file`

Full Disk Access is missing from the process that actually launches Python.
Granting it to a different terminal or editor will not help.

### Messages or sender names are missing

The installed WhatsApp version may use a new schema or identifier format. Run
`--discover`, remove all message content from any diagnostic material, and
open an issue containing only the relevant table and column names.

### Voice notes show as `[media:3]`

- Confirm the note is downloaded locally in WhatsApp.
- Confirm `ffmpeg` is installed.
- Confirm the `--whisper-python` path exists.
- Run that Python executable with `-c "import mlx_whisper"`.
- Review diagnostic output on standard error.

### The first transcription is slow

The first run downloads and loads the Whisper model. Later runs reuse the model
files and transcript cache.

## Development

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

Compile-check the scripts:

```bash
python3 -m py_compile whatsapp_reader.py whatsapp_transcribe.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting database compatibility
changes. Never attach a real WhatsApp database, exported conversation, voice
note, screenshot, or transcript to an issue or pull request.

## Origin

The first version was created for Byron Darlison's private daily-brief workflow,
with Claude believed to have written the original reader. This public edition
was generalized, privacy-hardened, documented, and released as a standalone
open-source project.

## Licence

[MIT](LICENSE)
