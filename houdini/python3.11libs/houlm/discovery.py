"""Static type/parameter discovery for the open session; no eval or menu scripts."""
import math

from . import scene


MAX_TEMPLATE_ENTRIES = 4096
MAX_TEMPLATES = 2048
_UNAVAILABLE = object()


def _node(hou, path):
    node = hou.node(path)
    if node is None:
        raise ValueError("The requested node does not exist.")
    return node


def _text(value, limit=256):
    return scene._text(str(value), limit)


def _read(obj, name, result, default=None):
    try:
        method = getattr(obj, name, None)
    except Exception:
        result["read_failures"] += 1
        return default
    if method is None:
        return default
    return scene._read(method, default, result)


def _category(hou, arguments):
    if ("parent" in arguments) == ("category" in arguments):
        raise ValueError("Specify exactly one existing parent or installed network category.")
    if "category" in arguments:
        category = hou.nodeTypeCategories().get(arguments["category"])
        if category is None:
            raise ValueError("Use an exact installed network category name.")
        return category
    category = _node(hou, arguments["parent"]).childTypeCategory()
    if category is None:
        raise ValueError("The parent does not contain an operator network.")
    return category


def _page(items, arguments, result, convert, key):
    offset, limit = arguments.get("offset", 0), arguments.get("limit", 32)
    result.update({"matched_count": len(items), "offset": offset, "next_offset": None, key: []})
    scanned = 0
    for item in items[offset:offset + limit]:
        record = convert(item)
        # A single Unicode-heavy static menu plus raw value must still fit a
        # page; never return a next_offset that cannot advance past that item.
        while scene._size(record) > 48 * 1024 and record.get("menu"):
            record["menu"].pop()
            record["menu_truncated"] = True
        if scene._size(record) > 48 * 1024 and isinstance(record.get("raw_value"), str):
            record["raw_value"] = record["raw_value"][:2048]
            record["value_truncated"] = True
        result[key].append(record)
        if scene._size(result) > 62 * 1024:
            result[key].pop()
            break
        scanned += 1
        if record.get("menu_truncated") or record.get("value_truncated") or record.get("defaults_truncated"):
            result["truncated"] = True
    if offset + scanned < len(items):
        result["next_offset"] = offset + scanned
        result["truncated"] = True
    result["truncated"] |= offset > 0
    return result


def _finite_number(value):
    # HOM numeric defaults are small scalars. Bound integer serialization too;
    # arbitrary-size Python ints can otherwise exceed JSON's own digit limit.
    if type(value) is int and value.bit_length() <= 1024:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    return None


def _optional_bool(obj, method, result):
    value = _read(obj, method, result)
    if type(value) is bool:
        return value
    if value is not None:
        result["read_failures"] += 1
    return None


def _bounded_defaults(template, result):
    """Read stored primitives and expressions; never eval or stringify HOM objects."""
    values = {"default_values": None, "default_values_unavailable": False}
    truncated = False
    raw_defaults = _read(template, "defaultValue", result, _UNAVAILABLE)
    if raw_defaults is _UNAVAILABLE:
        values["default_values_unavailable"] = True
    else:
        raw_defaults = raw_defaults if type(raw_defaults) in (tuple, list) else (raw_defaults,)
        values["default_values"] = []
        truncated = len(raw_defaults) > 8
        for value in raw_defaults[:8]:
            if type(value) is str:
                safe_value = _text(value, 512)
                truncated |= safe_value != value
            elif type(value) is bool:
                safe_value = value
            else:
                safe_value = _finite_number(value)
                if safe_value is None:
                    values["default_values_unavailable"] = True
                    result["read_failures"] += 1
                    # None is an explicit placeholder, never a repr of Ramp
                    # or another non-JSON HOM object.
            values["default_values"].append(safe_value)
    for method, key, limit in (("defaultExpression", "default_expressions", 512),
                               ("defaultExpressionLanguage", "default_expression_languages", 128)):
        raw = _read(template, method, result, ())
        if raw is None:
            raw = ()
        elif not isinstance(raw, (tuple, list)):
            raw = (raw,)
        values[key] = [_text(value, limit) for value in raw[:8]]
        truncated |= len(raw) > 8 or any(len(str(value)) > limit for value in raw[:8])
    # JSON escapes non-ASCII text, so character limits alone do not bound a
    # Unicode-heavy tuple. Keep room for the current value and menu metadata.
    array_keys = ("default_values", "default_expressions", "default_expression_languages")
    while scene._size(values) > 12 * 1024:
        arrays = [values[key] for key in array_keys if isinstance(values[key], list)]
        last = max(len(entries) for entries in arrays) - 1
        for entries in arrays:
            if len(entries) > last:
                entries.pop()
        truncated = True
    values["defaults_truncated"] = truncated
    return values


def _numeric_limits(template, kind, result):
    fields = {"minimum": None, "maximum": None,
              "minimum_is_strict": None, "maximum_is_strict": None}
    if kind not in {"float", "int"}:
        return fields
    for method, key in (("minValue", "minimum"), ("maxValue", "maximum")):
        raw = _read(template, method, result)
        if raw is not None:
            fields[key] = _finite_number(raw)
            if fields[key] is None:
                result["read_failures"] += 1
    fields["minimum_is_strict"] = _optional_bool(template, "minIsStrict", result)
    fields["maximum_is_strict"] = _optional_bool(template, "maxIsStrict", result)
    return fields


def _representation(kind, data_type, use_token):
    if kind == "toggle":
        return "boolean_or_0_or_1"
    if kind == "menu":
        if use_token is True:
            return "menu_token_or_integer_token"
        if use_token is False:
            return "menu_token_or_zero_based_index"
        return "menu_token_or_integer"
    if kind not in {"int", "float", "string"}:
        return "unsupported"
    return {"string": "literal_string", "int": "integer", "float": "number"}.get(data_type, "unsupported")


def _templates(group, result):
    """Traverse static folders and multiparms without recursion or evaluation."""
    entries = _read(group, "entries", result)
    if entries is None:
        # Compatibility with older test doubles; real HOM exposes entries().
        entries = _read(group, "entriesWithoutFolders", result, ())
    stack = [iter(entries or ())]
    templates = []
    visited = 0
    truncated = False
    while stack:
        try:
            template = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if visited >= MAX_TEMPLATE_ENTRIES or len(templates) >= MAX_TEMPLATES:
            truncated = True
            break
        visited += 1
        kind = str(template.type()).rsplit(".", 1)[-1].lower()
        if kind == "folder":
            if _read(template, "isActualFolder", result) is not True:
                templates.append(template)
            children = _read(template, "parmTemplates", result)
            if children is None:
                truncated = True
            else:
                stack.append(iter(children))
        else:
            templates.append(template)
    result["template_scan"] = {
        "visited_entries": visited, "collected_templates": len(templates),
        "truncated": truncated,
        "limits": {"visited_entries": MAX_TEMPLATE_ENTRIES, "templates": MAX_TEMPLATES},
    }
    result["truncated"] |= truncated
    return templates


def _template(template, result):
    record = {
        "template_name": _text(template.name()), "label": _text(template.label()),
        "type": _text(template.type()), "data_type": _text(_read(template, "dataType", result)),
        "components": _read(template, "numComponents", result),
        "naming_scheme": _text(_read(template, "namingScheme", result)),
    }
    use_token = _read(template, "menuUseToken", result)
    record["menu_use_token"] = use_token if type(use_token) is bool else None
    kind = record["type"].rsplit(".", 1)[-1].lower()
    data_type = record["data_type"].rsplit(".", 1)[-1].lower()
    record["value_representation"] = _representation(kind, data_type, record["menu_use_token"])
    if kind == "folder":
        folder_type = _text(_read(template, "folderType", result))
        record["folder_type"] = folder_type
        record["multiparm_count"] = folder_type.rsplit(".", 1)[-1] in {
            "MultiparmBlock", "ScrollingMultiparmBlock", "TabbedMultiparmBlock"}
        record["editable"] = False
        record["editability_reasons"] = ["multiparm_count" if record["multiparm_count"] else "unsupported_parameter_type"]
    if kind == "menu":
        record["value_note"] = "Use tokens, not display labels. Raw values may be tokens even when menu_use_token is false."
    record.update(_bounded_defaults(template, result))
    record.update(_numeric_limits(template, kind, result))
    # Read the static template menu, never Parm.menuItems(), which can execute
    # an asset's dynamic menu generator in the artist's session.
    generator = _read(template, "itemGeneratorScript", result)
    record["dynamic_menu"] = bool(generator) if generator is not None else None
    if generator == "":
        items = _read(template, "menuItems", result, ()) or ()
        labels = _read(template, "menuLabels", result, ()) or ()
        record["menu"] = [{"index": index, "token": _text(token),
                           "label": _text(labels[index] if index < len(labels) else token)}
                          for index, token in enumerate(items[:32])]
        record["menu_truncated"] = len(items) > 32
    record["has_callback"] = bool(_read(template, "scriptCallback", result, ""))
    record["hidden"] = bool(_read(template, "isHidden", result, False))
    return record


def _editability(parm, template, record, raw, result):
    """Describe literal-edit eligibility without evaluating parameter values."""
    reasons = []
    channels = _read(parm, "keyframes", result)
    locked = _read(parm, "isLocked", result)
    multiparm = _read(parm, "isMultiParmParent", result)
    tags = _read(template, "tags", result)
    record["animated_or_expression"] = bool(channels) if channels is not None else None
    record["locked"] = locked if type(locked) is bool else None
    record["multiparm_count"] = multiparm if type(multiparm) is bool else None
    if channels is None or type(locked) is not bool or type(multiparm) is not bool or not isinstance(tags, dict):
        reasons.append("metadata_unavailable")
    if channels:
        reasons.append("authored_channel_protected")
    if locked:
        reasons.append("locked_parameter")
    if multiparm:
        reasons.append("multiparm_count")
    if isinstance(tags, dict) and "python" in str(tags.get("editorlang", "")).lower():
        reasons.append("python_code_parameter")
    representation = record["value_representation"]
    if representation == "unsupported":
        reasons.append("unsupported_parameter_type")
    if type(raw) is not str:
        reasons.append("current_value_unavailable")
    elif len(raw) > 8192 or any(ord(char) == 0 or 0xD800 <= ord(char) <= 0xDFFF for char in raw):
        reasons.append("current_value_outside_text_limits")
    elif not channels and representation in {"integer", "number", "boolean_or_0_or_1"}:
        if representation == "boolean_or_0_or_1":
            literal = raw.lower() in {"on", "off", "true", "false", "0", "1"}
        else:
            try:
                number = int(raw) if representation == "integer" else float(raw)
                literal = abs(number) <= 1e100 and math.isfinite(number)
            except (TypeError, ValueError, OverflowError):
                literal = False
        if not literal:
            reasons.append("nonliteral_current_value")
    record["editable"] = not reasons
    record["editability_reasons"] = reasons


def execute(hou, operation, arguments, result):
    """Called by inspection after UI-thread, argument and path validation."""
    query = arguments.get("query", "").casefold()
    if operation == "list_node_types":
        category = _category(hou, arguments)
        result.update({"parent": arguments.get("parent"), "category": _text(category.name())})
        types = category.nodeTypes()
        names = sorted(name for name, value in types.items()
                       if query in name.casefold() or query in str(value.description()).casefold())
        def convert(name):
            value = types[name]
            child_category = _read(value, "childTypeCategory", result)
            return {"name": name, "label": _text(value.description()),
                    "min_inputs": _read(value, "minNumInputs", result),
                    "max_inputs": _read(value, "maxNumInputs", result),
                    "max_outputs": _read(value, "maxNumOutputs", result),
                    "child_category": _text(child_category.name()) if child_category else None}
        return _page(names, arguments, result, convert, "node_types")

    if operation == "node_type_schema":
        category = _category(hou, arguments)
        node_type = category.nodeTypes().get(arguments["node_type"])
        if node_type is None:
            raise ValueError("That exact node type is not installed in this network category.")
        templates = _templates(node_type.parmTemplateGroup(), result)
        result.update({"parent": arguments.get("parent"), "node_type": node_type.name(),
                       "category": _text(category.name()),
                       "note": "Static tuple/multiparm templates, including # instance patterns. Multiparm counts are unsupported edits. Read parameter_schema after creation for actual scalar names; matched_count covers the bounded scan."})
        filtered = [template for template in templates
                    if query in template.name().casefold() or query in template.label().casefold()]
        return _page(filtered, arguments, result, lambda template: _template(template, result), "parameters")

    node = _node(hou, arguments["path"])
    result.update({"path": arguments["path"], "node_type": _text(node.type().name()),
                   "note": "Raw authored values only. editable describes literal parameter eligibility; node permissions, requested value validation and stale checks still apply. Existing expressions and animation are protected."})
    parms = [parm for parm in node.parms()
             if query in parm.name().casefold() or query in parm.parmTemplate().label().casefold()]
    def convert(parm):
        template = parm.parmTemplate()
        record = _template(template, result)
        record["name"] = parm.name()
        record["is_spare"] = _optional_bool(parm, "isSpare", result)
        value = _read(parm, "rawValue", result)
        record["raw_value"] = _text(value, 4096) if value is not None else None
        record["value_truncated"] = len(str(value)) > 4096
        _editability(parm, template, record, value, result)
        return record
    return _page(parms, arguments, result, convert, "parameters")
