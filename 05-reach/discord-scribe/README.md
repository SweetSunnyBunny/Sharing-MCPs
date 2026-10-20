# Discord Scribe

Record a Discord voice channel, transcribe it locally with Whisper, and post notes to a text channel. At the end of a session it can generate a summary through your installed, authenticated Claude Code CLI. **This is a Discord bot, not an MCP server:** you use Discord slash commands and do not add it to `mcpServers`.

These instructions use Windows PowerShell, Node.js 24, and Python 3.11 (64-bit). You need a Discord account with permission to add a bot and manage a test server. The bot code can also run on other systems with adjusted paths and working native audio dependencies.

## 1. Install the prerequisites and libraries

Install [Node.js 24](https://nodejs.org/en/download) and [Python 3.11](https://www.python.org/downloads/), including the Python launcher. Reopen PowerShell afterward. Extract the collection to `C:\MCP-Starter\Sharing-MCPs`; replace this example path throughout if you chose another folder. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\discord-scribe"
node --version
npm.cmd --version
py -3.11 --version
npm.cmd ci
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`npm.cmd` avoids PowerShell's script execution-policy issue with `npm.ps1`. Using the virtual environment's Python directly means no activation script is needed.

## 2. Create your Discord bot and invite it

1. Open the [Discord Developer Portal](https://discord.com/developers/applications). Create an application with your own name. Copy its **Application ID** from General Information.
2. Open its **Bot** settings, create the bot if needed, and generate/reset its token. Save that token locally for the next step. Enable the **Server Members Intent**, because this bot requests that intent. Message Content Intent is not used.
3. In the application's OAuth2 URL Generator, select **bot** and **applications.commands**. Select the permissions **View Channels, Connect, Speak, Send Messages, Read Message History, Embed Links, and Attach Files**. Open the generated invite link, choose your test server, and authorize the bot.
4. In the Discord app, enable **User Settings → Advanced → Developer Mode**. Right-click your test server and choose **Copy Server ID**; right-click the text channel for notes and choose **Copy Channel ID**. The bot must have permission to view/send in that text channel and connect to your voice channel, including any channel-specific overrides.

Discord's [application setup guide](https://docs.discord.com/developers/quick-start/getting-started) explains the portal workflow. Use a dedicated bot application: the command registration script replaces that application's command list in the selected server/global scope.

## 3. Create your local configuration

For a fresh installation, copy the example and open it in Notepad:

```powershell
Copy-Item -LiteralPath .\.env.example -Destination .\.env
notepad .\.env
```

On an existing installation, edit `.env` directly so you do not overwrite your settings. Replace the empty values for these fields with your own values:

```dotenv
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_APP_ID=your_application_id
DISCORD_GUILD_ID=your_test_server_id
NOTES_CHANNEL_ID=your_notes_text_channel_id
PYTHON_PATH=C:/MCP-Starter/Sharing-MCPs/discord-scribe/.venv/Scripts/python.exe
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
CHUNK_INTERVAL_SECONDS=180
```

Save the file. The words beginning with `your_` are placeholders, not usable credentials. Keep `.env` private. Leave `NOTES_WEBHOOK_URL` empty for the first setup; the bot will post using its own account. `DISCORD_GUILD_ID` makes command registration target your test server; leaving it empty registers commands globally, which may take longer to appear.

Download the chosen public Whisper model once before starting the bot:

```powershell
.\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
```

Wait for this command to finish. It needs internet access for the first download and sufficient disk space for the model cache. It performs no recording. Using the same model/device settings in `.env` avoids doing that download during a transcription timeout. This package currently transcribes **English**. CPU/int8 avoids requiring NVIDIA CUDA; GPU setup is an advanced option. The included WAV pipeline does not require a separate FFmpeg executable.

## 4. Set up summaries, if you want them

Install the native Windows Claude Code CLI using its [official installation instructions](https://code.claude.com/docs/en/setup). Reopen PowerShell, then check and sign in:

```powershell
claude --version
claude
```

Complete the CLI's login with an account that has Claude Code access, then exit that session. Use this command to find the executable:

```powershell
(Get-Command claude.exe).Source
```

Copy the printed path into `CLAUDE_CMD` in `.env`; you may replace backslashes with forward slashes. Use the native **`claude.exe`**, not an npm `.cmd` shim, because the bot launches it directly. Choose a `CLAUDE_MODEL` your account supports. No account credentials are included in this folder.

Transcription and notes work without this step. The bot attempts a summary when `/scribe stop` has collected text; if the CLI is unavailable or not authenticated, it posts a summary-failed message while keeping the chunk notes. There is currently no separate setting to disable that summary attempt. Summary generation sends the collected transcript through your configured Claude Code account and uses that account's access/usage.

## 5. Register commands, build, and start

Run these commands from the **discord-scribe folder**, where `.env` is saved:

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\discord-scribe"
npm.cmd run register
npm.cmd run build
npm.cmd start
```

Registration should report that commands were registered and `Done!`; a successful build exits without TypeScript errors. Startup should show `[Scribe] Logged in as ...` and `[Scribe] Ready to record. Use /scribe start in a voice channel.` Keep this terminal open while the bot is running. Ctrl+C shuts it down.

Unlike a stdio MCP process, this bot should show the startup messages and appear online in Discord. There is no browser page or local MCP URL to open.

## 6. Try one short recording

1. Join a regular voice channel in the test server. Let everyone in that channel know you are testing recording and transcription.
2. As a server administrator or member with **Moderate Members** permission, type **`/scribe start`** in a text channel. You can choose `notes_channel` to override the default notes destination.
3. Speak a short test sentence. Use **`/scribe status`** to confirm the session is active.
4. Use **`/scribe stop`**. The final chunk is transcribed, so you do not have to wait the full three-minute interval. Confirm the test sentence appears in your notes channel and, if configured, a summary follows.

A startup login proves Discord authentication; the short test above separately verifies voice capture, Python transcription, and posting permissions. The current setup has not been tested against your account or microphone just by installing libraries.

## If something goes wrong

- **`node`, `npm`, or `py` is not recognized:** finish installing the prerequisites, then open a new PowerShell window. Use `npm.cmd`, not `npm.ps1`.
- **`npm ci` reports native compilation errors:** the audio libraries need a compatible native build. Check that Node is 64-bit and use the documented version; if the error specifically requires `node-gyp`/MSVC, install Visual Studio Build Tools with Desktop development with C++ and retry.
- **Missing environment variable:** run in this package's folder and check the file is named `.env`, not `.env.txt`. Supply the real bot token and application ID, not the example words.
- **Disallowed intents / bot disconnects:** enable Server Members Intent for this same bot in the Developer Portal. Restart afterward.
- **Slash command missing:** confirm the invite included `applications.commands`, the configured server ID is correct, and `npm.cmd run register` succeeded. Check your Moderate Members/admin permission. Re-register after editing the application or server ID.
- **Bot cannot join/post:** inspect the voice and text channel permission overrides, including View Channel, Connect, Send Messages, Embed Links, and Attach Files.
- **No text or Python process error:** confirm `PYTHON_PATH` points to this package's `.venv\Scripts\python.exe`, rerun the model download command, and check the terminal for transcription errors. Speak audibly in English and use a regular voice channel for the first test.
- **Transcription times out:** start with `tiny`, `base`, or `small` on CPU; download the matching model before recording. A slow computer may need a shorter chunk interval.
- **Summary fails:** run Claude Code manually to confirm login and model access, and set `CLAUDE_CMD` to the native executable. Existing chunk notes remain available.

## What lives where

`src/` contains the bot, recorder, transcription adapter, and command registration. `npm.cmd run build` creates `dist/`; `npm.cmd ci` recreates `node_modules/`. `.venv/`, `.env`, model downloads, tokens, recordings, and account authentication are not supplied.

The process buffers audio and notes during a recording. Temporary WAV files are written under the system temporary directory's `discord-scribe` folder and removed after transcription. Notes and transcript attachments are posted to your configured Discord channel or optional webhook. Save any notes you need before removing that destination.

## Reading the code

Start with [src/index.ts](src/index.ts), then [src/recorder.ts](src/recorder.ts), then [src/transcriber.ts](src/transcriber.ts). The installation steps above describe the runtime configuration.
