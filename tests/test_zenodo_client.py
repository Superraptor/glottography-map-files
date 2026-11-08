"""Unit tests for glottography_to_commons.zenodo_client"""

import json
import unittest
from pathlib import Path

from glottography_to_commons.config import Config
from glottography_to_commons.zenodo_client import ZenodoClient


class TestZenodoClient(unittest.TestCase):

    def setUp(self):
        self.config = Config()
        self.client = ZenodoClient(self.config)
        self.fixtures_dir = Path(__file__).parent / "fixtures"

    def test_parse_record(self):
        with open(self.fixtures_dir / "sample_zenodo_record.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        record = self.client._parse_record(data)
        self.assertIsNotNone(record)
        self.assertEqual(record.id, "15287258")
        self.assertEqual(record.doi, "10.5281/zenodo.15287258")
        self.assertEqual(record.license, "CC-BY-4.0")
        self.assertEqual(len(record.creators), 2)
        self.assertEqual(record.creators[0], "Asher, R. E.")
        self.assertEqual(len(record.files), 1)
        self.assertEqual(record.files[0].filename, "glottography_dataset_ashermoseley2007.zip")


if __name__ == "__main__":
    unittest.main()

