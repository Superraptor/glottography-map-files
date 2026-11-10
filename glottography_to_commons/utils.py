"""
Utility functions for geometry validation, coordinate rounding,
license normalization, title sanitization, and safe archive extraction.
"""

import json
import logging
import math
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


def get_language_sort_key(language_name: str) -> str:
    """
    Derives the sort key for a language name, stripping 'Standard' prefix/suffix
    so that 'Standard Arabic' sorts as 'Arabic', 'Standard English' as 'English',
    'Croatian Standard' as 'Croatian', etc.
    """
    if not language_name:
        return ""
    clean = language_name.strip()
    # Strip leading 'Standard ' (case-insensitive)
    stripped = re.sub(r"^standard\s+", "", clean, flags=re.IGNORECASE)
    # Strip trailing ' Standard' (case-insensitive)
    stripped = re.sub(r"\s+standard$", "", stripped, flags=re.IGNORECASE).strip()
    return stripped if stripped else clean


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


def extract_all_coordinates(coords: Any) -> List[Tuple[float, float]]:
    """Recursively extracts all [lon, lat] pairs from arbitrary nested GeoJSON coordinate structures."""
    if not coords:
        return []
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and isinstance(coords[0], (int, float)) and isinstance(coords[1], (int, float)):
            return [(float(coords[0]), float(coords[1]))]
        pts = []
        for item in coords:
            pts.extend(extract_all_coordinates(item))
        return pts
    return []


def lat_to_mercator_y(lat: float) -> float:
    """Projects latitude in degrees to Web Mercator Y in [0, 1]."""
    lat = max(-85.05112878, min(85.05112878, lat))
    rad = math.radians(lat)
    return 0.5 - (math.log(math.tan(math.pi / 4.0 + rad / 2.0)) / (2.0 * math.pi))


def calculate_center_and_zoom(
    features: List[Dict[str, Any]],
    target_width: float = 480.0,
    target_height: float = 320.0,
    margin: float = 0.12
) -> Tuple[float, float, int]:
    """
    Computes geographical center (latitude, longitude) and optimal
    Kartographer zoom level (1 to 16) to frame all features into view.
    Ensures small regional or local language maps are zoomed directly into frame
    instead of defaulting to an unzoomed world map.

    Uses Web Mercator projection bounds fitted to a standard preview viewport.

    Returns:
        (latitude, longitude, zoom)
    """
    all_pts = []
    for f in features:
        geom = f.get("geometry", {})
        coords = geom.get("coordinates")
        if coords:
            all_pts.extend(extract_all_coordinates(coords))

    if not all_pts:
        return 0.0, 0.0, 2

    min_lon = min(pt[0] for pt in all_pts)
    max_lon = max(pt[0] for pt in all_pts)
    min_lat = min(pt[1] for pt in all_pts)
    max_lat = max(pt[1] for pt in all_pts)

    center_lat = (min_lat + max_lat) / 2.0
    center_lon = (min_lon + max_lon) / 2.0

    center_lat = max(-85.0, min(85.0, center_lat))
    center_lon = max(-180.0, min(180.0, center_lon))

    d_lon = max(max_lon - min_lon, 0.005)

    y_min = lat_to_mercator_y(max_lat)
    y_max = lat_to_mercator_y(min_lat)
    d_y = max(y_max - y_min, 0.0001)

    avail_w = target_width * (1.0 - margin)
    avail_h = target_height * (1.0 - margin)

    zoom_lon = math.log2((avail_w / 256.0) * (360.0 / d_lon))
    zoom_lat = math.log2((avail_h / 256.0) / d_y)

    raw_zoom = min(zoom_lon, zoom_lat)
    zoom = max(1, min(16, int(math.floor(raw_zoom))))

    # Single point or identical extrema
    if max_lon == min_lon and max_lat == min_lat:
        zoom = 9

    return round(center_lat, 5), round(center_lon, 5), zoom


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


def count_geometry_vertices(geom: dict) -> int:
    """Counts total number of coordinate pairs in a GeoJSON geometry."""
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return 0
    if gtype == "Point":
        return 1
    elif gtype in ("MultiPoint", "LineString"):
        return len(coords)
    elif gtype in ("MultiLineString", "Polygon"):
        return sum(len(part) for part in coords if isinstance(part, list))
    elif gtype == "MultiPolygon":
        total = 0
        for poly in coords:
            if isinstance(poly, list):
                for ring in poly:
                    if isinstance(ring, list):
                        total += len(ring)
        return total
    return 0


def rdp_simplify_points(pts: List[List[float]], eps: float) -> List[List[float]]:
    """
    Iterative Ramer-Douglas-Peucker (RDP) polyline simplification.
    Preserves start and end vertices without hitting Python recursion limits.
    """
    n = len(pts)
    if n < 3:
        return pts

    keep = [False] * n
    keep[0] = True
    keep[-1] = True
    stack = [(0, n - 1)]

    while stack:
        s_idx, e_idx = stack.pop()
        p1 = pts[s_idx]
        p2 = pts[e_idx]

        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        denom = (dx * dx + dy * dy) ** 0.5

        dmax = 0.0
        max_idx = s_idx

        if denom == 0:
            for i in range(s_idx + 1, e_idx):
                px, py = pts[i][0], pts[i][1]
                d = ((px - p1[0]) ** 2 + (py - p1[1]) ** 2) ** 0.5
                if d > dmax:
                    dmax = d
                    max_idx = i
        else:
            inv_denom = 1.0 / denom
            c = p2[0] * p1[1] - p2[1] * p1[0]
            for i in range(s_idx + 1, e_idx):
                px, py = pts[i][0], pts[i][1]
                d = abs(dy * px - dx * py + c) * inv_denom
                if d > dmax:
                    dmax = d
                    max_idx = i

        if dmax > eps:
            keep[max_idx] = True
            if max_idx - s_idx > 1:
                stack.append((s_idx, max_idx))
            if e_idx - max_idx > 1:
                stack.append((max_idx, e_idx))

    return [pts[i] for i in range(n) if keep[i]]


def simplify_polygon_ring(ring: List[List[float]], eps: float) -> List[List[float]]:
    """
    Simplifies a closed polygon linear ring using RDP.
    Splits the closed loop at the maximum chord distance to preserve closure
    and maintain at least 4 vertices for valid polygon topology.
    """
    if len(ring) <= 4:
        return ring

    is_closed = (ring[0] == ring[-1])
    if is_closed:
        x0, y0 = ring[0][0], ring[0][1]
        dmax_sq = 0.0
        split_idx = 1
        for i in range(1, len(ring) - 1):
            d_sq = (ring[i][0] - x0) ** 2 + (ring[i][1] - y0) ** 2
            if d_sq > dmax_sq:
                dmax_sq = d_sq
                split_idx = i

        r1 = rdp_simplify_points(ring[:split_idx + 1], eps)
        r2 = rdp_simplify_points(ring[split_idx:], eps)
        simplified = r1[:-1] + r2
        if len(simplified) < 4:
            return ring
        if simplified[0] != simplified[-1]:
            simplified.append(simplified[0])
        return simplified
    else:
        return rdp_simplify_points(ring, eps)


def simplify_geometry(geom: dict, eps: float) -> Tuple[dict, int, int]:
    """
    Simplifies GeoJSON geometry rings with tolerance eps (in degrees).
    Returns (simplified_geometry_dict, original_pts_count, simplified_pts_count).
    """
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return geom, 0, 0

    orig_pts = 0
    simp_pts = 0

    if gtype == "Polygon":
        new_coords = []
        for ring in coords:
            orig_pts += len(ring)
            new_ring = simplify_polygon_ring(ring, eps)
            simp_pts += len(new_ring)
            new_coords.append(new_ring)
        return {"type": "Polygon", "coordinates": new_coords}, orig_pts, simp_pts

    elif gtype == "MultiPolygon":
        new_coords = []
        for poly in coords:
            new_poly = []
            for ring in poly:
                orig_pts += len(ring)
                new_ring = simplify_polygon_ring(ring, eps)
                simp_pts += len(new_ring)
                new_poly.append(new_ring)
            new_coords.append(new_poly)
        return {"type": "MultiPolygon", "coordinates": new_coords}, orig_pts, simp_pts

    elif gtype == "LineString":
        orig_pts = len(coords)
        new_coords = rdp_simplify_points(coords, eps)
        simp_pts = len(new_coords)
        return {"type": "LineString", "coordinates": new_coords}, orig_pts, simp_pts

    elif gtype == "MultiLineString":
        new_coords = []
        for line in coords:
            orig_pts += len(line)
            new_line = rdp_simplify_points(line, eps)
            simp_pts += len(new_line)
            new_coords.append(new_line)
        return {"type": "MultiLineString", "coordinates": new_coords}, orig_pts, simp_pts

    return geom, orig_pts, simp_pts


def build_simplification_provenance(
    orig_vertices: int,
    simp_vertices: int,
    epsilon: float,
    orig_bytes: int,
    final_bytes: int
) -> str:
    """Builds wikitext documentation describing polygon simplification and data loss."""
    reduction_pct = (1.0 - simp_vertices / orig_vertices) * 100.0 if orig_vertices > 0 else 0.0
    approx_meters = round(epsilon * 111_000, 1)
    return (
        f"\n\n* '''Compression & Optimization''':\n"
        f"** '''Lossless Serialization''': Compact JSON minification (stripping non-semantic whitespace).\n"
        f"** '''Coordinate Precision''': Coordinates rounded to 5 decimal places (~1.1m resolution; sub-meter decimals discarded).\n"
        f"** '''Polygon Simplification''': Ramer-Douglas-Peucker (RDP) iterative polyline algorithm with perpendicular distance tolerance $\\epsilon = {epsilon}^\\circ$ (~{approx_meters} meters).\n"
        f"** '''Vertex Reduction''': Reduced vertices from {orig_vertices:,} to {simp_vertices:,} ({reduction_pct:.1f}% vertex reduction; payload {orig_bytes:,} B -> {final_bytes:,} B).\n"
        f"** '''Data Loss & Fidelity''': Sub-resolution coastal and border micro-variations whose perpendicular distance is under {approx_meters} meters were smoothed to comply with the Wikimedia Commons 2,048 KB (2 MB) JsonConfig payload ceiling. Core boundaries, topology, enclave/exclave polygons, and total territorial area are preserved."
    )


def build_lossless_optimization_provenance(orig_bytes: int, final_bytes: int) -> str:
    """Builds wikitext documentation describing lossless minification."""
    return (
        f"\n\n* '''Compression & Optimization''':\n"
        f"** '''Lossless Serialization''': Compact JSON minification (stripping cosmetic whitespace).\n"
        f"** '''Coordinate Precision''': Coordinates rounded to 5 decimal places (~1.1m resolution; sub-meter decimals discarded).\n"
        f"** '''Data Loss & Fidelity''': 0% topological data loss; exact polygon boundary coordinates and geometry retained."
    )


def optimize_map_payload(
    map_payload: dict,
    max_bytes: int = 1_850_000,
    force_epsilon: Optional[float] = None
) -> Tuple[dict, dict]:
    """
    Optimizes a Wikimedia Commons Map Data JSON payload.
    Applies lossless compact serialization and, if the payload exceeds max_bytes
    (or force_epsilon is given), adaptively applies Ramer-Douglas-Peucker polyline simplification.
    Updates the payload's 'sources' section with detailed provenance documentation.

    Returns:
        (optimized_payload_dict, stats_dict)
    """
    compact_json = json.dumps(map_payload, separators=(',', ':'), ensure_ascii=False)
    orig_bytes = len(compact_json.encode('utf-8'))

    # Count total vertices
    features = map_payload.get("data", {}).get("features", [])
    total_vertices = sum(count_geometry_vertices(f.get("geometry", {})) for f in features)

    # Check both compact size and 4-space formatted size (to guarantee that even if opened
    # and copied from a browser JSON viewer that auto-formats with 4 spaces, it stays under 2,048 KB)
    indent4_json = json.dumps(map_payload, indent=4, ensure_ascii=False)
    orig_indent4_bytes = len(indent4_json.encode('utf-8'))

    epsilons = [force_epsilon] if force_epsilon else [0.0003, 0.0005, 0.001, 0.0015, 0.002, 0.005]

    needs_simplification = (orig_bytes > max_bytes) or (orig_indent4_bytes > max_bytes) or (force_epsilon is not None)

    if not needs_simplification:
        stats = {
            "simplified": False,
            "orig_vertices": total_vertices,
            "final_vertices": total_vertices,
            "orig_bytes": orig_bytes,
            "final_bytes": orig_bytes,
            "epsilon": 0.0
        }
        return map_payload, stats

    # Adaptively simplify
    best_payload = map_payload
    best_stats = None

    for eps in epsilons:
        new_features = []
        cur_orig_pts = 0
        cur_simp_pts = 0

        for f in features:
            geom = f.get("geometry", {})
            simp_geom, o_pts, s_pts = simplify_geometry(geom, eps)
            cur_orig_pts += o_pts
            cur_simp_pts += s_pts
            new_f = dict(f)
            new_f["geometry"] = simp_geom
            new_features.append(new_f)

        candidate_payload = dict(map_payload)
        candidate_payload["data"] = {
            "type": map_payload.get("data", {}).get("type", "FeatureCollection"),
            "features": new_features
        }

        # Check candidate size in both compact and indent=4 format
        test_json = json.dumps(candidate_payload, separators=(',', ':'), ensure_ascii=False)
        test_bytes = len(test_json.encode('utf-8'))

        test_indent4 = json.dumps(candidate_payload, indent=4, ensure_ascii=False)
        test_indent4_bytes = len(test_indent4.encode('utf-8'))

        best_payload = candidate_payload
        best_stats = {
            "simplified": True,
            "orig_vertices": cur_orig_pts or total_vertices,
            "final_vertices": cur_simp_pts,
            "orig_bytes": orig_bytes,
            "final_bytes": test_bytes,
            "epsilon": eps
        }

        # Safe limit: both compact JSON and browser 4-space formatted JSON must be <= max_bytes
        if test_bytes <= max_bytes and test_indent4_bytes <= max_bytes and force_epsilon is None:
            break

    # Append provenance notes to sources
    if best_stats and best_stats["simplified"]:
        prov_note = build_simplification_provenance(
            orig_vertices=best_stats["orig_vertices"],
            simp_vertices=best_stats["final_vertices"],
            epsilon=best_stats["epsilon"],
            orig_bytes=orig_bytes,
            final_bytes=best_stats["final_bytes"]
        )
        sources_text = best_payload.get("sources", "")
        best_payload["sources"] = sources_text + prov_note

    return best_payload, best_stats

