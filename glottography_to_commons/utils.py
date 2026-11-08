"""
Utility functions for geometry validation, coordinate rounding,
license normalization, title sanitization, and safe archive extraction.
"""

import json
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Wikimedia Commons allowed licenses for the Data: namespace (Extension:JsonConfig)
ALLOWED_COMMONS_LICENSES = {
    "CC0-1.0",
    "CC-BY-1.0",
    "CC-BY-2.0",
    "CC-BY-2.5",
    "CC-BY-3.0",
    "CC-BY-4.0",
    "CC-BY-4.0+",
    "CC-BY-SA-1.0",
    "CC-BY-SA-2.0",
    "CC-BY-SA-2.5",
    "CC-BY-SA-3.0",
    "CC-BY-SA-4.0",
    "CC-BY-SA-4.0+",
    "ODbL-1.0",
}

# License normalization map
LICENSE_MAP = {
    "cc0": "CC0-1.0",
    "cc0 1.0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "public domain": "CC0-1.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc by 4.0": "CC-BY-4.0",
    "cc-by-4.0+": "CC-BY-4.0+",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "cc by-sa 4.0": "CC-BY-SA-4.0",
    "cc-by-3.0": "CC-BY-3.0",
    "cc by 3.0": "CC-BY-3.0",
    "cc-by-sa-3.0": "CC-BY-SA-3.0",
    "odbl": "ODbL-1.0",
    "odbl-1.0": "ODbL-1.0",
}

# MediaWiki disallowed title characters: # < > [ ] | { } \ /
# Note: ':' is allowed as namespace separator, but in title text we replace colons with dashes.
DISALLOWED_TITLE_CHARS = re.compile(r'[#<>\[\]|{}\\\/]')


def sanitize_title(title: str, namespace: str = "Data:") -> str:
    r"""
    Sanitizes a page title to be compliant with MediaWiki title rules.
    Removes disallowed characters (# < > [ ] | { } \ /), strips leading/trailing spaces,
    and replaces underscores or colons within the title body.
    """
    clean_ns = namespace.rstrip(":") + ":" if namespace else ""
    body = title
    if namespace and body.startswith(clean_ns):
        body = body[len(clean_ns):]

    # Replace disallowed chars with a hyphen or remove
    body = DISALLOWED_TITLE_CHARS.sub(" - ", body)
    # Replace internal colons that might confuse namespace parsing
    body = body.replace(":", " - ")
    # Replace multiple spaces/hyphens
    body = re.sub(r'[\s_]+', ' ', body)
    body = re.sub(r'\s*-\s*', ' - ', body)
    body = re.sub(r'( - )+', ' - ', body).strip(" - ")

    # Ensure max byte length for MediaWiki page title (255 bytes limit)
    encoded = body.encode("utf-8")
    if len(encoded) > 230:  # reserve room for namespace and extension
        body = encoded[:230].decode("utf-8", "ignore").strip()

    return f"{clean_ns}{body}"


def slugify(text: str) -> str:
    """Convert text to a safe alphanumeric slug for filenames and keys."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '_', text)
    return text.strip('_')


def normalize_license(raw_license: Optional[Union[str, dict]]) -> str:
    """
    Maps arbitrary license strings or Zenodo license objects to an allowed
    Wikimedia Commons JsonConfig license string.
    Defaults to CC-BY-4.0 if unmapped or unknown.
    """
    if isinstance(raw_license, dict):
        raw_license = raw_license.get("id") or raw_license.get("title") or ""

    if not raw_license:
        return "CC-BY-4.0"

    cleaned = str(raw_license).strip().lower()

    if cleaned in LICENSE_MAP:
        return LICENSE_MAP[cleaned]

    # Look for partial matches
    if "cc0" in cleaned or "public domain" in cleaned:
        return "CC0-1.0"
    if "by-sa" in cleaned or "sharealike" in cleaned:
        if "3.0" in cleaned:
            return "CC-BY-SA-3.0"
        return "CC-BY-SA-4.0"
    if "by" in cleaned:
        if "3.0" in cleaned:
            return "CC-BY-3.0"
        return "CC-BY-4.0"
    if "odbl" in cleaned:
        return "ODbL-1.0"

    # Fallback to standard open Glottography license
    logger.warning("Unrecognized license '%s', defaulting to CC-BY-4.0", raw_license)
    return "CC-BY-4.0"


def round_coordinates(coords: Any, precision: int = 5) -> Any:
    """
    Recursively round coordinate numbers in GeoJSON coordinates.
    Precision 5 provides ~1.1 meter accuracy while dramatically reducing JSON size.
    """
    if isinstance(coords, (int, float)):
        return round(float(coords), precision)
    elif isinstance(coords, (list, tuple)):
        return [round_coordinates(c, precision) for c in coords]
    return coords


def validate_coordinates(coords: Any) -> Tuple[bool, Optional[str]]:
    """
    Validate that coordinate pair [lon, lat] is within WGS84 bounds:
    -180 <= lon <= 180 and -90 <= lat <= 90.
    """
    if not isinstance(coords, (list, tuple)):
        return False, f"Coordinates must be a list/tuple, got {type(coords)}"

    if len(coords) == 0:
        return False, "Empty coordinates array"

    # If first item is float/int, this is a [lon, lat] or [lon, lat, elev] point
    if isinstance(coords[0], (int, float)):
        if len(coords) < 2:
            return False, f"Coordinate point must have at least 2 numbers, got {coords}"
        lon, lat = coords[0], coords[1]
        if not (-180.0 <= lon <= 180.0):
            # Sometimes Pacific coordinates are shifted (e.g. 180..360), normalize if needed
            if 180.0 < lon <= 360.0:
                lon -= 360.0
            else:
                return False, f"Longitude {lon} out of bounds [-180, 180]"
        if not (-90.0 <= lat <= 90.0):
            return False, f"Latitude {lat} out of bounds [-90, 90]"
        return True, None

    # Nested array: validate each element recursively
    for sub in coords:
        valid, err = validate_coordinates(sub)
        if not valid:
            return False, err

    return True, None


def validate_geometry(geometry: Optional[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    """
    Validates a GeoJSON geometry dictionary against basic WGS84 standards.
    """
    if not geometry or not isinstance(geometry, dict):
        return False, "Geometry is missing or not a dict"

    geom_type = geometry.get("type")
    valid_types = {
        "Point", "MultiPoint", "LineString", "MultiLineString",
        "Polygon", "MultiPolygon", "GeometryCollection"
    }
    if geom_type not in valid_types:
        return False, f"Unsupported geometry type: {geom_type}"

    if geom_type == "GeometryCollection":
        geometries = geometry.get("geometries", [])
        if not geometries:
            return False, "GeometryCollection has no geometries"
        for g in geometries:
            valid, err = validate_geometry(g)
            if not valid:
                return False, err
        return True, None

    coords = geometry.get("coordinates")
    if coords is None:
        return False, "Geometry missing coordinates"

    # Validate coordinate bounds
    valid, err = validate_coordinates(coords)
    if not valid:
        return False, err

    # Check polygon ring closure
    if geom_type == "Polygon":
        for ring in coords:
            if len(ring) < 4:
                return False, f"Polygon linear ring has {len(ring)} points, minimum is 4"
            if ring[0] != ring[-1]:
                # Automatically fix ring closure if slightly off
                ring.append(ring[0])
    elif geom_type == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                if len(ring) < 4:
                    return False, f"MultiPolygon linear ring has {len(ring)} points, minimum is 4"
                if ring[0] != ring[-1]:
                    ring.append(ring[0])

    return True, None


def safe_extract_zip(zip_path: Union[str, Path], extract_to: Union[str, Path]) -> List[Path]:
    """
    Safely extract a ZIP archive protecting against path traversal (Zip Slip vulnerability).
    Returns list of extracted file paths.
    """
    zip_path = Path(zip_path)
    extract_to = Path(extract_to).resolve()
    extract_to.mkdir(parents=True, exist_ok=True)
    extracted_files = []

    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.infolist():
            # Resolve target path
            target_path = (extract_to / member.filename).resolve()
            if not str(target_path).startswith(str(extract_to)):
                logger.warning("Skipping suspicious ZIP entry in %s: %s", zip_path.name, member.filename)
                continue

            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())
                extracted_files.append(target_path)

    return extracted_files

