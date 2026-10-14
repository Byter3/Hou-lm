from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm.chat_format import render_markdown


class Tags(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class ChatFormatTests(unittest.TestCase):
    def test_empty_and_plain_paragraphs(self):
        self.assertEqual(render_markdown(""), "")
        result = render_markdown("First line\r\nsecond line\r\n\r\nNext paragraph")
        self.assertEqual(result.count("<p "), 2)
        self.assertIn("First line<br>second line", result)

    def test_headings_bold_and_inline_code(self):
        result = render_markdown("## Verified\n**Created** `falloff_ramp` and __checked__ OUT.")
        self.assertIn("<b>Verified</b>", result)
        self.assertIn("<b>Created</b>", result)
        self.assertIn("<b>checked</b>", result)
        self.assertIn('falloff_ramp</code>', result)
        self.assertNotIn("**", result)

    def test_fenced_vex_preserves_text_and_escapes_html(self):
        source = '```c\nfloat u = relbbox(0, @P).x;\nif (u < 0.5) { @P.y = 0; }\n```'
        result = render_markdown(source)
        self.assertIn("<pre ", result)
        self.assertIn("u &lt; 0.5", result)
        self.assertIn("\nif ", result)
        self.assertNotIn("```", result)
        self.assertEqual("".join(Tags(result).text), source.split("\n", 1)[1].rsplit("\n", 1)[0])

    def test_unfinished_fence_is_inert_code(self):
        result = render_markdown("```python\nhou.node('/obj')\n<img src='file:///secret'>")
        self.assertIn("</pre>", result)
        self.assertEqual([tag for tag, _ in Tags(result).tags], ["pre"])

    def test_fence_closes_only_with_matching_delimiter_and_width(self):
        result = render_markdown("````python\n```\n~~~\n````\nAfter")
        self.assertIn("```\n~~~</pre>", result)
        self.assertIn("After</p>", result)
        self.assertIn("a &lt; b", render_markdown("~~~\na < b\n~~~"))

    def test_ordered_and_unordered_lists(self):
        result = render_markdown("- Grid\n- Wrangle\n  with controls\n\n1. Apply\n2. Verify")
        self.assertIn("<ul><li>Grid</li><li>Wrangle<br>with controls</li></ul>", result)
        self.assertIn("<ol><li>Apply</li><li>Verify</li></ol>", result)

    def test_raw_html_entities_and_attributes_are_escaped(self):
        result = render_markdown('<img src="file:///secret"><script>alert(1)</script> &lt;b&gt;')
        self.assertIn("&lt;img", result)
        self.assertIn("&amp;lt;b&amp;gt;", result)
        self.assertEqual([tag for tag, _ in Tags(result).tags], ["p"])

    def test_links_are_visible_text_and_images_have_no_resource_url(self):
        result = render_markdown("[docs](https://example.org/a_(b)) ![preview](file:///private.png)")
        self.assertIn("docs (https://example.org/a_(b))", result)
        self.assertIn("[Image: preview]", result)
        self.assertNotIn("file:///private.png", result)
        self.assertEqual([tag for tag, _ in Tags(result).tags], ["p"])

    def test_link_target_cannot_inject_tags(self):
        result = render_markdown('[x](javascript:alert\(1\)) [z]("><img src=x>)')
        self.assertNotIn("<a ", result)
        self.assertNotIn("<img", result)
        self.assertEqual([tag for tag, _ in Tags(result).tags], ["p"])

    def test_inline_code_treats_markup_literally(self):
        result = render_markdown('``a ` b <img src=x> **bold**``')
        self.assertIn("a ` b &lt;img src=x&gt; **bold**</code>", result)
        self.assertNotIn("<b>", result)

    def test_escaped_and_unmatched_markers_remain_text(self):
        result = render_markdown(r"\**literal\** and `unclosed [label](unfinished")
        self.assertIn("**literal**", result)
        self.assertIn("`unclosed [label](unfinished", result)

    def test_generated_markup_has_only_fixed_tags_and_styles(self):
        source = ('# heading\n\n**bold** `code` [link](file:///local)\n'
                  '![alt](http://local) <iframe src=x>\n\n- a\n1. b\n\n```html\n<img src=x>\n```')
        for tag, attributes in Tags(render_markdown(source)).tags:
            self.assertIn(tag, {"p", "b", "code", "pre", "br", "ul", "ol", "li"})
            self.assertLessEqual(set(attributes), {"style"})
            for value in attributes.values():
                self.assertNotIn("url", value.lower())

    def test_large_malformed_markup_is_retained_without_recursion(self):
        source = "[" * 20000 + "`" * 10000 + "x"
        result = render_markdown(source)
        self.assertIn(source, result)


if __name__ == "__main__":
    unittest.main()
