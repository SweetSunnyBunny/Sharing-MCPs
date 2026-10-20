'Stripping <face>/<orb> tags must never flatten the rest of the reply.'

import unittest

from services.face_store import parse_face_tag
from services.orb_store import parse_orb_tag

LESSON = (
    "Here it is:\n"
    "```python\n"
    "def is_read_signal(event_type):\n"
    "    if event_type in READ_SIGNALS:\n"
    "        return True\n"
    "    return False\n"
    "```\n"
)


class CodeSurvivesTagStripping(unittest.TestCase):
    def test_face_tag_keeps_indentation(self):
        cleaned = parse_face_tag(LESSON + "<face>(o_o) | teaching</face>")[0]
        self.assertIn("\n    if event_type in READ_SIGNALS:\n        return True\n    return False\n", cleaned)

    def test_orb_tag_keeps_indentation(self):
        cleaned = parse_orb_tag(LESSON + "<orb>#E3AE52 ember breathing | teaching</orb>")[0]
        self.assertIn("\n        return True\n", cleaned)

    def test_both_tags_together_keep_the_lesson_byte_for_byte(self):
        text = LESSON + "<face>(o_o)</face>\n<orb>#E3AE52 ember</orb>"
        cleaned = parse_orb_tag(parse_face_tag(text)[0])[0]
        self.assertEqual(cleaned, LESSON.strip())

    def test_blank_lines_and_prose_spacing_elsewhere_are_untouched(self):
        text = "Line one.\n\n    indented quote\n<face>(o_o)</face>"
        self.assertEqual(parse_face_tag(text)[0], "Line one.\n\n    indented quote")


class SeamsStayTidy(unittest.TestCase):
    """The old behavior the global collapse existed for still holds."""

    def test_mid_line_face_leaves_one_space(self):
        self.assertEqual(parse_face_tag("word <face>x</face> word")[0], "word word")
        self.assertEqual(parse_face_tag("word<face>x</face>word")[0], "word word")

    def test_mid_line_orb_uses_no_filler(self):
        self.assertEqual(parse_orb_tag("word <orb>#fff</orb> word")[0], "word word")
        self.assertEqual(parse_orb_tag("word<orb>#fff</orb>word")[0], "wordword")

    def test_tag_at_line_edges_leaves_no_dangling_space(self):
        self.assertEqual(parse_face_tag("<face>x</face> Hello")[0], "Hello")
        self.assertEqual(parse_face_tag("Hi <face>x</face>\nnext")[0], "Hi\nnext")


if __name__ == "__main__":
    unittest.main()
