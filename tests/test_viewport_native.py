"""Synthetic native flipbook export; no desktop capture, live HOM or model requests."""
import copy
from pathlib import Path
import types
import unittest
from unittest import mock

import test_panel as support

if support._qt_available:
    from PySide2 import QtCore, QtGui, QtWidgets
    from houlm import vision_ui


class SyntheticCaptureError(Exception):
    pass


class FakeFlipbookSettings:
    # Strict names verified against the installed Houdini 20.5 HOM declarations.
    methods = {
        "frameRange", "frameIncrement", "outputToMPlay", "initializeSimulations",
        "useMotionBlur", "useDepthOfField", "renderAllViewports", "beautyPassOnly",
        "cropOutMaskOverlay", "useSheetSize", "scopeChannelKeyframesOnly",
        "appendFramesToCurrent", "leaveFrameAtEnd", "visibleObjects", "visibleTypes",
        "antialias", "useResolution", "resolution", "outputZoom", "setUseFrameTimeLimit",
        "setFrameTimeLimit", "setUseFrameProgressLimit", "output",
    }

    def __init__(self, values=None):
        self.values = dict(values or {"frameRange": (1, 240), "outputToMPlay": True,
            "initializeSimulations": True, "useMotionBlur": True,
            "resolution": (1920, 1080), "output": "original-output.png"})
        self.calls = []
        self.stashed = []

    def stash(self):
        settings = FakeFlipbookSettings(copy.deepcopy(self.values))
        self.stashed.append(settings)
        return settings

    def __getattr__(self, name):
        if name not in self.methods:
            raise AttributeError(name)
        def setting(*args):
            if not args:
                return self.values.get(name)
            if len(args) != 1:
                raise AssertionError("Settings use one value argument")
            self.calls.append((name, args[0]))
            self.values[name] = args[0]
        return setting


def native_fixture(width=320, height=180):
    original = FakeFlipbookSettings()
    viewport = object()
    viewer = types.SimpleNamespace(flipbookSettings=mock.Mock(return_value=original))
    def export(target, settings, open_dialog=False):
        if target is not viewport or open_dialog:
            raise AssertionError("Only the selected viewport and no dialog are allowed")
        size = settings.values["resolution"]
        pixels = QtGui.QImage(*size, QtGui.QImage.Format_RGB32)
        pixels.fill(QtGui.QColor("#123456"))
        pixels.setPixelColor(1, 1, QtGui.QColor("#f1a044"))
        if not pixels.save(settings.values["output"], "PNG"):
            raise AssertionError("Synthetic image fixture could not be written")
    viewer.flipbook = mock.Mock(side_effect=export)
    native = types.SimpleNamespace(
        frame=mock.Mock(return_value=12.5),
        setFrame=mock.Mock(side_effect=AssertionError("Do not set timeline time")),
        playbar=types.SimpleNamespace(isPlaying=mock.Mock(return_value=False)),
        flipbookObjectType=types.SimpleNamespace(Visible=object()),
        flipbookAntialias=types.SimpleNamespace(UseViewportSetting=object()),
    )
    binding = {"viewer": viewer, "viewport": viewport,
               "rectangle": QtCore.QRect(100, 80, width, height), "signature": ("persp1",),
               "metadata": {"viewer": "scene1", "viewport": "persp1"}}
    return binding, native, original


@unittest.skipUnless(support._qt_available, support._qt_reason)
class NativeViewportCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def assert_temp_cleaned(self, original):
        path = Path(original.stashed[0].values["output"])
        self.assertEqual(path.name, "viewport.png")
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_native_export_uses_isolated_settings_and_one_current_frame(self):
        binding, native, original = native_fixture(1920, 1080)
        before = copy.deepcopy(original.values)
        validate = mock.Mock()
        with mock.patch.object(QtGui.QScreen, "grabWindow", side_effect=AssertionError("No desktop pixels")):
            data, mime, width, height, metadata = vision_ui._capture_native(binding, native, validate)
        self.assertEqual(original.values, before)
        self.assertEqual(original.calls, [])
        self.assertEqual(len(original.stashed), 1)
        options = original.stashed[0]
        binding["viewer"].flipbook.assert_called_once_with(binding["viewport"], options, open_dialog=False)
        self.assertEqual(options.values["frameRange"], (12.5, 12.5))
        self.assertEqual(options.values["frameIncrement"], 1.0)
        for name in ("outputToMPlay", "initializeSimulations", "useMotionBlur", "useDepthOfField",
                     "renderAllViewports", "useSheetSize", "scopeChannelKeyframesOnly",
                     "appendFramesToCurrent", "leaveFrameAtEnd"):
            self.assertIs(options.values[name], False, name)
        self.assertIs(options.values["visibleTypes"], native.flipbookObjectType.Visible)
        self.assertIs(options.values["antialias"], native.flipbookAntialias.UseViewportSetting)
        self.assertEqual(options.values["resolution"], (1280, 720))
        self.assertEqual(options.values["outputZoom"], 100)
        self.assertIs(options.values["useResolution"], True)
        self.assertIs(options.values["setUseFrameTimeLimit"], True)
        self.assertEqual(options.values["setFrameTimeLimit"], 5.0)
        self.assertIs(options.values["setUseFrameProgressLimit"], False)
        self.assertEqual(validate.call_count, 2)
        native.setFrame.assert_not_called()
        self.assertEqual((mime, width, height), ("image/png", 1280, 720))
        self.assertEqual(metadata["method"], "houdini_native_flipbook")
        self.assertIs(metadata["fresh_evaluation_requested"], True)
        self.assertEqual(metadata["frame"], 12.5)
        self.assertIn("incomplete", metadata["freshness"])
        decoded = QtGui.QImage.fromData(data)
        self.assertEqual(decoded.pixelColor(1, 1), QtGui.QColor("#f1a044"))
        self.assertEqual(decoded.pixelColor(2, 1), QtGui.QColor("#123456"))
        self.assert_temp_cleaned(original)

    def test_portrait_and_small_viewports_keep_proportions_without_upscaling(self):
        for source, expected in (((1080, 1920), (720, 1280)), ((320, 180), (320, 180))):
            with self.subTest(source=source):
                binding, native, original = native_fixture(*source)
                data, mime, width, height, metadata = vision_ui._capture_native(binding, native)
                self.assertEqual((width, height), expected)
                self.assert_temp_cleaned(original)

    def test_playback_and_nonfinite_frame_are_rejected_before_export(self):
        for playing, frame in ((True, 12.5), (False, float("nan")), (False, float("inf"))):
            with self.subTest(playing=playing, frame=frame):
                binding, native, original = native_fixture()
                native.playbar.isPlaying.return_value = playing
                native.frame.return_value = frame
                with self.assertRaises(ValueError):
                    vision_ui._capture_native(binding, native)
                binding["viewer"].flipbookSettings.assert_not_called()
                binding["viewer"].flipbook.assert_not_called()
                native.setFrame.assert_not_called()

    def test_tiny_viewport_is_rejected_before_native_export(self):
        for size in ((1, 100), (100, 1), (0, 0)):
            with self.subTest(size=size):
                binding, native, original = native_fixture(*size)
                with self.assertRaisesRegex(ValueError, "too small"):
                    vision_ui._capture_native(binding, native)
                binding["viewer"].flipbookSettings.assert_not_called()
                binding["viewer"].flipbook.assert_not_called()

    def test_wrong_thread_is_rejected_before_native_reads(self):
        binding, native, original = native_fixture()
        with mock.patch.object(QtCore.QThread, "currentThread", return_value=object()):
            with self.assertRaisesRegex(ValueError, "UI thread"):
                vision_ui._capture_native(binding, native)
        native.frame.assert_not_called()
        native.playbar.isPlaying.assert_not_called()
        binding["viewer"].flipbook.assert_not_called()

    def test_cancelled_failed_missing_or_corrupt_output_cleans_temporary_files(self):
        for mode in ("cancelled", "failed", "missing", "corrupt"):
            with self.subTest(mode=mode):
                binding, native, original = native_fixture()
                def export(target, settings, open_dialog=False):
                    output = Path(settings.values["output"])
                    if mode == "cancelled":
                        raise SyntheticCaptureError("Synthetic user cancellation")
                    if mode == "failed":
                        output.write_bytes(b"partial")
                        raise SyntheticCaptureError("Synthetic native failure")
                    if mode == "corrupt":
                        output.write_bytes(b"invalid image data")
                binding["viewer"].flipbook.side_effect = export
                with self.assertRaises(ValueError):
                    vision_ui._capture_native(binding, native)
                self.assert_temp_cleaned(original)
                native.setFrame.assert_not_called()

    def test_frame_change_before_or_during_capture_is_rejected_without_restoring_time(self):
        for frames, expected_exports in (([12.5, 13.5], 0), ([12.5, 12.5, 13.5], 1)):
            with self.subTest(expected_exports=expected_exports):
                binding, native, original = native_fixture()
                native.frame.side_effect = frames
                with mock.patch.object(vision_ui, "normalized_image") as encode:
                    with self.assertRaisesRegex(ValueError, "frame changed"):
                        vision_ui._capture_native(binding, native)
                self.assertEqual(binding["viewer"].flipbook.call_count, expected_exports)
                encode.assert_not_called()
                native.setFrame.assert_not_called()
                self.assert_temp_cleaned(original)

    def test_validation_failure_before_or_after_capture_discards_output(self):
        for validations, expected_exports in (([ValueError("view changed")], 0),
                                               ([None, ValueError("view changed")], 1)):
            with self.subTest(expected_exports=expected_exports):
                binding, native, original = native_fixture()
                with mock.patch.object(vision_ui, "normalized_image") as encode:
                    with self.assertRaisesRegex(ValueError, "view changed"):
                        vision_ui._capture_native(binding, native, mock.Mock(side_effect=validations))
                self.assertEqual(binding["viewer"].flipbook.call_count, expected_exports)
                encode.assert_not_called()
                self.assert_temp_cleaned(original)


if __name__ == "__main__":
    unittest.main()
