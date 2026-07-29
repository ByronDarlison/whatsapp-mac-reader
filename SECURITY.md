# Security policy

## Reporting a vulnerability

Please report security vulnerabilities privately through GitHub's **Report a
vulnerability** feature when it is available. Do not open a public issue that
contains private message data, database extracts, local paths, phone numbers,
credentials, or other personal information.

## Sensitive data

This project reads highly sensitive local data. Never attach any of the
following to an issue or pull request:

- `ChatStorage.sqlite` or its WAL/SHM files
- message exports
- voice notes or other WhatsApp media
- transcript caches
- screenshots containing conversations
- credentials, environment files, or authentication material

If schema information is needed, reduce it to table and column names and review
it manually before sharing.

## Supported versions

The project follows the current `main` branch. WhatsApp's local schema is
private and can change without notice, so compatibility is best-effort.
