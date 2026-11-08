"""Unit tests for glottography_to_commons.state_tracker"""

import tempfile
import unittest
from pathlib import Path

from glottography_to_commons.state_tracker import StateTracker


class TestStateTracker(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_state.db"
        self.tracker = StateTracker(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_dataset_lifecycle(self):
        # Discover
        self.tracker.record_dataset_discovered(
            record_id="12345",
            doi="10.5281/zenodo.12345",
            title="Test Dataset",
            slug="12345_test",
            metadata={"license": "CC-BY-4.0"}
        )
        self.assertFalse(self.tracker.is_dataset_downloaded("12345"))

        ds = self.tracker.get_dataset("12345")
        self.assertEqual(ds["status"], "DISCOVERED")
        self.assertEqual(ds["title"], "Test Dataset")

        # Download
        dl_dir = Path("/tmp/dl")
        ext_dir = Path("/tmp/ext")
        self.tracker.record_dataset_downloaded("12345", dl_dir, ext_dir)
        self.assertTrue(self.tracker.is_dataset_downloaded("12345"))

        ds = self.tracker.get_dataset("12345")
        self.assertEqual(ds["status"], "DOWNLOADED")

    def test_map_lifecycle_and_stats(self):
        map_id = self.tracker.record_map_generated(
            record_id="12345",
            glottocode="stan1295",
            language_name="English",
            dataset_slug="12345_test",
            page_title="Data:Glottography - English (stan1295) - test.map",
            map_path=Path("/tmp/maps/english.map"),
            content_hash="abc123hash"
        )
        self.assertIn("12345:12345_test:stan1295", map_id)
        self.assertFalse(self.tracker.is_map_uploaded(map_id))

        pending = self.tracker.get_pending_uploads()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["glottocode"], "stan1295")

        # Mark uploaded
        url = "https://commons.wikimedia.org/wiki/Data:Glottography_-_English.map"
        self.tracker.record_map_uploaded(map_id, url)
        self.assertTrue(self.tracker.is_map_uploaded(map_id))
        self.assertTrue(self.tracker.is_map_uploaded(map_id, current_hash="abc123hash"))
        self.assertFalse(self.tracker.is_map_uploaded(map_id, current_hash="changed_hash"))

        # Check statistics
        stats = self.tracker.get_statistics()
        self.assertEqual(stats["total_maps"], 1)
        self.assertEqual(stats["uploaded_maps"], 1)
        self.assertEqual(stats["pending_maps"], 0)
        self.assertEqual(stats["unique_languages"], 1)


if __name__ == "__main__":
    unittest.main()

