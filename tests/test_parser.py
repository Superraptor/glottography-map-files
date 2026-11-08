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


if __name__ == "__main__":
    unittest.main()

