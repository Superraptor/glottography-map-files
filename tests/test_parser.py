"""Unit tests for glottography_to_commons.parser"""

import json
import tempfile
import unittest
from pathlib import Path

from glottography_to_commons.parser import DatasetParser


class TestDatasetParser(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.fixtures_dir = Path(__file__).parent / "fixtures"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_parse_cldf_dataset(self):
        # Setup simulated CLDF dataset structure
        cldf_dir = self.root / "cldf"
        cldf_dir.mkdir(parents=True)

        # Copy sample languages.csv and sample_features.geojson
        with open(self.fixtures_dir / "sample_cldf_languages.csv", "r", encoding="utf-8") as src:
            (cldf_dir / "languages.csv").write_text(src.read(), encoding="utf-8")

        with open(self.fixtures_dir / "sample_features.geojson", "r", encoding="utf-8") as src:
            (cldf_dir / "features.geojson").write_text(src.read(), encoding="utf-8")

        parser = DatasetParser(coordinate_precision=4)
        bundles = parser.parse_dataset_dir(self.root, "test_dataset")

        self.assertEqual(len(bundles), 2)

        # Check English bundle
        bundle_map = {b.clean_glottocode: b for b in bundles}
        self.assertIn("stan1295", bundle_map)
        self.assertIn("sout2991", bundle_map)

        eng = bundle_map["stan1295"]
        self.assertEqual(eng.language_name, "English")
        self.assertEqual(eng.iso639_3, "eng")
        self.assertEqual(len(eng.features), 1)
        self.assertEqual(eng.features[0]["geometry"]["type"], "Polygon")
        self.assertEqual(eng.features[0]["properties"]["title"], "English")

        que = bundle_map["sout2991"]
        self.assertEqual(que.language_name, "Southern Quechua")
        self.assertEqual(que.iso639_3, "quz")

    def test_cldf_languages_geojson_precedence(self):
        # Verify that languages.geojson takes strict precedence and avoids integer ID collision
        cldf_dir = self.root / "cldf"
        cldf_dir.mkdir(parents=True, exist_ok=True)
        etc_dir = self.root / "etc"
        etc_dir.mkdir(parents=True, exist_ok=True)

        # languages.csv maps stan1293 -> English and mode1248 -> Modern Greek
        (cldf_dir / "languages.csv").write_text(
            "ID,Name,Glottocode,ISO639P3code\n"
            "stan1293,English,stan1293,eng\n"
            "mode1248,Modern Greek,mode1248,ell\n",
            encoding="utf-8"
        )

        # etc/features.csv has row id 43 = Greek / mode1248
        (etc_dir / "features.csv").write_text(
            "id,name,glottocode\n"
            "43,Greek,mode1248\n",
            encoding="utf-8"
        )

        # languages.geojson has official language boundaries
        lang_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "title": "English",
                        "cldf:languageReference": "stan1293",
                        "id": "43"  # Integer ID that collides with etc/features.csv
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0.0, 50.0], [1.0, 50.0], [1.0, 51.0], [0.0, 51.0], [0.0, 50.0]]]
                    }
                },
                {
                    "type": "Feature",
                    "properties": {
                        "title": "Modern Greek",
                        "cldf:languageReference": "mode1248"
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[20.0, 38.0], [24.0, 38.0], [24.0, 40.0], [20.0, 40.0], [20.0, 38.0]]]
                    }
                }
            ]
        }
        (cldf_dir / "languages.geojson").write_text(json.dumps(lang_geojson), encoding="utf-8")

        parser = DatasetParser()
        bundles = parser.parse_dataset_dir(self.root, "test_precedence")
        bundle_map = {b.glottocode: b for b in bundles}

        # Verify English is stan1293, NOT mode1248
        self.assertIn("stan1293", bundle_map)
        self.assertEqual(bundle_map["stan1293"].language_name, "English")
        self.assertEqual(bundle_map["stan1293"].iso639_3, "eng")

        # Verify Modern Greek is mode1248
        self.assertIn("mode1248", bundle_map)
        self.assertEqual(bundle_map["mode1248"].language_name, "Modern Greek")
        self.assertEqual(bundle_map["mode1248"].iso639_3, "ell")


if __name__ == "__main__":
    unittest.main()

