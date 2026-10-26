"""Local image decoding and native single-frame Houdini viewport capture.

Capture uses a stashed flipbook configuration and may evaluate the current frame.
No desktop pixels, MPlay, animation range or simulation reset is requested.
"""
import math
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

from PySide2 import QtCore, QtGui, QtWidgets

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_EDGE = 1280
MAX_IMAGE_BYTES = 512 * 1024


def normalized_image(source):
    """Decode locally, bound pixels, remove metadata, and encode a small PNG/JPEG."""
    if isinstance(source, QtGui.QImage):
        image = source.copy()
    else:
        if isinstance(source, str):
            if os.path.getsize(source) > MAX_INPUT_BYTES:
                raise ValueError("Image exceeds the 20 MiB input limit.")
            reader = QtGui.QImageReader(source)
        else:
            if len(source) > MAX_INPUT_BYTES:
                raise ValueError("Image exceeds the 20 MiB input limit.")
            buffer = QtCore.QBuffer()
            buffer.setData(QtCore.QByteArray(bytes(source)))
            buffer.open(QtCore.QIODevice.ReadOnly)
            reader = QtGui.QImageReader(buffer)
        if bytes(reader.format()).lower() not in (b"png", b"jpeg", b"jpg", b"webp"):
            raise ValueError("Attach a PNG, JPEG or WebP image.")
        size = reader.size()
        if not size.isValid() or size.width() * size.height() > MAX_PIXELS:
            raise ValueError("Image dimensions are invalid or exceed 40 megapixels.")
        reader.setAutoTransform(True)
        if max(size.width(), size.height()) > MAX_EDGE:
            reader.setScaledSize(size.scaled(MAX_EDGE, MAX_EDGE, QtCore.Qt.KeepAspectRatio))
        image = reader.read()
    if image.isNull() or image.width() * image.height() > MAX_PIXELS:
        raise ValueError("Could not decode this image within the image limit.")
    if max(image.width(), image.height()) > MAX_EDGE:
        image = image.scaled(MAX_EDGE, MAX_EDGE, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    # Draw into a fresh image so source EXIF, text, profiles and paths are not retained.
    clean = QtGui.QImage(image.size(), QtGui.QImage.Format_ARGB32)
    clean.fill(QtCore.Qt.transparent if image.hasAlphaChannel() else QtCore.Qt.white)
    painter = QtGui.QPainter(clean)
    painter.drawImage(0, 0, image)
    painter.end()
    fmt = "PNG"  # Always available in bundled Qt; optional JPEG writers may be absent.
    for _ in range(9):
        output = QtCore.QBuffer()
        output.open(QtCore.QIODevice.WriteOnly)
        if not clean.save(output, fmt, 85):
            raise ValueError("Could not encode the image.")
        data = bytes(output.data())
        if len(data) <= MAX_IMAGE_BYTES:
            return data, "image/png" if fmt == "PNG" else "image/jpeg", clean.width(), clean.height()
        clean = clean.scaled(max(1, clean.width() * 3 // 4), max(1, clean.height() * 3 // 4),
                             QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    raise ValueError("Image could not fit the visual input budget.")


def _rect_tuple(rect):
    return rect.x(), rect.y(), rect.width(), rect.height()


def discover_viewport(hou_module=None):
    """Resolve a visible native GeometryViewport on Houdini's UI thread.

    The SceneViewer Qt parenting proxy supplies the display origin, not pixels.
    Pane bounds include toolbar chrome and are only a containment check.
    Native capture uses the selected viewport and an aspect-preserving size.
    """
    app = QtWidgets.QApplication.instance()
    if app is None or QtCore.QThread.currentThread() != app.thread():
        raise ValueError("Viewport capture must run on Houdini's UI thread.")
    if hou_module is None:
        import hou as hou_module
    try:
        candidates = []
        for tab in hou_module.ui.currentPaneTabs():
            if not tab.isCurrentTab():
                continue
            if tab.type() == hou_module.paneTabType.SceneViewer:
                viewer = tab
            elif tab.type() == hou_module.paneTabType.ContextViewer:
                viewer = tab.sceneViewer()
            else:
                continue
            if viewer is None:
                continue
            window = tab.qtParentWindow()
            proxy = viewer.qtWindow()
            if (window is None or proxy is None or not window.isVisible()
                    or window.isMinimized() or not proxy.isVisible()):
                continue
            candidates.append((tab, viewer, window, proxy))
        if not candidates:
            raise ValueError("No visible Scene Viewer was found. Open a Scene View and keep it visible.")
        if len(candidates) != 1:
            raise ValueError("More than one Scene Viewer is visible. Leave one Scene View visible for capture.")
        tab, viewer, window, proxy = candidates[0]
        viewport = viewer.selectedViewport()
        if viewport is None:
            raise ValueError("The Scene Viewer has no selected viewport.")
        sx, sy, sw, sh = viewer.geometry()
        vx, vy, vw, vh = viewport.geometry()
        values = (sw, sh, vx, vy, vw, vh)
        if (not all(math.isfinite(v) for v in values) or min(sw, sh, vw, vh) <= 0
                or min(vx, vy) < 0 or vx + vw > sw or vy + vh > sh):
            raise ValueError("The selected viewport has invalid display bounds.")
        # HOM uses a lower-left origin. Qt uses a top-left origin in logical pixels.
        # Scale against the native proxy to derive the selected view
        # dimensions without including pane toolbars or other viewports.
        px, py = proxy.width() / sw, proxy.height() / sh
        left, top = math.ceil(vx * px), math.ceil((sh - vy - vh) * py)
        right, bottom = math.floor((vx + vw) * px), math.floor((sh - vy) * py)
        local = QtCore.QRect(left, top, right - left, bottom - top)
        if local.isEmpty() or not proxy.rect().contains(local):
            raise ValueError("The selected viewport is outside its Scene Viewer.")
        global_rect = QtCore.QRect(proxy.mapToGlobal(local.topLeft()), local.size())
        if not tab.qtScreenGeometry().contains(global_rect):
            raise ValueError("The selected viewport is outside its visible pane.")
        rectangle = QtCore.QRect(window.mapFromGlobal(global_rect.topLeft()), global_rect.size())
        if not window.rect().contains(rectangle):
            raise ValueError("The selected viewport is outside its window.")
        name = viewport.name()
        geometry = QtCore.QRect(window.geometry())
        return {
            "window": window, "rectangle": rectangle, "geometry": geometry,
            "tab": tab, "viewer": viewer, "viewport": viewport, "proxy": proxy,
            "signature": (tab, viewer, int(window.winId()),
                          _rect_tuple(geometry), _rect_tuple(global_rect),
                          _rect_tuple(tab.qtScreenGeometry()), name, tuple(viewport.geometry())),
            "metadata": {"viewer": tab.name(), "viewport": name},
        }
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The visible viewport could not be resolved. Keep a Scene View open and retry.") from exc


def capture_viewport():
    """Export the automatically selected viewport through Houdini's native API."""
    import hou
    binding = discover_viewport()

    def validate():
        current = discover_viewport()
        if current["signature"] != binding["signature"]:
            raise ValueError("The viewport changed during capture. Retry with the view visible.")

    data, mime, width, height, metadata = _capture_native(binding, hou, validate=validate)
    metadata.update(binding["metadata"])
    return data, mime, width, height, metadata


def _capture_native(binding, hou_module, validate=None):
    """One frame, using copied settings; never fall back to desktop capture."""
    app = QtWidgets.QApplication.instance()
    if app is None or QtCore.QThread.currentThread() != app.thread():
        raise ValueError("Viewport capture must run on Houdini's UI thread.")
    if hou_module.playbar.isPlaying():
        raise ValueError("Stop timeline playback before capturing the current viewport frame.")
    frame = float(hou_module.frame())
    if not math.isfinite(frame):
        raise ValueError("The current frame is invalid.")
    viewer, viewport = binding["viewer"], binding["viewport"]
    rectangle = binding["rectangle"]
    if rectangle.width() < 2 or rectangle.height() < 2:
        raise ValueError("The selected viewport is too small to capture.")
    scale = min(1.0, MAX_EDGE / max(rectangle.width(), rectangle.height()))
    resolution = (max(2, int(rectangle.width() * scale)),
                  max(2, int(rectangle.height() * scale)))
    try:
        settings = viewer.flipbookSettings().stash()
        settings.frameRange((frame, frame))
        settings.frameIncrement(1.0)
        settings.outputToMPlay(False)
        settings.initializeSimulations(False)
        settings.useMotionBlur(False)
        settings.useDepthOfField(False)
        settings.renderAllViewports(False)
        settings.beautyPassOnly(False)
        settings.cropOutMaskOverlay(False)
        settings.useSheetSize(False)
        settings.scopeChannelKeyframesOnly(False)
        settings.appendFramesToCurrent(False)
        settings.leaveFrameAtEnd(False)
        settings.visibleObjects("*")
        settings.visibleTypes(hou_module.flipbookObjectType.Visible)
        settings.antialias(hou_module.flipbookAntialias.UseViewportSetting)
        settings.useResolution(True)
        settings.resolution(resolution)
        settings.outputZoom(100)
        # This limits LOP progressive-render waiting, not geometry cook time.
        settings.setUseFrameTimeLimit(True)
        settings.setFrameTimeLimit(5.0)
        settings.setUseFrameProgressLimit(False)
        with tempfile.TemporaryDirectory(prefix="houlm-viewport-") as directory:
            output = Path(directory) / "viewport.png"
            settings.output(output.as_posix())
            if validate is not None:
                validate()
            if float(hou_module.frame()) != frame or hou_module.playbar.isPlaying():
                raise ValueError("The frame changed before capture. No image was accepted.")
            viewer.flipbook(viewport, settings, open_dialog=False)
            if validate is not None:
                validate()
            if float(hou_module.frame()) != frame:
                raise ValueError("The frame changed during capture. No image was accepted.")
            if not output.is_file():
                raise ValueError("Houdini did not produce a viewport image. Capture may have been cancelled.")
            # Close the file before decoding: a failing QImageReader can keep
            # a Windows file locked through its exception traceback.
            with output.open("rb") as image_file:
                image_bytes = image_file.read(MAX_INPUT_BYTES + 1)
            data, mime, width, height = normalized_image(image_bytes)
    except (OSError, RuntimeError, ValueError):
        raise
    except Exception as exc:
        # hou.Error subclasses are not necessarily RuntimeError subclasses.
        raise ValueError("Houdini native viewport capture failed: " + str(exc)[:300]) from exc
    return data, mime, width, height, {
        "host": "Houdini", "method": "houdini_native_flipbook",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "frame": frame, "fresh_evaluation_requested": True,
        "progressive_render_time_limit_seconds": 5.0,
        "freshness": "Native single-frame viewport preview; current-frame evaluation may occur. "
                     "Progressive rendering may be incomplete; this is not final render verification.",
    }
