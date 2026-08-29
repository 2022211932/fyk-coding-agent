import unittest

from slugify import slugify


class SlugifyTests(unittest.TestCase):
    def test_words_are_lowercase_and_joined(self) -> None:
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_punctuation_is_removed(self) -> None:
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_repeated_separators_are_collapsed(self) -> None:
        self.assertEqual(slugify("  one -- two___three  "), "one-two-three")

    def test_accented_characters_are_transliterated(self) -> None:
        self.assertEqual(slugify("Caf\u00e9 d\u00e9j\u00e0 vu"), "cafe-deja-vu")

    def test_empty_result_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "no letters or numbers"):
            slugify("!!!")


if __name__ == "__main__":
    unittest.main()

