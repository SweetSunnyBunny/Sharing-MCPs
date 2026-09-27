# Audio and video MCP

Analyze your own audio files, create spectrograms, and turn local videos or supported HTTP(S) video links into frame contact sheets. Optional Groq transcription adds speech text. The package is standalone: it does not need the collection's UI or any private setup. No sample media, browser profiles or account credentials are included.

Required: Python 3.11, the libraries in `requirements.txt`, FFmpeg and ffprobe. Optional: your own Groq account/key for transcription; a separate Chrome profile for videos that require your login. Local visual analysis uses no remote model.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\audio-visualizer`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\audio-visualizer"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Install FFmpeg and choose a sample

Install an FFmpeg build from [ffmpeg.org](https://ffmpeg.org/download.html). On Windows, extract it and add its `bin` directory to your user **Path** in **Edit environment variables for your account**. Open a new PowerShell window, return to this package folder, and check:

```powershell
ffmpeg -version
ffprobe -version
```

Put a short WAV/MP3 or video you own in a folder such as `C:\Media`. Tools need the full path to the file on this computer.

Local visual analysis needs no API key. `AUDIO_MCP_OUTPUT_DIR` optionally changes the output folder; otherwise generated artifacts go into this package's `output` folder. This package does not load a `.env` file automatically. Keep the output folder private: it can contain downloaded media, extracted audio, frames and transcripts.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\audio_mcp_server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "audio-visualizer": {
      "command": "C:/MCP-Starter/Sharing-MCPs/audio-visualizer/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/audio-visualizer/audio_mcp_server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client: `Use audio_info on C:/Media/sample.wav.` A successful result shows duration/format. Then try `audio_visualize` and open the returned PNG path. For video, use `video_watch` with a small frame count first.

The client should list **nine tools**:

| Tool | What to supply | Result |
|---|---|---|
| `audio_info` | `path` to a local audio file | Duration, format and stream metadata |
| `audio_tempo_key` | Local `path` | Estimated tempo/key and onset density |
| `audio_visualize` | Local `path` | Waveform, spectrogram and chromagram image |
| `audio_analyze` | Local `path` | Features, notes, MIDI and image/JSON artifacts |
| `audio_review` | Local `path` | Analysis plus a prompt for the client's musical review |
| `video_watch` | Local path or HTTP(S) URL in `path`; optional `frames` | Frames and optional transcript; original interface retained |
| `watch_video` | Local path or HTTP(S) URL in `url`; optional `max_frames` | Alias for the same combined analysis |
| `video_see` | Local path or HTTP(S) URL in `url`; optional `max_frames` | Frames only; no transcription call |
| `video_listen` | Local path or HTTP(S) URL in `url` | Transcript only; URLs download audio only |

Video tools accept an optional `identity` label, defaulting to `default`. That label only selects a browser profile you explicitly configure below. Frame counts are limited to 4–24. Transcription covers at most the first 10 minutes and returns at most 6,000 characters. Local `audio_*` tools still require file paths; URL support belongs to the video tools.

## 5. Optional: use video links

`yt-dlp` is installed by `requirements.txt`; no extra server is needed. Check that this environment has it:

```powershell
.\.venv\Scripts\python.exe -m yt_dlp --version
```

Ask the client to run `video_see` on a supported video URL you are allowed to access. Success returns a contact-sheet image path. By default the downloader uses **no browser cookies**, ignores user-wide yt-dlp configuration, and downloads one video rather than a playlist. Website support depends on yt-dlp; an ordinary HTML page is not necessarily a downloadable video. Downloads and analyses are cached under `output`; different configured profile labels use separate download cache keys.

## 6. Optional: enable speech transcription

Create your own API key in the [Groq console](https://console.groq.com/keys). Transcription sends extracted audio to that service and uses your account. In the client JSON, add an `env` object inside `audio-visualizer`, beside `command` and `args`:

```json
{
  "GROQ_API_KEY": "REPLACE_WITH_YOUR_OWN_KEY",
  "GROQ_WHISPER_MODEL": "whisper-large-v3-turbo"
}
```

The block above is the **value of `env`**, not a replacement for the full client configuration. Save and restart the client. Ask for `video_listen` on a short local video with speech. Success includes `AUDIO TRANSCRIPT`. Missing keys or failed transcription appear as `AUDIO STATUS`; they are not transcripts. A later call can retry after you fix the key or connection. Existing successful analyses can be served from cache.

For a manual PowerShell process, set `$env:GROQ_API_KEY = "REPLACE_WITH_YOUR_OWN_KEY"` in that same terminal before starting it. Keep real keys in your private client configuration; do not add them to shared files.

## 7. Optional: use your own Chrome login

Skip this for public videos. The package does not search your normal browser profile. Make a separate Chrome user-data folder, open it yourself, and sign into the media website you want to use. For example, if Chrome is installed at the standard system location:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\BrowserProfiles\Research"
```

If Chrome is installed elsewhere, use its actual executable path. After signing in, close that Chrome window. Copy the generic mapping, then edit it to the existing profile directory (usually the `Default` subfolder):

```powershell
Copy-Item .\browser-profiles.example.json .\browser-profiles.local.json
notepad .\browser-profiles.local.json
```

The example maps the generic label `research` to `C:/BrowserProfiles/Research/Default`. Add `AUDIO_CHROME_PROFILES_FILE` to the client entry's `env` object with the **absolute path** to your `browser-profiles.local.json`, then restart the client. Call a video tool with `identity: "research"`. It tries that profile's Chrome cookies, then retries without cookies if necessary. Unknown labels fail with a configuration message. The `default` label remains cookie-free unless you explicitly add it to your mapping.

Browser cookie encryption or a locked profile can prevent extraction. Use a local downloaded file if your browser/website combination is unsupported. Never share a logged-in profile, exported cookies, or your local mapping.

## 8. Optional: run a local HTTP server

Use this only if your client supports a local Streamable HTTP MCP endpoint. Keep the PowerShell terminal open:

```powershell
.\.venv\Scripts\python.exe .\audio_mcp_server.py --transport http --port 8813
```

The startup message should show `http://127.0.0.1:8813/mcp`. Configure that URL as a Streamable HTTP server in your client, then check that the nine tools are listed. This is an MCP endpoint, not a normal web page. The server is bound to this computer's loopback interface and has no authentication; do not expose it through a tunnel or reverse proxy. Other computers and hosted clients cannot use `127.0.0.1` to reach your PC. Stdio remains the default and needs no listening port.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **FFmpeg/ffprobe not found:** restart PowerShell and the AI client after changing Path.
- **No transcript:** local visuals work without a Groq key; a remote transcription failure does not mean frame extraction failed.
- **File not found:** pass an existing absolute media path, not a browser upload name.
- **Video link fails:** update yt-dlp with `.\.venv\Scripts\python.exe -m pip install --upgrade yt-dlp`. If its error requests a JavaScript runtime, follow [yt-dlp's runtime instructions](https://github.com/yt-dlp/yt-dlp/wiki/EJS) for your machine. Some sites require login or block downloads; try a local file to separate that problem from analysis.
- **Browser profile missing:** the mapping must contain your requested label and an existing absolute directory, and its filename must be supplied in `AUDIO_CHROME_PROFILES_FILE`.
- **Port already in use:** stop the previous manual server with Ctrl+C or choose another `--port`, then update the client's URL.

## Offline developer checks

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

These tests mock media processes and transcription. They check tool registration, compatibility, URL/profile handling, cache retries and transport selection without contacting websites, reading a real browser profile, or charging an API account.

## Standalone conversion

You can create a sound image without an MCP client:

```powershell
.\.venv\Scripts\python.exe .\sound_to_image.py visualize "C:\Media\sample.wav"
```

The command reports the generated image path. Long media files take longer to process.

## Understanding the Output

### Waveform (Top)
- **X-axis**: Time
- **Y-axis**: Amplitude (loudness)
- **Use**: See rhythm, beats, quiet/loud sections

### Mel Spectrogram (Middle)
- **X-axis**: Time
- **Y-axis**: Frequency (pitch) - logarithmic like human hearing
- **Color**: Intensity (bright = loud)
- **Use**: See instruments, vocals, bass, treble content

### Chromagram (Bottom)
- **X-axis**: Time
- **Y-axis**: The 12 musical notes (C, C#, D, etc.)
- **Color**: Note intensity
- **Use**: See chords, key changes, harmonic content

---

## Supported Formats

Any format supported by librosa/ffmpeg:
- MP3
- WAV
- FLAC
- OGG
- M4A
- AAC
- And many more

---
