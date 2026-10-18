# anam-touch-mcp

A portable copy of the current anam-touch MCP adapter. The sibling Anam UI, running with your own API key. Wrist tools also require the included UI wrist gate and a configured notification/reply client.

## Install

In this folder, run in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Merge `mcp-servers.example.json` into your client's private configuration, replace the paths and key, then restart the client. `.env.example` documents the settings; it is not loaded automatically. The client launches `server.py` over stdio; a quiet terminal waiting for input is normal.

Use `timer_list` as the first read-only connection check. `ANAM_ROOT` defaults to the sibling `ui` folder; override it for a separate UI install. Keys can be supplied directly as `ANAM_API_KEY`, otherwise the adapter reads that UI's `.env`.

No original accounts, device records, conversations, contact numbers or credentials are included. SMS has no allowed recipients until you configure `ANAM_SMS_ALLOWED_CONTACTS` on the UI; the separate SMS-capable Android app is not bundled.
