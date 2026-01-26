"""
Krita MCP Server - Standalone Edition

Let Claude draw and paint in Krita! This MCP bridges Claude and Krita's
painting capabilities through a local HTTP plugin.

Requirements:
    1. Krita (free at krita.org)
    2. The Krita MCP Plugin (included in plugin/ folder)
    3. Enable the plugin in Krita: Settings > Configure Krita > Python Plugin Manager

Built with love for sharing.
"""

from fastmcp import FastMCP
import httpx
import os
from typing import Optional

# Configuration - Krita plugin listens on this port
KRITA_URL = os.environ.get("KRITA_URL", "http://localhost:5678")

mcp = FastMCP("krita-mcp")


def send_command(action: str, params: dict = None, timeout: float = 30.0) -> dict:
    """Send command to Krita plugin."""
    if params is None:
        params = {}

    try:
        response = httpx.post(
            KRITA_URL,
            json={"action": action, "params": params},
            timeout=timeout
        )
        return response.json()
    except httpx.ConnectError:
        return {"error": "Cannot connect to Krita. Is Krita running with the MCP plugin enabled?"}
    except httpx.TimeoutException:
        return {"error": f"Operation timed out. Try krita_export() for saving."}
    except Exception as e:
        return {"error": str(e)}


# ============ CONNECTION ============

@mcp.tool()
def krita_health() -> str:
    """Check if Krita is running and the MCP plugin is active."""
    try:
        response = httpx.get(f"{KRITA_URL}/health", timeout=5.0)
        data = response.json()
        return f"Krita is running. Plugin: {data.get('plugin', 'unknown')}"
    except:
        return "Cannot connect to Krita. Make sure Krita is running with the MCP plugin enabled."


# ============ CANVAS ============

@mcp.tool()
def krita_new_canvas(
    width: int = 800,
    height: int = 600,
    name: str = "New Canvas",
    background: str = "#1a1a2e"
) -> str:
    """
    Create a new canvas in Krita.

    Args:
        width: Canvas width in pixels
        height: Canvas height in pixels
        name: Document name
        background: Background color as hex
    """
    result = send_command("new_canvas", {
        "width": width, "height": height,
        "name": name, "background": background
    })
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created canvas: {width}x{height}"


@mcp.tool()
def krita_clear(color: str = "#1a1a2e") -> str:
    """Clear the canvas to a solid color."""
    result = send_command("clear", {"color": color})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas cleared to {color}"


@mcp.tool()
def krita_get_document_info() -> str:
    """Get information about the current document."""
    result = send_command("get_document_info", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Document: {result.get('name')}\nSize: {result.get('width')}x{result.get('height')}\nActive Layer: {result.get('activeLayer')}"


# ============ COLORS & BRUSHES ============

@mcp.tool()
def krita_set_color(color: str) -> str:
    """Set the foreground (paint) color. Args: color - Hex code like '#ff6b6b'"""
    result = send_command("set_color", {"color": color})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Color set to {color}"


@mcp.tool()
def krita_set_brush(
    preset: Optional[str] = None,
    size: Optional[int] = None,
    opacity: Optional[float] = None
) -> str:
    """Set brush preset and properties."""
    params = {}
    if preset:
        params["preset"] = preset
    if size:
        params["size"] = size
    if opacity is not None:
        params["opacity"] = opacity
    result = send_command("set_brush", params)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Brush set"


@mcp.tool()
def krita_list_brushes(filter: str = "", limit: int = 20) -> str:
    """List available brush presets."""
    result = send_command("list_brushes", {"filter": filter, "limit": limit})
    if "error" in result:
        return f"Error: {result['error']}"
    brushes = result.get("brushes", [])
    return f"Brushes: {', '.join(brushes[:limit])}"


@mcp.tool()
def krita_get_color_at(x: int, y: int) -> str:
    """Sample the color at a pixel (eyedropper)."""
    result = send_command("get_color_at", {"x": x, "y": y})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Color at ({x},{y}): {result.get('color')}"


# ============ DRAWING ============

@mcp.tool()
def krita_stroke(points: list[list[int]], pressure: float = 1.0) -> str:
    """
    Paint a stroke through points.
    Args:
        points: List of [x, y] coordinates, e.g., [[100,100], [150,120], [200,150]]
        pressure: Brush pressure 0.0-1.0
    """
    if len(points) < 2:
        return "Error: Need at least 2 points"
    result = send_command("stroke", {"points": points, "pressure": pressure})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Stroke painted with {len(points)} points"


@mcp.tool()
def krita_draw_shape(
    shape: str,
    x: int, y: int,
    width: int = 100, height: int = 100,
    fill: bool = True, stroke: bool = False,
    x2: Optional[int] = None, y2: Optional[int] = None
) -> str:
    """Draw a shape: 'rectangle', 'ellipse', or 'line'."""
    params = {"shape": shape, "x": x, "y": y, "width": width, "height": height, "fill": fill, "stroke": stroke}
    if x2 is not None:
        params["x2"] = x2
    if y2 is not None:
        params["y2"] = y2
    result = send_command("draw_shape", params)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Drew {shape}"


@mcp.tool()
def krita_fill(x: int, y: int, radius: int = 50) -> str:
    """Fill an area with current color (paints a circle)."""
    result = send_command("fill", {"x": x, "y": y, "radius": radius})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Filled at ({x},{y})"


@mcp.tool()
def krita_flood_fill(x: int, y: int, tolerance: int = 20) -> str:
    """Flood fill (bucket tool) at a point."""
    result = send_command("flood_fill", {"x": x, "y": y, "tolerance": tolerance})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Flood filled at ({x},{y})"


@mcp.tool()
def krita_gradient(
    x1: int, y1: int, x2: int, y2: int,
    color1: str = "#000000", color2: str = "#ffffff",
    gradient_type: str = "linear"
) -> str:
    """Draw a gradient between two points."""
    result = send_command("gradient", {
        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "color1": color1, "color2": color2, "type": gradient_type
    })
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Drew {gradient_type} gradient"


@mcp.tool()
def krita_text(
    text: str, x: int, y: int,
    font_size: int = 24, color: str = "#ffffff", font: str = "Arial"
) -> str:
    """Add text to the canvas."""
    result = send_command("text", {
        "text": text, "x": x, "y": y,
        "font_size": font_size, "color": color, "font": font
    })
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Added text: '{text}'"


@mcp.tool()
def krita_bezier_curve(points: list[list[int]], size: int = 3) -> str:
    """Draw a bezier curve through control points."""
    result = send_command("bezier_curve", {"points": points, "size": size})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Drew bezier curve"


# ============ LAYERS ============

@mcp.tool()
def krita_new_layer(name: str = "New Layer") -> str:
    """Create a new paint layer."""
    result = send_command("new_layer", {"name": name})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created layer: {name}"


@mcp.tool()
def krita_list_layers() -> str:
    """List all layers."""
    result = send_command("list_layers", {})
    if "error" in result:
        return f"Error: {result['error']}"
    layers = result.get("layers", [])
    return "\n".join(f"{'  '*l.get('depth',0)}{l['name']}" for l in layers)


@mcp.tool()
def krita_select_layer(name: str) -> str:
    """Select a layer by name."""
    result = send_command("select_layer", {"name": name})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Selected: {name}"


@mcp.tool()
def krita_delete_layer() -> str:
    """Delete the active layer."""
    result = send_command("delete_layer", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Layer deleted"


@mcp.tool()
def krita_set_layer_opacity(opacity: int) -> str:
    """Set layer opacity (0-255)."""
    result = send_command("set_layer_opacity", {"opacity": opacity})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Opacity set to {opacity}"


@mcp.tool()
def krita_duplicate_layer() -> str:
    """Duplicate the active layer."""
    result = send_command("duplicate_layer", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Layer duplicated"


@mcp.tool()
def krita_merge_down() -> str:
    """Merge the active layer down."""
    result = send_command("merge_down", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Merged down"


# ============ SELECTIONS ============

@mcp.tool()
def krita_select_rectangle(x: int, y: int, width: int, height: int) -> str:
    """Create a rectangular selection."""
    result = send_command("select_rectangle", {"x": x, "y": y, "width": width, "height": height})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Selected rectangle"


@mcp.tool()
def krita_select_ellipse(x: int, y: int, width: int, height: int) -> str:
    """Create an elliptical selection."""
    result = send_command("select_ellipse", {"x": x, "y": y, "width": width, "height": height})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Selected ellipse"


@mcp.tool()
def krita_select_all() -> str:
    """Select entire canvas."""
    result = send_command("select_all", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Selected all"


@mcp.tool()
def krita_deselect() -> str:
    """Clear selection."""
    result = send_command("deselect", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Deselected"


@mcp.tool()
def krita_invert_selection() -> str:
    """Invert selection."""
    result = send_command("invert_selection", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Inverted"


# ============ TRANSFORMS & FILTERS ============

@mcp.tool()
def krita_transform(operation: str) -> str:
    """Transform layer: 'flip_h', 'flip_v', 'rotate_cw', 'rotate_ccw'"""
    result = send_command("transform", {"operation": operation})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Applied: {operation}"


@mcp.tool()
def krita_filter(name: str, strength: int = 5) -> str:
    """Apply filter: 'blur', 'sharpen', 'desaturate', 'invert', 'gaussianblur'"""
    result = send_command("filter", {"name": name, "strength": strength})
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Applied: {name}"


@mcp.tool()
def krita_resize_canvas(
    width: Optional[int] = None,
    height: Optional[int] = None,
    anchor: str = "center"
) -> str:
    """Resize canvas. Anchor: topleft, top, topright, left, center, right, etc."""
    params = {"anchor": anchor}
    if width:
        params["width"] = width
    if height:
        params["height"] = height
    result = send_command("resize_canvas", params)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Resized to {result.get('width')}x{result.get('height')}"


@mcp.tool()
def krita_crop_to_selection() -> str:
    """Crop canvas to selection."""
    result = send_command("crop_to_selection", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Cropped"


# ============ FILE OPERATIONS ============

@mcp.tool()
def krita_export(path: str, format: str = "png") -> str:
    """Export to file. Format: png, jpg, webp, bmp, tiff"""
    import os
    path = os.path.abspath(path)
    if not os.path.splitext(path)[1]:
        path = f"{path}.{format}"
    result = send_command("export", {"path": path, "format": format}, timeout=60.0)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Exported to: {path}"


@mcp.tool()
def krita_save() -> str:
    """Save current document."""
    result = send_command("save", {}, timeout=60.0)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Saved"


@mcp.tool()
def krita_save_as(path: str) -> str:
    """Save as .kra file."""
    import os
    path = os.path.abspath(path)
    if not path.lower().endswith('.kra'):
        path = f"{path}.kra"
    result = send_command("save_as", {"path": path}, timeout=60.0)
    if "error" in result:
        return f"Error: {result['error']}"
    return f"Saved as: {path}"


@mcp.tool()
def krita_undo() -> str:
    """Undo last action."""
    result = send_command("undo", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Undone"


@mcp.tool()
def krita_redo() -> str:
    """Redo last undone action."""
    result = send_command("redo", {})
    if "error" in result:
        return f"Error: {result['error']}"
    return "Redone"


# ============ MAIN ============

def main():
    mcp.run()

if __name__ == "__main__":
    main()
