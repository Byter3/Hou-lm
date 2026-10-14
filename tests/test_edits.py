"""Typed edit plans tested against an inert HOM double, never a live scene."""

from contextlib import contextmanager
import copy
from pathlib import Path
import json
import sys
import threading
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import edits


class Template:
    def __init__(self, kind="Float", tags=None, *, name="value", count=1, naming="XYZW",
                 items=(), dynamic="", use_token=False, children=(), actual_folder=True):
        self.kind = kind
        self._tags = tags or {}
        self._name, self.count, self.naming = name, count, naming
        self.items, self.dynamic, self.use_token = items, dynamic, use_token
        self._children, self.actual_folder = children, actual_folder
        self._label, self.defaults, self.settings = name, (), {}

    def type(self):
        return "parmTemplateType." + self.kind

    def dataType(self):
        return "parmData." + ({"Menu": "Int", "Toggle": "Int"}.get(self.kind, self.kind))

    def tags(self):
        return self._tags

    def name(self):
        return self._name

    def label(self):
        return self._label

    def numComponents(self):
        return self.count

    def namingScheme(self):
        return "parmNamingScheme." + self.naming

    def menuItems(self):
        if self.dynamic:
            raise AssertionError("Never request dynamically generated menu items")
        return self.items

    def itemGeneratorScript(self):
        return self.dynamic

    def menuUseToken(self):
        return self.use_token

    def parmTemplates(self):
        return self._children

    def isActualFolder(self):
        return self.actual_folder

    def asCode(self):
        return json.dumps({"name": self._name, "label": self._label, "kind": self.kind,
                           "count": self.count, "naming": self.naming, "tags": self._tags,
                           "items": self.items, "dynamic": self.dynamic, "use_token": self.use_token,
                           "defaults": self.defaults, "settings": self.settings,
                           "children": [item.asCode() for item in self._children]}, sort_keys=True)

    def minIsStrict(self):
        return self.settings.get("min_is_strict", False)

    def maxIsStrict(self):
        return self.settings.get("max_is_strict", False)

    def minValue(self):
        return self.settings.get("min", 0)

    def maxValue(self):
        return self.settings.get("max", 10)

    def defaultValue(self):
        if self.kind == "Toggle":
            return self.defaults[0]
        if self.kind == "Menu":
            return self.items.index(self.defaults[0])
        return self.defaults

    def menuLabels(self):
        return self.settings.get("menu_labels", self.items)

    def look(self):
        return self.settings.get("look", "Regular")

    def scriptCallback(self):
        return self.settings.get("script_callback", "")

    def defaultExpression(self):
        return self.settings.get("default_expression", ())

    def isHidden(self):
        return self.settings.get("hidden", False)


class Group:
    def __init__(self, *templates):
        self.templates = templates

    def entries(self):
        return copy.deepcopy(self.templates)

    def entriesWithoutFolders(self):
        raise AssertionError("This HOM method omits multiparm children")

    def append(self, template):
        self.templates = tuple(self.templates) + (copy.deepcopy(template),)

    def find(self, name):
        pending = list(self.templates)
        while pending:
            template = pending.pop()
            if template.name() == name:
                return copy.deepcopy(template)
            pending.extend(template._children)
        return None

    def remove(self, name):
        # Spare additions are intentionally top-level in this implementation.
        self.templates = tuple(item for item in self.templates if item.name() != name)

    def asDialogScript(self):
        return json.dumps([item.asCode() for item in self.templates])


class Keyframe:
    def __init__(self, expression):
        self.expression = expression

    def asCode(self):
        return "keyframe:" + self.expression


class Parm:
    def __init__(self, value, kind="Float", tags=None):
        if kind == "Float" and type(value) in (int, float, bool):
            value = float(value)
        self.value, self.template = value, Template(kind, tags)
        self.keys, self.locked, self.multi = (), False, False
        self.calls, self.fail_once, self.fail_always = [], False, False
        self.side_effect = None
        self._name = "value"

    def rawValue(self):
        if self.template.kind == "Toggle" and type(self.value) in (bool, int):
            return "on" if self.value else "off"
        return str(self.value)

    def name(self):
        return self._name

    def keyframes(self):
        return self.keys

    def isLocked(self):
        return self.locked

    def isMultiParmParent(self):
        return self.multi

    def parmTemplate(self):
        return self.template

    def isSpare(self):
        return edits._SPARE_OWNER_TAG in self.template.tags()

    def set(self, value, *, follow_parm_reference):
        self.calls.append((value, follow_parm_reference))
        self.value = value
        if self.template.kind == "Float" and type(value) in (int, float, bool):
            self.value = float(value)
        if self.template.kind == "Menu" and type(value) in (int, bool) and self.template.items:
            self.value = str(int(value)) if self.template.use_token else self.template.items[value]
        if self.side_effect:
            self.side_effect()
        if self.fail_once or self.fail_always:
            self.fail_once = False
            raise RuntimeError("private details must not be returned")

    def eval(self):
        raise AssertionError("Edits must never evaluate parameters")

    evalAsString = eval
    evalAsFloat = eval
    setExpression = eval
    pressButton = eval
    menuItems = eval


class NodeType:
    def __init__(self, name, child_category=None, inputs=100, outputs=10):
        self._name, self.child_category = name, child_category
        self.inputs, self.outputs = inputs, outputs

    def name(self):
        return self._name

    def childTypeCategory(self):
        return self.child_category

    def maxNumInputs(self):
        return self.inputs

    def maxNumOutputs(self):
        return self.outputs


class Connection:
    def __init__(self, index, source, output):
        self.index, self.source, self.output = index, source, output

    def inputIndex(self):
        return self.index

    def inputItem(self):
        return self.source

    def inputItemOutputIndex(self):
        return self.output


class Node:
    def __init__(self, hou, path, node_type):
        self.hou, self._path, self._type = hou, path, node_type
        self.id = hou.next_id
        hou.next_id += 1
        self.parameters = {"density": Parm(1.0), "snippet": Parm("@Cd = 0;", "String"),
                           "assetpath": Parm("$HIP/input.usd", "String"), "enabled": Parm(1, "Toggle")}
        self.wires, self.flags, self.pos = {}, {"display": False, "render": False}, (0.0, 0.0)
        self.locked_inside, self.destroyed, self.unordered = False, False, False
        self._group = None
        hou.nodes[path] = self
        self.parms()

    def path(self):
        return self._path

    def sessionId(self):
        if self.destroyed:
            raise RuntimeError("Node deleted")
        return self.id

    def type(self):
        return self._type

    def parm(self, name):
        parm = self.parameters.get(name)
        if parm is not None:
            parm._name = name
            if parm.template.count == 1:
                parm.template._name = name
        return parm

    def parms(self):
        for name, parm in self.parameters.items():
            parm._name = name
            if parm.template.count == 1:
                parm.template._name = name
        return tuple(self.parameters.values())

    def parmTemplateGroup(self):
        if self._group is not None:
            return copy.deepcopy(self._group)
        self.parms()
        return Group(*(copy.deepcopy(parm.template) for parm in self.parameters.values()))

    def setParmTemplateGroup(self, group, *, rename_conflicting_parms):
        if rename_conflicting_parms:
            raise AssertionError("Never rename conflicts")
        self.hou.layout_calls.append(self.path())
        old, replacements = self.parameters, {}
        for template in group.entries():
            if template.count == 1:
                names = (template.name(),)
            else:
                suffixes = edits._SUFFIXES[template.naming]
                names = tuple(template.name() + suffix for suffix in suffixes[:template.count])
            for index, name in enumerate(names):
                if name in old:
                    parm = copy.copy(old[name])  # Real layout changes can replace HOM handles.
                else:
                    default = template.defaults[index]
                    parm = Parm(default, template.kind)
                parm.template = copy.deepcopy(template)
                parm._name = name
                replacements[name] = parm
        self.parameters = replacements
        self._group = copy.deepcopy(group)
        if self.hou.on_layout:
            self.hou.on_layout(self)
        if self.hou.layout_raises_once:
            self.hou.layout_raises_once = False
            raise RuntimeError("private layout failure after mutation")

    def isInsideLockedHDA(self):
        return self.locked_inside

    def inputConnections(self):
        return tuple(Connection(index, source, output) for index, (source, output) in self.wires.items())

    def setInput(self, index, source, output=0):
        self.hou.mutations.append(("connect", self._path, index, source, output))
        if source is None:
            self.wires.pop(index, None)
            if self.unordered:
                self.wires = {i: wire for i, (_, wire) in enumerate(sorted(self.wires.items()))}
        else:
            self.wires[index] = (source, output)

    def children(self):
        return tuple(node for path, node in self.hou.nodes.items()
                     if path != self._path and edits._parent(path) == self._path)

    def childTypeCategory(self):
        return self._type.childTypeCategory()

    def createNode(self, node_type, **kwargs):
        self.hou.create_calls.append((self._path, node_type, kwargs))
        path = self._path.rstrip("/") + "/" + kwargs["node_name"]
        node = Node(self.hou, path, self.hou.nodeType(self.childTypeCategory(), node_type))
        if self.hou.on_create:
            self.hou.on_create(node)
        if self.hou.create_raises:
            raise RuntimeError("Creation failed after node existed")
        return node

    def destroy(self):
        self.hou.mutations.append(("destroy", self._path))
        for child in tuple(self.children()):
            child.destroy()
        self.destroyed = True
        self.hou.nodes.pop(self._path)
        for node in self.hou.nodes.values():
            node.wires = {index: wire for index, wire in node.wires.items() if wire[0] != self}

    def isDisplayFlagSet(self):
        return self.flags["display"]

    def isRenderFlagSet(self):
        return self.flags["render"]

    def _flag(self, name, value):
        if value:
            for sibling in self.hou.node(edits._parent(self._path)).children():
                sibling.flags[name] = False
        self.flags[name] = value

    def setDisplayFlag(self, value):
        self._flag("display", value)

    def setRenderFlag(self, value):
        self._flag("render", value)

    def position(self):
        return self.pos

    def setPosition(self, value):
        self.pos = tuple(value)

    def cook(self, *args, **kwargs):
        raise AssertionError("Edits must not force cooking, rendering, saving, or Python execution")

    render = cook
    geometry = cook


class Undos:
    def __init__(self):
        self.groups = []

    @contextmanager
    def group(self, label):
        self.groups.append(label)
        yield

    def performUndo(self):
        raise AssertionError("Never undo unrelated artist actions")


class Hou:
    Vector2 = tuple
    parmNamingScheme = SimpleNamespace(XYZW="XYZW", RGBA="RGBA")
    parmLook = SimpleNamespace(ColorSquare="ColorSquare", Vector="Vector")

    @staticmethod
    def _template(kind, name, label, count=1, default_value=(), **options):
        template = Template(kind, options.pop("tags", {}), name=name, count=count,
                            naming=options.pop("naming_scheme", "XYZW"))
        template._label, template.defaults, template.settings = label, tuple(default_value), options
        return template

    @staticmethod
    def FloatParmTemplate(name, label, count, **options):
        return Hou._template("Float", name, label, count, **options)

    @staticmethod
    def IntParmTemplate(name, label, count, **options):
        return Hou._template("Int", name, label, count, **options)

    @staticmethod
    def StringParmTemplate(name, label, count, **options):
        return Hou._template("String", name, label, count, **options)

    @staticmethod
    def ToggleParmTemplate(name, label, default_value=False, **options):
        return Hou._template("Toggle", name, label, default_value=(default_value,), **options)

    @staticmethod
    def MenuParmTemplate(name, label, menu_items, menu_labels=(), default_value=0, **options):
        template = Hou._template("Menu", name, label, default_value=(menu_items[default_value],), **options)
        template.items = tuple(menu_items)
        template.settings["menu_labels"] = tuple(menu_labels)
        return template

    def __init__(self):
        self.nodes, self.next_id, self.mutations, self.create_calls = {}, 1, [], []
        self.create_raises = False
        self.on_create = None
        self.on_layout, self.layout_raises_once, self.layout_calls = None, False, []
        self.undos = Undos()
        self.types = {
            ("Object", "geo"): NodeType("geo", "Sop"),
            ("Sop", "attribwrangle"): NodeType("attribwrangle"),
            ("Sop", "null"): NodeType("null"),
            ("Lop", "reference"): NodeType("reference"),
        }
        Node(self, "/obj", NodeType("obj", "Object"))
        Node(self, "/obj/geo1", self.types[("Object", "geo")])
        self.a = Node(self, "/obj/geo1/a", self.types[("Sop", "attribwrangle")])
        self.b = Node(self, "/obj/geo1/b", self.types[("Sop", "null")])
        self.a.flags = {"display": True, "render": True}
        Node(self, "/stage", NodeType("lopnet", "Lop"))

    def node(self, path):
        return self.nodes.get(path)

    def nodeType(self, category, name):
        return self.types.get((category, name))


def arguments(*operations):
    return {"summary": "Artist reviewed change", "operations": list(operations)}


def parameter(path="/obj/geo1/a", name="density", value=2.0):
    return {"op": "set_parameter", "path": path, "parameter": name, "value": value}


def spare(path="/obj/geo1/a", name="falloff", kind="float", default=1.0, **options):
    return {"op": "add_spare_parameter", "path": path, "name": name, "label": "Artist control",
            "parameter_type": kind, "default": default, **options}


class EditTests(unittest.TestCase):
    def setUp(self):
        self.hou = Hou()

    def test_add_float_review_is_inert_and_add_then_set_works(self):
        request = arguments(spare(default=0.5, min_value=0, max_value=1, min_strict=True, max_strict=True),
                            parameter(name="falloff", value=0.75),
                            parameter(name="snippet", value='@P *= chf("falloff");'))
        plan = edits.prepare(self.hou, request)
        self.assertIn('"parameter_type": "float"', plan.summary)
        self.assertIn('"default": 0.5', plan.summary)
        self.assertIn('"min_strict": true', plan.summary)
        self.assertIn('Before: 0.5', plan.summary)
        self.assertEqual(self.hou.layout_calls, [])
        self.assertIsNone(self.hou.a.parm("falloff"))
        request["operations"][0]["default"] = 0.9
        result = edits.apply(self.hou, plan)
        self.assertTrue(result["success"])
        self.assertEqual(self.hou.a.parm("falloff").value, 0.75)
        self.assertEqual(self.hou.a.parm("falloff").template.defaults, (0.5,))
        self.assertEqual(self.hou.a.parm("snippet").value, '@P *= chf("falloff");')
        self.assertEqual(result["changed_paths"], [self.hou.a.path()])

    def test_all_generic_spare_types_defaults_and_vector_color_components(self):
        menu = [{"token": "near", "label": "Near Field"}, {"token": "far", "label": "Far Field"}]
        operations = [spare(name="float_value", default=2.5), spare(name="int_value", kind="int", default=4),
                      spare(name="text_value", kind="string", default="$HIP/test.usd"),
                      spare(name="toggle_value", kind="toggle", default=True),
                      spare(name="vector_value", kind="vector", default=[1, 2, 3]),
                      spare(name="color_value", kind="color", default=[0.1, 0.2, 0.3]),
                      spare(name="menu_value", kind="menu", default="far", menu_items=menu),
                      parameter(name="vector_valuey", value=9), parameter(name="color_valueb", value=0.8)]
        plan = edits.prepare(self.hou, arguments(*operations))
        self.assertIn('"token": "near"', plan.summary)
        self.assertIn('"label": "Far Field"', plan.summary)
        operations[4]["default"][0] = 99
        menu[1]["token"] = "changed_after_review"
        self.assertTrue(edits.apply(self.hou, plan)["success"])
        expected = {"float_value": 2.5, "int_value": 4, "text_value": "$HIP/test.usd", "toggle_value": True,
                    "vector_valuex": 1, "vector_valuey": 9, "vector_valuez": 3,
                    "color_valuer": 0.1, "color_valueg": 0.2, "color_valueb": 0.8, "menu_value": "far"}
        for name, value in expected.items():
            self.assertEqual(self.hou.a.parm(name).value, value)
            self.assertTrue(self.hou.a.parm(name).isSpare())
        self.assertEqual(self.hou.a.parm("vector_valuex").template.settings["look"], "Vector")
        self.assertEqual(self.hou.a.parm("color_valuer").template.settings["look"], "ColorSquare")

    def test_create_add_and_set_work_in_one_reviewed_batch(self):
        path = "/obj/geo1/controlled"
        plan = edits.prepare(self.hou, arguments(
            {"op": "create_node", "parent": "/obj/geo1", "node_type": "attribwrangle", "name": "controlled"},
            spare(path=path, kind="vector", default=[1, 2, 3]),
            parameter(path, "falloffz", 4), parameter(path, "snippet", '@P += chv("falloff");'),
        ))
        self.assertTrue(edits.apply(self.hou, plan)["success"])
        self.assertEqual(self.hou.node(path).parm("falloffz").value, 4)
        self.assertEqual(len(self.hou.undos.groups), 1)

    def test_spare_names_reject_existing_and_planned_tuple_component_collisions(self):
        self.hou.a.parameters["existingx"] = Parm(1)
        self.hou.a.parameters["existingx"].template = Template(name="existing", count=3)
        self.hou.types[("Sop", "null")].parmTemplateGroup = lambda: Group(Template(name="t", count=3))
        invalid = [arguments(spare(name="density")), arguments(spare(name="existing")),
                   arguments(spare(name="existingx")), arguments(spare(name="density", kind="vector", default=[0, 0, 0])),
                   arguments(spare(name="v", kind="vector", default=[0, 0, 0]), spare(name="vx")),
                   arguments(spare(name="vx"), spare(name="v", kind="vector", default=[0, 0, 0])),
                   arguments({"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "new"},
                             spare(path="/obj/geo1/new", name="tx"))]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(edits.EditError) as caught:
                edits.prepare(self.hou, request)
            self.assertEqual(caught.exception.code, "parameter_name_collision")
        self.assertEqual(self.hou.layout_calls, [])
        self.assertEqual(self.hou.create_calls, [])

    def test_spare_layout_stale_guard_refuses_changes_after_review(self):
        plan = edits.prepare(self.hou, arguments(spare()))
        group = self.hou.a.parmTemplateGroup()
        group.append(Hou.FloatParmTemplate("artist_control", "Artist", 1, default_value=(7,)))
        self.hou.a.setParmTemplateGroup(group, rename_conflicting_parms=False)
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["status"], "stale")
        self.assertIsNone(self.hou.a.parm("falloff"))
        self.assertEqual(self.hou.a.parm("artist_control").value, 7)
        self.assertEqual(len(self.hou.layout_calls), 1)

    def test_spare_add_preserves_existing_raw_references_keyframes_and_locks(self):
        self.hou.a.parm("density").value = 'ch("../other/amount")'
        self.hou.a.parm("density").keys = (Keyframe('ch("../other/amount")'),)
        self.hou.a.parm("assetpath").locked = True
        self.hou.a.parm("assetpath").value = "$HIP/asset.$F4.usd"
        old = self.hou.a.parm("density")
        result = edits.apply(self.hou, edits.prepare(self.hou, arguments(spare())))
        self.assertTrue(result["success"])
        current = self.hou.a.parm("density")
        self.assertIsNot(current, old)
        self.assertEqual(current.rawValue(), 'ch("../other/amount")')
        self.assertEqual(current.keys[0].expression, 'ch("../other/amount")')
        self.assertEqual(current.calls, [])
        self.assertEqual(self.hou.a.parm("assetpath").rawValue(), "$HIP/asset.$F4.usd")
        self.assertTrue(self.hou.a.parm("assetpath").isLocked())

    def test_spare_rollback_re_resolves_handles_and_reverses_in_exact_order(self):
        original = self.hou.a.parm("density")
        self.hou.b.parm("density").fail_once = True
        plan = edits.prepare(self.hou, arguments(
            parameter(value=2), spare(), parameter(value=3),
            spare(name="tint", kind="color", default=[0, 0, 0]), parameter(name="tintr", value=0.8),
            parameter("/obj/geo1/b", value=5),
        ))
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        self.assertFalse(result["manual_undo_needed"])
        self.assertIsNot(self.hou.a.parm("density"), original)
        self.assertEqual(self.hou.a.parm("density").value, 1.0)
        self.assertEqual(self.hou.b.parm("density").value, 1.0)
        self.assertIsNone(self.hou.a.parm("falloff"))
        self.assertIsNone(self.hou.a.parm("tintr"))

    def test_spare_rollback_preserves_an_unrelated_definition_added_by_callback(self):
        def callback(node):
            self.hou.on_layout = None
            group = node.parmTemplateGroup()
            group.append(Hou.FloatParmTemplate("artist_control", "Artist", 1, default_value=(7,)))
            node.setParmTemplateGroup(group, rename_conflicting_parms=False)
        self.hou.on_layout = callback
        self.hou.b.parm("density").fail_once = True
        plan = edits.prepare(self.hou, arguments(spare(), parameter("/obj/geo1/b")))
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        self.assertIsNone(self.hou.a.parm("falloff"))
        self.assertEqual(self.hou.a.parm("artist_control").value, 7)

    def test_spare_layout_setter_raises_after_mutation_and_targeted_rollback_removes_it(self):
        self.hou.layout_raises_once = True
        result = edits.apply(self.hou, edits.prepare(self.hou, arguments(spare())))
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        self.assertIsNone(self.hou.a.parm("falloff"))
        self.assertEqual(result["failed_operation"]["op"], "add_spare_parameter")
        self.assertEqual(result["available_schema"]["parameters"][0]["name"], "falloff")
        self.assertNotIn("private layout", json.dumps(result))

    def test_spare_layout_side_effect_on_existing_channel_is_explicitly_incomplete(self):
        def callback(node):
            self.hou.on_layout = None
            node.parm("density").value = 99
        self.hou.on_layout = callback
        result = edits.apply(self.hou, edits.prepare(self.hou, arguments(spare())))
        self.assertEqual(result["error"]["code"], "parameter_layout_changed_values")
        self.assertTrue(result["manual_undo_needed"])
        self.assertEqual(result["rollback"], "incomplete")
        self.assertIsNone(self.hou.a.parm("falloff"))
        self.assertEqual(self.hou.a.parm("density").value, 99)

    def test_spare_creation_rejects_a_changed_current_default_and_removes_only_addition(self):
        def callback(node):
            self.hou.on_layout = None
            node.parm("falloff").value = 99.0
        self.hou.on_layout = callback
        result = edits.apply(self.hou, edits.prepare(self.hou, arguments(spare(default=0.5))))
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "spare_default_mismatch")
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        self.assertFalse(result["manual_undo_needed"])
        self.assertIsNone(self.hou.a.parm("falloff"))
        self.assertEqual(self.hou.a.parm("density").value, 1.0)

    def test_spare_creation_verifies_actual_definition_before_accepting_ownership_signature(self):
        def default_changed(template):
            template.defaults = (99.0,)
        def label_changed(template):
            template._label = "Not the reviewed label"
        changes = [default_changed, label_changed,
                   lambda template: template.settings.update(max=999),
                   lambda template: template.settings.update(script_callback="DO_NOT_EXECUTE"),
                   lambda template: template.settings.update(default_expression=("DO_NOT_EVALUATE",)),
                   lambda template: template.settings.update(hidden=True)]
        for change in changes:
            with self.subTest(change=change):
                self.hou = Hou()
                def callback(node):
                    self.hou.on_layout = None
                    group = node.parmTemplateGroup()
                    for template in group.templates:
                        if template.name() == "falloff":
                            change(template)
                    node.setParmTemplateGroup(group, rename_conflicting_parms=False)
                self.hou.on_layout = callback
                result = edits.apply(self.hou, edits.prepare(self.hou, arguments(spare(default=0.5))))
                self.assertEqual(result["error"]["code"], "spare_definition_mismatch")
                self.assertEqual(result["rollback"], "reverted_scene_fields")
                self.assertIsNone(self.hou.a.parm("falloff"))
                self.assertNotIn("DO_NOT_EXECUTE", result["error"]["message"])

    def test_spare_default_verification_checks_every_component_and_protects_new_channels(self):
        for mutation in (lambda parm: setattr(parm, "value", 99),
                         lambda parm: setattr(parm, "keys", (Keyframe("DO_NOT_EVALUATE"),)),
                         lambda parm: setattr(parm, "locked", True)):
            with self.subTest(mutation=mutation):
                self.hou = Hou()
                def callback(node):
                    self.hou.on_layout = None
                    mutation(node.parm("falloffz"))
                self.hou.on_layout = callback
                plan = edits.prepare(self.hou, arguments(spare(kind="vector", default=[0.1, 0.2, 0.3])))
                result = edits.apply(self.hou, plan)
                self.assertEqual(result["error"]["code"], "spare_default_mismatch")
                self.assertEqual(result["rollback"], "reverted_scene_fields")
                self.assertIsNone(self.hou.a.parm("falloffz"))

    def test_spare_definition_check_allows_inert_extra_hom_metadata(self):
        def callback(node):
            self.hou.on_layout = None
            group = node.parmTemplateGroup()
            for template in group.templates:
                if template.name() == "falloff":
                    template._tags["spare_category"] = "Artist Controls"
            node.setParmTemplateGroup(group, rename_conflicting_parms=False)
        self.hou.on_layout = callback
        result = edits.apply(self.hou, edits.prepare(self.hou, arguments(spare(default=0.5))))
        self.assertTrue(result["success"])
        self.assertEqual(self.hou.a.parm("falloff").value, 0.5)

    def test_spare_strict_limits_reject_clamping_before_any_mutation(self):
        for value in (-1, 2):
            with self.subTest(value=value), self.assertRaises(edits.EditError) as caught:
                edits.prepare(self.hou, arguments(spare(default=0.5, min_value=0, max_value=1,
                                                      min_strict=True, max_strict=True),
                                                   parameter(name="falloff", value=value)))
            self.assertEqual(caught.exception.code, "parameter_value_out_of_range")
        self.assertEqual(self.hou.layout_calls, [])
        self.assertTrue(edits.apply(self.hou, edits.prepare(self.hou, arguments(spare(default=0.5, min_value=0, max_value=1,
                                                                                   min_strict=True, max_strict=True))))["success"])
        with self.assertRaises(edits.EditError) as caught:
            edits.prepare(self.hou, arguments(parameter(name="falloff", value=2)))
        self.assertEqual(caught.exception.code, "parameter_value_out_of_range")

    def test_spare_validation_is_typed_bounded_and_has_no_callback_or_code_fields(self):
        menu = [{"token": "a", "label": "A"}]
        invalid = [spare(kind="unknown"), spare(name="bad/name"), spare(label="x" * 257),
                   spare(script_callback="print('no')"), spare(kind="string", default="`pythonexprs('no')`"),
                   spare(kind="int", default=2.5), spare(kind="int", default=2**31),
                   spare(default=float("nan")), spare(kind="toggle", default=1),
                   spare(kind="vector", default=[1, 2]), spare(kind="color", default=[1, 2, True]),
                   spare(kind="vector", default=[1, 2, 3], min_value=0),
                   spare(kind="menu", default="a"), spare(kind="menu", default="b", menu_items=menu),
                   spare(kind="menu", default="a", menu_items=menu * 2),
                   spare(menu_items=menu), spare(min_value=2, max_value=1), spare(min_strict=True),
                   spare(default=-1, min_value=0, min_strict=True)]
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(edits.EditError):
                edits.prepare(self.hou, arguments(operation))
        self.assertEqual(self.hou.layout_calls, [])

    def test_spare_creation_does_not_unlock_an_asset(self):
        self.hou.a.locked_inside = True
        with self.assertRaises(edits.EditError):
            edits.prepare(self.hou, arguments(spare()))
        self.assertEqual(self.hou.layout_calls, [])

    def test_spare_preservation_snapshot_bounds_keyframe_work_before_serialization(self):
        class TooManyKeys:
            def asCode(self):
                raise AssertionError("Oversized keyframe sets must be rejected before serializing")
        self.hou.a.parm("density").keys = (TooManyKeys(),) * (edits.MAX_LAYOUT_KEYFRAMES + 1)
        with self.assertRaises(edits.EditError) as caught:
            edits.prepare(self.hou, arguments(spare()))
        self.assertEqual(caught.exception.code, "parameter_layout_too_large")
        self.assertEqual(self.hou.layout_calls, [])

    def test_spare_rollback_does_not_remove_a_definition_changed_outside_its_operation(self):
        def callback():
            self.hou.a.parm("density").side_effect = None
            group = self.hou.a.parmTemplateGroup()
            for template in group.templates:
                if template.name() == "falloff":
                    template._label = "Changed by an unrelated callback"
            self.hou.a.setParmTemplateGroup(group, rename_conflicting_parms=False)
        self.hou.a.parm("density").side_effect = callback
        self.hou.b.parm("density").fail_once = True
        plan = edits.prepare(self.hou, arguments(spare(), parameter(), parameter("/obj/geo1/b")))
        result = edits.apply(self.hou, plan)
        self.assertTrue(result["manual_undo_needed"])
        self.assertEqual(result["rollback"], "incomplete")
        self.assertIsNotNone(self.hou.a.parm("falloff"))
        self.assertEqual(self.hou.a.parm("falloff").template.label(), "Changed by an unrelated callback")

    def test_prepare_is_inert_and_review_contains_literal_vex_and_before_value(self):
        source = 'vector tint = set(1, 0, 0);\n@Cd = tint; // "quoted"'
        plan = edits.prepare(self.hou, arguments(parameter(name="snippet", value=source)))
        self.assertIn('Before: "@Cd = 0;"', plan.summary)
        self.assertIn(json.dumps(source, ensure_ascii=False), plan.summary)
        self.assertEqual(self.hou.a.parm("snippet").value, "@Cd = 0;")
        self.assertEqual(self.hou.create_calls, [])
        self.assertEqual(self.hou.mutations, [])
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.hou.a.parm("snippet").value, source)
        self.assertEqual(self.hou.a.parm("snippet").calls, [(source, False)])
        self.assertEqual(result["changed_paths"], ["/obj/geo1/a"])
        self.assertEqual(len(self.hou.undos.groups), 1)
        json.dumps(result)

    def test_nested_creation_explicit_names_and_hda_contents(self):
        plan = edits.prepare(self.hou, arguments(
            {"op": "create_node", "parent": "/obj", "node_type": "geo", "name": "newgeo"},
            {"op": "create_node", "parent": "/obj/newgeo", "node_type": "attribwrangle", "name": "wrangle"},
            parameter("/obj/newgeo/wrangle", "snippet", "@P.y += 1;"),
            {"op": "set_position", "path": "/obj/newgeo/wrangle", "x": 2, "y": -1},
        ))
        self.assertEqual(edits.apply(self.hou, plan)["status"], "applied")
        self.assertEqual(self.hou.node("/obj/newgeo/wrangle").pos, (2, -1))
        for _, _, options in self.hou.create_calls:
            self.assertFalse(options["run_init_scripts"])
            self.assertTrue(options["load_contents"])
            self.assertTrue(options["exact_type_name"])
            self.assertFalse(options["force_valid_node_name"])

    def test_solaris_reference_file_path_is_preserved_not_evaluated(self):
        plan = edits.prepare(self.hou, arguments(
            {"op": "create_node", "parent": "/stage", "node_type": "reference", "name": "reference1"},
            parameter("/stage/reference1", "assetpath", "$HIP/assets/hero.usd"),
        ))
        self.assertTrue(edits.apply(self.hou, plan)["success"])
        self.assertEqual(self.hou.node("/stage/reference1").parm("assetpath").value, "$HIP/assets/hero.usd")

    def test_stale_parameter_refuses_before_any_mutation(self):
        plan = edits.prepare(self.hou, arguments(parameter()))
        self.hou.a.parm("density").value = 99
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(self.hou.a.parm("density").value, 99)
        self.assertEqual(self.hou.undos.groups, [])

    def test_stale_output_index_and_replaced_node_are_detected(self):
        self.hou.b.wires[2] = (self.hou.a, 3)
        plan = edits.prepare(self.hou, arguments(parameter("/obj/geo1/b")))
        self.hou.b.wires[2] = (self.hou.a, 4)
        self.assertEqual(edits.apply(self.hou, plan)["status"], "stale")
        plan = edits.prepare(self.hou, arguments(parameter()))
        Node(self.hou, "/obj/geo1/a", self.hou.a.type())
        self.assertEqual(edits.apply(self.hou, plan)["status"], "stale")

    def test_stale_flags_and_created_name_collision_are_detected(self):
        op = {"op": "set_flags", "path": "/obj/geo1/b", "display": True}
        plan = edits.prepare(self.hou, arguments(op))
        self.hou.a.flags["display"] = False
        self.assertEqual(edits.apply(self.hou, plan)["status"], "stale")
        create = {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "new"}
        plan = edits.prepare(self.hou, arguments(create))
        Node(self.hou, "/obj/geo1/new", self.hou.b.type())
        self.assertEqual(edits.apply(self.hou, plan)["status"], "stale")

    def test_failure_reverses_only_own_changes_and_restores_connection_output(self):
        self.hou.b.wires[2] = (self.hou.a, 7)
        self.hou.a.parm("density").side_effect = lambda: setattr(self.hou.b.parm("density"), "value", 77)
        plan = edits.prepare(self.hou, arguments(
            parameter(),
            {"op": "connect_nodes", "path": "/obj/geo1/b", "input_index": 2, "source": None},
            {"op": "set_flags", "path": "/obj/geo1/b", "display": True, "render": True},
            {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "temporary"},
            parameter("/obj/geo1/temporary", "missing", 3),
        ))
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["manual_undo_needed"])
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        self.assertEqual(self.hou.a.parm("density").value, 1.0)
        self.assertEqual(self.hou.b.parm("density").value, 77)  # Unrelated callback change is untouched.
        self.assertEqual(self.hou.b.wires[2], (self.hou.a, 7))
        self.assertEqual(self.hou.a.flags, {"display": True, "render": True})
        self.assertEqual(self.hou.b.flags, {"display": False, "render": False})
        self.assertIsNone(self.hou.node("/obj/geo1/temporary"))
        self.assertEqual(len(self.hou.undos.groups), 1)
        self.assertNotIn("private details", json.dumps(result))

    def test_setter_raises_after_mutation_and_rollback_still_restores(self):
        self.hou.a.parm("density").fail_once = True
        plan = edits.prepare(self.hou, arguments(parameter()))
        result = edits.apply(self.hou, plan)
        self.assertEqual(self.hou.a.parm("density").value, 1.0)
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        self.assertEqual(result["changed_paths"], ["/obj/geo1/a"])
        self.assertEqual(result["error"]["code"], "houdini_operation_failed")
        self.assertEqual(result["error"]["exception_type"], "RuntimeError")
        self.assertNotIn("private details", json.dumps(result))

    def test_toggle_on_off_raw_values_are_literal_and_restore_as_booleans(self):
        for initial, requested, restored in (("on", False, True), ("off", 1, False)):
            with self.subTest(initial=initial):
                toggle = Parm(initial, "Toggle")
                self.hou.a.parameters["enabled"] = toggle
                self.hou.a.parm("density").fail_once = True
                plan = edits.prepare(self.hou, arguments(parameter(name="enabled", value=requested), parameter()))
                result = edits.apply(self.hou, plan)
                self.assertEqual(result["rollback"], "reverted_scene_fields")
                self.assertIs(toggle.value, restored)
                self.assertEqual(toggle.calls, [(requested, False), (restored, False)])
        for invalid in (2, -1, "on", 0.5):
            with self.subTest(invalid=invalid), self.assertRaises(edits.EditError) as caught:
                edits.prepare(self.hou, arguments(parameter(name="enabled", value=invalid)))
            self.assertEqual(caught.exception.code, "invalid_toggle_value")

    def test_integer_menu_raw_tokens_accept_indices_and_restore_tokens(self):
        menu = Parm("sourceflip", "Menu")
        menu.template.items = ("sourceflip", "sink")
        self.hou.a.parameters["initialize"] = menu
        for requested in (1, "sink"):
            with self.subTest(requested=requested):
                menu.calls.clear()
                self.hou.a.parm("density").fail_once = True
                plan = edits.prepare(self.hou, arguments(parameter(name="initialize", value=requested), parameter()))
                result = edits.apply(self.hou, plan)
                self.assertEqual(result["rollback"], "reverted_scene_fields")
                self.assertEqual(menu.value, "sourceflip")
                self.assertEqual(menu.calls, [(requested, False), ("sourceflip", False)])
        for invalid in (2, -1, "guessed"):
            with self.subTest(invalid=invalid), self.assertRaises(edits.EditError):
                edits.prepare(self.hou, arguments(parameter(name="initialize", value=invalid)))

    def test_numeric_menu_tokens_are_preserved_before_numeric_conversion(self):
        menu = Parm("20", "Menu")
        menu.template.items = ("10", "20")
        self.hou.a.parameters["choice"] = menu
        self.hou.a.parm("density").fail_once = True
        plan = edits.prepare(self.hou, arguments(parameter(name="choice", value=0), parameter()))
        self.assertFalse(edits.apply(self.hou, plan)["success"])
        self.assertEqual(menu.calls[-1], ("20", False))
        menu.template.use_token = True
        with self.assertRaises(edits.EditError):
            edits.prepare(self.hou, arguments(parameter(name="choice", value=1)))
        plan = edits.prepare(self.hou, arguments(parameter(name="choice", value=10)))
        self.assertTrue(edits.apply(self.hou, plan)["success"])
        self.assertEqual(menu.value, "10")

    def test_dynamic_menu_schema_does_not_call_generators(self):
        menu = Parm("old_token", "Menu")
        menu.template.dynamic = "raise RuntimeError('never execute this generator')"
        self.hou.a.parameters["choice"] = menu
        menu.fail_once = True
        plan = edits.prepare(self.hou, arguments(parameter(name="choice", value="new_token")))
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        record = result["available_schema"]["parameters"][0]
        self.assertEqual(record["name"], "choice")
        self.assertTrue(record["dynamic_menu"])
        self.assertNotIn("menu", record)

    def test_missing_new_parameter_returns_schema_captured_before_node_rollback(self):
        plan = edits.prepare(self.hou, arguments(
            {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "temporary"},
            parameter("/obj/geo1/temporary", "densitty", 2),
        ))
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["failed_operation"], {"index": 2, "op": "set_parameter",
                         "path": "/obj/geo1/temporary", "parameter": "densitty"})
        self.assertEqual(result["error"]["code"], "parameter_not_found")
        self.assertIsNone(self.hou.node("/obj/geo1/temporary"))
        schema = result["available_schema"]
        self.assertTrue(schema["captured_before_rollback"])
        self.assertEqual(schema["parameters"][0]["name"], "density")
        self.assertEqual(schema["parameters"][0]["raw_value"], "1.0")
        self.assertEqual(schema["node_type"], "null")

    def test_future_static_schema_rejects_guesses_and_bad_values_before_mutation(self):
        node_type = self.hou.types[("Sop", "null")]
        node_type.parmTemplateGroup = lambda: Group(Template(name="density"), Template("Toggle", name="enabled"))
        create = {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "temporary"}
        for name, value, code in (("densitty", 2, "parameter_not_found"),
                                  ("density", "bad", "parameter_value_type"),
                                  ("enabled", 2, "invalid_toggle_value")):
            with self.subTest(name=name), self.assertRaises(edits.EditError) as caught:
                edits.prepare(self.hou, arguments(create, parameter("/obj/geo1/temporary", name, value)))
            error = caught.exception
            self.assertEqual(error.code, code)
            self.assertEqual(error.details()["failed_operation"]["index"], 2)
            self.assertEqual(error.available_schema["source"], "static_before_creation")
            self.assertTrue(error.available_schema["parameters"])
        self.assertEqual(self.hou.create_calls, [])
        plan = edits.prepare(self.hou, arguments(create, parameter("/obj/geo1/temporary")))
        self.assertTrue(edits.apply(self.hou, plan)["success"])

    def test_future_static_schema_expands_components_and_multiparm_children(self):
        node_type = self.hou.types[("Sop", "null")]
        node_type.parmTemplateGroup = lambda: Group(
            Template(name="t", count=3),
            Template("Folder", name="blocks", actual_folder=False,
                     children=(Template(name="weight#"),)),
        )
        def on_create(node):
            node.parameters.update({"tx": Parm(0), "weight1": Parm(1)})
        self.hou.on_create = on_create
        create = {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "temporary"}
        plan = edits.prepare(self.hou, arguments(create, parameter("/obj/geo1/temporary", "tx", 2),
                                                parameter("/obj/geo1/temporary", "weight1", 3)))
        self.assertTrue(edits.apply(self.hou, plan)["success"])
        self.assertEqual(self.hou.node("/obj/geo1/temporary").parm("weight1").value, 3)
        # A static multiparm pattern does not promise an instance exists.
        create["name"] = "missing_instance"
        plan = edits.prepare(self.hou, arguments(create, parameter("/obj/geo1/missing_instance", "weight2", 3)))
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["error"]["code"], "parameter_not_found")
        self.assertIsNone(self.hou.node("/obj/geo1/missing_instance"))

    def test_existing_expression_has_actionable_protection_and_no_mutation(self):
        parm = self.hou.a.parm("density")
        parm.value, parm.keys = "$FSTART", (object(),)
        with self.assertRaises(edits.EditError) as caught:
            edits.prepare(self.hou, arguments(parameter()))
        details = caught.exception.details()
        self.assertEqual(details["error"]["code"], "authored_channel_protected")
        self.assertTrue(details["available_schema"]["parameters"][0]["authored_channel"])
        self.assertNotIn("captured_before_rollback", details["available_schema"])
        self.assertEqual(parm.calls, [])

    def test_schema_capture_failure_never_masks_failure_or_prevents_rollback(self):
        def broken_parms():
            raise RuntimeError("private details must not escape")
        self.hou.a.parms = broken_parms
        with self.assertRaises(edits.EditError) as caught:
            edits.prepare(self.hou, arguments(parameter(name="missing")))
        self.assertEqual(caught.exception.code, "parameter_not_found")
        self.assertTrue(caught.exception.available_schema["unavailable"])
        self.hou.a.parm("density").fail_once = True
        result = edits.apply(self.hou, edits.prepare(self.hou, arguments(parameter())))
        self.assertTrue(result["available_schema"]["unavailable"])
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        self.assertNotIn("private details", json.dumps(result))

    def test_large_instantiated_schema_is_bounded_and_requested_parameter_first(self):
        for index in range(100):
            parm = Parm("token" + "x" * 8000, "Menu")
            parm.template.items = tuple("token" + str(i) + "x" * 1000 for i in range(30))
            self.hou.a.parameters["choice" + str(index)] = parm
        self.hou.a.parm("density").fail_once = True
        result = edits.apply(self.hou, edits.prepare(self.hou, arguments(parameter())))
        schema = result["available_schema"]
        self.assertEqual(schema["parameters"][0]["name"], "density")
        self.assertTrue(schema["truncated"])
        self.assertLessEqual(len(schema["parameters"]), edits.MAX_SCHEMA_PARAMETERS)
        self.assertLess(len(json.dumps(schema, ensure_ascii=True).encode()), edits.MAX_SCHEMA_BYTES)

    def test_unordered_merge_rollback_restores_all_indices_and_output_ports(self):
        second = Node(self.hou, "/obj/geo1/second", self.hou.a.type())
        third = Node(self.hou, "/obj/geo1/third", self.hou.a.type())
        self.hou.b.unordered = True
        self.hou.b.wires = {0: (self.hou.a, 2), 1: (second, 4), 2: (third, 6)}
        before = dict(self.hou.b.wires)
        self.hou.a.parm("density").fail_once = True
        plan = edits.prepare(self.hou, arguments(
            {"op": "connect_nodes", "path": "/obj/geo1/b", "input_index": 0, "source": None},
            parameter(),
        ))
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["rollback"], "reverted_scene_fields")
        self.assertEqual(self.hou.b.wires, before)

    def test_invalid_connector_bounds_are_rejected_before_creation(self):
        for bad in (
            {"op": "connect_nodes", "path": "/obj/geo1/b", "input_index": 100, "source": None},
            {"op": "connect_nodes", "path": "/obj/geo1/b", "input_index": 0,
             "source": "/obj/geo1/a", "output_index": 10},
        ):
            with self.assertRaises(edits.EditError):
                edits.prepare(self.hou, arguments(bad))
        with self.assertRaises(edits.EditError):
            edits.prepare(self.hou, arguments(
                {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "new"},
                {"op": "connect_nodes", "path": "/obj/geo1/new", "input_index": 100, "source": None},
            ))
        self.assertEqual(self.hou.create_calls, [])

    def test_partial_rollback_is_explicit_and_does_not_claim_atomicity(self):
        self.hou.a.parm("density").fail_always = True
        plan = edits.prepare(self.hou, arguments(parameter()))
        result = edits.apply(self.hou, plan)
        self.assertEqual(result["rollback"], "incomplete")
        self.assertTrue(result["manual_undo_needed"])
        self.assertFalse(result["external_side_effects_covered"])

    def test_uncertain_create_failure_does_not_delete_an_unowned_node(self):
        self.hou.create_raises = True
        plan = edits.prepare(self.hou, arguments(
            {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "partial"}))
        result = edits.apply(self.hou, plan)
        self.assertTrue(result["manual_undo_needed"])
        self.assertIsNotNone(self.hou.node("/obj/geo1/partial"))

    def test_rejects_animation_expression_python_and_non_scalar_parms(self):
        for change in (lambda p: setattr(p, "keys", (object(),)),
                       lambda p: setattr(p, "locked", True),
                       lambda p: setattr(p, "multi", True),
                       lambda p: setattr(p, "template", Template("Button")),
                       lambda p: setattr(p, "template", Template("String", {"editorlang": "python"}))):
            self.hou.a.parameters["density"] = Parm(1)
            change(self.hou.a.parm("density"))
            with self.assertRaises(ValueError):
                edits.prepare(self.hou, arguments(parameter()))
        self.hou.a.parameters["density"] = Parm(1)
        with self.assertRaises(ValueError):
            edits.prepare(self.hou, arguments(parameter(value='__import__("os")')))

    def test_invalid_ops_paths_types_shapes_bounds_and_names_are_rejected(self):
        invalid = [
            {"op": "execute", "source": "print('no')"},
            {"op": []},
            dict(parameter(), path="../a"),
            dict(parameter(), path="/obj/*"),
            dict(parameter(), path="/obj/geo1/../a"),
            dict(parameter(), extra=True),
            dict(parameter(), value=float("nan")),
            dict(parameter(), value=10 ** 400),
            parameter(name="snippet", value="`pythonexprs('bad')`"),
            parameter(name="snippet", value="x" * (edits.MAX_VALUE_CHARS + 1)),
            {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "a"},
            {"op": "create_node", "parent": "/obj/geo1", "node_type": "python", "name": "py"},
            {"op": "create_node", "parent": "/obj/geo1", "node_type": "unknown", "name": "new"},
            {"op": "create_node", "parent": "/obj/geo1", "node_type": "null", "name": "bad/name"},
            {"op": "connect_nodes", "path": "/obj/geo1/b", "input_index": True, "source": "/obj/geo1/a"},
            {"op": "set_flags", "path": "/obj/geo1/a", "display": 1},
            {"op": "set_flags", "path": "/obj/geo1/a"},
        ]
        for operation in invalid:
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    edits.prepare(self.hou, arguments(operation))
        with self.assertRaises(ValueError):
            edits.prepare(self.hou, arguments(*([parameter()] * 33)))
        self.assertEqual(self.hou.create_calls, [])

    def test_single_use_session_binding_and_request_mutation(self):
        request = arguments(parameter())
        plan = edits.prepare(self.hou, request)
        request["operations"][0]["value"] = 9
        with self.assertRaises(ValueError):
            edits.apply(Hou(), plan)
        self.assertTrue(edits.apply(self.hou, plan)["success"])
        self.assertEqual(self.hou.a.parm("density").value, 2)
        with self.assertRaises(ValueError):
            edits.apply(self.hou, plan)

    def test_worker_thread_cannot_prepare_or_apply(self):
        plan = edits.prepare(self.hou, arguments(parameter()))
        failures = []
        def work():
            for fn in (lambda: edits.prepare(self.hou, arguments(parameter())),
                       lambda: edits.apply(self.hou, plan)):
                try:
                    fn()
                except RuntimeError:
                    failures.append(True)
        thread = threading.Thread(target=work)
        thread.start()
        thread.join()
        self.assertEqual(failures, [True, True])
        self.assertEqual(self.hou.a.parm("density").calls, [])


if __name__ == "__main__":
    unittest.main()
