"""Unit tests for glottography_to_commons.map_builder"""

import json
import tempfile
import unittest
from pathlib import Path

from glottography_to_commons.config import Config
from glottography_to_commons.map_builder import MapBuilder
from glottography_to_commons.parser import LanguageMapBundle
from glottography_to_commons.zenodo_client import ZenodoRecord


class TestMapBuilder(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config = Config(base_data_dir=Path(self.tmpdir.name))
        self.builder = MapBuilder(self.config)

        self.bundle = LanguageMapBundle(
            glottocode="stan1295",
            language_name="English",
            iso639_3="eng",
            features=[
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0.0, 50.0], [1.0, 50.0], [1.0, 51.0], [0.0, 51.0], [0.0, 50.0]]]
                    },
                    "properties": {
                        "title": "English",
                        "stroke": "#2a4b8d",
                    }
                }
            ],
            dataset_slug="15287258_asher2007"
        )

        self.record = ZenodoRecord(
            id="15287258",
            doi="10.5281/zenodo.15287258",
            title="Atlas of the World's Languages (2007)",
            creators=["Asher, R. E.", "Moseley, Christopher J."],
            publication_date="2021-04-15",
            description="Global language distribution atlas.",
            license="CC-BY-4.0"
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_build_page_title(self):
        title = self.builder.build_page_title(self.bundle, self.record)
        self.assertTrue(title.startswith("Data:"))
        self.assertTrue(title.endswith(".map"))
        self.assertIn("English", title)
        self.assertIn("stan1295", title)

    def test_build_map_data_schema(self):
        payload, title = self.builder.build_map_data(self.bundle, self.record)

        # Check required schema keys
        self.assertNotIn("version", payload)
        self.assertEqual(payload["license"], "CC-BY-4.0")
        self.assertIn("en", payload["description"])
        self.assertIn("English", payload["description"]["en"])

        # Check mediawikiCategories: Category:Linguistic maps of the <Language> language
        cats = payload.get("mediawikiCategories", [])
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["name"], "Linguistic maps of the English language")
        self.assertEqual(cats[0]["sort"], "English")

        # Check sources wikitext provenance
        sources = payload["sources"]
        self.assertIn("10.5281/zenodo.15287258", sources)
        self.assertIn("Asher, R. E.", sources)
        self.assertIn("glottolog.org/resource/languoid/id/stan1295", sources)
        self.assertIn("iso639-3.sil.org/code/eng", sources)

        # Check data
        self.assertEqual(payload["data"]["type"], "FeatureCollection")
        self.assertEqual(len(payload["data"]["features"]), 1)

        # Check framing zoom and center
        self.assertIn("zoom", payload)
        self.assertIn("latitude", payload)
        self.assertIn("longitude", payload)
        self.assertIsInstance(payload["zoom"], int)
        self.assertTrue(1 <= payload["zoom"] <= 18)

    def test_save_map_file(self):
        gen_map = self.builder.save_map_file(self.bundle, self.record)
        self.assertIsNotNone(gen_map)
        self.assertTrue(gen_map.file_path.exists())

        with open(gen_map.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("version", data)
        self.assertEqual(data["license"], "CC-BY-4.0")

    def test_standard_language_category_and_sort_key(self):
        # Test "Standard Arabic"
        arabic_bundle = LanguageMapBundle(
            glottocode="stan1318",
            language_name="Standard Arabic",
            iso639_3=None,
            features=[
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0.0, 20.0], [1.0, 20.0], [1.0, 21.0], [0.0, 21.0], [0.0, 20.0]]]
                    },
                    "properties": {"title": "Standard Arabic"}
                }
            ],
            dataset_slug="test_slug"
        )
        payload, _ = self.builder.build_map_data(arabic_bundle, self.record)
        cats = payload.get("mediawikiCategories", [])
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["name"], "Linguistic maps of the Arabic language")
        self.assertEqual(cats[0]["sort"], "Arabic")

        # Test category helper directly
        self.assertEqual(
            MapBuilder.get_language_map_category("Standard Arabic"),
            "Linguistic maps of the Arabic language"
        )
        self.assertEqual(
            MapBuilder.get_language_map_category("Standard English"),
            "Linguistic maps of the English language"
        )
        self.assertEqual(
            MapBuilder.get_language_map_category("Standard Indonesian"),
            "Linguistic maps of the Indonesian language"
        )
        self.assertEqual(
            MapBuilder.get_language_map_category("Croatian Standard"),
            "Linguistic maps of the Croatian language"
        )


if __name__ == "__main__":
    unittest.main()

