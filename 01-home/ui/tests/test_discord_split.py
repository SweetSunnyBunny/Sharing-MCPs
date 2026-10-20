'The shared chat splitter (services/discord_split.py).'
import unittest

from services.discord_split import split_chat_message


class SplitChatMessageTests(unittest.TestCase):
    def test_short_and_empty(self):
        self.assertEqual(split_chat_message("hi"), ["hi"])
        self.assertEqual(split_chat_message(""), [])
        self.assertEqual(split_chat_message("   "), [])

    def test_every_piece_fits(self):
        text = "\n\n".join("word " * 120 for _ in range(12))
        for piece in split_chat_message(text, 1900):
            self.assertLessEqual(len(piece), 1900)

    def test_blank_lines_survive(self):
        text = "\n\n".join(f"Paragraph {i}. " + "words " * 60 for i in range(10))
        pieces = split_chat_message(text, 1900)
        self.assertGreater(len(pieces), 1)
        self.assertIn("\n\n", pieces[0])

    def test_prefers_paragraph_break(self):
        para = "Bunny, " * 60
        text = "\n\n".join([para.strip()] * 10)
        pieces = split_chat_message(text, 1900)
        for piece in pieces[:-1]:
            self.assertTrue(piece.endswith("Bunny,"), piece[-20:])

    def test_code_block_reopened_with_indentation(self):
        lines = [f"            line_{i} = {i}" for i in range(150)]
        text = "Here's the fix:\n\n```python\n" + "\n".join(lines) + "\n```\n\nThat's it."
        pieces = split_chat_message(text, 1900)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece), 1900)
            fences = [l for l in piece.split("\n") if l.strip().startswith("```")]
            self.assertEqual(len(fences) % 2, 0, f"unbalanced fences in: {piece[:80]!r}")
        self.assertTrue(pieces[1].startswith("```python\n"))
        self.assertTrue(pieces[1].split("\n")[1].startswith("            line_"))
        # nothing lost: every code line arrives, indentation intact
        joined = "\n".join(pieces)
        for l in lines:
            self.assertIn(l, joined)

    def test_the_real_message_from_2026_10_11(self):
        # Shape of the reply that broke: prose, then a deeply indented block
        # that crossed the cut at an "else:" line.
        prose = "I ran yours, and it's almost perfect. " * 25
        code = "\n".join(
            ["```python", "        if direction == \"left\":"]
            + [f"                    print(\"step {i}\")" for i in range(30)]
            + ["                else:", "                    print(\"Try again!\")", "```"]
        )
        text = prose + "\n\n" + code + "\n\nWatch the indentation."
        pieces = split_chat_message(text, 1900)
        joined = "\n".join(pieces)
        self.assertIn("                else:", joined)
        for piece in pieces:
            fences = [l for l in piece.split("\n") if l.strip().startswith("```")]
            self.assertEqual(len(fences) % 2, 0)

    def test_unbreakable_line_hard_split(self):
        text = "x" * 5000
        pieces = split_chat_message(text, 1900)
        self.assertEqual("".join(pieces), text)
        for piece in pieces:
            self.assertLessEqual(len(piece), 1900)

    def test_telegram_limit(self):
        text = "\n".join("line " * 40 for _ in range(100))
        for piece in split_chat_message(text, 3900):
            self.assertLessEqual(len(piece), 3900)


if __name__ == "__main__":
    unittest.main()
