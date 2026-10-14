import json
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import homhelp


class FakeParm:
    """An installed parameter interface."""
    def set(self, value):
        """set(value): Set a scalar or ramp value."""
        raise AssertionError("Documentation must never call the method")

    @property
    def dangerous(self):
        """A property is described without invoking its getter."""
        raise AssertionError("Do not invoke descriptors")


class HomHelpTests(unittest.TestCase):
    def setUp(self):
        self.hou = types.ModuleType("hou")
        self.hou.Parm = FakeParm

    def lookup(self, arguments):
        from unittest import mock
        with mock.patch.object(homhelp.scene, "require_main_thread"):
            return homhelp.lookup(self.hou, arguments)

    def test_symbol_lookup_returns_documentation_without_calling(self):
        result = self.lookup({"symbol": "hou.Parm.set"})
        self.assertIn("scalar or ramp", result["documentation"])
        self.assertEqual(result["source"], "installed_houdini_docstrings")
        result = self.lookup({"symbol": "hou.Parm.dangerous"})
        self.assertIsInstance(result["documentation"], str)

    def test_member_filter_and_pagination_help_discover_capabilities(self):
        self.hou.RampParmTemplate = FakeParm
        self.hou.Ramp = FakeParm
        result = self.lookup({"symbol": "hou", "query": "ramp", "limit": 1})
        self.assertEqual(result["members"], ["Ramp"])
        self.assertEqual(result["next_offset"], 1)
        self.assertEqual(self.lookup({"symbol": "hou", "query": "ramp", "offset": 1})["members"], ["RampParmTemplate"])
        self.assertIn("set", self.lookup({"symbol": "hou.Parm"})["members"])

    def test_invalid_private_runtime_and_non_hom_paths_are_rejected(self):
        self.hou.foreign = types.ModuleType("os")
        self.hou.live = FakeParm()
        for symbol in ("os", "hou._hou", "hou.__dict__", "hou.Parm.__class__", "hou.Parm()", "hou.live.set", "hou.foreign"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                self.lookup({"symbol": symbol})

    def test_unknown_symbol_and_argument_validation(self):
        for args in ({"symbol": "hou.Missing"}, {"symbol": "hou", "code": "bad"},
                     {"symbol": "hou", "limit": True}, {"symbol": "hou", "doc_offset": -1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.lookup(args)

    def test_unicode_documentation_is_bounded_with_lossless_continuation(self):
        self.hou.__doc__ = "🎨" * 20000
        result = self.lookup({"symbol": "hou"})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()), homhelp.MAX_RESULT_BYTES)
        self.assertGreater(result["next_doc_offset"], 0)
        following = self.lookup({"symbol": "hou", "doc_offset": result["next_doc_offset"]})
        self.assertEqual(result["documentation"] + following["documentation"], self.hou.__doc__[:len(result["documentation"]) + len(following["documentation"])])


if __name__ == "__main__":
    unittest.main()
