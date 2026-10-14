"""Internal, non-executable links to standard Houdini network paths."""

import html
import re
from urllib.parse import quote, unquote, urlsplit


ROOTS = ("obj", "stage", "mat", "out", "ch", "img", "shop", "vex", "tasks")
_ROOT = "(?:" + "|".join(ROOTS) + ")"
_PATH = re.compile(r"/" + _ROOT + r"(?:/[A-Za-z0-9_][A-Za-z0-9_.:-]*)*\Z")
_MENTION = re.compile(r"(?<![A-Za-z0-9_:/])(/" + _ROOT + r"(?:/[A-Za-z0-9_][A-Za-z0-9_.:-]*)*)(?![A-Za-z0-9_/])")


def valid_path(path):
    return isinstance(path, str) and len(path) <= 1024 and bool(_PATH.fullmatch(path))


def node_url(path):
    if not valid_path(path):
        raise ValueError("Only absolute Houdini node paths can be linked.")
    return "houlm-node://" + quote(path, safe="/")


def path_from_url(value):
    """Reject hosts, other schemes and navigation extras before HOM lookup."""
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "houlm-node" or parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = unquote(parsed.path)
    return path if valid_path(path) else None


def linked_text(text):
    """Escape plain text and link only recognizable node paths, never URLs."""
    output, start = [], 0
    for match in _MENTION.finditer(text):
        path = match.group(1).rstrip(".:")
        if not valid_path(path):
            continue
        end = match.start(1) + len(path)
        output.append(html.escape(text[start:match.start(1)]))
        output.append('<a href="' + html.escape(node_url(path), quote=True)
                      + '" style="color: #95d5dc; text-decoration: none;">'
                      + html.escape(path) + "</a>")
        start = end
    output.append(html.escape(text[start:]))
    return "".join(output)
