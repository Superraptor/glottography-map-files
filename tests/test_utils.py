"""Unit tests for glottography_to_commons.utils"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from glottography_to_commons.utils import (
    ALLOWED_COMMONS_LICENSES,
    get_language_sort_key,
    normalize_license,
    optimize_map_payload,
    rdp_simplify_points,
    round_coordinates,
    safe_extract_zip,
    sanitize_title,
    simplify_geometry,
    simplify_polygon_ring,
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

    def test_get_language_sort_key(self):
        self.assertEqual(get_language_sort_key("Standard Arabic"), "Arabic")
        self.assertEqual(get_language_sort_key("Standard English"), "English")
        self.assertEqual(get_language_sort_key("Standard Indonesian"), "Indonesian")
        self.assertEqual(get_language_sort_key("Standard Malay"), "Malay")
        self.assertEqual(get_language_sort_key("Croatian Standard"), "Croatian")
        self.assertEqual(get_language_sort_key("Northern Ju"), "Northern Ju")
        self.assertEqual(get_language_sort_key(""), "")

    def test_rdp_simplification_and_ring_preservation(self):
        # Collinear points along a line
        line = [[0.0, 0.0], [0.5, 0.00001], [1.0, 0.0]]
        simplified = rdp_simplify_points(line, eps=0.001)
        self.assertEqual(len(simplified), 2)
        self.assertEqual(simplified, [[0.0, 0.0], [1.0, 0.0]])

        # Square ring with redundant points along the edges
        square_ring = [
            [0.0, 0.0], [0.5, 0.00001], [1.0, 0.0],
            [1.0, 0.5], [1.0, 1.0],
            [0.5, 1.00001], [0.0, 1.0],
            [0.0, 0.5], [0.0, 0.0]
        ]
        simplified_ring = simplify_polygon_ring(square_ring, eps=0.001)
        self.assertEqual(len(simplified_ring), 5)  # 4 corners + closed point
        self.assertEqual(simplified_ring[0], simplified_ring[-1])

    def test_optimize_map_payload(self):
        # Build dummy payload with redundant points along polygon edges
        coords = [
            [[i * 0.01, 0.0] for i in range(25)] +
            [[0.25, i * 0.01] for i in range(25)] +
            [[(25 - i) * 0.01, 0.25] for i in range(25)] +
            [[0.0, (25 - i) * 0.01] for i in range(25)]
        ]
        coords[0].append(coords[0][0])
        payload = {
            "license": "CC-BY-4.0",
            "description": {"en": "Test"},
            "sources": "Base sources",
            "mediawikiCategories": [{"name": "Linguistic maps of the Arabic language", "sort": "Arabic"}],
            "data": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": coords
                        },
                        "properties": {}
                    }
                ]
            }
        }
        # Optimize with force_epsilon
        opt_payload, stats = optimize_map_payload(payload, force_epsilon=0.001)
        self.assertTrue(stats["simplified"])
        self.assertLess(stats["final_vertices"], stats["orig_vertices"])
        self.assertIn("Compression & Optimization", opt_payload["sources"])
        self.assertIn("Ramer-Douglas-Peucker", opt_payload["sources"])


if __name__ == "__main__":
    unittest.main()
