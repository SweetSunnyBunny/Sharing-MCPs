# home-assistant-mcp

A portable copy of the current home-assistant MCP adapter. A running Home Assistant installation and your own long-lived access token.

## Install

In this folder, run in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Merge `mcp-servers.example.json` into your client's private configuration, replace the paths and key, then restart the client. `.env.example` documents the settings; it is not loaded automatically. The client launches `server.py` over stdio; a quiet terminal waiting for input is normal.

Use `ha_get_states` as the first read-only connection check. Service calls change your own devices only when invoked.

No original accounts, device records, conversations, contact numbers or credentials are included. SMS has no allowed recipients until you configure `ANAM_SMS_ALLOWED_CONTACTS` on the UI; the separate SMS-capable Android app is not bundled.
