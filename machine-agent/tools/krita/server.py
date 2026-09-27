"""
Krita MCP Server
Bridge between Claude and Krita painting plugin.
"""

from fastmcp import FastMCP
import httpx
import os
import sys
from typing import Optional

# When machine-agent loads this module via importlib.spec_from_file_location,
# the script's directory is NOT auto-added to sys.path the way it would be for
# `python server.py`. Without this, sibling-file imports fail with
# ModuleNotFoundError. Add the script dir explicitly so _dialog_handling loads
# cleanly under both stdio and importlib launchers.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _dialog_handling import send_command_with_dialog_handling

# Configuration
KRITA_URL = os.environ.get("KRITA_URL", "http://localhost:5678")

mcp = FastMCP("krita-mcp")


def send_command(action: str, params: dict = None, timeout: float = 30.0) -> dict:
    """Send command to Krita plugin and return result.

    Args:
        action: The command action to execute
        params: Parameters for the command
        timeout: Request timeout in seconds (default 30s, use higher for file ops)
    """
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
        return {"error": f"Operation timed out after {timeout}s. If a dialog appeared in Krita, dismiss it and try using krita_export() instead."}
    except Exception as e:
        return {"error": str(e)}


def _get_document_info_raw() -> dict:
    """Fetch document metadata without formatting."""
    return send_command("get_document_info", {})


def _looks_unsaved_document(doc_info: dict) -> bool:
    """Heuristic: Krita new docs often only have a display name, not a real file path."""
    file_name = (doc_info.get("fileName") or "").strip()
    if file_name:
        return False

    name = (doc_info.get("name") or "").strip()
    if not name:
        return True

    _, ext = os.path.splitext(name)
    return ext.lower() not in {".kra", ".ora", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}


@mcp.tool()
def krita_health() -> str:
    """Check if Krita is running and the MCP plugin is active."""
    try:
        response = httpx.get(f"{KRITA_URL}/health", timeout=5.0)
        data = response.json()
        return f"Krita is running. Plugin: {data.get('plugin', 'unknown')}"
    except:
        return "Cannot connect to Krita. Make sure Krita is running with the MCP plugin enabled."


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
        width: Canvas width in pixels (default 800)
        height: Canvas height in pixels (default 600)
        name: Document name
        background: Background color as hex (default dark blue)
    """
    result = send_command("new_canvas", {
        "width": width,
        "height": height,
        "name": name,
        "background": background
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created canvas: {width}x{height}, background: {background}"


@mcp.tool()
def krita_set_color(color: str) -> str:
    """
    Set the foreground (paint) color.

    Args:
        color: Hex color code (e.g., "#ff6b6b", "#b8a9c9")
    """
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
    """
    Set brush preset and properties.

    Args:
        preset: Brush preset name (partial match, e.g., "Basic", "Soft", "Airbrush")
        size: Brush size in pixels
        opacity: Brush opacity (0.0 to 1.0)
    """
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
    return f"Brush set: preset={preset}, size={size}, opacity={opacity}"


@mcp.tool()
def krita_stroke(points: list[list[int]], pressure: float = 1.0) -> str:
    """
    Paint a stroke through a series of points.

    Args:
        points: List of [x, y] coordinate pairs, e.g., [[100, 100], [150, 120], [200, 150]]
        pressure: Brush pressure (0.0 to 1.0, affects stroke thickness/opacity)
    """
    if len(points) < 2:
        return "Error: Need at least 2 points for a stroke"

    result = send_command("stroke", {
        "points": points,
        "pressure": pressure
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Stroke painted with {len(points)} points"


@mcp.tool()
def krita_fill(x: int, y: int, radius: int = 50) -> str:
    """
    Fill an area with current color (paints a filled circle at the point).

    Args:
        x: X coordinate
        y: Y coordinate
        radius: Fill radius in pixels
    """
    result = send_command("fill", {"x": x, "y": y, "radius": radius})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Filled at ({x}, {y}) with radius {radius}"


@mcp.tool()
def krita_draw_shape(
    shape: str,
    x: int,
    y: int,
    width: int = 100,
    height: int = 100,
    fill: bool = True,
    stroke: bool = False,
    x2: Optional[int] = None,
    y2: Optional[int] = None
) -> str:
    """
    Draw a shape on the canvas.

    Args:
        shape: Type of shape - "rectangle", "ellipse", or "line"
        x: X coordinate (top-left for shapes, start point for lines)
        y: Y coordinate (top-left for shapes, start point for lines)
        width: Width of shape (ignored for lines if x2/y2 provided)
        height: Height of shape (ignored for lines if x2/y2 provided)
        fill: Whether to fill the shape
        stroke: Whether to draw outline
        x2: End X for lines (optional)
        y2: End Y for lines (optional)
    """
    params = {
        "shape": shape,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "fill": fill,
        "stroke": stroke
    }
    if x2 is not None:
        params["x2"] = x2
    if y2 is not None:
        params["y2"] = y2

    result = send_command("draw_shape", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Drew {shape} at ({x}, {y})"


@mcp.tool()
def krita_export(
    path: str,
    format: str = "png"
) -> str:
    """
    Export the current canvas to a file.

    This is the safest way to persist work from MCP because it saves directly
    to the specified path without opening native save dialogs.

    Args:
        path: Full file path to save to (e.g., "C:/Art/my_painting.png")
        format: Image format - "png", "jpg", "jpeg", "webp", "bmp", "tiff"
    """
    # Normalize path and ensure extension matches format
    import os
    path = os.path.abspath(path)

    # Add extension if missing
    ext = os.path.splitext(path)[1].lower()
    format_lower = format.lower()
    if not ext:
        path = f"{path}.{format_lower}"
    elif ext[1:] not in ["png", "jpg", "jpeg", "webp", "bmp", "tiff"]:
        path = f"{path}.{format_lower}"

    result = send_command_with_dialog_handling(
        KRITA_URL,
        "export",
        {"path": path, "format": format_lower},
        timeout=60.0,  # Give file ops more time
    )

    if "error" in result:
        return f"Error: {result['error']}"

    msg = f"Exported to: {path}"
    dismissed = result.get("dismissed_dialogs") or []
    if dismissed:
        msg += f" (auto-dismissed: {', '.join(dismissed)})"
    return msg


@mcp.tool()
def krita_save() -> str:
    """
    Save the current document in place.

    Warning:
        This depends on Krita's native document save path and can stall if Krita
        blocks on UI or file handling. Prefer krita_export() for reliable MCP use.

    Use this only when the document already has a real file path and you
    specifically need to update that native document.
    """
    doc_info = _get_document_info_raw()
    if "error" in doc_info:
        return f"Error: {doc_info['error']}"
    if _looks_unsaved_document(doc_info):
        return (
            "Error: This document does not appear to have a saved file path yet. "
            "Use krita_export() for reliable output, or krita_save_as() only if you "
            "explicitly need a .kra file."
        )

    result = send_command_with_dialog_handling(
        KRITA_URL, "save", {}, timeout=20.0,
    )

    if "error" in result:
        return (
            f"Error: {result['error']} "
            "Native Krita save is known to stall in some setups; prefer "
            "krita_export() for images."
        )

    msg = f"Saved to: {result.get('path', 'current location')}"
    dismissed = result.get("dismissed_dialogs") or []
    if dismissed:
        msg += f" (auto-dismissed: {', '.join(dismissed)})"
    return msg


@mcp.tool()
def krita_save_as(path: str) -> str:
    """
    Save the document to a new location (Krita native format .kra).

    Warning:
        This uses Krita's native save path, which can stall in some setups.
        Prefer krita_export() unless you explicitly need an editable .kra file.

    Args:
        path: Full file path to save to (e.g., "C:/Art/my_painting.kra")
    """
    import os
    path = os.path.abspath(path)

    # Ensure .kra extension
    if not path.lower().endswith('.kra'):
        path = f"{path}.kra"

    result = send_command_with_dialog_handling(
        KRITA_URL, "save_as", {"path": path}, timeout=20.0,
    )

    if "error" in result:
        return (
            f"Error: {result['error']} "
            "If you only need an output image, use krita_export() instead."
        )

    msg = f"Saved as: {path}"
    dismissed = result.get("dismissed_dialogs") or []
    if dismissed:
        msg += f" (auto-dismissed: {', '.join(dismissed)})"
    return msg


@mcp.tool()
def krita_request_save(suggested_path: str = "") -> str:
    """
    Request the user to save the canvas manually.

    Use this only as a fallback when native .kra saving is required.
    For automatic image output, prefer krita_export().

    Args:
        suggested_path: Suggested file path for the user to save to
    """
    if suggested_path:
        return f"Please save your canvas to: {suggested_path}\nOnce saved, let me know and I can view the file."
    else:
        return "Please save your canvas (File > Export or Ctrl+Shift+E) and tell me the file path so I can view your progress."


@mcp.tool()
def krita_undo() -> str:
    """Undo the last action."""
    result = send_command("undo", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Undone"


@mcp.tool()
def krita_redo() -> str:
    """Redo the last undone action."""
    result = send_command("redo", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Redone"


@mcp.tool()
def krita_clear(color: str = "#1a1a2e") -> str:
    """
    Clear the canvas to a solid color.

    Args:
        color: Color to fill canvas with (default dark blue)
    """
    result = send_command("clear", {"color": color})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Canvas cleared to {color}"


@mcp.tool()
def krita_get_color_at(x: int, y: int) -> str:
    """
    Sample the color at a specific pixel (eyedropper).

    Args:
        x: X coordinate
        y: Y coordinate
    """
    result = send_command("get_color_at", {"x": x, "y": y})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Color at ({x}, {y}): {result.get('color', 'unknown')} (R:{result.get('r')}, G:{result.get('g')}, B:{result.get('b')})"


@mcp.tool()
def krita_list_brushes(filter: str = "", limit: int = 20) -> str:
    """
    List available brush presets.

    Args:
        filter: Filter brushes by name (partial match)
        limit: Maximum number to return
    """
    result = send_command("list_brushes", {"filter": filter, "limit": limit})

    if "error" in result:
        return f"Error: {result['error']}"

    brushes = result.get("brushes", [])
    if not brushes:
        return "No brushes found matching filter"

    return f"Available brushes ({len(brushes)}):\n" + "\n".join(f"  - {b}" for b in brushes)


# ============ GRADIENT TOOL ============
@mcp.tool()
def krita_gradient(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color1: str = "#000000",
    color2: str = "#ffffff",
    gradient_type: str = "linear"
) -> str:
    """
    Draw a gradient between two points.

    Args:
        x1: Start X coordinate
        y1: Start Y coordinate
        x2: End X coordinate
        y2: End Y coordinate
        color1: Start color (hex)
        color2: End color (hex)
        gradient_type: "linear" or "radial"
    """
    result = send_command("gradient", {
        "x1": x1, "y1": y1,
        "x2": x2, "y2": y2,
        "color1": color1, "color2": color2,
        "type": gradient_type
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Drew {gradient_type} gradient from ({x1},{y1}) to ({x2},{y2})"


# ============ TEXT TOOL ============
@mcp.tool()
def krita_text(
    text: str,
    x: int,
    y: int,
    font_size: int = 24,
    color: str = "#ffffff",
    font: str = "Arial"
) -> str:
    """
    Add text to the canvas.

    Args:
        text: The text to render
        x: X position
        y: Y position
        font_size: Font size in points
        color: Text color (hex)
        font: Font family name
    """
    result = send_command("text", {
        "text": text,
        "x": x, "y": y,
        "font_size": font_size,
        "color": color,
        "font": font
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Added text '{text}' at ({x},{y})"


# ============ FLOOD FILL ============
@mcp.tool()
def krita_flood_fill(x: int, y: int, tolerance: int = 20) -> str:
    """
    Flood fill (bucket tool) at a point using current foreground color.

    Args:
        x: X coordinate to start fill
        y: Y coordinate to start fill
        tolerance: Color tolerance (0-255, higher = more area filled)
    """
    result = send_command("flood_fill", {
        "x": x, "y": y,
        "tolerance": tolerance
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Flood filled at ({x},{y}), {result.get('pixels_filled', 0)} pixels"


# ============ LAYER MANAGEMENT ============
@mcp.tool()
def krita_new_layer(name: str = "New Layer") -> str:
    """
    Create a new paint layer.

    Args:
        name: Name for the new layer
    """
    result = send_command("new_layer", {"name": name})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created layer: {name}"


@mcp.tool()
def krita_delete_layer() -> str:
    """Delete the currently active layer."""
    result = send_command("delete_layer", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Deleted layer: {result.get('deleted', 'unknown')}"


@mcp.tool()
def krita_list_layers() -> str:
    """List all layers in the document."""
    result = send_command("list_layers", {})

    if "error" in result:
        return f"Error: {result['error']}"

    layers = result.get("layers", [])
    active = result.get("active", "")
    output = f"Layers (active: {active}):\n"
    for layer in layers:
        indent = "  " * layer.get("depth", 0)
        vis = "👁" if layer.get("visible") else "  "
        opacity = layer.get("opacity", 255)
        output += f"{indent}{vis} {layer['name']} ({layer['type']}) opacity:{opacity}\n"
    return output


@mcp.tool()
def krita_set_layer_opacity(opacity: int) -> str:
    """
    Set opacity of the active layer.

    Args:
        opacity: Opacity value (0-255, where 255 is fully opaque)
    """
    result = send_command("set_layer_opacity", {"opacity": opacity})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Set layer opacity to {opacity}"


@mcp.tool()
def krita_select_layer(name: str) -> str:
    """
    Select a layer by name.

    Args:
        name: Name of the layer to select
    """
    result = send_command("select_layer", {"name": name})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Selected layer: {name}"


@mcp.tool()
def krita_duplicate_layer() -> str:
    """Duplicate the currently active layer."""
    result = send_command("duplicate_layer", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Duplicated layer: {result.get('original')} -> {result.get('duplicate')}"


@mcp.tool()
def krita_merge_down() -> str:
    """Merge the active layer down into the layer below it."""
    result = send_command("merge_down", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Merged layer down"


# ============ TRANSFORM TOOLS ============
@mcp.tool()
def krita_transform(operation: str) -> str:
    """
    Transform the active layer.

    Args:
        operation: Transform type - "flip_h" (horizontal flip), "flip_v" (vertical flip),
                   "rotate_cw" (90° clockwise), "rotate_ccw" (90° counter-clockwise)
    """
    result = send_command("transform", {"operation": operation})

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Applied transform: {operation}"


# ============ SELECTION TOOLS ============
@mcp.tool()
def krita_select_rectangle(x: int, y: int, width: int, height: int) -> str:
    """
    Create a rectangular selection.

    Args:
        x: Left edge X coordinate
        y: Top edge Y coordinate
        width: Selection width
        height: Selection height
    """
    result = send_command("select_rectangle", {
        "x": x, "y": y,
        "width": width, "height": height
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created rectangle selection at ({x},{y}) size {width}x{height}"


@mcp.tool()
def krita_select_ellipse(x: int, y: int, width: int, height: int) -> str:
    """
    Create an elliptical selection.

    Args:
        x: Left edge X coordinate
        y: Top edge Y coordinate
        width: Selection width
        height: Selection height
    """
    result = send_command("select_ellipse", {
        "x": x, "y": y,
        "width": width, "height": height
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Created ellipse selection at ({x},{y}) size {width}x{height}"


@mcp.tool()
def krita_select_all() -> str:
    """Select the entire canvas."""
    result = send_command("select_all", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Selected all"


@mcp.tool()
def krita_deselect() -> str:
    """Clear the current selection."""
    result = send_command("deselect", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Deselected"


@mcp.tool()
def krita_invert_selection() -> str:
    """Invert the current selection."""
    result = send_command("invert_selection", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Inverted selection"


# ============ FILTERS ============
@mcp.tool()
def krita_filter(name: str, strength: int = 5) -> str:
    """
    Apply a filter to the active layer.

    Args:
        name: Filter name (e.g., "blur", "sharpen", "desaturate", "invert", "gaussianblur")
        strength: Filter strength/radius (meaning varies by filter)
    """
    result = send_command("filter", {
        "name": name,
        "strength": strength
    })

    if "error" in result:
        if "available" in result:
            return f"Error: {result['error']}\nAvailable filters: {', '.join(result['available'])}"
        return f"Error: {result['error']}"
    return f"Applied filter: {name}"


# ============ DOCUMENT INFO ============
@mcp.tool()
def krita_get_document_info() -> str:
    """Get information about the current document (size, layers, etc.)."""
    result = send_command("get_document_info", {})

    if "error" in result:
        return f"Error: {result['error']}"

    return f"""Document: {result.get('name')}
Size: {result.get('width')}x{result.get('height')}
Resolution: {result.get('resolution')} DPI
Color: {result.get('colorModel')} {result.get('colorDepth')}
Active Layer: {result.get('activeLayer')}"""


@mcp.tool()
def krita_resize_canvas(
    width: Optional[int] = None,
    height: Optional[int] = None,
    anchor: str = "center"
) -> str:
    """
    Resize the canvas.

    Args:
        width: New width (or None to keep current)
        height: New height (or None to keep current)
        anchor: Where to anchor existing content - "topleft", "top", "topright",
                "left", "center", "right", "bottomleft", "bottom", "bottomright"
    """
    params = {"anchor": anchor}
    if width is not None:
        params["width"] = width
    if height is not None:
        params["height"] = height

    result = send_command("resize_canvas", params)

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Resized canvas to {result.get('width')}x{result.get('height')}"


@mcp.tool()
def krita_crop_to_selection() -> str:
    """Crop the canvas to the current selection."""
    result = send_command("crop_to_selection", {})

    if "error" in result:
        return f"Error: {result['error']}"
    return "Cropped to selection"


# ============ BEZIER CURVE ============
@mcp.tool()
def krita_bezier_curve(
    points: list[list[int]],
    size: int = 3
) -> str:
    """
    Draw a bezier curve through control points.

    Args:
        points: List of [x, y] coordinates - needs at least 4 points:
                [start, control1, control2, end] for a cubic bezier.
                More points create connected curves.
        size: Stroke width in pixels
    """
    result = send_command("bezier_curve", {
        "points": points,
        "size": size
    })

    if "error" in result:
        return f"Error: {result['error']}"
    return f"Drew bezier curve with {len(points)} control points"


if __name__ == "__main__":
    mcp.run()
