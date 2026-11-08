"""
Map builder for Wikimedia Commons Data: namespace.
Produces schema-compliant GeoJSON .map files with full provenance and categorization.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from glottography_to_commons.config import Config
from glottography_to_commons.parser import LanguageMapBundle
from glottography_to_commons.utils import (
    normalize_license,
    sanitize_title,
    slugify,
)
from glottography_to_commons.zenodo_client import ZenodoRecord

logger = logging.getLogger(__name__)


@dataclass
class GeneratedMap:
    """Represents a generated .map file ready for upload to Commons."""
    page_title: str
    file_path: Path
    content_json: str
    content_hash: str
    size_bytes: int
    glottocode: str
    language_name: str
    record_id: str
    dataset_slug: str


class MapBuilder:
    """Builds valid Wikimedia Commons .map data pages from language bundles."""

    def __init__(self, config: Config):
        self.config = config

    def build_page_title(self, bundle: LanguageMapBundle, record: ZenodoRecord) -> str:
        """
        Builds canonical MediaWiki title in the Data: namespace.
        E.g.: 'Data:Glottography - English (stan1295) - ashermoseley2007.map'
        """
        # Dataset short qualifier
        ds_short = slugify(record.slug.split("_", 1)[-1] if "_" in record.slug else record.slug)[:25]

        name_part = bundle.clean_name
        gc_part = f"({bundle.clean_glottocode})" if bundle.clean_glottocode != "unknown" else ""

        components = ["Glottography", name_part]
        if gc_part:
            components.append(gc_part)
        if ds_short:
            components.append(f"- {ds_short}")

        raw_title = f"Data:{' '.join(components)}.map"
        return sanitize_title(raw_title, namespace="Data:")

    def build_sources_wikitext(self, bundle: LanguageMapBundle, record: ZenodoRecord) -> str:
        """
        Constructs rich MediaWiki wikitext documentation for provenance.
        """
        creators_str = "; ".join(record.creators) if record.creators else "The Glottography Consortium"
        doi_link = f"[https://doi.org/{record.doi} {record.doi}]" if record.doi else "N/A"
        date_str = record.publication_date or "N/A"

        glotto_link = (
            f"[https://glottolog.org/resource/languoid/id/{bundle.clean_glottocode} {bundle.clean_glottocode}]"
            if bundle.clean_glottocode != "unknown" else "N/A"
        )
        iso_str = bundle.iso639_3 or "N/A"
        if bundle.iso639_3:
            iso_str = f"[https://iso639-3.sil.org/code/{bundle.iso639_3} {bundle.iso639_3}]"

        license_name = normalize_license(record.license)

        lines = [
            "Data derived from the [https://zenodo.org/communities/glottography Glottography project] on Zenodo.",
            "",
            f"* '''Dataset''': ''{record.title}''",
            f"* '''Zenodo Record / DOI''': {doi_link}",
            f"* '''Creators / Cartographers''': {creators_str}",
            f"* '''Publication Date''': {date_str}",
            f"* '''Language''': {bundle.clean_name}",
            f"* '''Glottocode''': {glotto_link}",
            f"* '''ISO 639-3''': {iso_str}",
            f"* '''License''': {license_name}",
            f"* '''Glottography Project Community''': [https://zenodo.org/communities/glottography https://zenodo.org/communities/glottography]",
        ]

        if bundle.extra_metadata:
            family = bundle.extra_metadata.get("family") or bundle.extra_metadata.get("Family")
            macroarea = bundle.extra_metadata.get("macroarea") or bundle.extra_metadata.get("Macroarea")
            if family:
                lines.append(f"* '''Language Family''': {family}")
            if macroarea:
                lines.append(f"* '''Macroarea''': {macroarea}")

        return "\n".join(lines)

    def build_description(self, bundle: LanguageMapBundle, record: ZenodoRecord) -> Dict[str, str]:
        """Multi-language description dictionary."""
        gc_str = f" ({bundle.clean_glottocode})" if bundle.clean_glottocode != "unknown" else ""
        desc_en = (
            f"Speaker area map of {bundle.clean_name}{gc_str} "
            f"derived from {record.title} (Glottography project)."
        )
        return {"en": desc_en}

    @staticmethod
    def get_language_map_category(language_name: str) -> str:
        """
        Derives the linguistic map category name under 'Category:Linguistic maps by language'.
        e.g. 'Northern Ju' -> 'Northern Ju-language linguistic maps'
             'English' -> 'English-language linguistic maps'
             'Modern Greek' -> 'Modern Greek-language linguistic maps'
        """
        clean = language_name.strip()
        if not clean or clean.lower().startswith("language unknown"):
            return "Linguistic maps by language"
        if clean.lower().endswith("language"):
            return f"{clean} linguistic maps"
        return f"{clean}-language linguistic maps"

    def build_map_data(
        self,
        bundle: LanguageMapBundle,
        record: ZenodoRecord
    ) -> Tuple[Dict[str, Any], str]:
        """
        Builds the complete JSON dictionary adhering to the Wikimedia Commons
        JsonConfig Map Data specification.
        """
        license_str = normalize_license(record.license)
        description_obj = self.build_description(bundle, record)
        sources_wikitext = self.build_sources_wikitext(bundle, record)
        page_title = self.build_page_title(bundle, record)

        # Main category for .map file:
        # Placed under Category:<Language>-language linguistic maps
        # (subcategorized under Category:Linguistic maps by language)
        lang_cat = self.get_language_map_category(bundle.clean_name)
        categories = [
            {
                "name": lang_cat,
                "sort": bundle.clean_name
            }
        ]

        # Standard Commons Map Data Schema
        map_payload = {
            "license": license_str,
            "description": description_obj,
            "sources": sources_wikitext,
            "mediawikiCategories": categories,
            "data": {
                "type": "FeatureCollection",
                "features": bundle.features
            }
        }

        return map_payload, page_title

    def save_map_file(
        self,
        bundle: LanguageMapBundle,
        record: ZenodoRecord
    ) -> Optional[GeneratedMap]:
        """
        Builds, validates, serializes, and writes a .map file to disk.
        Returns GeneratedMap instance or None if empty.
        """
        if not bundle.features:
            logger.warning("Skipping bundle with no features: %s (%s)", bundle.clean_name, bundle.clean_glottocode)
            return None

        map_data, page_title = self.build_map_data(bundle, record)

        # Serialize to formatted JSON
        content_json = json.dumps(map_data, indent=2, ensure_ascii=False)
        content_bytes = content_json.encode("utf-8")
        size_bytes = len(content_bytes)

        if size_bytes > self.config.max_map_bytes:
            logger.warning(
                "Map payload for %s (%d bytes) exceeds safe Commons threshold (%d bytes). Compacting JSON...",
                page_title, size_bytes, self.config.max_map_bytes
            )
            # Compact whitespace
            content_json = json.dumps(map_data, separators=(',', ':'), ensure_ascii=False)
            content_bytes = content_json.encode("utf-8")
            size_bytes = len(content_bytes)

        # Generate local filename based on title (bounded to prevent Windows MAX_PATH errors)
        base_slug = slugify(page_title.replace("Data:", "").replace(".map", ""))
        if len(base_slug) > 75:
            import hashlib
            base_slug = f"{base_slug[:75]}_{hashlib.md5(base_slug.encode()).hexdigest()[:6]}"
        filename = f"{base_slug}.map"
        file_path = self.config.maps_output_dir / filename

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content_json)

        import hashlib
        content_hash = hashlib.sha256(content_bytes).hexdigest()

        logger.info("Saved .map file: %s (%d bytes, %d features)", file_path.name, size_bytes, len(bundle.features))

        return GeneratedMap(
            page_title=page_title,
            file_path=file_path,
            content_json=content_json,
            content_hash=content_hash,
            size_bytes=size_bytes,
            glottocode=bundle.clean_glottocode,
            language_name=bundle.clean_name,
            record_id=record.id,
            dataset_slug=record.slug
        )

