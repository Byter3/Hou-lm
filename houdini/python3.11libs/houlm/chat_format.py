"""Small, inert Markdown subset for Houdini's Qt rich-text transcript.

Model output is never accepted as HTML. Only this module's fixed tags and
styles reach Qt; only internal node links are active and images never create resources.
This deliberately does not implement full CommonMark or embedded HTML.
"""

import bisect
import html
import re

from . import node_paths


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+)$")
_LIST = re.compile(r"^ {0,3}([-+*]|[0-9]{1,9}[.)])[ \t]+(.+)$")
_CODE_STYLE = "font-family: Consolas, monospace; color: #c2e1e5;"


def _pairs_and_ticks(text):
    """Index delimiters once, so malformed repeated openers do not rescan."""
    pairs, ticks, stack = {}, {}, []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "`":
            end = index + 1
            while end < len(text) and text[end] == "`":
                end += 1
            ticks.setdefault(end - index, []).append(index)
            index = end
            continue
        if char in "[(":
            stack.append((char, index))
        elif char in "])" and stack:
            expected = "[" if char == "]" else "("
            if stack[-1][0] == expected:
                _, start = stack.pop()
                pairs[start] = index
        index += 1
    return pairs, ticks


def _inline(text, depth=0):
    if depth >= 4:
        return html.escape(text)
    pairs, ticks = _pairs_and_ticks(text)
    output = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in r"\`*_{}[]()#+-.!":
            output.append(html.escape(text[index + 1]))
            index += 2
            continue
        if char == "`":
            end = index + 1
            while end < len(text) and text[end] == "`":
                end += 1
            width = end - index
            positions = ticks[width]
            next_position = bisect.bisect_right(positions, index)
            if next_position < len(positions):
                close = positions[next_position]
                output.append('<code style="' + _CODE_STYLE + '">'
                              + node_paths.linked_text(text[end:close]) + "</code>")
                index = close + width
                continue
            output.append(html.escape(text[index:end]))
            index = end
            continue
        marker = text[index:index + 2]
        if marker in ("**", "__"):
            close = text.find(marker, index + 2)
            if close > index + 2:
                output.append("<b>" + _inline(text[index + 2:close], depth + 1) + "</b>")
                index = close + 2
                continue
        image = char == "!" and text[index:index + 2] == "!["
        label_start = index + 1 if image else index
        if char == "[" or image:
            label_end = pairs.get(label_start)
            if label_end is not None and text[label_end + 1:label_end + 2] == "(":
                target_end = pairs.get(label_end + 1)
                if target_end is not None:
                    label = text[label_start + 1:label_end]
                    if image:
                        output.append("[Image: " + html.escape(label) + "]")
                    else:
                        target = text[label_end + 2:target_end]
                        output.append(_inline(label, depth + 1) + " (" + html.escape(target) + ")")
                    index = target_end + 1
                    continue
        if char == "/":
            match = node_paths._MENTION.match(text, index)
            if match is not None:
                output.append(node_paths.linked_text(match.group(0)))
                index = match.end()
                continue
        output.append(html.escape(char))
        index += 1
    return "".join(output)


def render_markdown(text):
    """Return an HTML body using only a safe subset of Qt-supported tags.

    Fences may use backticks or tildes. An unfinished fence is displayed as
    code, including while an answer is still arriving. Raw HTML is escaped,
    and no part of the input can become a tag, attribute or resource URL.
    """
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output, paragraph, items = [], [], []
    list_tag = None

    def flush_paragraph():
        if paragraph:
            output.append('<p style="margin: 4px 0 10px 0;">'
                          + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph[:] = []

    def flush_list():
        nonlocal list_tag
        if items:
            output.append("<" + list_tag + ">"
                          + "".join("<li>" + item + "</li>" for item in items)
                          + "</" + list_tag + ">")
            items[:] = []
        list_tag = None

    index = 0
    while index < len(lines):
        line = lines[index]
        fence = _FENCE.match(line)
        if fence:
            flush_paragraph()
            flush_list()
            marker = fence.group(1)
            code = []
            index += 1
            while index < len(lines):
                closing = _FENCE.match(lines[index])
                if (closing and closing.group(1)[0] == marker[0]
                        and len(closing.group(1)) >= len(marker)
                        and not closing.group(2).strip()):
                    break
                code.append(lines[index])
                index += 1
            output.append('<pre style="' + _CODE_STYLE
                          + ' margin: 8px 0; white-space: pre-wrap;">'
                          + html.escape("\n".join(code)) + "</pre>")
        elif not line.strip():
            flush_paragraph()
            flush_list()
        else:
            heading = _HEADING.match(line)
            item = _LIST.match(line)
            if heading:
                flush_paragraph()
                flush_list()
                # Use weight and spacing for hierarchy while preserving the
                # inherited Houdini UI font size in narrow docked panels.
                output.append('<p style="margin: 12px 0 6px 0;"><b>'
                              + _inline(heading.group(2)) + "</b></p>")
            elif item:
                flush_paragraph()
                next_tag = "ol" if item.group(1)[0].isdigit() else "ul"
                if list_tag and list_tag != next_tag:
                    flush_list()
                list_tag = next_tag
                items.append(_inline(item.group(2)))
            elif items and line.startswith(("  ", "\t")):
                items[-1] += "<br>" + _inline(line.lstrip())
            else:
                flush_list()
                paragraph.append(line)
        index += 1
    flush_paragraph()
    flush_list()
    return "".join(output)
