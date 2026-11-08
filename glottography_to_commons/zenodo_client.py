"""
Zenodo client to query, harvest, and download datasets from the Glottography community.
Uses standard library urllib.request to remain free of external dependencies.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from glottography_to_commons.config import Config
from glottography_to_commons.utils import normalize_license, safe_extract_zip, slugify

logger = logging.getLogger(__name__)


@dataclass
class ZenodoFile:
    """Represents a downloadable file in a Zenodo record."""
    filename: str
    download_url: str
    size: int = 0
    checksum: Optional[str] = None


@dataclass
class ZenodoRecord:
    """Metadata for a Zenodo dataset record."""
    id: str
    doi: str
    title: str
    creators: List[str] = field(default_factory=list)
    publication_date: str = ""
    description: str = ""
    license: str = "CC-BY-4.0"
    files: List[ZenodoFile] = field(default_factory=list)
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        """Safe short identifier for directories and page titles."""
        # Try to extract key name from title or DOI
        short = slugify(self.title[:40])
        return f"{self.id}_{short}" if short else str(self.id)


class ZenodoClient:
    """Client for Zenodo REST API."""

    def __init__(self, config: Config):
        self.config = config
        self.headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
        }
        if self.config.zenodo_token:
            self.headers["Authorization"] = f"Bearer {self.config.zenodo_token}"

    def _http_get(self, url: str, retries: int = 3, backoff: float = 2.0) -> bytes:
        """Execute HTTP GET with exponential backoff on transient errors."""
        req = urllib.request.Request(url, headers=self.headers)
        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                logger.warning("HTTP %d for %s (attempt %d/%d)", e.code, url, attempt, retries)
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    sleep_time = backoff * (2 ** (attempt - 1))
                    time.sleep(sleep_time)
                else:
                    raise
            except (urllib.error.URLError, TimeoutError) as e:
                logger.warning("Network error for %s: %s (attempt %d/%d)", url, e, attempt, retries)
                if attempt < retries:
                    time.sleep(backoff * (2 ** (attempt - 1)))
                else:
                    raise
        raise RuntimeError(f"Failed to fetch {url} after {retries} retries")

    def _parse_record(self, item: Dict[str, Any]) -> Optional[ZenodoRecord]:
        """Convert a Zenodo API record dictionary into a ZenodoRecord object."""
        rec_id = str(item.get("id") or item.get("record_id", ""))
        doi = item.get("doi") or f"10.5281/zenodo.{rec_id}"
        metadata = item.get("metadata", {})
        title = metadata.get("title") or item.get("title", f"Zenodo Record {rec_id}")

        # Creators
        creators_raw = metadata.get("creators", [])
        creators = []
        for c in creators_raw:
            if isinstance(c, dict):
                name = c.get("name") or c.get("person_or_org", {}).get("name", "")
                if name:
                    creators.append(name)
            elif isinstance(c, str):
                creators.append(c)

        pub_date = metadata.get("publication_date", "")
        desc = metadata.get("description", "")

        # License
        raw_lic = metadata.get("license") or metadata.get("rights")
        normalized_lic = normalize_license(raw_lic)

        # Files
        files = []
        files_list = item.get("files", [])
        if isinstance(files_list, dict):
            # InvenioRDM files dict with 'entries'
            files_list = files_list.get("entries", [])
            if isinstance(files_list, dict):
                files_list = list(files_list.values())

        for f in files_list:
            fname = f.get("key") or f.get("filename") or f.get("name", "")
            links = f.get("links", {})
            download_url = links.get("content") or links.get("self") or f.get("download_url", "")
            if not download_url and fname and rec_id:
                download_url = f"{self.config.zenodo_api_base}/records/{rec_id}/files/{fname}/content"

            size = f.get("size") or f.get("filesize", 0)
            checksum = f.get("checksum")

            if fname and download_url:
                files.append(ZenodoFile(
                    filename=fname,
                    download_url=download_url,
                    size=int(size),
                    checksum=checksum
                ))

        # Check if record has geospatial or data files
        has_data_files = any(
            f.filename.endswith((".zip", ".geojson", ".json", ".csv", ".tar.gz", ".tgz"))
            for f in files
        )

        return ZenodoRecord(
            id=rec_id,
            doi=doi,
            title=title,
            creators=creators,
            publication_date=pub_date,
            description=desc,
            license=normalized_lic,
            files=files,
            raw_metadata=item
        )

    def list_community_records(self) -> List[ZenodoRecord]:
        """
        Query Zenodo API for all records in the Glottography community.
        Note: Zenodo limits anonymous requests to size=25 per page (size=100 requires auth token).
        """
        records: List[ZenodoRecord] = []
        seen_ids = set()

        page_size = 100 if self.config.zenodo_token else 25

        endpoints_to_try = [
            f"{self.config.zenodo_api_base}/communities/{self.config.zenodo_community}/records?size={page_size}",
            f"{self.config.zenodo_api_base}/records?q=communities.slug:{self.config.zenodo_community}&size={page_size}",
            f"{self.config.zenodo_api_base}/records?q=communities:{self.config.zenodo_community}&size={page_size}",
        ]

        logger.info("Harvesting Zenodo records for community: %s", self.config.zenodo_community)

        for endpoint in endpoints_to_try:
            current_url: Optional[str] = endpoint
            page_records = []
            page = 1

            while current_url:
                logger.info("Fetching Zenodo page %d: %s", page, current_url)
                try:
                    payload = self._http_get(current_url)
                    data = json.loads(payload.decode("utf-8"))
                except Exception as e:
                    logger.warning("Failed query on endpoint %s: %s", current_url, e)
                    break

                hits_wrapper = data.get("hits", {})
                hits = hits_wrapper.get("hits", [])
                if not hits and isinstance(hits_wrapper, list):
                    hits = hits_wrapper

                for item in hits:
                    record = self._parse_record(item)
                    if record and record.id not in seen_ids:
                        seen_ids.add(record.id)
                        page_records.append(record)

                # Pagination
                links = data.get("links", {})
                next_url = links.get("next")
                if next_url and next_url != current_url:
                    current_url = next_url
                    page += 1
                else:
                    # Try explicit page increment if total hits exceed current count
                    total = hits_wrapper.get("total", 0)
                    if isinstance(total, dict):
                        total = total.get("value", 0)
                    if len(seen_ids) < total and page < 50:
                        page += 1
                        parsed = urllib.parse.urlparse(endpoint)
                        query = urllib.parse.parse_qs(parsed.query)
                        query["page"] = [str(page)]
                        new_qs = urllib.parse.urlencode(query, doseq=True)
                        current_url = urllib.parse.urlunparse(parsed._replace(query=new_qs))
                    else:
                        current_url = None

            if page_records:
                records.extend(page_records)
                logger.info("Discovered %d records from Zenodo community.", len(records))
                break

        return records

    def download_dataset(self, record: ZenodoRecord) -> Tuple[Path, Path]:
        """
        Downloads all data files for a Zenodo record and extracts archives.
        Returns (download_dir, extracted_dir).
        """
        rec_dir_name = record.slug
        download_dir = self.config.downloads_dir / rec_dir_name
        extracted_dir = self.config.extracted_dir / rec_dir_name

        download_dir.mkdir(parents=True, exist_ok=True)
        extracted_dir.mkdir(parents=True, exist_ok=True)

        # Save metadata JSON for provenance
        metadata_file = download_dir / "metadata.json"
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump({
                "id": record.id,
                "doi": record.doi,
                "title": record.title,
                "creators": record.creators,
                "publication_date": record.publication_date,
                "description": record.description,
                "license": record.license,
                "slug": record.slug,
                "files": [asdict(f) for f in record.files],
            }, f, indent=2, ensure_ascii=False)

        # Download files
        for zf in record.files:
            target_path = download_dir / zf.filename
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.exists() and target_path.stat().st_size > 0:
                logger.info("File already downloaded: %s", target_path.name)
            else:
                logger.info("Downloading %s (from %s)...", zf.filename, zf.download_url)
                data = self._http_get(zf.download_url)
                with open(target_path, "wb") as f:
                    f.write(data)
                logger.info("Saved %s (%d bytes)", zf.filename, len(data))

            # If ZIP, safely extract
            if zf.filename.lower().endswith(".zip"):
                logger.info("Extracting %s into %s...", zf.filename, extracted_dir)
                safe_extract_zip(target_path, extracted_dir)
            elif zf.filename.lower().endswith((".geojson", ".json", ".csv")):
                # Copy standalone data files into extracted dir as well
                dest_file = extracted_dir / zf.filename
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                with open(target_path, "rb") as src, open(dest_file, "wb") as dst:
                    dst.write(src.read())

        return download_dir, extracted_dir
