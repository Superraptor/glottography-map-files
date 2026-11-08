"""Unit tests for glottography_to_commons.utils"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from glottography_to_commons.utils import (
    ALLOWED_COMMONS_LICENSES,
    normalize_license,
    round_coordinates,
    safe_extract_zip,
    sanitize_title,
    slugify,
    validate_coordinates,
    validate_geometry,
)


class TestUtils(unittest.TestCase):

    def test_sanitize_title(self):
        title = "Data:Glottography - English [stan1295] / UK: dialect.map"
        clean = sanitize_title(title, namespace="Data:")
        self.assertTrue(clean.startswith("Data:"))
        self.assertNotIn("[", clean)
        self.assertNotIn("]", clean)
        self.assertNotIn("/", clean)
        self.assertNotIn(":", clean[5:])  # no colons inside title body
        self.assertTrue(clean.endswith(".map"))

    def test_normalize_license(self):
        self.assertEqual(normalize_license("CC-BY-4.0"), "CC-BY-4.0")
        self.assertEqual(normalize_license("cc by 4.0"), "CC-BY-4.0")
        self.assertEqual(normalize_license("cc0"), "CC0-1.0")
        self.assertEqual(normalize_license("CC-BY-SA-4.0"), "CC-BY-SA-4.0")
        self.assertEqual(normalize_license({"id": "CC-BY-4.0"}), "CC-BY-4.0")
        # Check that result is in allowed Commons licenses
        norm = normalize_license("unknown-license")
        self.assertIn(norm, ALLOWED_COMMONS_LICENSES)

    def test_round_coordinates(self):
        coords = [12.3456789, 56.7890123]
        rounded = round_coordinates(coords, precision=4)
        self.assertEqual(rounded, [12.3457, 56.789])

        nested = [[[12.111111, 23.222222], [34.333333, 45.444444]]]
        rounded_nested = round_coordinates(nested, precision=2)
        self.assertEqual(rounded_nested, [[[12.11, 23.22], [34.33, 45.44]]])

    def test_validate_geometry(self):
        # Valid polygon
        poly = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
            ]
        }
        valid, err = validate_geometry(poly)
        self.assertTrue(valid, msg=err)

        # Out-of-bounds coordinates
        bad_poly = {
            "type": "Polygon",
            "coordinates": [
                [[400.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [400.0, 0.0]]
            ]
        }
        valid, err = validate_geometry(bad_poly)
        self.assertFalse(valid)
        self.assertIn("Longitude", err)

        # Unclosed ring auto-closes
        unclosed = {
            "type": "Polygon",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
            ]
        }
        valid, err = validate_geometry(unclosed)
        self.assertTrue(valid)
        self.assertEqual(unclosed["coordinates"][0][0], unclosed["coordinates"][0][-1])

    def test_slugify(self):
        self.assertEqual(slugify("English Language (stan1295)"), "english_language_stan1295")
        self.assertEqual(slugify("Asher & Moseley 2007!"), "asher_moseley_2007")

    def test_safe_extract_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "test.zip"
            extract_to = Path(tmpdir) / "out"

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("sub/hello.txt", "world")

            files = safe_extract_zip(zip_path, extract_to)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].exists())
            self.assertEqual(files[0].read_text(), "world")


if __name__ == "__main__":
    unittest.main()
