# Use ChatGPT through Anam

The browser bridge is included in this UI. It sends messages through a dedicated
Chrome window signed into your own ChatGPT account, then brings the replies back
into Anam. It uses your account's available ChatGPT models and limits.

You need Windows, the installed Anam UI, Google Chrome, and a ChatGPT account.
You do not need an OpenAI API key, Node.js, Playwright, Cloudflare, or a public
tunnel for basic bridge chat.

## 1. Get the UI running first

Complete steps 1–4 of [the UI README](../README.md). Leave its server terminal
open. Open a second PowerShell window in the same `ui` folder for the commands
below.

## 2. Open the dedicated Chrome window

Install [Google Chrome](https://www.google.com/chrome/) if you do not have it.
Then run this command in the second terminal:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\pack-browser.ps1 -Action open -Visible
```

`-ExecutionPolicy Bypass` applies only to this launch process. It does not change
your computer's permanent PowerShell policy.

**Expected result:** a separate Chrome window opens at ChatGPT. This uses a new
profile called `ChatGPT`, so your normal Chrome login is not automatically there.

If Chrome was not found, locate `chrome.exe`, then set its actual path and rerun
the command. For example, for a per-user Chrome installation:

```powershell
$env:CHROME_EXE = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
```

That example works only if Chrome exists there. For a permanent alternate path,
add `CHROME_EXE=your actual path` to the UI's local `.env` and restart Anam.
Anam's automatic launcher reads that setting. Manual PowerShell launches do not
load `.env`; set `$env:CHROME_EXE` in that terminal again when needed.

## 3. Sign into your own ChatGPT account

1. In that dedicated Chrome window, sign into ChatGPT.
2. Complete any account verification prompts yourself.
3. Make sure you can see a normal chat composer and send a short message there.
4. Leave the window open for your first Anam test.

Your login cookies stay in your own Windows profile, under
`%LOCALAPPDATA%\Anam\BrowserProfiles\ChatGPT`. The shared download contains no
browser session or account login.

## 4. Tell Anam to use that browser

In Anam, open **Settings → System**:

1. Under **LLM Provider**, choose **ChatGPT (browser bridge)**.
2. Set **Chrome profile** to `ChatGPT`.
3. Set **CDP port** to `9225`.
4. Click **Save**.

Click **Test Connection** for a read-only check of the local browser port and
whether a ChatGPT tab is present. It does not launch a browser, sign you in or
send a message. Continue to step 5 even if that check succeeds.

CDP is Chrome's local control connection. The profile name and port must match
the launcher. You do not need to expose this port to the internet or configure
a router/firewall rule.

## 5. Send the first message

Return to chat, choose one of the example identities, and send a short greeting.

**Success means:** your message receives a completed reply inside Anam. A browser
window opening, a ready port, or a provider being saved is not yet a full chat
test. The first response may take longer while the browser bridge starts.

The bridge sends the selected identity prompt, recent conversation and relevant
configured context to your signed-in ChatGPT account. Put only your own material
in those prompts and conversations.

## 6. Add tools later

Basic chat works without the cloud packages. To add Qualia, Limbic or another
MCP service, deploy it using its own README and add your own connection in
`mcp-servers.json`. Use `mcp-servers.example.json` as a reference; merge selected
entries into `mcpServers`, keeping the existing tool configuration.

Bridge actions use Anam's configured MCP connections. The separate `machine-agent`
package is optional for local computer tools. The external machine HTTP gateway
is an additional setup, not a prerequisite for basic ChatGPT chat.

## Start it again tomorrow

Start Anam using the command in the UI README. The dedicated browser profile
keeps your login locally. If ChatGPT asks you to sign in again, repeat step 2
with `-Visible` and complete sign-in in that window.

To inspect the local browser connection without sending a chat:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\pack-browser.ps1 -Action status
```

`Ready: true` means the local Chrome control port answered. It does not prove
ChatGPT is signed in or able to generate a response.

## Troubleshooting

| What happened | What to check |
| --- | --- |
| Chrome was not found | Set `CHROME_EXE` to your installed `chrome.exe`; see step 2. |
| The port belongs to another browser | Close the other bridge you started, or follow the alternate-port instructions below. Do not terminate unrelated browser sessions. |
| ChatGPT asks for login or verification | Open the dedicated profile visibly and complete it yourself. Logging into your everyday browser does not log into this profile. |
| Anam still uses another provider | Select ChatGPT under **LLM Provider** and click **Save**. Check that the identity does not have a separate provider override. |
| A response times out | Inspect the dedicated Chrome window for login prompts, account limits or a site error. Confirm ChatGPT works there before retrying in Anam. |
| Tools are missing | Chat alone needs no MCP servers; tools do. Configure your own endpoints and restart Anam. |
| The website layout changed | This is a website adapter. ChatGPT site changes can require bridge code updates; switch to another configured provider while diagnosing. |

### Use a different port or profile

Keep the launcher and Anam settings identical. For example, before launching
the browser in PowerShell:

```powershell
$env:ANAM_CHATGPT_CDP_PORT = "9226"
$env:ANAM_CHATGPT_IDENTITY = "MyChatGPT"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\pack-browser.ps1 -Action open -Visible
```

Then save port `9226` and profile `MyChatGPT` in Anam's provider settings. For
future manual launches, set those variables again or add them to your shell's
own configuration. A new profile requires its own first sign-in.

## Included files and verification scope

The package includes `services/chatgpt_bridge.py`, `chatgpt_provider.py`,
`chatgpt_actions.py`, the browser launcher, action/gateway support and regression
tests. Local tests check the implementation and configuration wiring. Your
authenticated login and a completed ChatGPT reply must be checked with your own
account using steps 3–5.
