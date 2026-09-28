# Photos MCP

Generate an image, save it where your app can display it, and optionally copy images into your own photo-frame folder. This is a standalone local MCP server with three tools. It works with World Feed's `photo_generate` calls and does not need Discord or a household setup.

You need **Python 3.11**, the libraries below, and a local MCP client. Image generation also needs your own OpenAI API key and access to your chosen image model. Frame copying/listing works without an API key. No images, real keys, logged-in accounts or frame folders are included.

## 1. Install Python and the package

These commands use Windows PowerShell. Install Python 3.11 from [python.org](https://www.python.org/downloads/) with the Python launcher enabled, then reopen PowerShell. Extract the collection first; do not run from inside the ZIP.

The example location is `C:\MCP-Starter\Sharing-MCPs`. If you chose another folder, replace it in the commands and configuration examples.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\photos-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use that `.venv` Python directly; you do not need to activate it or change PowerShell's execution policy. The Python code uses portable paths, but the commands here are for Windows; on macOS/Linux use your Python 3.11 command and `.venv/bin/python` with your own absolute paths.

## 2. Run the offline check first

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\server.py --check-config
```

The tests should end with `OK`. They use tiny generated test fixtures and mocked provider responses; they do not access accounts, personal images or an actual display. The configuration check prints readiness and output directories, never the key. `missing or placeholder` is expected before configuration. Frame should say `disabled (optional)`.

## 3. Choose your model and destinations

Create an API key in your own [OpenAI API project](https://platform.openai.com/api-keys). Set a model your project can use; `gpt-image-2.5-flare` is the included example. The model is an explicit setting, so you can change it without editing Python. Supported API options and account requirements are described in the [official image generation guide](https://developers.openai.com/api/docs/guides/image-generation).

| Environment variable | Meaning |
|---|---|
| `OPENAI_API_KEY` | Your own API key; required for generation |
| `OPENAI_IMAGE_MODEL` | Your chosen GPT Image model; required for generation |
| `PHOTOS_CHAT_DIR` | Where `to="chat"` saves; defaults to this package's `output/images` |
| `PHOTOS_FILE_DIR` | Where `to="file"` saves; defaults to this package's `output/generated` |
| `PHOTOS_FRAME_DIR` | Optional folder for your display/sync software; disabled until set |

Relative directory values resolve from **this package's folder**, regardless of the client's working directory. Absolute paths point to the location you supply. This package never searches another app's `.env`, browser profiles or account files. [.env.example](.env.example) documents the settings; it is **not automatically loaded**. Put settings in the MCP client's `env` object below, or in the terminal that starts the server.

For World Feed, `PHOTOS_CHAT_DIR` must be the running app's actual **data/images** folder. The full UI defaults to `Sharing-MCPs/ui/data/images`; a separate World Feed installation may use a different data folder. Use an absolute path matching that app's image directory. A mismatch causes World Feed to reject the receipt even if an image was generated.

## 4. Connect your client or World Feed app

Start from [mcp-servers.example.json](mcp-servers.example.json). Edit the paths and replace the key placeholder in your **private** configuration. The example is:

```json
{
  "mcpServers": {
    "photos": {
      "command": "C:/MCP-Starter/Sharing-MCPs/photos-mcp/.venv/Scripts/python.exe",
      "args": ["C:/MCP-Starter/Sharing-MCPs/photos-mcp/server.py"],
      "env": {
        "OPENAI_API_KEY": "REPLACE_WITH_YOUR_OWN_API_KEY",
        "OPENAI_IMAGE_MODEL": "gpt-image-2.5-flare",
        "PHOTOS_CHAT_DIR": "C:/MCP-Starter/Sharing-MCPs/ui/data/images"
      }
    }
  }
}
```

For the collection's UI/World Feed app, merge the `photos` entry into its private `mcp-servers.json`. If its configuration has an `enabledServers` list, include `photos` there too. Keep existing servers. For Claude Desktop, use **Settings → Developer → Edit Config**, then merge the entry into `mcpServers`. Other local clients have their own MCP configuration location.

Save and restart your app/client so it starts the new subprocess. The available tools should include:

- `photo_generate(prompt, to, subject, identity, size, quality, reference_paths)` — generation or reference-based editing, saved locally.
- `photo_to_frame(path, subject, identity)` — copies an existing local image into your configured frame folder.
- `photo_frame_list(limit)` — lists images in that folder.

This is a **stdio** server, not an HTTP endpoint. Your client starts and communicates with Python. If you start `server.py` manually, a quiet terminal waiting for messages is normal; stop it with Ctrl+C. No live generation happens at startup.

## 5. Make one test image

This step makes **one billable request to your OpenAI API account**. It sends your prompt and, if supplied, reference images to the provider. Before trying automatic World Feed photos, use the client to call:

```json
{
  "prompt": "A small paper boat on a calm blue pond, simple storybook illustration",
  "to": "chat",
  "subject": "FirstBoatTest",
  "identity": "Demo",
  "size": "square",
  "quality": "low"
}
```

A successful result begins `Saved (… KB):` followed by an absolute PNG path. Open that path to inspect the image. A refusal or provider error is not a saved image. Existing identity/subject/date filenames are not overwritten; choose a new subject for a different image.

For World Feed, enable/configure its photo feature in the app only after its text generation and this Photos connection work. World Feed supplies `identity="Worldfeed"` and its job ID as the subject. The returned file stays in the configured image directory as `Worldfeed_<job-id>_YYYY-MM-DD.png`, so the app can validate and display it. This Photos package itself does not schedule or publish posts.

## 6. Optional: reference images or a frame folder

To carry appearance into a new scene, supply `reference_paths` containing local PNG/JPEG/WebP paths. Maximum: four images, 10 MB each and 20 MB combined. The image bytes are uploaded for editing; local directory names are not sent as upload filenames. Output is always validated PNG. `size` accepts `square`, `landscape` or `portrait`. `quality` accepts `low`, `medium`, `high`, `xhigh`, `max` or `auto`; your chosen model must support the value. Older models may not accept the highest levels.

Frame tools are optional. Add, for example, `"PHOTOS_FRAME_DIR": "C:/PhotoFrame/Images"` to the private client `env` object and restart it. Use a folder you deliberately want your display software to read. Then call `photo_to_frame` with an existing image path and a unique subject; `photo_frame_list` confirms the file is in that folder.

Copying a file does not set up a display, cloud sync or device account. Configure those separately with your own software. Inspect an image before copying it into a folder that syncs elsewhere.

## Common fixes

- **`py` is not recognized:** install the Python launcher, reopen PowerShell and check `py -3.11 --version`.
- **Missing Python module:** rerun the requirements command using this package's `.venv` Python. The client must use that same executable.
- **Tools absent:** check absolute `command`/`args` paths, valid JSON and any `enabledServers` restriction, then restart the app.
- **Key/model missing:** add both settings to the actual client's `env` entry. Editing `.env.example` alone does nothing.
- **HTTP 401/403:** check your key, project permissions and model access. **HTTP 429:** check account limits and available usage. **HTTP 400:** check model, prompt, size, quality and reference support. The server keeps raw provider diagnostics out of tool results.
- **Unexpected destination in World Feed:** set `PHOTOS_CHAT_DIR` to that running app's exact image directory and restart the Photos subprocess. Inspect the existing saved image before generating again.
- **Timeout or save failure:** the request may already have completed at the provider. Check your account and destination before retrying; the server does not automatically repeat ambiguous requests.
- **Frame is disabled:** set `PHOTOS_FRAME_DIR` only if you intend to use it; ordinary chat/World Feed images do not require a frame.

Keep your real MCP configuration, generated output and account information private. Only generic examples are included in this package.
