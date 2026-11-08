"""
Parser for Glottography datasets (CLDF, raw GeoJSON, and metadata tables).
Extracts language geometries and bundles them per language with rich attributes.
"""

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from glottography_to_commons.utils import (
    round_coordinates,
    slugify,
    validate_geometry,
)

logger = logging.getLogger(__name__)


@dataclass
class LanguageMapBundle:
    """Represents the complete spatial data and metadata for one language in a dataset."""
    glottocode: str
    language_name: str
    iso639_3: Optional[str] = None
    features: List[Dict[str, Any]] = field(default_factory=list)
    dataset_slug: str = ""
    extra_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def clean_glottocode(self) -> str:
        gc = self.glottocode.strip()
        return gc if gc and gc.lower() != "none" else "unknown"

    @property
    def clean_name(self) -> str:
        name = self.language_name.strip()
        return name if name else f"Language {self.clean_glottocode}"


class DatasetParser:
    """Parses extracted Glottography directory structures into LanguageMapBundles."""

    def __init__(self, coordinate_precision: int = 5):
        self.coordinate_precision = coordinate_precision

    def parse_dataset_dir(self, dataset_dir: Path, dataset_slug: str) -> List[LanguageMapBundle]:
        """
        Scan and parse an extracted dataset directory.
        Handles CLDF packages, raw GeoJSON with features.csv, and standalone GeoJSON.
        """
        dataset_dir = Path(dataset_dir)
        bundles: Dict[Tuple[str, str], LanguageMapBundle] = {}

        logger.info("Parsing dataset directory: %s", dataset_dir.name)

        # 1. Discover all languages.csv / LanguageTable files
        languages_meta = self._discover_languages_metadata(dataset_dir)

        # 2. Discover features.csv (often in etc/ or raw/)
        features_meta = self._discover_features_metadata(dataset_dir)

        # 3. Discover and process all GeoJSON files
        geojson_files = list(dataset_dir.rglob("*.geojson")) + list(dataset_dir.rglob("*.json"))

        for gfile in geojson_files:
            # Skip metadata and schema json files
            if gfile.name.endswith(("-metadata.json", "metadata.json", "schema.json")):
                continue

            try:
                with open(gfile, "r", encoding="utf-8") as f:
                    content = json.load(f)
            except Exception as e:
                logger.debug("Skipping non-GeoJSON file %s: %s", gfile.name, e)
                continue

            if not isinstance(content, dict):
                continue

            # Could be a FeatureCollection or a single Feature or Geometry
            features = []
            if content.get("type") == "FeatureCollection":
                features = content.get("features", [])
            elif content.get("type") == "Feature":
                features = [content]
            elif content.get("type") in ("Polygon", "MultiPolygon", "Point", "LineString"):
                features = [{"type": "Feature", "geometry": content, "properties": {}}]

            if not features:
                continue

            logger.info("Found %d features in %s", len(features), gfile.relative_to(dataset_dir))

            for feat in features:
                if not isinstance(feat, dict):
                    continue

                geom = feat.get("geometry")
                valid, err = validate_geometry(geom)
                if not valid:
                    logger.debug("Skipping feature with invalid geometry in %s: %s", gfile.name, err)
                    continue

                # Clean and round coordinates
                geom["coordinates"] = round_coordinates(geom.get("coordinates"), self.coordinate_precision)

                # Resolve language identity
                glottocode, lang_name, iso, props = self._resolve_feature_language(
                    feat, languages_meta, features_meta, gfile
                )

                if not glottocode and not lang_name:
                    logger.debug("Feature in %s has no identifiable language/glottocode", gfile.name)
                    continue

                key = (glottocode, lang_name)
                if key not in bundles:
                    bundles[key] = LanguageMapBundle(
                        glottocode=glottocode,
                        language_name=lang_name,
                        iso639_3=iso,
                        features=[],
                        dataset_slug=dataset_slug,
                        extra_metadata=props
                    )

                # Standardize feature properties for Wikimedia Commons Kartographer
                standard_feature = {
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "title": lang_name or glottocode,
                        "description": f"Glottocode: {glottocode}" if glottocode else "",
                        "stroke": "#2a4b8d",
                        "stroke-width": 2,
                        "fill": "#3366cc",
                        "fill-opacity": 0.35,
                    }
                }
                # Preserve any original properties under extra
                for k, v in feat.get("properties", {}).items():
                    if k not in standard_feature["properties"] and v is not None:
                        standard_feature["properties"][k] = v

                bundles[key].features.append(standard_feature)

        result = list(bundles.values())
        logger.info("Extracted %d language bundles from %s", len(result), dataset_dir.name)
        return result

    def _discover_languages_metadata(self, root: Path) -> Dict[str, Dict[str, str]]:
        """
        Find languages.csv or CLDF LanguageTable files to map ID -> Name, Glottocode, ISO.
        """
        meta_lookup: Dict[str, Dict[str, str]] = {}

        for csv_path in root.rglob("*.csv"):
            if "language" not in csv_path.name.lower():
                continue

            try:
                with open(csv_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    if not reader.fieldnames:
                        continue

                    # Determine relevant column names case-insensitively
                    col_map = {c.lower(): c for c in reader.fieldnames}
                    id_col = col_map.get("id") or col_map.get("cldf:id")
                    name_col = col_map.get("name") or col_map.get("cldf:name")
                    gc_col = (
                        col_map.get("glottocode")
                        or col_map.get("cldf:glottocode")
                        or col_map.get("glottolog_id")
                    )
                    iso_col = (
                        col_map.get("iso639p3code")
                        or col_map.get("cldf:iso639p3code")
                        or col_map.get("isocode")
                        or col_map.get("iso")
                    )

                    for row in reader:
                        row_id = (row.get(id_col) or "").strip()
                        gc = (row.get(gc_col) or "").strip() if gc_col else ""
                        name = (row.get(name_col) or "").strip() if name_col else ""
                        iso = (row.get(iso_col) or "").strip() if iso_col else ""

                        data = {
                            "glottocode": gc,
                            "name": name,
                            "iso": iso,
                        }
                        if row_id:
                            meta_lookup[row_id] = data
                            meta_lookup[row_id.lower()] = data
                        if gc:
                            meta_lookup[gc] = data
                            meta_lookup[gc.lower()] = data

            except Exception as e:
                logger.warning("Error reading languages metadata CSV %s: %s", csv_path.name, e)

        return meta_lookup

    def _discover_features_metadata(self, root: Path) -> Dict[str, Dict[str, str]]:
        """
        Find features.csv (common in pyglottography/etc/) mapping feature ID -> Glottocode/Name.
        """
        meta_lookup: Dict[str, Dict[str, str]] = {}

        for csv_path in root.rglob("*.csv"):
            if "feature" not in csv_path.name.lower():
                continue

            try:
                with open(csv_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    if not reader.fieldnames:
                        continue

                    col_map = {c.lower(): c for c in reader.fieldnames}
                    id_col = col_map.get("id") or col_map.get("feature_id")
                    gc_col = col_map.get("glottocode") or col_map.get("glottolog_id")
                    name_col = col_map.get("name") or col_map.get("language")

                    for row in reader:
                        fid = (row.get(id_col) or "").strip() if id_col else ""
                        gc = (row.get(gc_col) or "").strip() if gc_col else ""
                        name = (row.get(name_col) or "").strip() if name_col else ""

                        if fid:
                            meta_lookup[fid] = {"glottocode": gc, "name": name}
                            meta_lookup[fid.lower()] = {"glottocode": gc, "name": name}

            except Exception as e:
                logger.warning("Error reading features metadata CSV %s: %s", csv_path.name, e)

        return meta_lookup

    def _resolve_feature_language(
        self,
        feat: Dict[str, Any],
        languages_meta: Dict[str, Dict[str, str]],
        features_meta: Dict[str, Dict[str, str]],
        gfile: Path
    ) -> Tuple[str, str, Optional[str], Dict[str, Any]]:
        """
        Inspect feature properties, ID, and context to determine Glottocode and Language Name.
        """
        props = feat.get("properties") or {}
        feat_id = str(feat.get("id") or props.get("id") or "").strip()

        # 1. Check feature properties directly
        glottocode = (
            props.get("glottocode")
            or props.get("cldf:glottocode")
            or props.get("glottolog_id")
            or props.get("Glottocode")
            or ""
        ).strip()

        lang_name = (
            props.get("name")
            or props.get("cldf:name")
            or props.get("language")
            or props.get("Language")
            or props.get("Language_name")
            or ""
        ).strip()

        iso = (
            props.get("iso639p3code")
            or props.get("cldf:iso639p3code")
            or props.get("iso")
            or props.get("isocode")
            or ""
        ).strip() or None

        # 2. Check feature ID against features_meta
        if feat_id and (not glottocode or not lang_name):
            f_info = features_meta.get(feat_id) or features_meta.get(feat_id.lower())
            if f_info:
                if not glottocode:
                    glottocode = f_info.get("glottocode", "")
                if not lang_name:
                    lang_name = f_info.get("name", "")

        # 3. Check CLDF language reference or language ID
        lang_ref = (
            props.get("cldf:languageReference")
            or props.get("Language_ID")
            or props.get("language_id")
            or props.get("LanguageTable_ID")
            or ""
        ).strip()

        candidate_keys = [lang_ref, glottocode, feat_id]
        for key in candidate_keys:
            if key and (not glottocode or not lang_name or not iso):
                l_info = languages_meta.get(key) or languages_meta.get(key.lower())
                if l_info:
                    if not glottocode:
                        glottocode = l_info.get("glottocode", "")
                    if not lang_name:
                        lang_name = l_info.get("name", "")
                    if not iso:
                        iso = l_info.get("iso")

        # 4. Fallback to file name or feature ID if glottocode looks like a glottocode (4 letters + 4 digits)
        if not glottocode:
            for cand in [feat_id, gfile.stem]:
                cand = cand.strip()
                if len(cand) == 8 and cand[:4].isalpha() and cand[4:].isdigit():
                    glottocode = cand
                    break

        # Fallback language name
        if not lang_name:
            if glottocode:
                lang_name = f"Language ({glottocode})"
            elif feat_id:
                lang_name = f"Feature {feat_id}"
            else:
                lang_name = gfile.stem.replace("_", " ").title()

        return glottocode, lang_name, iso, props

