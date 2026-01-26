"""
Krita MCP Plugin

This plugin runs an HTTP server inside Krita that receives commands from
the Krita MCP server, allowing Claude to draw and paint in Krita.

Install: Copy this folder to Krita's pykrita directory
Enable: Settings > Configure Krita > Python Plugin Manager > Krita MCP Plugin
"""

from krita import Krita, Extension
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
from PyQt5.QtWidgets import QMessageBox
import json
import http.server
import socketserver
import threading
import traceback

PORT = 5678

class CommandHandler(http.server.BaseHTTPRequestHandler):
    """Handle incoming MCP commands."""

    krita_instance = None

    def log_message(self, format, *args):
        pass  # Suppress logging

    def do_GET(self):
        """Handle health check."""
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "plugin": "krita-mcp-plugin"
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        """Handle command execution."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)

            action = data.get("action", "")
            params = data.get("params", {})

            result = execute_command(action, params)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())


def get_document():
    """Get active document or raise error."""
    app = Krita.instance()
    doc = app.activeDocument()
    if not doc:
        raise Exception("No document open. Create or open a document first.")
    return doc


def get_active_layer():
    """Get active layer or raise error."""
    doc = get_document()
    layer = doc.activeNode()
    if not layer:
        raise Exception("No active layer.")
    return layer


def hex_to_rgb(hex_color):
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def execute_command(action, params):
    """Execute a Krita command."""
    app = Krita.instance()

    try:
        # ============ CANVAS ============
        if action == "new_canvas":
            width = params.get("width", 800)
            height = params.get("height", 600)
            name = params.get("name", "New Canvas")
            bg = params.get("background", "#1a1a2e")

            doc = app.createDocument(width, height, name, "RGBA", "U8", "", 300.0)
            app.activeWindow().addView(doc)

            # Fill background
            r, g, b = hex_to_rgb(bg)
            layer = doc.activeNode()
            if layer:
                layer.setPixelData(bytes([b, g, r, 255] * (width * height)), 0, 0, width, height)

            doc.refreshProjection()
            return {"success": True, "width": width, "height": height}

        elif action == "clear":
            doc = get_document()
            layer = get_active_layer()
            color = params.get("color", "#1a1a2e")
            r, g, b = hex_to_rgb(color)

            width = doc.width()
            height = doc.height()
            layer.setPixelData(bytes([b, g, r, 255] * (width * height)), 0, 0, width, height)
            doc.refreshProjection()
            return {"success": True}

        elif action == "get_document_info":
            doc = get_document()
            return {
                "name": doc.name(),
                "width": doc.width(),
                "height": doc.height(),
                "activeLayer": doc.activeNode().name() if doc.activeNode() else None
            }

        # ============ COLORS & BRUSHES ============
        elif action == "set_color":
            color = params.get("color", "#000000")
            r, g, b = hex_to_rgb(color)

            from krita import ManagedColor
            doc = get_document()
            mc = ManagedColor("RGBA", "U8", "")
            mc.setComponents([r/255.0, g/255.0, b/255.0, 1.0])
            app.activeWindow().activeView().setForeGroundColor(mc)
            return {"success": True}

        elif action == "set_brush":
            preset = params.get("preset")
            size = params.get("size")
            opacity = params.get("opacity")

            view = app.activeWindow().activeView()

            if preset:
                presets = app.resources("preset")
                if preset in presets:
                    view.setCurrentBrushPreset(presets[preset])

            if size:
                view.setBrushSize(size)

            if opacity is not None:
                view.setPaintingOpacity(opacity)

            return {"success": True}

        elif action == "list_brushes":
            filter_str = params.get("filter", "").lower()
            limit = params.get("limit", 20)

            presets = app.resources("preset")
            names = list(presets.keys())

            if filter_str:
                names = [n for n in names if filter_str in n.lower()]

            return {"brushes": names[:limit]}

        elif action == "get_color_at":
            x = params.get("x", 0)
            y = params.get("y", 0)

            doc = get_document()
            layer = get_active_layer()
            pixel = layer.pixelData(x, y, 1, 1)

            if len(pixel) >= 3:
                b, g, r = pixel[0], pixel[1], pixel[2]
                color = f"#{r:02x}{g:02x}{b:02x}"
                return {"color": color}

            return {"color": "#000000"}

        # ============ DRAWING ============
        elif action == "stroke":
            points = params.get("points", [])
            pressure = params.get("pressure", 1.0)

            if len(points) < 2:
                return {"error": "Need at least 2 points"}

            doc = get_document()
            layer = get_active_layer()

            # Paint stroke using brush engine
            view = app.activeWindow().activeView()

            from krita import InfoObject
            info = InfoObject()
            info.setProperty("paintColor", view.foregroundColor().colorForCanvas(doc.activeNode()))

            # Create stroke path
            from PyQt5.QtCore import QPointF
            from PyQt5.QtGui import QPainterPath

            path = QPainterPath()
            path.moveTo(QPointF(points[0][0], points[0][1]))
            for pt in points[1:]:
                path.lineTo(QPointF(pt[0], pt[1]))

            # Alternative: draw directly on pixel data
            from PyQt5.QtGui import QImage, QPainter, QPen, QColor

            width = doc.width()
            height = doc.height()

            img = QImage(width, height, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))

            painter = QPainter(img)
            fg = view.foregroundColor()
            components = fg.components()
            r = int(components[0] * 255)
            g = int(components[1] * 255)
            b = int(components[2] * 255)

            pen = QPen(QColor(r, g, b))
            pen.setWidth(int(view.brushSize()))
            painter.setPen(pen)
            painter.drawPath(path)
            painter.end()

            # Convert to pixel data and composite
            ptr = img.bits()
            ptr.setsize(width * height * 4)
            pixel_data = bytes(ptr)

            # Simple composite onto layer
            existing = layer.pixelData(0, 0, width, height)
            if existing:
                result = bytearray(existing)
                for i in range(0, len(pixel_data), 4):
                    alpha = pixel_data[i + 3]
                    if alpha > 0:
                        result[i] = pixel_data[i]
                        result[i+1] = pixel_data[i+1]
                        result[i+2] = pixel_data[i+2]
                        result[i+3] = 255
                layer.setPixelData(bytes(result), 0, 0, width, height)

            doc.refreshProjection()
            return {"success": True, "points": len(points)}

        elif action == "draw_shape":
            shape = params.get("shape", "rectangle")
            x = params.get("x", 0)
            y = params.get("y", 0)
            width = params.get("width", 100)
            height = params.get("height", 100)
            fill = params.get("fill", True)
            x2 = params.get("x2")
            y2 = params.get("y2")

            doc = get_document()
            layer = get_active_layer()
            view = app.activeWindow().activeView()

            from PyQt5.QtGui import QImage, QPainter, QPen, QBrush, QColor
            from PyQt5.QtCore import QRect, QPoint

            doc_width = doc.width()
            doc_height = doc.height()

            img = QImage(doc_width, doc_height, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))

            painter = QPainter(img)
            fg = view.foregroundColor()
            components = fg.components()
            r = int(components[0] * 255)
            g = int(components[1] * 255)
            b = int(components[2] * 255)
            color = QColor(r, g, b)

            if fill:
                painter.setBrush(QBrush(color))
                painter.setPen(QPen(color))
            else:
                painter.setBrush(QBrush())
                painter.setPen(QPen(color, 2))

            if shape == "rectangle":
                painter.drawRect(QRect(x, y, width, height))
            elif shape == "ellipse":
                painter.drawEllipse(QRect(x, y, width, height))
            elif shape == "line":
                if x2 is not None and y2 is not None:
                    painter.drawLine(QPoint(x, y), QPoint(x2, y2))
                else:
                    painter.drawLine(QPoint(x, y), QPoint(x + width, y + height))

            painter.end()

            # Composite onto layer
            ptr = img.bits()
            ptr.setsize(doc_width * doc_height * 4)
            pixel_data = bytes(ptr)

            existing = layer.pixelData(0, 0, doc_width, doc_height)
            if existing:
                result = bytearray(existing)
                for i in range(0, len(pixel_data), 4):
                    alpha = pixel_data[i + 3]
                    if alpha > 0:
                        result[i] = pixel_data[i]
                        result[i+1] = pixel_data[i+1]
                        result[i+2] = pixel_data[i+2]
                        result[i+3] = 255
                layer.setPixelData(bytes(result), 0, 0, doc_width, doc_height)

            doc.refreshProjection()
            return {"success": True}

        elif action == "fill":
            x = params.get("x", 0)
            y = params.get("y", 0)
            radius = params.get("radius", 50)

            doc = get_document()
            layer = get_active_layer()
            view = app.activeWindow().activeView()

            from PyQt5.QtGui import QImage, QPainter, QBrush, QColor
            from PyQt5.QtCore import QRect

            doc_width = doc.width()
            doc_height = doc.height()

            img = QImage(doc_width, doc_height, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))

            painter = QPainter(img)
            fg = view.foregroundColor()
            components = fg.components()
            r = int(components[0] * 255)
            g = int(components[1] * 255)
            b = int(components[2] * 255)
            color = QColor(r, g, b)

            painter.setBrush(QBrush(color))
            painter.setPen(color)
            painter.drawEllipse(QRect(x - radius, y - radius, radius * 2, radius * 2))
            painter.end()

            ptr = img.bits()
            ptr.setsize(doc_width * doc_height * 4)
            pixel_data = bytes(ptr)

            existing = layer.pixelData(0, 0, doc_width, doc_height)
            if existing:
                result = bytearray(existing)
                for i in range(0, len(pixel_data), 4):
                    alpha = pixel_data[i + 3]
                    if alpha > 0:
                        result[i] = pixel_data[i]
                        result[i+1] = pixel_data[i+1]
                        result[i+2] = pixel_data[i+2]
                        result[i+3] = 255
                layer.setPixelData(bytes(result), 0, 0, doc_width, doc_height)

            doc.refreshProjection()
            return {"success": True}

        elif action == "flood_fill":
            x = params.get("x", 0)
            y = params.get("y", 0)
            tolerance = params.get("tolerance", 20)

            doc = get_document()
            doc.setSelection(None)

            # Use Krita's fill action
            app.action("fill_selection_foreground_color").trigger()

            doc.refreshProjection()
            return {"success": True}

        elif action == "gradient":
            x1 = params.get("x1", 0)
            y1 = params.get("y1", 0)
            x2 = params.get("x2", 100)
            y2 = params.get("y2", 100)
            color1 = params.get("color1", "#000000")
            color2 = params.get("color2", "#ffffff")
            gtype = params.get("type", "linear")

            doc = get_document()
            layer = get_active_layer()

            from PyQt5.QtGui import QImage, QPainter, QLinearGradient, QRadialGradient, QColor
            from PyQt5.QtCore import QPointF

            width = doc.width()
            height = doc.height()

            img = QImage(width, height, QImage.Format_ARGB32)

            r1, g1, b1 = hex_to_rgb(color1)
            r2, g2, b2 = hex_to_rgb(color2)

            if gtype == "radial":
                gradient = QRadialGradient(QPointF(x1, y1), ((x2-x1)**2 + (y2-y1)**2)**0.5)
            else:
                gradient = QLinearGradient(QPointF(x1, y1), QPointF(x2, y2))

            gradient.setColorAt(0, QColor(r1, g1, b1))
            gradient.setColorAt(1, QColor(r2, g2, b2))

            painter = QPainter(img)
            painter.fillRect(0, 0, width, height, gradient)
            painter.end()

            ptr = img.bits()
            ptr.setsize(width * height * 4)
            pixel_data = bytes(ptr)

            layer.setPixelData(pixel_data, 0, 0, width, height)
            doc.refreshProjection()
            return {"success": True}

        elif action == "text":
            text = params.get("text", "")
            x = params.get("x", 0)
            y = params.get("y", 0)
            font_size = params.get("font_size", 24)
            color = params.get("color", "#ffffff")
            font = params.get("font", "Arial")

            doc = get_document()
            layer = get_active_layer()

            from PyQt5.QtGui import QImage, QPainter, QFont, QColor
            from PyQt5.QtCore import QPoint

            width = doc.width()
            height = doc.height()

            img = QImage(width, height, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))

            r, g, b = hex_to_rgb(color)

            painter = QPainter(img)
            painter.setFont(QFont(font, font_size))
            painter.setPen(QColor(r, g, b))
            painter.drawText(QPoint(x, y + font_size), text)
            painter.end()

            ptr = img.bits()
            ptr.setsize(width * height * 4)
            pixel_data = bytes(ptr)

            existing = layer.pixelData(0, 0, width, height)
            if existing:
                result = bytearray(existing)
                for i in range(0, len(pixel_data), 4):
                    alpha = pixel_data[i + 3]
                    if alpha > 0:
                        result[i] = pixel_data[i]
                        result[i+1] = pixel_data[i+1]
                        result[i+2] = pixel_data[i+2]
                        result[i+3] = 255
                layer.setPixelData(bytes(result), 0, 0, width, height)

            doc.refreshProjection()
            return {"success": True}

        elif action == "bezier_curve":
            points = params.get("points", [])
            size = params.get("size", 3)

            if len(points) < 2:
                return {"error": "Need at least 2 points"}

            doc = get_document()
            layer = get_active_layer()
            view = app.activeWindow().activeView()

            from PyQt5.QtGui import QImage, QPainter, QPen, QColor, QPainterPath
            from PyQt5.QtCore import QPointF

            width = doc.width()
            height = doc.height()

            img = QImage(width, height, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))

            fg = view.foregroundColor()
            components = fg.components()
            r = int(components[0] * 255)
            g = int(components[1] * 255)
            b = int(components[2] * 255)

            path = QPainterPath()
            path.moveTo(QPointF(points[0][0], points[0][1]))

            if len(points) == 2:
                path.lineTo(QPointF(points[1][0], points[1][1]))
            elif len(points) == 3:
                path.quadTo(QPointF(points[1][0], points[1][1]),
                           QPointF(points[2][0], points[2][1]))
            elif len(points) >= 4:
                path.cubicTo(QPointF(points[1][0], points[1][1]),
                            QPointF(points[2][0], points[2][1]),
                            QPointF(points[3][0], points[3][1]))

            painter = QPainter(img)
            pen = QPen(QColor(r, g, b))
            pen.setWidth(size)
            painter.setPen(pen)
            painter.drawPath(path)
            painter.end()

            ptr = img.bits()
            ptr.setsize(width * height * 4)
            pixel_data = bytes(ptr)

            existing = layer.pixelData(0, 0, width, height)
            if existing:
                result = bytearray(existing)
                for i in range(0, len(pixel_data), 4):
                    alpha = pixel_data[i + 3]
                    if alpha > 0:
                        result[i] = pixel_data[i]
                        result[i+1] = pixel_data[i+1]
                        result[i+2] = pixel_data[i+2]
                        result[i+3] = 255
                layer.setPixelData(bytes(result), 0, 0, width, height)

            doc.refreshProjection()
            return {"success": True}

        # ============ LAYERS ============
        elif action == "new_layer":
            name = params.get("name", "New Layer")
            doc = get_document()

            layer = doc.createNode(name, "paintLayer")
            root = doc.rootNode()
            root.addChildNode(layer, None)
            doc.setActiveNode(layer)
            doc.refreshProjection()
            return {"success": True, "name": name}

        elif action == "list_layers":
            doc = get_document()

            def get_layers(node, depth=0):
                result = []
                for child in node.childNodes():
                    result.append({
                        "name": child.name(),
                        "depth": depth,
                        "visible": child.visible(),
                        "opacity": child.opacity()
                    })
                    result.extend(get_layers(child, depth + 1))
                return result

            layers = get_layers(doc.rootNode())
            return {"layers": layers}

        elif action == "select_layer":
            name = params.get("name", "")
            doc = get_document()

            def find_layer(node, name):
                for child in node.childNodes():
                    if child.name() == name:
                        return child
                    found = find_layer(child, name)
                    if found:
                        return found
                return None

            layer = find_layer(doc.rootNode(), name)
            if layer:
                doc.setActiveNode(layer)
                return {"success": True}
            return {"error": f"Layer '{name}' not found"}

        elif action == "delete_layer":
            doc = get_document()
            layer = get_active_layer()
            layer.remove()
            doc.refreshProjection()
            return {"success": True}

        elif action == "set_layer_opacity":
            opacity = params.get("opacity", 255)
            layer = get_active_layer()
            layer.setOpacity(opacity)
            get_document().refreshProjection()
            return {"success": True}

        elif action == "duplicate_layer":
            doc = get_document()
            layer = get_active_layer()
            new_layer = layer.duplicate()
            layer.parentNode().addChildNode(new_layer, layer)
            doc.setActiveNode(new_layer)
            doc.refreshProjection()
            return {"success": True}

        elif action == "merge_down":
            doc = get_document()
            layer = get_active_layer()
            merged = layer.mergeDown()
            if merged:
                doc.setActiveNode(merged)
            doc.refreshProjection()
            return {"success": True}

        # ============ SELECTIONS ============
        elif action == "select_rectangle":
            x = params.get("x", 0)
            y = params.get("y", 0)
            width = params.get("width", 100)
            height = params.get("height", 100)

            doc = get_document()
            from krita import Selection
            sel = Selection()
            sel.select(x, y, width, height, 255)
            doc.setSelection(sel)
            return {"success": True}

        elif action == "select_ellipse":
            x = params.get("x", 0)
            y = params.get("y", 0)
            width = params.get("width", 100)
            height = params.get("height", 100)

            doc = get_document()
            from krita import Selection
            sel = Selection()
            sel.select(x, y, width, height, 255)
            # Note: Krita's Python API doesn't have direct ellipse selection
            # This creates a rectangular selection as fallback
            doc.setSelection(sel)
            return {"success": True}

        elif action == "select_all":
            doc = get_document()
            from krita import Selection
            sel = Selection()
            sel.select(0, 0, doc.width(), doc.height(), 255)
            doc.setSelection(sel)
            return {"success": True}

        elif action == "deselect":
            doc = get_document()
            doc.setSelection(None)
            return {"success": True}

        elif action == "invert_selection":
            doc = get_document()
            sel = doc.selection()
            if sel:
                sel.invert()
                doc.setSelection(sel)
            return {"success": True}

        # ============ TRANSFORMS & FILTERS ============
        elif action == "transform":
            operation = params.get("operation", "")
            doc = get_document()

            if operation == "flip_h":
                app.action("mirrorNodeX").trigger()
            elif operation == "flip_v":
                app.action("mirrorNodeY").trigger()
            elif operation == "rotate_cw":
                app.action("rotateImage90CW").trigger()
            elif operation == "rotate_ccw":
                app.action("rotateImage90CCW").trigger()

            doc.refreshProjection()
            return {"success": True}

        elif action == "filter":
            name = params.get("name", "")
            strength = params.get("strength", 5)

            doc = get_document()
            layer = get_active_layer()

            filter_map = {
                "blur": "blur",
                "sharpen": "unsharp",
                "desaturate": "desaturate",
                "invert": "invert",
                "gaussianblur": "gaussian blur"
            }

            filter_name = filter_map.get(name.lower(), name)
            filt = app.filter(filter_name)

            if filt:
                config = filt.configuration()
                filt.apply(layer, 0, 0, doc.width(), doc.height())

            doc.refreshProjection()
            return {"success": True}

        elif action == "resize_canvas":
            width = params.get("width")
            height = params.get("height")
            anchor = params.get("anchor", "center")

            doc = get_document()

            new_width = width or doc.width()
            new_height = height or doc.height()

            # Calculate offset based on anchor
            x_offset = 0
            y_offset = 0

            if "left" in anchor:
                x_offset = 0
            elif "right" in anchor:
                x_offset = new_width - doc.width()
            else:
                x_offset = (new_width - doc.width()) // 2

            if "top" in anchor:
                y_offset = 0
            elif "bottom" in anchor:
                y_offset = new_height - doc.height()
            else:
                y_offset = (new_height - doc.height()) // 2

            doc.resizeImage(x_offset, y_offset, new_width, new_height)
            doc.refreshProjection()
            return {"success": True, "width": new_width, "height": new_height}

        elif action == "crop_to_selection":
            doc = get_document()
            app.action("crop").trigger()
            doc.refreshProjection()
            return {"success": True}

        # ============ FILE OPERATIONS ============
        elif action == "export":
            path = params.get("path", "")
            format = params.get("format", "png")

            doc = get_document()
            doc.exportImage(path, None)
            return {"success": True, "path": path}

        elif action == "save":
            doc = get_document()
            doc.save()
            return {"success": True}

        elif action == "save_as":
            path = params.get("path", "")
            doc = get_document()
            doc.saveAs(path)
            return {"success": True, "path": path}

        elif action == "undo":
            app.action("edit_undo").trigger()
            return {"success": True}

        elif action == "redo":
            app.action("edit_redo").trigger()
            return {"success": True}

        else:
            return {"error": f"Unknown action: {action}"}

    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


class KritaMCPServer(Extension):
    """Krita extension that runs the MCP HTTP server."""

    def __init__(self, parent):
        super().__init__(parent)
        self.server = None
        self.server_thread = None

    def setup(self):
        pass

    def start_server(self):
        """Start the HTTP server in a background thread."""
        try:
            self.server = socketserver.TCPServer(("", PORT), CommandHandler)
            self.server_thread = threading.Thread(target=self.server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()
            print(f"Krita MCP Plugin started on port {PORT}")
        except Exception as e:
            print(f"Failed to start Krita MCP server: {e}")

    def createActions(self, window):
        pass


# Global instance
krita_mcp_instance = None

def start_plugin():
    """Called when Krita starts."""
    global krita_mcp_instance
    krita_mcp_instance = KritaMCPServer(Krita.instance())

    # Delay server start to ensure Krita is fully loaded
    QTimer.singleShot(2000, krita_mcp_instance.start_server)


# Register the extension
Krita.instance().addExtension(KritaMCPServer(Krita.instance()))

# Start server after a delay
QTimer.singleShot(2000, lambda: None)  # Ensure Qt event loop is running
start_plugin()
