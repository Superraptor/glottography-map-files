"""
Pipeline orchestrator connecting Zenodo harvesting, language parsing,
.map generation, and Wikimedia Commons uploading with SQLite tracking.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from glottography_to_commons.config import Config
from glottography_to_commons.map_builder import MapBuilder
from glottography_to_commons.parser import DatasetParser
from glottography_to_commons.state_tracker import StateTracker
from glottography_to_commons.wikimedia_client import WikimediaClient
from glottography_to_commons.zenodo_client import ZenodoClient, ZenodoRecord

logger = logging.getLogger(__name__)


class Pipeline:
    """End-to-end pipeline runner."""

    def __init__(self, config: Config):
        self.config = config
        self.tracker = StateTracker(config.state_db_path)
        self.zenodo = ZenodoClient(config)
        self.parser = DatasetParser(coordinate_precision=config.coordinate_precision)
        self.builder = MapBuilder(config)
        self.wikimedia = WikimediaClient(config)

    def download_datasets(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Step 1: Harvest and download datasets from the Zenodo Glottography community.
        """
        logger.info("=== STEP 1: Harvesting datasets from Zenodo Glottography Community ===")
        records = self.zenodo.list_community_records()

        if limit and limit > 0:
            logger.info("Limiting to first %d records as requested.", limit)
            records = records[:limit]

        downloaded = []
        for i, rec in enumerate(records, 1):
            logger.info("[%d/%d] Processing Zenodo record %s: %s", i, len(records), rec.id, rec.title)
            self.tracker.record_dataset_discovered(
                record_id=rec.id,
                doi=rec.doi,
                title=rec.title,
                slug=rec.slug,
                metadata={
                    "creators": rec.creators,
                    "publication_date": rec.publication_date,
                    "license": rec.license,
                    "description": rec.description,
                    "files": [f.filename for f in rec.files]
                }
            )

            # Skip if already downloaded
            if self.tracker.is_dataset_downloaded(rec.id):
                logger.info("Record %s already downloaded. Skipping download step.", rec.id)
                ds_info = self.tracker.get_dataset(rec.id)
                if ds_info:
                    downloaded.append(ds_info)
                continue

            try:
                dl_dir, ext_dir = self.zenodo.download_dataset(rec)
                self.tracker.record_dataset_downloaded(rec.id, dl_dir, ext_dir)
                ds_info = self.tracker.get_dataset(rec.id)
                if ds_info:
                    downloaded.append(ds_info)
            except Exception as e:
                logger.error("Failed downloading dataset %s: %s", rec.id, e, exc_info=True)
                self.tracker.record_dataset_failed(rec.id, str(e))

        logger.info("Completed Step 1: %d datasets ready.", len(downloaded))
        return downloaded

    def process_datasets(self, limit: Optional[int] = None) -> int:
        """
        Step 2: Parse downloaded datasets and generate .map files per language.
        """
        logger.info("=== STEP 2: Extracting language maps and generating .map files ===")
        datasets = self.tracker.get_all_datasets()
        downloaded = [d for d in datasets if d.get("status") in ("DOWNLOADED", "PROCESSED")]

        if limit and limit > 0:
            downloaded = downloaded[:limit]

        total_maps_generated = 0

        for i, d in enumerate(downloaded, 1):
            rec_id = d["record_id"]
            title = d["title"]
            slug = d["slug"]
            ext_path = Path(d["extracted_path"]) if d.get("extracted_path") else None

            if not ext_path or not ext_path.exists():
                logger.warning("Extracted path missing for record %s (%s). Skipping.", rec_id, ext_path)
                continue

            logger.info("[%d/%d] Parsing dataset %s (%s)...", i, len(downloaded), rec_id, title)

            # Reconstruct ZenodoRecord metadata
            meta = json.loads(d.get("metadata_json") or "{}")
            rec = ZenodoRecord(
                id=rec_id,
                doi=d.get("doi") or f"10.5281/zenodo.{rec_id}",
                title=title,
                creators=meta.get("creators", []),
                publication_date=meta.get("publication_date", ""),
                description=meta.get("description", ""),
                license=meta.get("license", "CC-BY-4.0")
            )

            # Parse language bundles
            try:
                bundles = self.parser.parse_dataset_dir(ext_path, slug)
            except Exception as e:
                logger.error("Error parsing dataset %s: %s", rec_id, e, exc_info=True)
                continue

            for bundle in bundles:
                try:
                    generated_map = self.builder.save_map_file(bundle, rec)
                    if generated_map:
                        self.tracker.record_map_generated(
                            record_id=rec_id,
                            glottocode=bundle.clean_glottocode,
                            language_name=bundle.clean_name,
                            dataset_slug=slug,
                            page_title=generated_map.page_title,
                            map_path=generated_map.file_path,
                            content_hash=generated_map.content_hash
                        )
                        total_maps_generated += 1
                except Exception as e:
                    logger.error(
                        "Error generating map for %s (%s) in dataset %s: %s",
                        bundle.clean_name, bundle.clean_glottocode, rec_id, e
                    )

        logger.info("Completed Step 2: %d maps generated and tracked.", total_maps_generated)
        return total_maps_generated

    def upload_maps(self, limit: Optional[int] = None) -> int:
        """
        Step 3: Upload generated .map files to Wikimedia Commons, respecting rate limits.
        """
        logger.info("=== STEP 3: Uploading .map files to Wikimedia Commons ===")

        # Authenticate if credentials available
        self.wikimedia.login()

        # Ensure parent category and subcategory exist
        try:
            self.wikimedia.ensure_category()
        except Exception as e:
            logger.error("Error ensuring category %s exists: %s", self.config.category_name, e)
            if not self.config.dry_run:
                raise

        pending_maps = self.tracker.get_pending_uploads()
        logger.info("Found %d pending maps to upload.", len(pending_maps))

        if limit and limit > 0:
            logger.info("Limiting upload to first %d maps as requested.", limit)
            pending_maps = pending_maps[:limit]

        uploaded_count = 0
        for i, m in enumerate(pending_maps, 1):
            map_id = m["map_id"]
            page_title = m["page_title"]
            map_path = Path(m["map_path"])
            gc = m["glottocode"]
            lang = m["language_name"]
            rec_id = m["record_id"]

            logger.info("[%d/%d] Uploading %s (%s)...", i, len(pending_maps), page_title, lang)

            if not map_path.exists():
                logger.error("Map file %s does not exist on disk! Skipping.", map_path)
                self.tracker.record_map_upload_failed(map_id, f"File not found on disk: {map_path}")
                continue

            with open(map_path, "r", encoding="utf-8") as f:
                content_json = f.read()

            summary = f"Importing Glottography speaker area map for {lang} ({gc}) from Zenodo record {rec_id}"

            try:
                res = self.wikimedia.upload_map(
                    page_title=page_title,
                    content_json=content_json,
                    summary=summary,
                    language_name=lang
                )
                url = res.get("url", f"https://commons.wikimedia.org/wiki/{page_title}")
                self.tracker.record_map_uploaded(map_id, url)
                uploaded_count += 1
            except Exception as e:
                logger.error("Failed uploading %s: %s", page_title, e)
                self.tracker.record_map_upload_failed(map_id, str(e))

        logger.info("Completed Step 3: Successfully processed %d map uploads.", uploaded_count)
        return uploaded_count

    def run_all(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """Run full end-to-end pipeline: download -> process -> upload."""
        logger.info("Starting Glottography to Commons end-to-end pipeline...")
        self.download_datasets(limit=limit)
        self.process_datasets(limit=limit)
        self.upload_maps(limit=limit)
        stats = self.tracker.get_statistics()
        logger.info("Pipeline run finished. Current Statistics:\n%s", json.dumps(stats, indent=2))
        return stats

