# Contributing

Contributions that improve macOS compatibility, schema detection, privacy, and
documentation are welcome.

## Before opening a pull request

1. Create a focused branch.
2. Add or update tests.
3. Run:

   ```bash
   python3 -m unittest discover -s tests -v
   python3 -m py_compile whatsapp_reader.py whatsapp_transcribe.py
   ```

4. Review the complete diff for personal data.

## Privacy rule

Do not commit, upload, paste, or attach real WhatsApp databases, messages,
phone numbers, contact names, media, transcripts, screenshots, or local
credentials. Build synthetic SQLite fixtures when a database example is
required.

## Compatibility reports

When reporting a schema change, provide only:

- macOS version
- WhatsApp Desktop version
- relevant table names
- relevant column names
- the exact sanitized error

Do not provide row contents.
