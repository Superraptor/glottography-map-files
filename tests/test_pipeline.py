"""Integration tests for the complete pipeline in dry-run mode."""

import json
import tempfile
import unittest
from pathlib import Path

from glottography_to_commons.config import Config
from glottography_to_commons.pipeline import Pipeline
from glottography_to_commons.state_tracker import StateTracker


class TestPipelineIntegration(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmpdir.name)
        self.fixtures_dir = Path(__file__).parent / "fixtures"

        self.config = Config(
            base_data_dir=self.base_dir,
            dry_run=True,
            request_delay=0.0,
            coordinate_precision=4
        )
        self.pipeline = Pipeline(self.config)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_pipeline_process_and_dry_run_upload(self):
        # 1. Simulate a downloaded dataset in the tracker & extracted folder
        record_id = "15287258"
        slug = "15287258_asher2007"
        extracted_dataset_dir = self.config.extracted_dir / slug
        extracted_dataset_dir.mkdir(parents=True)

        # Copy sample fixtures into extracted directory
        with open(self.fixtures_dir / "sample_cldf_languages.csv", "r", encoding="utf-8") as src:
            (extracted_dataset_dir / "languages.csv").write_text(src.read(), encoding="utf-8")
        with open(self.fixtures_dir / "sample_features.geojson", "r", encoding="utf-8") as src:
            (extracted_dataset_dir / "features.geojson").write_text(src.read(), encoding="utf-8")

        # Register in SQLite tracker as DOWNLOADED
        self.pipeline.tracker.record_dataset_discovered(
            record_id=record_id,
            doi="10.5281/zenodo.15287258",
            title="Atlas of the World's Languages (2007)",
            slug=slug,
            metadata={
                "creators": ["Asher, R. E.", "Moseley, Christopher J."],
                "publication_date": "2021",
                "license": "CC-BY-4.0",
                "description": "Global language map dataset."
            }
        )
        self.pipeline.tracker.record_dataset_downloaded(
            record_id=record_id,
            download_path=self.config.downloads_dir / slug,
            extracted_path=extracted_dataset_dir
        )

        # 2. Process datasets
        maps_generated = self.pipeline.process_datasets()
        self.assertEqual(maps_generated, 2)  # English & Southern Quechua

        # Verify generated map files on disk
        map_files = list(self.config.maps_output_dir.glob("*.map"))
        self.assertEqual(len(map_files), 2)

        for mf in map_files:
            with open(mf, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertNotIn("version", data)
            self.assertEqual(data["license"], "CC-BY-4.0")
            self.assertIn("sources", data)
            self.assertIn("10.5281/zenodo.15287258", data["sources"])
            self.assertIn("mediawikiCategories", data)
            self.assertIn(data["mediawikiCategories"][0]["name"], [
                "Linguistic maps of the English language",
                "Linguistic maps of the Southern Quechua language"
            ])
            self.assertIn(data["mediawikiCategories"][0]["sort"], ["English", "Southern Quechua"])

        # 3. Upload maps (dry run)
        uploaded_count = self.pipeline.upload_maps()
        self.assertEqual(uploaded_count, 2)

        # 4. Check statistics in SQLite
        stats = self.pipeline.tracker.get_statistics()
        self.assertEqual(stats["total_datasets"], 1)
        self.assertEqual(stats["downloaded_datasets"], 1)
        self.assertEqual(stats["total_maps"], 2)
        self.assertEqual(stats["uploaded_maps"], 2)
        self.assertEqual(stats["pending_maps"], 0)
        self.assertEqual(stats["unique_languages"], 2)


if __name__ == "__main__":
    unittest.main()

