"""Metadata queries must never cook/evaluate nodes or dynamic menus."""
from pathlib import Path
import json
import sys
import unittest
from unittest import mock
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import inspection


class Template:
    def __init__(self, name, label, dynamic=False, kind="String", data_type="String"):
        self._name, self._label, self.dynamic = name, label, dynamic
        self.kind, self.data_type = kind, data_type
    def name(self): return self._name
    def label(self): return self._label
    def type(self): return "parmTemplateType." + self.kind
    def dataType(self): return "parmData." + self.data_type
    def numComponents(self): return 1
    def namingScheme(self): return "Base1"
    def itemGeneratorScript(self): return "never execute" if self.dynamic else ""
    def menuItems(self):
        if self.dynamic: raise AssertionError("Must not inspect dynamic choices")
        return ("a", "b")
    def menuLabels(self): return ("Alpha", "Beta")
    def scriptCallback(self): return "never execute callback"
    def tags(self): return {}


class Parm:
    def __init__(self, name, value, dynamic=False):
        self._name, self.value = name, value
        self.template = Template(name, name.capitalize(), dynamic)
    def name(self): return self._name
    def parmTemplate(self): return self.template
    def rawValue(self): return self.value
    def keyframes(self): return ()
    def isLocked(self): return False
    def isMultiParmParent(self): return False
    def menuItems(self): raise AssertionError("No evaluated menu")
    def eval(self): raise AssertionError("No expression evaluation")


class Folder(Template):
    def __init__(self, name, children=(), multiparm=False):
        super().__init__(name, name.capitalize(), kind="Folder", data_type="Int")
        self.children, self.multiparm = children, multiparm
    def isActualFolder(self): return not self.multiparm
    def folderType(self): return "folderType.MultiparmBlock" if self.multiparm else "folderType.Tabs"
    def parmTemplates(self): return self.children


class Type:
    def __init__(self, name, label): self._name, self._label = name, label
    def name(self): return self._name
    def description(self): return self._label
    def minNumInputs(self): return 0
    def maxNumInputs(self): return 1
    def parmTemplateGroup(self):
        return NS(entriesWithoutFolders=lambda: [Template("primpath", "Primitive Path")])


def session(parms=()):
    types = {"distantlight": Type("distantlight", "Distant Light"), "sublayer": Type("sublayer", "Sublayer")}
    category = NS(name=lambda: "Lop", nodeTypes=lambda: types)
    nodes = {"/stage": NS(childTypeCategory=lambda: category),
             "/stage/light1": NS(parms=lambda: parms, type=lambda: types["distantlight"])}
    hou = NS(session=NS(), applicationVersionString=lambda: "20.5.445",
             hipFile=NS(basename=lambda: "test.hip"), node=mock.Mock(side_effect=nodes.get),
             nodeTypeCategories=mock.Mock(return_value={"Lop": category}))
    return hou


class DiscoveryTests(unittest.TestCase):
    def schema_for_templates(self, entries, **arguments):
        hou = session()
        node_type = hou.nodeTypeCategories()["Lop"].nodeTypes()["distantlight"]
        node_type.parmTemplateGroup = lambda: NS(entries=lambda: entries)
        return inspection.execute(hou, "node_type_schema", {
            "category": "Lop", "node_type": "distantlight", **arguments})

    def test_multiparm_templates_are_included_with_instance_patterns(self):
        entries = [Folder("folder", [Template("outside", "Outside"), Folder("sources", [
            Template("soppath#", "SOP Path"), Folder("nested#", [Template("value#_#", "Value")], True),
        ], True)])]
        result = self.schema_for_templates(entries)
        records = result["parameters"]
        self.assertEqual([record["template_name"] for record in records],
                         ["outside", "sources", "soppath#", "nested#", "value#_#"])
        for index in (1, 3):
            self.assertTrue(records[index]["multiparm_count"])
            self.assertFalse(records[index]["editable"])
            self.assertEqual(records[index]["value_representation"], "unsupported")
            self.assertEqual(records[index]["editability_reasons"], ["multiparm_count"])
        self.assertEqual(result["template_scan"]["visited_entries"], 6)
        self.assertFalse(result["template_scan"]["truncated"])
        filtered = self.schema_for_templates(entries, query="soppath")
        self.assertEqual(filtered["parameters"][0]["template_name"], "soppath#")

    def test_static_template_scan_caps_collected_parameters(self):
        result = self.schema_for_templates([Template("p%d" % i, "Parameter") for i in range(2050)], limit=1)
        self.assertEqual(result["template_scan"]["collected_templates"], 2048)
        self.assertEqual(result["matched_count"], 2048)
        self.assertTrue(result["template_scan"]["truncated"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["next_offset"], 1)

    def test_deep_folder_scan_is_bounded_without_python_recursion(self):
        nested = Template("unreachable", "Beyond scan limit")
        for index in range(4100):
            nested = Folder("f%d" % index, [nested])
        result = self.schema_for_templates([nested])
        self.assertEqual(result["template_scan"]["visited_entries"], 4096)
        self.assertEqual(result["template_scan"]["collected_templates"], 0)
        self.assertTrue(result["template_scan"]["truncated"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["parameters"], [])

    def test_category_discovery_does_not_require_or_resolve_a_node(self):
        hou = session()
        types = inspection.execute(hou, "list_node_types", {"category": "Lop", "query": "light"})
        schema = inspection.execute(hou, "node_type_schema", {"category": "Lop", "node_type": "distantlight"})
        self.assertEqual(types["node_types"][0]["name"], "distantlight")
        self.assertEqual(schema["category"], "Lop")
        self.assertIsNone(schema["parent"])
        hou.node.assert_not_called()
        with self.assertRaises(ValueError):
            inspection.execute(hou, "list_node_types", {"category": "NotInstalled"})

    def test_types_use_actual_network_category_and_exact_names(self):
        hou = session()
        result = inspection.execute(hou, "list_node_types", {"parent": "/stage", "query": "LIGHT"})
        self.assertEqual(result["category"], "Lop")
        self.assertEqual([item["name"] for item in result["node_types"]], ["distantlight"])
        self.assertIsNone(result["next_offset"])

    def test_static_template_before_creation_and_paginated_actual_parameters(self):
        hou = session([Parm("intensity", "2"), Parm("primpath", "/world/key"), Parm("animated", "$F")])
        before = inspection.execute(hou, "node_type_schema", {"parent": "/stage", "node_type": "distantlight"})
        self.assertEqual(before["parameters"][0]["template_name"], "primpath")
        result = inspection.execute(hou, "parameter_schema", {"path": "/stage/light1", "offset": 1, "limit": 1})
        self.assertEqual(result["parameters"][0]["name"], "primpath")
        self.assertEqual(result["parameters"][0]["raw_value"], "/world/key")
        self.assertEqual(result["next_offset"], 2)
        filtered = inspection.execute(hou, "parameter_schema", {"path": "/stage/light1", "query": "animated"})
        self.assertEqual(filtered["parameters"][0]["raw_value"], "$F")

    def test_dynamic_menu_and_callbacks_are_not_executed(self):
        result = inspection.execute(session([Parm("choice", "a", True)]), "parameter_schema", {"path": "/stage/light1"})
        record = result["parameters"][0]
        self.assertTrue(record["dynamic_menu"])
        self.assertTrue(record["has_callback"])
        self.assertNotIn("menu", record)
        self.assertNotIn("never execute", str(result))

    def test_invalid_discovery_arguments_rejected_before_hom(self):
        for operation, args in [
            ("list_node_types", {"parent": "/stage/../obj"}),
            ("list_node_types", {"parent": "/stage", "offset": True}),
            ("list_node_types", {"parent": "/stage", "category": "Lop"}),
            ("list_node_types", {}),
            ("list_node_types", {"category": "../Lop"}),
            ("parameter_schema", {"path": "/stage/light1", "limit": 33}),
            ("parameter_schema", {"path": "/stage/light1", "query": "x" * 129}),
            ("node_type_schema", {"parent": "/stage", "node_type": "sop/*"}),
            ("node_type_schema", {"parent": "/stage", "node_type": "light", "code": "x"}),
        ]:
            hou = session()
            with self.subTest(args=args), self.assertRaises(ValueError):
                inspection.execute(hou, operation, args)
            hou.node.assert_not_called()

    def test_static_defaults_are_reported_without_expression_evaluation(self):
        hou = session()
        template = Template("f", "Frame Range", kind="Float", data_type="Float")
        template.defaultExpression = lambda: ("$FSTART", "$FEND", "")
        template.defaultExpressionLanguage = lambda: ("exprLanguage.Hscript",) * 3
        node_type = hou.nodeTypeCategories()["Lop"].nodeTypes()["distantlight"]
        node_type.parmTemplateGroup = lambda: NS(entriesWithoutFolders=lambda: [template])
        result = inspection.execute(hou, "node_type_schema", {"category": "Lop", "node_type": "distantlight"})
        record = result["parameters"][0]
        self.assertEqual(record["default_expressions"], ["$FSTART", "$FEND", ""])
        self.assertEqual(record["default_expression_languages"], ["exprLanguage.Hscript"] * 3)
        self.assertFalse(record["defaults_truncated"])
        self.assertEqual(record["value_representation"], "number")

    def test_actual_spare_parameter_reports_authored_default_and_numeric_limits(self):
        spare, builtin, unknown = Parm("falloff", "1.25"), Parm("scale", "2"), Parm("legacy", "a")
        spare.isSpare = lambda: True
        builtin.isSpare = lambda: False
        spare.template = Template("falloff", "Falloff", kind="Float", data_type="Float")
        spare.template.defaultValue = lambda: (0.75,)
        spare.template.defaultExpression = lambda: ("",)
        spare.template.defaultExpressionLanguage = lambda: ("exprLanguage.Hscript",)
        spare.template.minValue = lambda: 0.0
        spare.template.maxValue = lambda: 5.0
        spare.template.minIsStrict = lambda: True
        spare.template.maxIsStrict = lambda: False
        result = inspection.execute(session([spare, builtin, unknown]), "parameter_schema", {"path": "/stage/light1"})
        record = result["parameters"][0]
        self.assertTrue(record["is_spare"])
        self.assertFalse(result["parameters"][1]["is_spare"])
        self.assertIsNone(result["parameters"][2]["is_spare"])
        self.assertEqual(record["raw_value"], "1.25")
        self.assertEqual(record["default_values"], [0.75])
        self.assertFalse(record["default_values_unavailable"])
        self.assertFalse(record["defaults_truncated"])
        self.assertEqual(record["default_expressions"], [""])
        self.assertEqual(record["default_expression_languages"], ["exprLanguage.Hscript"])
        self.assertEqual(record["minimum"], 0.0)
        self.assertEqual(record["maximum"], 5.0)
        self.assertTrue(record["minimum_is_strict"])
        self.assertFalse(record["maximum_is_strict"])

    def test_static_vector_and_menu_defaults_preserve_values_without_instances(self):
        vector = Template("direction", "Direction", kind="Float", data_type="Float")
        vector.numComponents = lambda: 3
        vector.defaultValue = lambda: (-1.0, 0.0, 2.0)
        vector.minValue = lambda: -10.0
        vector.maxValue = lambda: 10.0
        vector.minIsStrict = lambda: False
        vector.maxIsStrict = lambda: True
        menu = Template("mode", "Mode", kind="Menu", data_type="Int")
        menu.defaultValue = lambda: 1
        menu.menuUseToken = lambda: False
        menu.menuItems = lambda: ("volume", "particles")
        menu.menuLabels = lambda: ("Volume", "Particles")
        result = self.schema_for_templates([vector, menu])
        first, second = result["parameters"]
        self.assertEqual(first["default_values"], [-1.0, 0.0, 2.0])
        self.assertEqual(first["components"], 3)
        self.assertEqual(first["minimum"], -10.0)
        self.assertTrue(first["maximum_is_strict"])
        self.assertNotIn("is_spare", first)
        self.assertEqual(second["default_values"], [1])
        self.assertEqual(second["menu"][1]["token"], "particles")
        self.assertEqual(second["value_representation"], "menu_token_or_zero_based_index")
        self.assertIsNone(second["minimum"])

    def test_default_scalars_keep_primitive_types_and_strings_unevaluated(self):
        values = (42, -2.5, True, "$HIP/asset.$F4.bgeo.sc")
        templates = []
        for index, value in enumerate(values):
            template = Template("p%d" % index, "Default")
            template.defaultValue = lambda current=value: current
            templates.append(template)
        result = self.schema_for_templates(templates)
        self.assertEqual([record["default_values"] for record in result["parameters"]], [[value] for value in values])
        self.assertIs(result["parameters"][2]["default_values"][0], True)
        self.assertTrue(all(not record["default_values_unavailable"] for record in result["parameters"]))

    def test_missing_optional_default_and_spare_methods_report_unknown(self):
        result = inspection.execute(session([Parm("old", "value")]), "parameter_schema", {"path": "/stage/light1"})
        record = result["parameters"][0]
        self.assertIsNone(record["default_values"])
        self.assertTrue(record["default_values_unavailable"])
        self.assertIsNone(record["is_spare"])
        for key in ("minimum", "maximum", "minimum_is_strict", "maximum_is_strict"):
            self.assertIsNone(record[key])

    def test_unsupported_and_nonfinite_defaults_are_null_without_object_repr(self):
        class Unserializable:
            def __str__(self):
                raise AssertionError("Never stringify a Ramp or arbitrary HOM object")
            __repr__ = __str__
        template = Template("ramp_like", "Unsupported default")
        template.defaultValue = lambda: (Unserializable(), float("nan"), float("inf"), 1 << 20000, True)
        result = self.schema_for_templates([template])
        record = result["parameters"][0]
        self.assertEqual(record["default_values"], [None, None, None, None, True])
        self.assertTrue(record["default_values_unavailable"])
        self.assertEqual(result["read_failures"], 4)
        json.dumps(result, allow_nan=False)

    def test_optional_limit_and_spare_failures_do_not_abort_query(self):
        parm = Parm("falloff", "0.5")
        parm.template = Template("falloff", "Falloff", kind="Float", data_type="Float")
        parm.template.defaultExpression = lambda: ("$F",)
        def fail():
            raise RuntimeError("PRIVATE_HOM_DETAILS")
        for method in ("defaultValue", "minValue", "maxValue", "minIsStrict", "maxIsStrict"):
            setattr(parm.template, method, fail)
        parm.isSpare = fail
        result = inspection.execute(session([parm]), "parameter_schema", {"path": "/stage/light1"})
        record = result["parameters"][0]
        self.assertEqual(result["read_failures"], 6)
        self.assertIsNone(record["is_spare"])
        self.assertIsNone(record["default_values"])
        self.assertEqual(record["default_expressions"], ["$F"])
        self.assertIsNone(record["minimum"])
        self.assertIsNone(record["maximum_is_strict"])
        self.assertNotIn("PRIVATE_HOM_DETAILS", str(result))

    def test_numeric_bounds_and_flags_must_have_finite_correct_types(self):
        template = Template("count", "Count", kind="Int", data_type="Int")
        template.defaultValue = lambda: (2,)
        template.minValue = lambda: float("nan")
        template.maxValue = lambda: {"unsupported": 1}
        template.minIsStrict = lambda: "yes"
        template.maxIsStrict = lambda: 1
        result = self.schema_for_templates([template])
        record = result["parameters"][0]
        for key in ("minimum", "maximum", "minimum_is_strict", "maximum_is_strict"):
            self.assertIsNone(record[key])
        self.assertEqual(result["read_failures"], 4)
        json.dumps(result, allow_nan=False)

    def test_default_component_limit_is_explicitly_truncated(self):
        template = Template("many", "Many", kind="Int", data_type="Int")
        template.defaultValue = lambda: tuple(range(12))
        result = self.schema_for_templates([template])
        self.assertEqual(result["parameters"][0]["default_values"], list(range(8)))
        self.assertTrue(result["parameters"][0]["defaults_truncated"])
        self.assertTrue(result["truncated"])

    def test_menu_token_raw_value_and_toggle_words_remain_editable(self):
        menu, toggle = Parm("type", "particles"), Parm("enabled", "on")
        menu.template = Template("type", "Type", kind="Menu", data_type="Int")
        menu.template.menuUseToken = lambda: False
        menu.template.menuItems = lambda: ("volume", "particles")
        menu.template.menuLabels = lambda: ("Volume", "Particles")
        toggle.template = Template("enabled", "Enabled", kind="Toggle", data_type="Int")
        result = inspection.execute(session([menu, toggle]), "parameter_schema", {"path": "/stage/light1"})
        first, second = result["parameters"]
        self.assertFalse(first["menu_use_token"])
        self.assertEqual(first["value_representation"], "menu_token_or_zero_based_index")
        self.assertEqual(first["raw_value"], "particles")
        self.assertTrue(first["editable"])
        self.assertEqual(second["value_representation"], "boolean_or_0_or_1")
        self.assertTrue(second["editable"])
        menu.template.menuUseToken = lambda: True
        result = inspection.execute(session([menu]), "parameter_schema", {"path": "/stage/light1"})
        self.assertEqual(result["parameters"][0]["value_representation"], "menu_token_or_integer_token")

    def test_parameter_editability_explains_protected_and_unsupported_values(self):
        parms = [Parm(name, value) for name, value in (
            ("expression", "$FSTART"), ("locked", "a"), ("count", "2"),
            ("python", "return 1"), ("button", "0"), ("invalid_number", "particles"),
            ("nonfinite", "nan"), ("invalid_toggle", "maybe"), ("oversized", "x" * 8193),
        )]
        parms[0].keyframes = lambda: (object(),)
        parms[1].isLocked = lambda: True
        parms[2].isMultiParmParent = lambda: True
        parms[3].template.tags = lambda: {"editorlang": "python"}
        for index, kind, data_type in ((4, "Button", "Int"), (5, "Int", "Int"),
                                       (6, "Float", "Float"), (7, "Toggle", "Int")):
            parms[index].template = Template(parms[index].name(), "Test", kind=kind, data_type=data_type)
        expected = ("authored_channel_protected", "locked_parameter", "multiparm_count", "python_code_parameter",
                    "unsupported_parameter_type", "nonliteral_current_value", "nonliteral_current_value",
                    "nonliteral_current_value", "current_value_outside_text_limits")
        result = inspection.execute(session(parms), "parameter_schema", {"path": "/stage/light1"})
        for record, reason in zip(result["parameters"], expected):
            with self.subTest(name=record["name"]):
                self.assertFalse(record["editable"])
                self.assertIn(reason, record["editability_reasons"])

    def test_failed_optional_metadata_is_safe_and_missing_state_is_not_editable(self):
        parm = Parm("choice", "a")
        def fail():
            raise RuntimeError("private callback or path must never be returned")
        for name in ("defaultExpression", "defaultExpressionLanguage", "menuUseToken", "tags", "itemGeneratorScript"):
            setattr(parm.template, name, fail)
        parm.template.menuItems = lambda: self.fail("Unknown menu must not be inspected")
        parm.rawValue = fail
        parm.isLocked = fail
        result = inspection.execute(session([parm]), "parameter_schema", {"path": "/stage/light1"})
        record = result["parameters"][0]
        self.assertGreaterEqual(result["read_failures"], 7)
        self.assertIsNone(record["menu_use_token"])
        self.assertIsNone(record["dynamic_menu"])
        self.assertIsNone(record["raw_value"])
        self.assertIsNone(record["locked"])
        self.assertEqual(record["default_expressions"], [])
        self.assertFalse(record["editable"])
        self.assertIn("metadata_unavailable", record["editability_reasons"])
        self.assertIn("current_value_unavailable", record["editability_reasons"])
        self.assertNotIn("private callback", str(result))

    def test_large_unicode_defaults_remain_bounded_and_page_advances(self):
        parm = Parm("choices", "😀" * 4096)
        parm.template.defaultValue = lambda: ("😀" * 2000,) * 16
        parm.template.defaultExpression = lambda: ("😀" * 2000,) * 16
        parm.template.defaultExpressionLanguage = lambda: ("exprLanguage.Hscript",) * 16
        parm.template.menuItems = lambda: ("😀" * 256,) * 32
        parm.template.menuLabels = lambda: ("😀" * 256,) * 32
        result = inspection.execute(session([parm, Parm("next", "value")]), "parameter_schema", {"path": "/stage/light1", "limit": 1})
        record = result["parameters"][0]
        self.assertTrue(record["defaults_truncated"])
        self.assertLessEqual(len(record["default_expressions"]), 8)
        self.assertLessEqual(len(record["default_values"]), 8)
        self.assertTrue(all(len(value) <= 512 for value in record["default_values"]))
        self.assertTrue(all(len(value) <= 512 for value in record["default_expressions"]))
        self.assertEqual(result["next_offset"], 1)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(inspection.scene._size(result), inspection.MAX_RESULT_BYTES)

    def test_large_raw_values_are_bounded_and_marked(self):
        parms = [Parm("p%d" % i, "😀" * 8000) for i in range(40)]
        result = inspection.execute(session(parms), "parameter_schema", {"path": "/stage/light1"})
        self.assertTrue(result["truncated"])
        self.assertTrue(result["parameters"][0]["value_truncated"])
        self.assertGreater(result["next_offset"], 0)
        self.assertLessEqual(inspection.scene._size(result), inspection.MAX_RESULT_BYTES)

    def test_one_oversized_unicode_menu_still_advances_pagination(self):
        parm = Parm("choices", "😀" * 8000)
        parm.template.menuItems = lambda: ("😀" * 256,) * 32
        parm.template.menuLabels = lambda: ("😀" * 256,) * 32
        result = inspection.execute(session([parm, Parm("next", "value")]), "parameter_schema", {"path": "/stage/light1", "limit": 1})
        self.assertEqual(result["next_offset"], 1)
        self.assertTrue(result["parameters"][0]["menu_truncated"])
        self.assertTrue(result["truncated"])
        self.assertLessEqual(inspection.scene._size(result), inspection.MAX_RESULT_BYTES)


if __name__ == "__main__":
    unittest.main()
