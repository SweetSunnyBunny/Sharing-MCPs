# Krita MCP

Connect an MCP client to a running Krita painting app through the included Python plugin. There are two pieces: a plugin inside Krita on port 5678, and this separate Python MCP process.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\krita-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\krita-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Install the plugin inside Krita

Install [Krita](https://krita.org/en/download/) with Python plugin support. The Python environment above is for the MCP process; Krita uses its own embedded Python for the plugin.

1. Close Krita before copying the plugin.
2. In PowerShell, still inside this package folder, run:

```powershell
$kritaPlugins = Join-Path $env:APPDATA "krita\pykrita"
New-Item -ItemType Directory -Force $kritaPlugins
Copy-Item -LiteralPath .\plugin\krita_mcp_plugin.desktop -Destination $kritaPlugins
Copy-Item -LiteralPath .\plugin\krita_mcp_plugin -Destination $kritaPlugins -Recurse
```

3. Open Krita → **Settings → Configure Krita → Python Plugin Manager**, enable **Krita MCP Plugin**, then restart Krita. Keep Krita open.
4. Wait a few seconds. The plugin starts its command listener automatically. No separate toolbar button is required.

If your Krita resource folder is customized, use **Settings → Manage Resources → Open Resource Folder**, then its `pykrita` directory, instead of the default above. See [Krita's plugin guide](https://docs.krita.org/en/user_manual/python_scripting/install_custom_python_plugin.html). macOS/Linux use their own Krita resource directory; these copy commands are Windows-specific.

The MCP server defaults to `http://localhost:5678`; set `KRITA_URL` in the client `env` only if you deliberately changed the plugin's address. The bundled plugin opens its listener on all interfaces; keep it behind your local firewall, with no public forwarding.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "krita-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/krita-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/krita-mcp/server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `krita_health`. A successful response confirms it reached the plugin. Then create a small blank canvas with `krita_new_canvas` and call `krita_get_document_info`; check the canvas appears in Krita.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **Cannot connect to Krita:** keep Krita open, enable the plugin, restart it, and check that port 5678 is not used by another program.
- **Plugin missing:** the `.desktop` file and `krita_mcp_plugin` directory must sit alongside each other directly inside `pykrita`, without an extra nesting level.
- **No Python Plugin Manager:** this Krita build may not include Python plugin support; use a supported desktop build.
- **Document-dependent tool fails:** open/create a canvas first and close blocking Krita dialogs.

## Available Tools

### Canvas
| Tool | Description |
|------|-------------|
| `krita_health` | Check if Krita is running with the plugin |
| `krita_new_canvas` | Create a new canvas |
| `krita_clear` | Clear canvas to a color |
| `krita_get_document_info` | Get document dimensions and active layer |

### Colors & Brushes
| Tool | Description |
|------|-------------|
| `krita_set_color` | Set foreground paint color |
| `krita_set_brush` | Set brush preset, size, and opacity |
| `krita_list_brushes` | List available brush presets |
| `krita_get_color_at` | Sample color at a pixel (eyedropper) |

### Drawing
| Tool | Description |
|------|-------------|
| `krita_stroke` | Paint a brush stroke through points |
| `krita_draw_shape` | Draw rectangle, ellipse, or line |
| `krita_fill` | Fill an area with current color |
| `krita_flood_fill` | Bucket fill at a point |
| `krita_gradient` | Draw linear or radial gradient |
| `krita_text` | Add text to the canvas |
| `krita_bezier_curve` | Draw a bezier curve |

### Layers
| Tool | Description |
|------|-------------|
| `krita_new_layer` | Create a new paint layer |
| `krita_list_layers` | List all layers |
| `krita_select_layer` | Select layer by name |
| `krita_delete_layer` | Delete active layer |
| `krita_set_layer_opacity` | Set layer opacity (0-255) |
| `krita_duplicate_layer` | Duplicate active layer |
| `krita_merge_down` | Merge layer down |

### Selections
| Tool | Description |
|------|-------------|
| `krita_select_rectangle` | Create rectangular selection |
| `krita_select_ellipse` | Create elliptical selection |
| `krita_select_all` | Select entire canvas |
| `krita_deselect` | Clear selection |
| `krita_invert_selection` | Invert selection |

### Transforms & Filters
| Tool | Description |
|------|-------------|
| `krita_transform` | Flip or rotate layer |
| `krita_filter` | Apply blur, sharpen, desaturate, invert |
| `krita_resize_canvas` | Resize canvas with anchor |
| `krita_crop_to_selection` | Crop to selection |

### File Operations
| Tool | Description |
|------|-------------|
| `krita_export` | Export to PNG, JPG, WEBP, etc. |
| `krita_save` | Save current document |
| `krita_save_as` | Save as .kra file |
| `krita_undo` | Undo last action |
| `krita_redo` | Redo last undone action |

---

## Example Session

```
Claude: Let me create a simple landscape painting.

1. krita_new_canvas(width=1200, height=800, name="Landscape", background="#87CEEB")
2. krita_new_layer(name="Mountains")
3. krita_set_color("#4a5568")
4. krita_draw_shape(shape="polygon", points=[[0,600], [300,300], [600,500], [900,250], [1200,600]])
5. krita_new_layer(name="Sun")
6. krita_set_color("#fbbf24")
7. krita_draw_shape(shape="ellipse", x=900, y=100, width=150, height=150)
8. krita_export(path="landscape.png")
```

---

## Reading the code

Start with [run_server.py](run_server.py), then [server.py](server.py), then [plugin/](plugin/). The installation steps above describe the runtime configuration.
