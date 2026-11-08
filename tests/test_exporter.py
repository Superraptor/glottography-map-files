"""Unit tests for glottography_to_commons.exporter"""

import json
import tempfile
import unittest
from pathlib import Path

from glottography_to_commons.config import Config
from glottography_to_commons.exporter import ManualUploadExporter
from glottography_to_commons.map_builder import MapBuilder
from glottography_to_commons.parser import LanguageMapBundle
from glottography_to_commons.zenodo_client import ZenodoRecord


class TestExporter(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config = Config(base_data_dir=Path(self.tmpdir.name))

        # Setup sample map
        builder = MapBuilder(self.config)
        bundle = LanguageMapBundle(
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
                        "description": "Glottocode: stan1295"
                    }
                }
            ],
            dataset_slug="15287258_asher2007"
        )
        record = ZenodoRecord(
            id="15287258",
            doi="10.5281/zenodo.15287258",
            title="Atlas of the World's Languages (2007)",
            creators=["Asher, R. E."],
            publication_date="2021",
            license="CC-BY-4.0"
        )
        builder.save_map_file(bundle, record)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_export_package(self):
        exporter = ManualUploadExporter(self.config)
        items = exporter.export()

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.language_name, "English")
        self.assertEqual(item.glottocode, "stan1295")
        self.assertIn("commons.wikimedia.org", item.commons_edit_url)

        # Verify files created on disk
        self.assertTrue((self.config.export_dir / "manifest.md").exists())
        self.assertTrue((self.config.export_dir / "manifest.csv").exists())
        self.assertTrue((self.config.export_dir / "index.html").exists())

        # Check provenance description file
        desc_files = list((self.config.export_dir / "descriptions").glob("*.txt"))
        self.assertEqual(len(desc_files), 1)
        desc_content = desc_files[0].read_text(encoding="utf-8")
        self.assertIn("10.5281/zenodo.15287258", desc_content)
        self.assertIn("Category:Map data by language", desc_content)
        self.assertIn("Asher, R. E.", desc_content)


if __name__ == "__main__":
    unittest.main()

