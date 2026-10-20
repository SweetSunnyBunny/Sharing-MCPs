# Use Codex as Anam's chat provider

This is an optional provider. Get the UI running with steps 1–4 of
[the UI README](../README.md) first. The browser-based ChatGPT bridge is a
different option with its own [guide](CHATGPT_BRIDGE_SETUP.md).

## 1. Install and sign into the Codex CLI

1. Open [OpenAI's Codex CLI installation guide](https://developers.openai.com/codex/cli/).
2. Select its **Windows** installation instructions and complete them.
3. Open a new PowerShell window in your `ui` folder and run:

```powershell
codex --version
codex
```

The first command should print a version. The second opens Codex and lets you
sign in using an account method available to you. Complete sign-in and confirm
you can send a small prompt there, then exit the interactive CLI.

## 2. Restart Anam and select Codex

1. Stop Anam with `Ctrl+C` in its server terminal.
2. Open a new PowerShell window in `ui` and start it again using the UI README.
   This lets the server see the newly installed CLI.
3. Open **Settings → System → LLM Provider** and choose **Codex**.
4. Keep **Runtime** set to **Companion thread (app-server)**.
5. Leave the model empty to use the CLI default, or select a model available to
   your own account. Click **Save**.

## 3. Check a real reply

Return to chat, select an example identity and send a short greeting. A completed
reply confirms both CLI authentication and Anam's provider connection. **Test
Connection** is a preliminary check, not a replacement for this step.

Anam starts and manages the app-server process. You do not need to launch another
app-server terminal yourself. This runtime keeps a resumable conversation and
streams tool activity. The one-shot `exec` option remains available as a fallback.

## If it fails

- If `codex` is not recognized, finish the official installation and reopen your
  terminal before restarting Anam.
- If authentication fails, run `codex` yourself and complete sign-in first.
- If the model is rejected, choose an available model or clear the model field
  and save again.
- If tools are missing, configure your own MCP servers. The starter contains
  generic prompts and no third-party service credentials.

For a passive report from `ui`, run:

```powershell
.\.venv\Scripts\python.exe scripts\anam_doctor.py --json
```

Keep account authentication, local session state, conversations and your own
prompts private. Do not copy another installation's auth or memory files.
