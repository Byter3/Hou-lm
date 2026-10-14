from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import chat_format, node_paths


class Links(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.links = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs)["href"])


class NodePathTests(unittest.TestCase):
    def test_node_links_only_and_round_trip(self):
        source = "Changed `/obj/geo1/wrangle1` and /stage/materiallibrary1."
        links = Links(chat_format.render_markdown(source)).links
        self.assertEqual([node_paths.path_from_url(link) for link in links],
                         ["/obj/geo1/wrangle1", "/stage/materiallibrary1"])

    def test_external_links_files_scripts_and_code_blocks_remain_inert(self):
        source = "[link](https://evil.test/obj/x) /etc/passwd file:///obj/geo1 https://foo/obj/geo1\n\n```python\nhou.node('/obj/geo1')\n```"
        self.assertEqual(Links(chat_format.render_markdown(source)).links, [])
        for value in ("javascript:foo", "file:///obj/x", "houlm-node://host/obj/x",
                      "houlm-node:///obj/x?code=bad", "houlm-node:///obj/x#bad",
                      "houlm-node:///obj/../etc", "houlm-node:///obj/%22%3E%3Cscript%3E",
                      "houlm-node://["):
            self.assertIsNone(node_paths.path_from_url(value))

    def test_raw_html_cannot_gain_navigation(self):
        output = node_paths.linked_text('<a href="javascript:bad">/obj/geo1</a>')
        self.assertIn("&lt;a", output)
        self.assertEqual(Links(output).links, ["houlm-node:///obj/geo1"])


if __name__ == "__main__":
    unittest.main()
