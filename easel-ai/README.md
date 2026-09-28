# Easel — run a local image gallery and prompt workshop

Easel is a local web app for generated images, character/style presets, folders
and a prompt workshop. It runs on your Windows computer, not Cloudflare. You
need Node.js and your own OpenAI API account/key with access to the models you
select. Loading an empty gallery is free of generation calls; generating images
or using the workshop calls your API account and may cost money.

This app is a browser/REST application, not an MCP server. It does not need the
Anam UI or a ChatGPT browser bridge. A ChatGPT subscription alone is not the
API key this app expects.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\easel-ai`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `easel-ai`; Node should report `v24...`.
Wait for installation to finish before continuing. If PowerShell blocks
`npm.ps1`, use `npm.cmd` instead of `npm`, and `npx.cmd` instead of `npx`.
No global Wrangler installation is needed.

## 2. Create your local settings file

Create an API key in your [OpenAI API account](https://platform.openai.com/api-keys).
Then run:

```powershell
Copy-Item ..env.example ..env
notepad ..env
```

Replace `OPENAI_API_KEY=sk-...` with your own key. Add this line so the first
installation listens only on your own computer:

```dotenv
HOST=127.0.0.1
```

Leave `PORT` at 5178 unless you already use that port. The shipped defaults are
`gpt-image-1.5` for images and `gpt-5.4` for workshop text/vision. Model access
depends on your API account. Set `EASEL_IMAGE_MODEL` or `EASEL_TEXT_MODEL` in
this file if using another supported model; the workshop model needs image
input support for image-to-prompt requests. Save and close Notepad.

Only run `Copy-Item` on first setup; it would replace an existing `.env`.
Restart the app after changing this file.

## 3. Check and start the app

```powershell
npm run typecheck
npm start
```

Leave this terminal open. When it prints its listening address, open
**http://127.0.0.1:5178** in your browser. You should see the gallery, initially
empty. Do not click Generate until you intend to make a paid API call.

For a read-only check, open a second PowerShell window in this same folder:

```powershell
Invoke-RestMethod http://127.0.0.1:5178/api/presets | ConvertTo-Json -Depth 5
```

An empty list is normal on a new installation. A JSON response confirms that
the app and local database started. To stop it, return to its terminal and
press Ctrl+C. Next time, open this folder and run `npm start` again.

## 4. Make your first image

In the browser, enter a short test prompt, choose one image, and use the
Generate action. This makes an OpenAI API request. Wait for the result and open
the image to review its saved prompt/settings. Presets and the workshop are
optional; start with a plain prompt so account/model problems are easier to see.

## Configuration and optional LAN access

| Setting | Purpose |
|---|---|
| `OPENAI_API_KEY` | Your private API credential. |
| `EASEL_IMAGE_MODEL` | Image model; shipped default `gpt-image-1.5`. |
| `EASEL_TEXT_MODEL` | Workshop model; shipped default `gpt-5.4`. |
| `PORT` | HTTP port; default 5178. |
| `HOST` | Use `127.0.0.1` for access on this computer only. |

For access from another device on your trusted LAN, change `HOST=0.0.0.0`,
restart the app and use your computer's LAN address with port 5178. The app has
no login gate; anyone who can reach it can use its API and your configured
generation account. Keep it on loopback unless you deliberately want LAN access.
This guide does not expose it to the public internet.

## Your data and backups

The app creates `data/` when it starts:

```text
data/
  easel.db          SQLite database: presets, tasks, variants and folders
  images/          Generated image files
  refs/            Uploaded reference images
```

Stop the app before copying `data/` for a simple consistent backup. Keep your
private `.env` separately. The shared starter contains neither your key nor
your image library. `npm run dev` is an optional auto-reloading development mode.

## REST API reference

All routes are on the same origin as the browser app:

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/tasks` and `/api/tasks/:id` | List or inspect generated work. |
| PATCH | `/api/tasks/:id` | Favorite, trash or move a task. |
| POST | `/api/generate` | Generate with `prompt`, optional presets/reference, `aspect`, `n`, `quality` and folder. |
| GET / POST | `/api/presets` | Read or create presets. |
| PATCH | `/api/presets/:id` | Edit or archive a preset. |
| GET / POST | `/api/folders` | Read or create folders. |
| POST | `/api/upload` | Upload a reference as multipart `file`. |
| POST | `/api/workshop/from-image` | Create a prompt from multipart `file`. |
| POST | `/api/workshop/brainstorm` | Generate prompt options from JSON `dump`. |

## If something goes wrong

- **`npm.ps1` is blocked:** use `npm.cmd`; no PowerShell policy change is needed.
- **`better-sqlite3` / native module error during install:** read the installation
  error. Use the supported Node version with a matching native build; Node 22
  is also supported by this dependency. If npm must compile it, install the
  C++ build tools it requests, then rerun installation.
- **`EADDRINUSE`:** another app uses port 5178. Stop that app or set `PORT=5179`
  in `.env`, restart, and open the new port.
- **401 from OpenAI:** check the key in this package's `.env` and restart.
- **Model/permission/quota error:** check your API account's model access and
  billing. A working gallery does not verify generation access.
- **Page will not open:** keep the server terminal running and check its printed
  port. Use `http`, not `https`, for this local address.

## License and upstream attribution

Easel AI is by Codependent AI. See the
[upstream project](https://github.com/codependentai/easel-ai),
[LICENSE](LICENSE) and [NOTICE](NOTICE). This guide describes the included copy.

## Reading the code

Start with [src/main.ts](src/main.ts), then [src/server.ts](src/server.ts), then [src/db.ts](src/db.ts). The installation steps above describe the runtime configuration.
