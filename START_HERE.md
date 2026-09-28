# Start here: get one thing working first

This is a box of separate applications, not one application with 32 required
parts. You do not need to install everything. The instructions below use
Windows and PowerShell. Some services also run on other systems; each package
explains its requirements.

You can build your own companion setup from these packages. Some optional
features from the larger installation still need code that is not in this
download; check the [coverage map](shared-docs/COVERAGE.md) before choosing them.

## 1. Extract the download

1. Right-click the clean ZIP and choose **Extract All**.
2. Open the extracted `Sharing-MCPs` folder. You should see `ui`, `mind-backend`,
   `limbic`, and this file beside each other.
3. Keep the folders together. Some optional tools find each other by looking in
   the folder next door.
4. Use a normal folder you can write to, such as
   `C:\MCP-Starter\Sharing-MCPs`. That is an example, not a required location.
   Do not run the applications from inside the ZIP or under `Program Files`.

## 2. Choose your first result

| I want to… | Open this guide first | What else is required? |
| --- | --- | --- |
| Have a chat in the web UI | [ui/README.md](ui/README.md) | One configured model provider |
| Play on a fictional social network with my favourite characters | [world-feed/README.md](world-feed/README.md) | The included UI and a supported background model provider; pictures are optional |
| Use my signed-in ChatGPT account through the UI | [ChatGPT bridge guide](ui/docs/CHATGPT_BRIDGE_SETUP.md) | The UI, Chrome, and your own ChatGPT login |
| Give companions cloud memory | [mind-backend/README.md](mind-backend/README.md) | Your own Cloudflare account and resources |
| Add emotional drive state | [limbic/README.md](limbic/README.md) | Your own Cloudflare account; matching identity IDs |
| Connect current Qualia to a client that only supports local tools | [qualia-mcp/README.md](qualia-mcp/README.md) | A deployed `mind-backend` service |
| Use one particular tool | Find its folder in the [package list](README.md#package-list) | Only that folder's listed requirements |

For the full companion setup, get a basic chat working first. Then add Qualia,
then Limbic, then any other tools you want. Complete a package's success check
before starting the next one. [Connecting the packages](shared-docs/CONNECTIONS.md)
explains how the pieces fit together.

## 3. Understand the instruction boxes

- **PowerShell** is the window where you type commands. To open it in the correct
  folder, open that folder in File Explorer, click the address bar, type
  `powershell`, and press Enter. Use an ordinary window, not Administrator.
- A command box contains commands to copy. Do not copy its heading or the
  surrounding backticks. Run one line at a time and wait for it to finish.
- If a command shows an error, stop there and use that guide's troubleshooting
  section. Continuing usually causes more confusing errors.
- `YOUR-...`, `REPLACE-...` and `/absolute/path/...` mean **replace this with your
  own value**. They are not working credentials or real locations.
- If a guide says to edit `.env`, use `notepad .env` from the package folder.
  Save it as exactly `.env`, not `.env.txt`. In File Explorer, enable **View →
  Show → File name extensions** to check.
- A `.venv` is a package's private Python installation. The guides call
  `.\.venv\Scripts\python.exe` directly, so you do not need to activate it or
  change your computer's script execution policy.
- `Ctrl+C` stops a server running in the current terminal. Keep that window
  open while you use the server.

## 4. Install only the runtimes your package asks for

| Runtime | Official download | Check in a new PowerShell window |
| --- | --- | --- |
| Python | [Python for Windows](https://www.python.org/downloads/windows/) | `py -3.11 --version` for the tested Python 3.11 instructions |
| Node.js | [Node.js downloads](https://nodejs.org/en/download) | `node --version`, then `npm.cmd --version`; the cloud guides use Node 24 |
| Chrome | [Google Chrome](https://www.google.com/chrome/) | Open Chrome; needed only for the ChatGPT browser bridge |

After installing a runtime, close and reopen PowerShell before checking it.
Use the version requested by the package; automatically choosing the newest
major Python release can break older optional audio/model libraries. The UI
guide has a locked dependency installation path.

## 5. Know what “connected” means

There are two common kinds of tool server:

- **Local / stdio:** your chat client starts a command and talks to it over its
  input/output streams. It may sit silently if you start it by hand. There is no
  web page to open. Add the guide's command, arguments and environment settings
  to your client's MCP configuration, restart that client, and check its tool list.
- **Remote / HTTP:** a server stays running locally or in your cloud account.
  Add its URL and authentication settings to a client that supports HTTP MCP.
  Opening an MCP URL in a normal browser is not a complete connection test.

MCP is the connection format that lets a chat application call a tool. A tool
server does not include the chat application or a model subscription. Each guide
states which connection type it uses and how to check it.

## 6. Keep your own installation private

The provided examples are deliberately generic. Populate your own `.env`,
tokens, browser login and identity prompts locally. Keep those files, databases,
logs and browser profiles out of anything you share. Do not copy someone else's
account tokens or personal data to make a setup work.

For your first UI test, follow its localhost-only instructions. Before putting it
on the internet, complete the UI's authenticated hosting section. Other people's
computers must not be able to reach a development server with login disabled.

## Common problems before you even reach a package

| What you see | What to do |
| --- | --- |
| `python`, `py`, `node` or `npm` is not recognized | Install that runtime and open a new terminal. Follow the package's requested version. |
| `npm.ps1 cannot be loaded` | Use `npm.cmd` and `npx.cmd` in PowerShell. This avoids changing execution policy. |
| `No such file`, `requirements.txt` missing, or `package.json` missing | You opened the wrong folder. Run `Get-Location` and `Get-ChildItem`; open the package containing the file. |
| `401` or `403` | Check that your own key is set and matches the service. A `YOUR-...` placeholder cannot authenticate. |
| Connection refused | Start the server first, keep its terminal open, and check the host/port from its guide. |
| Address/port already in use | An earlier copy may still be running. Stop your earlier copy or use the documented alternate port. |
| A local MCP command stays silent | That can be normal. Connect it through an MCP client and check whether its tools appear. |

The [review record](SETUP_REVIEW.md) separates checks performed on this snapshot
from integrations that need your own live accounts. The scripts in `scripts/`
are for checking and packaging the clean download; they are not an installer for
all 32 packages.
