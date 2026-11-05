import unittest

from core.brief import parse_markers


class TestParseMarkers(unittest.TestCase):
    def test_no_markers(self):
        brief = parse_markers("Solo testo\nsu due righe")
        self.assertEqual(brief, ("Solo testo\nsu due righe", None, None, None))

    def test_markers_are_extracted_and_removed(self):
        brief = parse_markers("Testo\ncopertina: 2\nlogo: 3\nordine: 10\nAltro")
        self.assertEqual(brief.text, "Testo\nAltro")
        self.assertEqual((brief.cover, brief.logo, brief.order), (2, 3, 10))

    def test_case_spaces_and_cover_synonym(self):
        brief = parse_markers("  Cover : 4  \nLOGO:1")
        self.assertEqual((brief.cover, brief.logo, brief.text), (4, 1, ""))

    def test_last_marker_wins(self):
        self.assertEqual(parse_markers("copertina: 1\ncopertina: 3").cover, 3)

    def test_inline_mentions_are_not_markers(self):
        text = "La copertina: bella foto 2 è carina\nil logo: 3 colori"
        self.assertEqual(parse_markers(text), (text, None, None, None))

    def test_empty_and_none(self):
        self.assertEqual(parse_markers(""), ("", None, None, None))
        self.assertEqual(parse_markers(None), ("", None, None, None))

    def test_blank_lines_left_by_markers_are_collapsed(self):
        self.assertEqual(parse_markers("A\n\nlogo: 1\n\nB").text, "A\n\nB")


if __name__ == "__main__":
    unittest.main()
