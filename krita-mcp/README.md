# Krita MCP Server

Let Claude draw and paint in Krita! This MCP bridges Claude and Krita's painting capabilities through a local HTTP plugin.

## How It Works

1. **Krita Plugin** - A Python plugin runs inside Krita, listening for commands on port 5678
2. **MCP Server** - This server receives tool calls from Claude and forwards them to the plugin
3. **Claude** - Can now create canvases, draw shapes, paint strokes, manage layers, and more!

## Requirements

- [Krita](https://krita.org) (free and open source)
- Python 3.8+

---

## Quick Start

### Step 1: Install the Krita Plugin

1. Find your Krita resources folder:
   - **Windows:** `%APPDATA%\krita\pykrita\`
   - **Mac:** `~/Library/Application Support/krita/pykrita/`
   - **Linux:** `~/.local/share/krita/pykrita/`

2. Copy the plugin files:
   - Copy `plugin/krita_mcp_plugin.desktop` to the pykrita folder
   - Copy the entire `plugin/krita_mcp_plugin/` folder to the pykrita folder

3. Enable the plugin in Krita:
   - Open Krita
   - Go to **Settings > Configure Krita > Python Plugin Manager**
   - Check **Krita MCP Plugin**
   - Restart Krita

### Step 2: Install MCP Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Run the MCP Server

```bash
python run_server.py
```

The server runs on `http://localhost:8080/mcp`

### Step 4: Connect Claude

Add to your MCP settings:

```json
{
  "mcpServers": {
    "krita": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

---

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

## Remote Access (Optional)

To use Krita from Claude on your phone or other devices, set up a Cloudflare Tunnel:

1. Get a domain (~$5/year from Cloudflare)
2. Install cloudflared
3. Create a tunnel pointing to `http://localhost:8080`
4. Update your MCP settings to use the tunnel URL

See the Filesystem MCP README for detailed tunnel setup instructions.

---

## Troubleshooting

### "Cannot connect to Krita"
- Make sure Krita is running
- Check that the plugin is enabled (Settings > Configure Krita > Python Plugin Manager)
- Restart Krita after enabling the plugin

### Plugin doesn't appear in Krita
- Verify the plugin files are in the correct location
- Check the folder structure: `pykrita/krita_mcp_plugin/__init__.py`
- The `.desktop` file must be directly in `pykrita/`

### Commands timeout
- Some operations take time (especially filters)
- Try simpler operations first
- Check Krita's Python scripting console for errors

### Colors look wrong
- Krita uses BGRA format internally
- The plugin handles conversion automatically
- If issues persist, check your document color space

---

## How Claude Uses This

When you ask Claude to draw something, it:

1. Creates a canvas with appropriate dimensions
2. Plans out layers for different elements
3. Sets colors and brushes
4. Draws shapes, strokes, and fills
5. Applies filters and transforms as needed
6. Exports the final image

Claude can see the exported images to verify the result!

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
