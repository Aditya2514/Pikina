# Security Policy

Pikina runs commands on your computer, so security reports are taken seriously.

## Reporting a vulnerability

Report it privately through
[GitHub Security Advisories](https://github.com/Aditya2514/Pikina/security/advisories/new).
Please don't open a public issue. Include what an attacker could do and how to
reproduce it. Expect a first reply within a week.

## Security model

- The backend listens on `127.0.0.1` only and every API request needs the token
  stored in the user's data folder (`.api_token`).
- Text from files, the clipboard, web pages or an AI model is `UNTRUSTED_DATA`:
  it is logged and indexed but can never trigger an action.
- High-risk actions (permission level 4+, or level 3 without rollback) need an
  explicit "yes" in a native dialog. If no dialog can be shown, they are denied.
- The kill switch halts every action until Pikina restarts.

In scope: anything that lets a web page, file, clipboard content or model output
run a capability, bypass the consent dialog, or read another user's data.

## Supported versions

Only the latest release receives fixes.
