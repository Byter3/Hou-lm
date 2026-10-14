"""Reviewed, bounded scene edits through typed HOM calls on the GUI thread.

This is not a sandbox for installed node types: asset contents, callbacks and
normal Houdini cooking may have effects beyond the fields edited here. We never
evaluate supplied Python, call a cook/render, save files, or run a global undo.
On failure we attempt to reverse only our own field edits and created nodes.
"""

from dataclasses import dataclass, field
import difflib
import json
import math
import re
import uuid

from . import scene


MAX_OPERATIONS = 32
MAX_VALUE_CHARS = 8192
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_PLAN_BYTES = 128 * 1024
MAX_NETWORK_CHILDREN = 1024
MAX_SCHEMA_PARAMETERS = 48
MAX_SCHEMA_BYTES = 24 * 1024
MAX_LAYOUT_BYTES = 1024 * 1024
MAX_LAYOUT_PARAMETERS = 4096
MAX_LAYOUT_KEYFRAMES = 4096
_SPARE_OWNER_TAG = "houlm_spare_owner"
_KEYS = {
    "create_node": ({"op", "parent", "node_type", "name"}, set()),
    "set_parameter": ({"op", "path", "parameter", "value"}, set()),
    "connect_nodes": ({"op", "path", "input_index", "source"}, {"output_index"}),
    "set_flags": ({"op", "path"}, {"display", "render"}),
    "set_position": ({"op", "path", "x", "y"}, set()),
    "add_spare_parameter": ({"op", "path", "name", "label", "parameter_type", "default"},
                            {"menu_items", "min_value", "max_value", "min_strict", "max_strict"}),
}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_TYPE = re.compile(r"[A-Za-z0-9_.:-]+\Z")
_FLAG_METHODS = {"display": ("isDisplayFlagSet", "setDisplayFlag"),
                 "render": ("isRenderFlagSet", "setRenderFlag")}


class EditError(ValueError):
    """A deliberately bounded diagnostic safe to show in the review workflow."""

    def __init__(self, message, code="invalid_edit", *, available_schema=None, failed_operation=None):
        super().__init__(message)
        self.code = code
        self.available_schema = available_schema
        self.failed_operation = failed_operation

    def details(self):
        """Structured evidence for UI/tool callers, without raw HOM exceptions."""
        result = {"error": {"code": self.code, "message": scene._text(str(self), 2000)}}
        if self.available_schema is not None:
            result["available_schema"] = self.available_schema
        if self.failed_operation is not None:
            result["failed_operation"] = self.failed_operation
        return result


@dataclass
class _Guard:
    node: object
    identity: tuple
    wires: tuple
    parameters: dict = field(default_factory=dict)
    position: object = None
    layout: object = None


class _Plan:
    """Only prepare constructs plans; callers should use the summary property."""
    __slots__ = ("summary", "_hou", "_operations", "_guards", "_networks", "_types", "_used")

    def __init__(self, hou, operations, guards, networks, types, summary):
        self.summary = summary
        self._hou = hou
        self._operations = operations
        self._guards = guards
        self._networks = networks
        self._types = types
        self._used = False


def _path(value):
    if (type(value) is not str or not value.startswith("/") or len(value) > 1024
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF
                   or c in "\\*?[]{}" for c in value)
            or (value != "/" and any(p in ("", ".", "..") for p in value[1:].split("/")))):
        raise EditError("Use a literal absolute Houdini node path (at most 1024 characters).")
    return value


def _text(value, limit, label):
    if (type(value) is not str or len(value) > limit
            or any(ord(c) == 0 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise EditError("Invalid " + label + ".")
    return value


def _number(value):
    if type(value) not in (int, float) or abs(value) > 1e100 or not math.isfinite(value):
        raise EditError("Use a finite numeric value.")
    return value


def _validate(arguments):
    if type(arguments) is not dict or set(arguments) != {"summary", "operations"}:
        raise EditError("An edit plan requires summary and operations only.")
    _text(arguments["summary"], 2000, "plan summary")
    operations = arguments["operations"]
    if type(operations) is not list or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise EditError("An edit plan must contain 1 to 32 operations.")
    normalized = []
    for source in operations:
        if type(source) is not dict or type(source.get("op")) is not str or source["op"] not in _KEYS:
            raise EditError("Unsupported scene edit operation.")
        required, optional = _KEYS[source["op"]]
        if not required <= set(source) or not set(source) <= required | optional:
            raise EditError("Invalid keys for " + source["op"] + ".")
        op = dict(source)
        for key in ("path", "parent"):
            if key in op:
                _path(op[key])
        if op["op"] == "create_node":
            if type(op["name"]) is not str or len(op["name"]) > 128 or not _NAME.fullmatch(op["name"]):
                raise EditError("Use an explicit node name containing letters, digits and underscores.")
            if (type(op["node_type"]) is not str or len(op["node_type"]) > 256
                    or not _TYPE.fullmatch(op["node_type"])):
                raise EditError("Use an exact installed node type name.")
            if "python" in op["node_type"].lower():
                raise EditError("Python node creation is not available through scene edit plans.")
            _path(op["parent"].rstrip("/") + "/" + op["name"])
        elif op["op"] == "set_parameter":
            if (type(op["parameter"]) is not str or len(op["parameter"]) > 128
                    or not _NAME.fullmatch(op["parameter"])):
                raise EditError("Use one literal parameter name.")
            value = op["value"]
            if type(value) is str:
                _text(value, MAX_VALUE_CHARS, "parameter text")
                if "`" in value:
                    raise EditError("Backtick expressions are not supported by literal parameter edits.")
            elif type(value) is not bool:
                _number(value)
        elif op["op"] == "add_spare_parameter":
            _validate_spare(op)
        elif op["op"] == "connect_nodes":
            if op["source"] is not None:
                _path(op["source"])
                if op["path"] == op["source"] or _parent(op["path"]) != _parent(op["source"]):
                    raise EditError("Connect distinct nodes within the same network.")
            op.setdefault("output_index", 0)
            for key in ("input_index", "output_index"):
                if type(op[key]) is not int or not 0 <= op[key] <= 1023:
                    raise EditError("Connector indices must be integers from 0 to 1023.")
            if op["source"] is None and op["output_index"] != 0:
                raise EditError("A disconnected input must use output_index 0.")
        elif op["op"] == "set_flags":
            if not (set(op) & {"display", "render"}):
                raise EditError("Specify a display or render flag.")
            if any(type(op[k]) is not bool for k in ("display", "render") if k in op):
                raise EditError("Flag values must be booleans.")
        else:
            _number(op["x"])
            _number(op["y"])
        normalized.append(op)
    if len(json.dumps(arguments, ensure_ascii=True).encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise EditError("The edit arguments exceed their 32 KiB limit.")
    return tuple(normalized)


def _validate_spare(op):
    if type(op["name"]) is not str or len(op["name"]) > 128 or not _NAME.fullmatch(op["name"]):
        raise EditError("Use a literal spare parameter name of at most 128 letters, digits and underscores.")
    _text(op["label"], 256, "spare parameter label")
    kind, default = op["parameter_type"], op["default"]
    if type(kind) is not str or kind not in {"float", "int", "string", "toggle", "vector", "color", "menu"}:
        raise EditError("Unsupported spare parameter type.", "unsupported_parameter")
    limits = {"min_value", "max_value", "min_strict", "max_strict"}
    if kind not in {"float", "int"} and limits & set(op):
        raise EditError("Slider limits are supported only on scalar float and int parameters.")
    if kind != "menu" and "menu_items" in op:
        raise EditError("Only menu parameters accept menu_items.")
    if kind in {"float", "int"}:
        _number(default)
        if kind == "int" and (type(default) is not int or not -(2**31) <= default < 2**31):
            raise EditError("Integer defaults must fit a signed 32-bit integer.")
        for side in ("min", "max"):
            strict, value = side + "_strict", side + "_value"
            if strict in op and (type(op[strict]) is not bool or value not in op):
                raise EditError("Each strict limit flag requires its corresponding numeric limit.")
            op.setdefault(value, 0 if side == "min" else 10)
            op.setdefault(strict, False)
            _number(op[value])
            if kind == "int" and (type(op[value]) is not int or not -(2**31) <= op[value] < 2**31):
                raise EditError("Integer slider limits must fit signed 32-bit integers.")
        if op["min_value"] > op["max_value"]:
            raise EditError("The minimum limit must not exceed the maximum; specify both limits when needed.")
        if (op["min_strict"] and default < op["min_value"]) or (op["max_strict"] and default > op["max_value"]):
            raise EditError("The default must be inside the strict parameter limits.")
    elif kind in {"vector", "color"}:
        if type(default) is not list or len(default) != 3:
            raise EditError("Vector and color defaults require exactly three numeric components.")
        for value in default:
            _number(value)
        op["default"] = list(default)
        if len(op["name"]) == 128:
            raise EditError("Vector and color base names must leave room for a component suffix.")
    elif kind == "toggle":
        if type(default) is not bool:
            raise EditError("Toggle defaults require true or false.")
    else:
        _text(default, MAX_VALUE_CHARS, "spare parameter default")
        if "`" in default:
            raise EditError("Backtick expressions are not supported in literal defaults.")
        if kind == "menu":
            items = op.get("menu_items")
            if type(items) is not list or not 1 <= len(items) <= 32:
                raise EditError("Menus require 1 to 32 explicit token/label items.")
            tokens = set()
            for item in items:
                if type(item) is not dict or set(item) != {"token", "label"}:
                    raise EditError("Each menu item requires token and label only.")
                _text(item["token"], 128, "menu token")
                _text(item["label"], 256, "menu label")
                if not item["token"] or item["token"] in tokens:
                    raise EditError("Menu tokens must be nonempty and unique.")
                tokens.add(item["token"])
            if default not in tokens:
                raise EditError("The menu default must be one of the declared tokens.", "invalid_menu_token")
            op["menu_items"] = [dict(item) for item in items]


def _spare_components(op):
    suffixes = {"vector": ("x", "y", "z"), "color": ("r", "g", "b")}.get(op["parameter_type"])
    return tuple(op["name"] + suffix for suffix in suffixes) if suffixes else (op["name"],)


def _spare_template(hou, op, owner=None):
    kind, name, label, default = op["parameter_type"], op["name"], op["label"], op["default"]
    options = {"tags": {_SPARE_OWNER_TAG: owner}} if owner else {}
    if kind in {"float", "int"}:
        constructor = hou.FloatParmTemplate if kind == "float" else hou.IntParmTemplate
        return constructor(name, label, 1, default_value=(default,), min=op["min_value"], max=op["max_value"],
                           min_is_strict=op["min_strict"], max_is_strict=op["max_strict"], **options)
    if kind in {"vector", "color"}:
        options["naming_scheme"] = hou.parmNamingScheme.RGBA if kind == "color" else hou.parmNamingScheme.XYZW
        options["look"] = hou.parmLook.ColorSquare if kind == "color" else hou.parmLook.Vector
        return hou.FloatParmTemplate(name, label, 3, default_value=tuple(default), **options)
    if kind == "string":
        return hou.StringParmTemplate(name, label, 1, default_value=(default,), **options)
    if kind == "toggle":
        return hou.ToggleParmTemplate(name, label, default_value=default, **options)
    items = op["menu_items"]
    tokens = tuple(item["token"] for item in items)
    return hou.MenuParmTemplate(name, label, tokens, menu_labels=tuple(item["label"] for item in items),
                                default_value=tokens.index(default), **options)


def _bounded_layout_text(value):
    if type(value) is not str or len(value.encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise EditError("The parameter layout is too large for a bounded edit.", "parameter_layout_too_large")
    return value


def _template_signature(template):
    method = getattr(template, "asCode", None)
    if method is not None:
        return _bounded_layout_text(method())
    # Compatibility for minimal HOM doubles; production templates have asCode.
    return (str(template.type()), str(template.dataType()), tuple(sorted(template.tags().items())))


def _layout_signature(node):
    return _bounded_layout_text(node.parmTemplateGroup().asDialogScript())


def _layout_names(group):
    pending, names, patterns, count = list(group.entries()), set(), [], 0
    while pending:
        template = pending.pop()
        count += 1
        if count > MAX_LAYOUT_PARAMETERS:
            raise EditError("The parameter layout has too many definitions for a bounded edit.", "parameter_layout_too_large")
        base = template.name()
        scalar_names = (base,)
        if str(template.type()).rsplit(".", 1)[-1].lower() == "folder":
            pending.extend(template.parmTemplates())
        elif template.numComponents() > 1:
            if template.numComponents() > MAX_LAYOUT_PARAMETERS:
                raise EditError("A parameter tuple has too many components for a bounded edit.", "parameter_layout_too_large")
            naming = str(template.namingScheme()).rsplit(".", 1)[-1]
            suffixes = tuple(str(i + 1) for i in range(template.numComponents())) if naming == "Base1" else _SUFFIXES.get(naming)
            if suffixes is None or template.numComponents() > len(suffixes):
                raise EditError("A parameter's component naming cannot be checked safely.")
            scalar_names += tuple(base + suffix for suffix in suffixes[:template.numComponents()])
        for name in scalar_names:
            if "#" in name:
                patterns.append(re.compile(re.escape(name).replace(r"\#", r"[0-9]+") + r"\Z"))
            else:
                names.add(name)
    return names, patterns


def _check_spare_collision(group, op, reserved=(), node=None):
    names, patterns = _layout_names(group)
    if node is not None:
        parms = node.parms()
        if len(parms) > MAX_LAYOUT_PARAMETERS:
            raise EditError("The node has too many scalar parameters for a bounded layout edit.", "parameter_layout_too_large")
        names.update(parm.name() for parm in parms)
    names.update(reserved)
    for name in {op["name"], *_spare_components(op)}:
        if name in names or any(pattern.fullmatch(name) for pattern in patterns):
            raise EditError("The spare parameter name or component already exists: " + op["path"] + "/" + name,
                            "parameter_name_collision")


def _channel_snapshot(node):
    parms = node.parms()
    if len(parms) > MAX_LAYOUT_PARAMETERS:
        raise EditError("The node has too many parameters for a bounded layout edit.", "parameter_layout_too_large")
    result, size, key_count = {}, 0, 0
    for parm in parms:
        raw = parm.rawValue()
        authored = parm.keyframes()
        key_count += len(authored)
        if key_count > MAX_LAYOUT_KEYFRAMES:
            raise EditError("The node has too many authored keyframes to verify a layout edit.", "parameter_layout_too_large")
        keys = []
        size += len(raw.encode("utf-8"))
        if size > MAX_LAYOUT_BYTES:
            raise EditError("Existing parameter data is too large to verify a layout edit.", "parameter_layout_too_large")
        for key in authored:
            code = key.asCode()
            size += len(code.encode("utf-8"))
            if size > MAX_LAYOUT_BYTES:
                raise EditError("Existing parameter data is too large to verify a layout edit.", "parameter_layout_too_large")
            keys.append(code)
        result[parm.name()] = (raw, tuple(keys), bool(parm.isLocked()))
    return result


def _verify_channels(node, snapshot):
    current = _channel_snapshot(node)
    if any(current.get(name) != state for name, state in snapshot.items()):
        raise EditError("A layout update changed existing parameter data; inspect the scene and Undo history.",
                        "parameter_layout_changed_values")


def _stored_float(value):
    # Houdini 20.5's parameter-layout serialization uses six significant
    # digits for floating defaults/ranges. Compare that authored representation
    # rather than evaluating parameters or accepting an arbitrary tolerance.
    return float(format(value, ".6g"))


def _spare_definition(template, kind):
    """Compare authored behavior while tolerating HOM-added metadata tags."""
    result = {"name": template.name(), "label": template.label(), "type": str(template.type()),
              "components": template.numComponents(), "default": template.defaultValue()}
    if kind in {"float", "vector", "color"}:
        result["default"] = tuple(_stored_float(value) for value in result["default"])
    for method in ("scriptCallback", "itemGeneratorScript", "isHidden", "isLabelHidden", "joinsWithNext", "conditionals"):
        result[method] = _optional(template, method)
    expressions = _optional(template, "defaultExpression", ())
    if type(expressions) is str:
        expressions = (expressions,)
    # HOM may materialize empty expression entries while installing a tuple.
    result["has_default_expression"] = bool(expressions and any(expressions))
    if kind in {"float", "int", "vector", "color"}:
        for method in ("minValue", "maxValue", "minIsStrict", "maxIsStrict"):
            result[method] = _optional(template, method)
            if kind != "int" and method in {"minValue", "maxValue"}:
                result[method] = _stored_float(result[method])
    if kind in {"vector", "color"}:
        result["naming"] = str(template.namingScheme())
        result["look"] = str(template.look())
    if kind == "menu":
        for method in ("menuItems", "menuLabels", "menuUseToken", "menuType"):
            result[method] = _optional(template, method)
    return result


def _verify_spare(node, op, expected, actual):
    if _spare_definition(actual, op["parameter_type"]) != _spare_definition(expected, op["parameter_type"]):
        raise EditError("The created spare parameter definition differs from the reviewed type, defaults or behavior.",
                        "spare_definition_mismatch")
    kind = op["parameter_type"]
    defaults = expected.defaultValue()
    if kind == "menu":
        defaults = (op["default"],)
    elif kind == "toggle":
        defaults = (bool(defaults),)
    try:
        for name, expected_value in zip(_spare_components(op), defaults):
            state = _parm_state(node.parm(name))
            current_value = state[2]
            if kind in {"float", "vector", "color"}:
                current_value, expected_value = _stored_float(current_value), _stored_float(expected_value)
            if current_value != expected_value:
                raise EditError("The created spare parameter's current value differs from its reviewed default.",
                                "spare_default_mismatch")
    except EditError as error:
        if error.code == "spare_default_mismatch":
            raise
        raise EditError("The created spare parameter is not an editable literal with the reviewed default.",
                        "spare_default_mismatch") from None


def _add_spare(hou, node, op, journal):
    group = node.parmTemplateGroup()
    _check_spare_collision(group, op, node=node)
    _editable(node)
    before, identity = _channel_snapshot(node), _identity(node)
    owner = uuid.uuid4().hex
    template = _spare_template(hou, op, owner)
    owned_signature = [None]
    def remove_added():
        _same(hou, node, identity)
        current = node.parmTemplateGroup()
        added = current.find(op["name"])
        if added is not None:
            if added.tags().get(_SPARE_OWNER_TAG) != owner:
                raise RuntimeError("The added parameter can no longer be identified safely.")
            if owned_signature[0] is not None and _template_signature(added) != owned_signature[0]:
                raise RuntimeError("The added parameter definition changed outside this edit.")
            if any(not node.parm(name).isSpare() for name in _spare_components(op) if node.parm(name) is not None):
                raise RuntimeError("The added parameter is no longer a spare parameter.")
            current.remove(op["name"])
            node.setParmTemplateGroup(current, rename_conflicting_parms=False)
        _verify_channels(node, before)
    journal.append(remove_added)
    group.append(template)
    node.setParmTemplateGroup(group, rename_conflicting_parms=False)
    added = node.parmTemplateGroup().find(op["name"])
    if added is None or added.tags().get(_SPARE_OWNER_TAG) != owner or any(node.parm(name) is None for name in _spare_components(op)):
        raise EditError("Houdini did not create the exact reviewed spare parameter.", "spare_parameter_not_created")
    if any(not node.parm(name).isSpare() for name in _spare_components(op)):
        raise EditError("Houdini did not create a spare parameter.", "spare_parameter_not_created")
    _verify_spare(node, op, template, added)
    owned_signature[0] = _template_signature(added)
    _verify_channels(node, before)


def _parent(path):
    return path.rsplit("/", 1)[0] or "/"


def _identity(node):
    return (node.path(), node.sessionId(), node.type().name())


def _resolve(hou, path):
    node = hou.node(path)
    if node is None:
        raise EditError("Node does not exist: " + path)
    return node


def _editable(node):
    # Do not unlock assets, even when a nested node could be made writable.
    method = getattr(node, "isInsideLockedHDA", None)
    if method is not None and method():
        raise EditError("Editing nodes inside locked assets is not supported: " + node.path())


def _wires(node):
    result = []
    for connection in node.inputConnections():
        item = connection.inputItem()
        if item is None:
            raise EditError("An input connection cannot be represented safely.")
        # Dots and indirect inputs are retained as actual items for rollback.
        result.append((connection.inputIndex(), item, item.sessionId(),
                       connection.inputItemOutputIndex()))
        if len(result) > 1024:
            raise EditError("The node has too many inputs for a bounded connection edit.")
    return tuple(sorted(result, key=lambda value: value[0]))


def _wire_signature(wires):
    return tuple((index, identity, output) for index, _, identity, output in wires)


def _kind(template):
    kind = str(template.type()).rsplit(".", 1)[-1].lower()
    if kind not in {"int", "float", "string", "toggle", "menu"}:
        raise EditError("Only scalar string, numeric, toggle and menu parameters can be set.", "unsupported_parameter")
    if kind in {"menu", "toggle"}:
        return kind
    return str(template.dataType()).rsplit(".", 1)[-1].lower()


def _optional(obj, name, default=None):
    method = getattr(obj, name, None)
    return default if method is None else method()


def _menu(template):
    # Template reads return authored menu entries. Parm.menuItems() may run a
    # dynamic menu generator and is deliberately never called.
    dynamic = bool(_optional(template, "itemGeneratorScript", ""))
    items = () if dynamic else tuple(_optional(template, "menuItems", ()) or ())
    return items, dynamic, bool(_optional(template, "menuUseToken", False))


def _template_value(template, requested):
    if "python" in str(template.tags().get("editorlang", "")).lower():
        raise EditError("Python code parameters are not supported by scene edit plans.", "unsupported_parameter")
    kind = _kind(template)
    if kind == "string":
        if type(requested) is not str:
            raise EditError("String parameters require string values.", "parameter_value_type")
    elif kind == "toggle":
        if type(requested) is not bool and (type(requested) is not int or requested not in (0, 1)):
            raise EditError("Toggle parameters require true/false or integer 0/1.", "invalid_toggle_value")
    elif kind == "menu":
        items, dynamic, use_token = _menu(template)
        if type(requested) is str:
            if items and requested not in items:
                raise EditError("Use an exact menu token from the parameter schema.", "invalid_menu_token")
        elif type(requested) in (int, bool):
            if items and (str(int(requested)) not in items if use_token else not 0 <= requested < len(items)):
                raise EditError("The menu value is outside its declared tokens or indices.", "invalid_menu_value")
        else:
            raise EditError("Menu parameters require a token string or integer value.", "parameter_value_type")
    elif kind == "int":
        if type(requested) not in (int, bool):
            raise EditError("Integer and toggle parameters require integer or boolean values.", "parameter_value_type")
    elif type(requested) not in (int, float, bool):
        raise EditError("Numeric parameters require numbers, not expression text.", "parameter_value_type")
    if kind in {"int", "float"}:
        if _optional(template, "minIsStrict", False) and requested < template.minValue():
            raise EditError("The value is below this parameter's strict minimum.", "parameter_value_out_of_range")
        if _optional(template, "maxIsStrict", False) and requested > template.maxValue():
            raise EditError("The value is above this parameter's strict maximum.", "parameter_value_out_of_range")
    return kind


def _parm_state(parm):
    if parm.keyframes():
        raise EditError("This parameter has authored keyframes or an expression. Leave its existing default/reference "
                        "unchanged or have the artist replace it; do not repeat this literal edit.", "authored_channel_protected")
    if parm.isLocked() or parm.isMultiParmParent():
        raise EditError("Locked parameters and multiparm counts require a manual edit.", "parameter_not_editable")
    template = parm.parmTemplate()
    tags = template.tags()
    language = str(tags.get("editorlang", "")).lower()
    if "python" in language:
        raise EditError("Python code parameters are not supported by scene edit plans.")
    kind = _kind(template)
    raw = parm.rawValue()
    _text(raw, MAX_VALUE_CHARS, "existing parameter value")
    if kind == "toggle":
        if raw.lower() not in ("on", "off", "true", "false", "0", "1"):
            raise EditError("The toggle's existing value is not a literal on/off value.", "nonliteral_parameter")
        value = raw.lower() in ("on", "true", "1")
    elif kind == "menu":
        items, _, _ = _menu(template)
        # Menus can return a token from rawValue() even with Int dataType.
        # Keep tokens as tokens so rollback never calls int('particles').
        if raw in items or not raw.lstrip("+-").isdigit():
            value = raw
        else:
            value = int(raw)
    elif kind == "string":
        # Existing string expansions are preserved verbatim on rollback.
        value = raw
    else:
        try:
            value = int(raw) if kind == "int" else float(raw)
        except (TypeError, ValueError, OverflowError):
            raise EditError("The existing parameter is not a literal numeric value.", "nonliteral_parameter") from None
        _number(value)
    return (kind, raw, value)


def _parm_value(parm, requested):
    state = _parm_state(parm)
    _template_value(parm.parmTemplate(), requested)
    return state


def _operation_info(op, index):
    result = {"index": index, "op": op["op"]}
    result["path"] = (op["parent"].rstrip("/") + "/" + op["name"]
                      if op["op"] == "create_node" else op["path"])
    for name in ("parameter", "node_type", "input_index", "output_index", "source"):
        if name in op:
            result[name] = op[name]
    if op["op"] == "add_spare_parameter":
        result["parameter"] = op["name"]
        result["parameter_type"] = op["parameter_type"]
    return result


def _template_record(template, name):
    record = {"name": scene._text(name, 128),
              "label": scene._text(_optional(template, "label", name), 128),
              "type": scene._text(template.type(), 64),
              "data_type": scene._text(_optional(template, "dataType", "unknown"), 64)}
    items, dynamic, use_token = _menu(template)
    record["dynamic_menu"] = dynamic
    if items:
        record["menu"] = [{"index": i, "token": scene._text(token, 128)} for i, token in enumerate(items[:24])]
        record["menu_uses_token_value"] = use_token
        record["menu_truncated"] = len(items) > 24
    return record


def _schema(records, path, node_type, requested, source, total):
    # Put the requested/similar names first so bounded evidence remains useful
    # for large assets. No menu generators or parameter expressions run.
    records.sort(key=lambda item: (item[0] != requested,
                                  -difflib.SequenceMatcher(None, requested.casefold(), item[0].casefold()).ratio(),
                                  item[0]))
    result = {"path": path, "node_type": node_type, "source": source,
              "requested_parameter": requested, "parameter_count": total,
              "parameters": [], "truncated": len(records) > MAX_SCHEMA_PARAMETERS}
    for name, template, parm in records[:MAX_SCHEMA_PARAMETERS]:
        try:
            record = _template_record(template, name)
            if parm is not None:
                record["raw_value"] = scene._text(parm.rawValue(), 256)
                record["authored_channel"] = bool(parm.keyframes())
                record["locked"] = bool(parm.isLocked())
            result["parameters"].append(record)
            if len(json.dumps(result, ensure_ascii=True).encode("utf-8")) > MAX_SCHEMA_BYTES - 256:
                result["parameters"].pop()
                result["truncated"] = True
                break
        except Exception:
            result["truncated"] = True
    return result


def _instance_schema(node, requested, source="instantiated_before_rollback"):
    result = {"path": node.path(), "requested_parameter": requested, "source": source}
    if source == "instantiated_before_rollback":
        result["captured_before_rollback"] = True
    try:
        parms = node.parms()
        records = []
        for parm in parms[:2048]:
            try:
                records.append((parm.name(), parm.parmTemplate(), parm))
            except Exception:
                pass
        result.update(_schema(records, node.path(), node.type().name(), requested, source, len(parms)))
        result["truncated"] = result["truncated"] or len(records) < len(parms)
    except Exception:
        # Schema collection must never mask the original validation failure or
        # prevent rollback. Unexpected HOM exception text is kept private.
        result["unavailable"] = True
    return result


_SUFFIXES = {"XYZW": ("x", "y", "z", "w"), "XYWH": ("x", "y", "w", "h"),
             "UVW": ("u", "v", "w"), "RGBA": ("r", "g", "b", "a"),
             "MinMax": ("min", "max"), "MaxMin": ("max", "min"),
             "StartEnd": ("start", "end"), "BeginEnd": ("begin", "end")}


def _static_parameters(node_type):
    group_method = getattr(node_type, "parmTemplateGroup", None)
    if group_method is None:
        return {}, [], False
    group = group_method()
    # entriesWithoutFolders omits multiparm children in HOM. Traverse folders
    # explicitly to retain their numbered scalar names without instantiation.
    entries_method = getattr(group, "entries", None)
    pending = list(entries_method() if entries_method else group.entriesWithoutFolders())
    templates, visited, complete = [], 0, entries_method is not None
    while pending and visited < 4096:
        template = pending.pop()
        visited += 1
        if str(template.type()).rsplit(".", 1)[-1].lower() == "folder":
            pending.extend(template.parmTemplates())
            if not _optional(template, "isActualFolder", True):
                templates.append(template)  # Reject edits to multiparm counts.
        else:
            templates.append(template)
    complete = complete and not pending and len(templates) <= 2048
    records, patterns = {}, []
    for template in templates[:2048]:
        name, count = template.name(), template.numComponents()
        if count == 1:
            names = (name,)
        else:
            naming = str(template.namingScheme()).rsplit(".", 1)[-1]
            suffixes = tuple(str(i + 1) for i in range(count)) if naming == "Base1" else _SUFFIXES.get(naming)
            if suffixes is None or count > len(suffixes):
                complete = False
                continue
            names = tuple(name + suffix for suffix in suffixes[:count])
        for scalar_name in names:
            if "#" in scalar_name:
                patterns.append((re.compile(re.escape(scalar_name).replace(r"\#", r"[0-9]+") + r"\Z"), template))
            else:
                records[scalar_name] = template
    return records, patterns, complete


def _future_parameter(node_type, path, name, requested, index, op):
    records, patterns, complete = _static_parameters(node_type)
    template = records.get(name)
    if template is None:
        template = next((item for pattern, item in patterns if pattern.fullmatch(name)), None)
    try:
        if template is not None:
            _template_value(template, requested)
        elif complete:
            suggestions = difflib.get_close_matches(name, list(records), n=8, cutoff=0.15)
            hint = " Similar installed names: " + ", ".join(suggestions) + "." if suggestions else ""
            raise EditError("Parameter is absent from this node type's static schema: " + path + "/" + name + hint,
                            "parameter_not_found")
    except EditError as error:
        error.failed_operation = _operation_info(op, index)
        error.available_schema = _schema([(key, value, None) for key, value in records.items()],
                                        path, node_type.name(), name, "static_before_creation", len(records))
        raise


def _flags(parent):
    children = parent.children()
    if len(children) > MAX_NETWORK_CHILDREN:
        raise EditError("This network is too large for a bounded flag edit.")
    result = []
    for child in children:
        values = {}
        for flag, (getter, setter) in _FLAG_METHODS.items():
            if hasattr(child, getter) and hasattr(child, setter):
                values[flag] = bool(getattr(child, getter)())
        result.append((child, _identity(child), values))
    return tuple(result)


def _flag_signature(snapshot):
    return tuple(sorted((identity, tuple(sorted(flags.items()))) for _, identity, flags in snapshot))


def _guard(hou, path, guards):
    if path not in guards:
        node = _resolve(hou, path)
        guards[path] = _Guard(node, _identity(node), _wires(node))
    return guards[path]


def _port(node_type, index, direction):
    method = getattr(node_type, "maxNumInputs" if direction == "input" else "maxNumOutputs", None)
    if method is not None:
        maximum = method()
        if type(maximum) is int and maximum >= 0 and index >= maximum:
            raise EditError(f"Type {node_type.name()} does not have {direction} connector {index}.")


def prepare(hou, arguments):
    """Validate and describe a concrete plan without mutating or cooking."""
    scene.require_main_thread(hou)
    operations = _validate(arguments)
    guards, networks, future_types, types, lines, values = {}, {}, {}, [], [], {}
    planned_parameters, reserved_names = {}, {}

    def existing_or_future(path):
        if path in future_types:
            return None
        return _guard(hou, path, guards).node

    def network(path):
        node = existing_or_future(path)
        if node is not None and path not in networks:
            networks[path] = _flags(node)
        return node

    for index, op in enumerate(operations, 1):
        action = op["op"]
        if action == "create_node":
            path = op["parent"].rstrip("/") + "/" + op["name"]
            if path in future_types or hou.node(path) is not None:
                raise EditError("Node name already exists or is created twice: " + path)
            parent = network(op["parent"])
            if parent is not None:
                _editable(parent)
                category = parent.childTypeCategory()
            else:
                category = future_types[op["parent"]].childTypeCategory()
            if category is None:
                raise EditError("The chosen parent cannot contain nodes.")
            node_type = hou.nodeType(category, op["node_type"])
            if node_type is None or node_type.name() != op["node_type"]:
                raise EditError("Exact node type is not installed: " + op["node_type"])
            types.append((category, op["node_type"], node_type))
            future_types[path] = node_type
            lines.append(f"{index}. Create {path} (exact type: {op['node_type']}).")
        else:
            path = op["path"]
            node = existing_or_future(path)
            if node is not None:
                _editable(node)
                if "python" in node.type().name().lower():
                    raise EditError("Python nodes are not supported by scene edit plans.")
            if action == "set_parameter":
                key = (path, op["parameter"])
                previous = values.get(key, "<new node default; checked when created>")
                if key in planned_parameters:
                    _template_value(planned_parameters[key], op["value"])
                elif node is not None:
                    parm = node.parm(op["parameter"])
                    if parm is None:
                        raise EditError("Parameter does not exist: " + path + "/" + op["parameter"],
                                        "parameter_not_found", failed_operation=_operation_info(op, index),
                                        available_schema=_instance_schema(node, op["parameter"], "instantiated_during_preflight"))
                    try:
                        state = _parm_value(parm, op["value"])
                    except EditError as error:
                        error.failed_operation = _operation_info(op, index)
                        error.available_schema = _instance_schema(node, op["parameter"], "instantiated_during_preflight")
                        raise
                    guards[path].parameters.setdefault(op["parameter"], state)
                    if key not in values:
                        previous = state[1]
                else:
                    _future_parameter(future_types[path], path, op["parameter"], op["value"], index, op)
                values[key] = op["value"]
                lines.append(f"{index}. Set {path}/{op['parameter']}\n"
                             f"   Before: {json.dumps(previous, ensure_ascii=False)}\n"
                             f"   After:  {json.dumps(op['value'], ensure_ascii=False)}")
            elif action == "add_spare_parameter":
                template = _spare_template(hou, op)
                reserved = reserved_names.setdefault(path, set())
                group_source = node if node is not None else future_types[path]
                method = getattr(group_source, "parmTemplateGroup", None)
                if method is not None:
                    _check_spare_collision(method(), op, reserved, node)
                elif reserved & {op["name"], *_spare_components(op)}:
                    raise EditError("A planned spare parameter name or component is already used.", "parameter_name_collision")
                if node is not None and guards[path].layout is None:
                    guards[path].layout = _layout_signature(node)
                    _channel_snapshot(node)  # Verify preservation can be checked before review.
                reserved.update((op["name"], *_spare_components(op)))
                defaults = op["default"] if op["parameter_type"] in {"vector", "color"} else (op["default"],)
                for name, default in zip(_spare_components(op), defaults):
                    planned_parameters[(path, name)] = template
                    values[(path, name)] = default
                definition = {name: value for name, value in op.items() if name not in {"op", "path"}}
                lines.append(f"{index}. Add spare parameter {path}/{op['name']}\n"
                             f"   Definition: {json.dumps(definition, ensure_ascii=False, sort_keys=True)}")
            elif action == "connect_nodes":
                target_type = node.type() if node is not None else future_types[path]
                _port(target_type, op["input_index"], "input")
                if op["source"] is not None:
                    source_node = existing_or_future(op["source"])
                    source_type = source_node.type() if source_node is not None else future_types[op["source"]]
                    _port(source_type, op["output_index"], "output")
                source = "disconnected" if op["source"] is None else f"{op['source']} output {op['output_index']}"
                lines.append(f"{index}. Connect {path} input {op['input_index']} <- {source}.")
            elif action == "set_flags":
                network(_parent(path))
                for flag in _FLAG_METHODS:
                    if flag in op:
                        if node is not None and not all(hasattr(node, method) for method in _FLAG_METHODS[flag]):
                            raise EditError("This node does not support the " + flag + " flag.")
                        lines.append(f"{index}. Set {path} {flag} flag = {str(op[flag]).lower()} "
                                     "(may switch the same flag on sibling nodes).")
            else:
                if node is not None:
                    guards[path].position = tuple(node.position())
                lines.append(f"{index}. Position {path} at ({op['x']}, {op['y']}).")
    summary = (arguments["summary"] + "\n\n" + "\n".join(lines)
               + "\n\nInstalled asset contents are loaded; node initialization scripts are disabled. "
                 "These edits may trigger normal node callbacks or automatic cooking. "
                 "No explicit cook, render, file save or supplied Python is run. "
                 "One Undo group covers the edits. Failure rollback covers only these scene changes.")
    if any(op["op"] == "add_spare_parameter" and op["parameter_type"] in {"float", "vector", "color"} for op in operations):
        summary += "\nHoudini 20.5 stores floating parameter defaults and slider bounds at six significant digits; inspect the created controls for their stored values."
    if len(summary.encode("utf-8")) > MAX_PLAN_BYTES:
        raise EditError("The complete edit preview exceeds its 128 KiB limit.")
    return _Plan(hou, operations, guards, networks, types, summary)


def _check(hou, plan):
    for path, guard in plan._guards.items():
        node = _resolve(hou, path)
        if _identity(node) != guard.identity or node != guard.node:
            raise EditError("A node was replaced or renamed: " + path)
        if _wire_signature(_wires(node)) != _wire_signature(guard.wires):
            raise EditError("Node connections changed after review: " + path)
        if guard.layout is not None and _layout_signature(node) != guard.layout:
            raise EditError("The parameter layout changed after review: " + path)
        for name, state in guard.parameters.items():
            parm = node.parm(name)
            if parm is None or _parm_state(parm) != state:
                raise EditError("A parameter changed after review: " + path + "/" + name)
        if guard.position is not None and tuple(node.position()) != guard.position:
            raise EditError("A node position changed after review: " + path)
    for path, snapshot in plan._networks.items():
        if _flag_signature(_flags(_resolve(hou, path))) != _flag_signature(snapshot):
            raise EditError("Network children or flags changed after review: " + path)
    for category, name, expected in plan._types:
        if hou.nodeType(category, name) != expected:
            raise EditError("An installed node type changed after review.")
    for op in plan._operations:
        if op["op"] == "create_node" and hou.node(op["parent"].rstrip("/") + "/" + op["name"]) is not None:
            raise EditError("A planned node name is now occupied.")


def _same(hou, node, identity):
    if hou.node(identity[0]) != node or _identity(node) != identity:
        raise RuntimeError("A changed node no longer has its original identity.")


def _restore_flags(hou, snapshot):
    for child, identity, flags in snapshot:
        _same(hou, child, identity)
    # Clear first, then restore enabled flags, respecting exclusive SOP flags.
    for desired in (False, True):
        for child, _, flags in snapshot:
            for flag, value in flags.items():
                getter, setter = _FLAG_METHODS[flag]
                if value is desired and bool(getattr(child, getter)()) != value:
                    getattr(child, setter)(value)


def _perform(hou, op, journal):
    action = op["op"]
    if action == "create_node":
        parent = _resolve(hou, op["parent"])
        path = op["parent"].rstrip("/") + "/" + op["name"]
        if hou.node(path) is not None:
            raise EditError("A planned node name is occupied.")
        prior_flags = _flags(parent)
        # Register flag reversal before creation in case it raises partway.
        journal.append(lambda: _restore_flags(hou, prior_flags))
        created = parent.createNode(op["node_type"], node_name=op["name"],
                                    run_init_scripts=False, load_contents=True,
                                    exact_type_name=True, force_valid_node_name=False)
        identity = _identity(created)
        def remove_created():
            _same(hou, created, identity)
            created.destroy()  # Only the node returned by this create call.
        journal.append(remove_created)
        if created.path() != path or created.type().name() != op["node_type"]:
            raise EditError("Houdini created a different node than the reviewed plan.")
        return path
    node = _resolve(hou, op["path"])
    identity = _identity(node)
    if action == "set_parameter":
        parm = node.parm(op["parameter"])
        if parm is None:
            raise EditError("Parameter does not exist on the instantiated node: " + op["path"] + "/" + op["parameter"],
                            "parameter_not_found")
        state = _parm_value(parm, op["value"])
        template_signature = _template_signature(parm.parmTemplate())
        def restore_parameter():
            _same(hou, node, identity)
            current = node.parm(op["parameter"])
            if current is None or current.keyframes() or _template_signature(current.parmTemplate()) != template_signature:
                raise RuntimeError("The changed parameter can no longer be restored safely.")
            current.set(state[2], follow_parm_reference=False)
        journal.append(restore_parameter)
        parm.set(op["value"], follow_parm_reference=False)
    elif action == "add_spare_parameter":
        _add_spare(hou, node, op, journal)
    elif action == "connect_nodes":
        prior = _wires(node)
        def restore_connection():
            _same(hou, node, identity)
            for _, item, source_identity, _ in prior:
                if item.sessionId() != source_identity:
                    raise RuntimeError("The original connection source was replaced.")
            # Unordered inputs (Merge) can compact when one is disconnected.
            # Restore the entire target input set, clearing highest indices
            # first so compaction does not skip any remaining connection.
            for _ in range(1025):
                current = _wires(node)
                if not current:
                    break
                node.setInput(current[-1][0], None, 0)
            else:
                raise RuntimeError("The node's input connections could not be cleared safely.")
            for index, item, _, output_index in prior:
                node.setInput(index, item, output_index)
            if _wire_signature(_wires(node)) != _wire_signature(prior):
                raise RuntimeError("Houdini did not restore the original input connectors.")
        journal.append(restore_connection)
        source = None if op["source"] is None else _resolve(hou, op["source"])
        node.setInput(op["input_index"], source, op["output_index"])
    elif action == "set_flags":
        prior = _flags(_resolve(hou, _parent(op["path"])))
        journal.append(lambda: _restore_flags(hou, prior))
        for flag, (_, setter) in _FLAG_METHODS.items():
            if flag in op:
                getattr(node, setter)(op[flag])
    else:
        prior = tuple(node.position())
        def restore_position():
            _same(hou, node, identity)
            node.setPosition(hou.Vector2(prior))
        journal.append(restore_position)
        node.setPosition(hou.Vector2((op["x"], op["y"])))
    return op["path"]


def apply(hou, plan):
    """Apply a once-only reviewed plan; return bounded JSON-safe outcome data.

    Reversal uses specific field setters and references to our created nodes,
    never performUndo(). A rollback result is about supported scene fields,
    not a guarantee about callbacks, asset scripts or external side effects.
    """
    scene.require_main_thread(hou)
    if not isinstance(plan, _Plan) or plan._hou is not hou:
        raise EditError("Use the plan returned by prepare in this Houdini session.")
    if plan._used:
        raise EditError("This edit plan was already used; prepare a fresh plan.")
    plan._used = True
    try:
        _check(hou, plan)
    except Exception:
        return {"success": False, "status": "stale", "changed_paths": [],
                "completed_operations": 0, "rollback": "not_needed", "manual_undo_needed": False,
                "message": "The scene changed after review. Inspect it and prepare a new plan."}
    journal, changed, completed, attempted = [], [], 0, []
    failure, rollback_errors, diagnostic, evidence, failed_op = False, 0, None, None, None
    try:
        with hou.undos.group("Hou-lm: reviewed scene edits"):
            try:
                for op in plan._operations:
                    attempted_path = (op["parent"].rstrip("/") + "/" + op["name"]
                                      if op["op"] == "create_node" else op["path"])
                    attempted.append(attempted_path)
                    path = _perform(hou, op, journal)
                    if path not in changed:
                        changed.append(path)
                    completed += 1
            except Exception as error:
                failure = True
                failed_op = _operation_info(op, completed + 1)
                if isinstance(error, EditError):
                    diagnostic = {"code": error.code, "message": scene._text(str(error), 2000)}
                else:
                    diagnostic = {"code": "houdini_operation_failed",
                                  "message": "Houdini rejected this operation. Use the captured schema to correct the plan.",
                                  "exception_type": scene._text(type(error).__name__, 128)}
                if op["op"] in {"set_parameter", "add_spare_parameter"}:
                    requested_parameter = op["parameter"] if op["op"] == "set_parameter" else op["name"]
                    try:
                        failed_node = hou.node(op["path"])
                        if failed_node is not None:
                            evidence = _instance_schema(failed_node, requested_parameter)
                    except Exception:
                        # Diagnostics must never prevent rollback or leak a
                        # private path/script from an unexpected HOM exception.
                        evidence = {"path": op["path"], "requested_parameter": requested_parameter,
                                    "source": "instantiated_before_rollback", "unavailable": True}
                for reverse in reversed(journal):
                    try:
                        reverse()
                    except Exception:
                        rollback_errors += 1
    except Exception:
        # An undo-context failure is not grounds to undo unrelated user work.
        failure = True
        rollback_errors += 1
    if failure:
        # A create call may raise after making a node but before returning its
        # reference. Do not guess ownership or delete that node automatically.
        uncertain_created = [op["parent"].rstrip("/") + "/" + op["name"]
                             for op in plan._operations if op["op"] == "create_node"
                             and hou.node(op["parent"].rstrip("/") + "/" + op["name"]) is not None]
        incomplete = bool(rollback_errors or uncertain_created)
        result = {"success": False, "status": "failed", "changed_paths": sorted(set(attempted + uncertain_created)),
                "completed_operations": completed, "rollback": "incomplete" if incomplete else "reverted_scene_fields",
                "failed_operation_index": min(completed + 1, len(plan._operations)),
                "manual_undo_needed": incomplete,
                "message": ("The edit failed. Some scene changes could not be reversed; review the scene and Undo history."
                            if incomplete else "The edit failed; supported scene changes were reversed. Inspect the scene before retrying."),
                "external_side_effects_covered": False}
        result["error"] = diagnostic or {"code": "undo_context_failed", "message": "The Undo group could not be completed."}
        if failed_op is not None:
            result["failed_operation"] = failed_op
        if evidence is not None:
            result["available_schema"] = evidence
        return result
    return {"success": True, "status": "applied", "changed_paths": changed,
            "completed_operations": completed, "rollback": "not_needed", "manual_undo_needed": False,
            "message": "Reviewed edits applied in one Undo group. Inspect the changed nodes to verify the result."}
