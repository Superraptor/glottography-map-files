# Glottography to Wikimedia Commons Map Data Pipeline

Automated pipeline to download all geolinguistic datasets from the **[Zenodo Glottography Community](https://zenodo.org/communities/glottography/)**, process and split the speaker-area geometries **per language**, and upload them as structured `.map` files (in the `Data:` namespace) with complete provenance and licensing to **[Wikimedia Commons](https://commons.wikimedia.org/)** under a dedicated subcategory:

> **[Category:Map data by language](https://commons.wikimedia.org/wiki/Category:Map_data_by_language)**  
> *(Subcategory of [Category:Map data by object](https://commons.wikimedia.org/wiki/Category:Map_data_by_object))*

---

## Table of Contents

- [Overview & Architecture](#overview--architecture)
- [Wikimedia Commons Map Data Schema](#wikimedia-commons-map-data-schema)
- [Compliance & Rate Limiting](#compliance--rate-limiting)
- [Repository Structure](#repository-structure)
- [Prerequisites & Installation](#prerequisites--installation)
- [Configuration](#configuration)
- [Usage & CLI Reference](#usage--cli-reference)
- [Manual Upload Mode & Portal](#manual-upload-mode--portal)
- [State Tracking & Resumability](#state-tracking--resumability)
- [Testing](#testing)
- [License](#license)

---

## Overview & Architecture

```
 Zenodo Glottography Community
 (https://zenodo.org/communities/glottography/)
               │
               ▼  1. Harvest & Download (zenodo_client.py)
   Raw Datasets (.zip / GeoJSON / CLDF)
               │
               ▼  2. Parse & Extract Per Language (parser.py)
 Language Map Bundles (Glottocode + WGS84 Polygons)
               │
               ▼  3. Build Wikimedia .map Schema (map_builder.py)
 Standardized Data:*.map Files with Provenance & Categories
               │
               ▼  4. Upload with Rate Limiting (wikimedia_client.py)
 Wikimedia Commons:
   ├── Category:Map data by language (under Category:Map data by object)
   └── Data:Glottography - <Language> (<Glottocode>) - <Dataset>.map
```

1. **Zenodo Harvester (`zenodo_client.py`)**:
   - Queries the Zenodo InvenioRDM REST API for all records belonging to the `glottography` community.
   - Paginates through all published datasets.
   - Downloads archives (ZIP, GeoJSON, tabular files) and extracts them safely (with path-traversal protection).
   - Preserves complete record metadata (DOI, title, creators, publication date, license).

2. **Dataset Parser (`parser.py`)**:
   - Understands Cross-Linguistic Data Formats (CLDF): reads `languages.csv` / `LanguageTable` to resolve Glottolog identifiers, ISO 639-3 codes, and standardized language names.
   - Understands Pyglottography and raw GeoJSON layouts (`raw/dataset.geojson`, `etc/features.csv`).
   - Extracts and groups all polygons and multipolygons **per language**.
   - Validates coordinates against WGS84 bounds ($-180^\circ \le \text{lon} \le 180^\circ$ and $-90^\circ \le \text{lat} \le 90^\circ$).
   - Rounds coordinate precision (default 5 decimal places $\approx 1.1\,\text{m}$) to reduce payload sizes and ensure compliance with Wikimedia's 2 MB limit for `.map` pages.

3. **Map Builder (`map_builder.py`)**:
   - Builds schema-compliant JSON payloads for the MediaWiki JsonConfig extension (`Data:*.map`).
   - Generates full provenance wikitext in `"sources"` citing:
     - Dataset title, Zenodo DOI link (`https://doi.org/...`), creators, publication date
     - Glottolog language languoid link (`https://glottolog.org/resource/languoid/id/...`)
     - SIL ISO 639-3 link (`https://iso639-3.sil.org/code/...`)
     - Glottography project on Zenodo
   - Injects the category assignment into the JSON schema:
     ```json
     "mediawikiCategories": [
       {
         "name": "Map data by language",
         "sort": "<Language Name>"
       }
     ]
     ```

4. **Wikimedia Commons Client (`wikimedia_client.py`)**:
   - Ensures the subcategory `Category:Map data by language` exists under `Category:Map data by object`. If missing, it automatically creates it with:
     ```wikitext
     {{CategoryMapData}}
     Map data files (.map) in the Data: namespace representing geographical speaker areas and distribution boundaries for the world's languages.

     [[Category:Map data by object|Language]]
     ```
   - Authenticates using MediaWiki **Bot Passwords**.
   - Edits pages in the `Data:` namespace using the `action=edit` API with `maxlag=5` and `assert=user`.
   - Enforces configurable rate-limiting (default 2 seconds between edits) and exponential backoff on HTTP 429 / 503 or maxlag responses.
   - Fully supports a `--dry-run` mode to preview and validate locally without modifying Commons.

5. **State Tracker (`state_tracker.py`)**:
   - SQLite-backed state persistence in `data/pipeline_state.db`.
   - Tracks download status, content hashes, upload timestamps, and error messages.
   - Makes runs fully idempotent and resumable if interrupted.

---

## Wikimedia Commons Map Data Schema

Each uploaded language map page (e.g. `Data:Glottography - English (stan1295) - asher2007.map`) follows the official MediaWiki Kartographer / JsonConfig format:

```json
{
  "version": 1,
  "license": "CC-BY-4.0",
  "description": {
    "en": "Speaker area map of English (stan1295) derived from Atlas of the World's Languages (2007) (Glottography project)."
  },
  "sources": "Data derived from the [https://zenodo.org/communities/glottography Glottography project] on Zenodo.\n\n* '''Dataset''': ''Atlas of the World's Languages (2007)''\n* '''Zenodo Record / DOI''': [https://doi.org/10.5281/zenodo.15287258 10.5281/zenodo.15287258]\n* '''Creators / Cartographers''': Asher, R. E.; Moseley, Christopher J.\n* '''Publication Date''': 2021-04-15\n* '''Language''': English\n* '''Glottocode''': [https://glottolog.org/resource/languoid/id/stan1295 stan1295]\n* '''ISO 639-3''': [https://iso639-3.sil.org/code/eng eng]\n* '''License''': CC-BY-4.0\n* '''Glottography Project Community''': [https://zenodo.org/communities/glottography https://zenodo.org/communities/glottography]",
  "mediawikiCategories": [
    {
      "name": "Map data by language",
      "sort": "English"
    }
  ],
  "data": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": {
          "type": "Polygon",
          "coordinates": [...]
        },
        "properties": {
          "title": "English",
          "description": "Glottocode: stan1295",
          "stroke": "#2a4b8d",
          "stroke-width": 2,
          "fill": "#3366cc",
          "fill-opacity": 0.35
        }
      }
    ]
  }
}
```

---

## Compliance & Rate Limiting

The pipeline is designed in strict accordance with the [Wikimedia User-Agent policy](https://meta.wikimedia.org/wiki/User-Agent_policy) and [Wikimedia Bot policy](https://commons.wikimedia.org/wiki/Commons:Bots):

- **User-Agent:** All HTTP requests include a customized, descriptive User-Agent header containing tool identification and contact information.
- **Maxlag Protocol:** MediaWiki `maxlag=5` is sent on every edit request. If database replication lag exceeds this threshold, the client pauses and retries with exponential backoff.
- **Rate Limiting:** A default delay of 2.0 seconds between edit requests is strictly enforced to protect Wikimedia infrastructure.
- **Bot Password Authentication:** Uses dedicated Bot Passwords rather than main account credentials, with `assert=user` to guarantee edits never fail silently or occur anonymously.
- **Content Hashing:** Checks existing page content and SHA-256 hashes to prevent redundant revision spamming.

---

## Repository Structure

```
LanguageMaps/
├── glottography_to_commons/        # Core pipeline Python package
│   ├── __init__.py
│   ├── config.py                  # Dataclass configuration & .env loader
│   ├── map_builder.py             # Schema-compliant .map builder & provenance
│   ├── parser.py                  # CLDF & GeoJSON language extractor
│   ├── pipeline.py                # End-to-end pipeline orchestrator
│   ├── state_tracker.py           # SQLite persistence for resume & idempotency
│   ├── utils.py                   # WGS84 validation, coordinate rounding, sanitization
│   ├── wikimedia_client.py        # MediaWiki API client, bot auth, maxlag, rate limiter
│   └── zenodo_client.py           # Zenodo REST API harvester & downloader
├── tests/                         # Test suite
│   ├── fixtures/                  # Sample CLDF, GeoJSON, and Zenodo records
│   ├── test_map_builder.py
│   ├── test_parser.py
│   ├── test_pipeline.py
│   ├── test_state_tracker.py
│   ├── test_utils.py
│   ├── test_wikimedia_client.py
│   └── test_zenodo_client.py
├── .env.example                   # Environment configuration template
├── .gitignore
├── main.py                        # CLI entrypoint
├── pyproject.toml                 # Package metadata
├── README.md                      # Documentation
├── requirements.txt               # Dependencies (standard library by default)
├── run.bat                        # Windows CMD launcher
└── run.ps1                        # Windows PowerShell launcher
```

---

## Prerequisites & Installation

The core pipeline has **zero external dependencies** and runs on Python 3.8+ using only the Python Standard Library (`urllib`, `sqlite3`, `json`, `csv`, `pathlib`, `dataclasses`).

### Clone repository:
```bash
git clone https://github.com/LanguageMaps/GlottographyToCommons.git
cd GlottographyToCommons
```

---

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Configure your credentials and preferences:

```ini
# Zenodo Community
ZENODO_COMMUNITY=glottography
ZENODO_API_BASE=https://zenodo.org/api

# Wikimedia Commons Credentials (create at https://commons.wikimedia.org/wiki/Special:BotPasswords)
COMMONS_USERNAME=YourUsername@YourBotName
COMMONS_BOT_PASSWORD=your_bot_password_here

# API & Etiquette
COMMONS_API_URL=https://commons.wikimedia.org/w/api.php
USER_AGENT=GlottographyCommonsBot/1.0 (https://github.com/LanguageMaps/GlottographyToCommons; contact: bot@glottography.org)

# MediaWiki Categories
CATEGORY_NAME=Map data by language
PARENT_CATEGORY=Map data by object
PARENT_SORTKEY=Language

# Rate Limiting & Etiquette
REQUEST_DELAY=2.0
MAXLAG=5
DRY_RUN=false
```

---

## Usage & CLI Reference

The pipeline can be executed via `python main.py` or the platform launchers (`./run.ps1` on PowerShell, `run.bat` on CMD).

### 1. Show Status & Statistics
Displays discovered datasets, downloaded items, and map upload statistics from the local SQLite database:

```bash
./run.ps1 status
# or: python main.py status
```

### 2. Step 1: Download Glottography Datasets from Zenodo
Harvests community records and downloads archive packages into `data/downloads/`:

```bash
./run.ps1 download
# Limit to first 2 datasets for testing:
./run.ps1 download --limit 2
```

### 3. Step 2: Extract & Generate `.map` Files
Parses extracted datasets, aggregates features per language, rounds coordinates, and saves `.map` files into `data/generated_maps/`:

```bash
./run.ps1 process
```

### 4. Step 3: Upload Maps to Wikimedia Commons
Ensures `Category:Map data by language` exists, checks existing pages, and uploads pending maps with rate limiting:

```bash
# Test upload in dry-run mode (does not modify Commons):
./run.ps1 upload --dry-run

# Live upload respecting rate limits:
./run.ps1 upload --delay 2.0
```

### 5. Step 4: Export for Manual Upload & Review
Generates a complete manual upload package with standalone provenance sheets, CSV/Markdown manifests, and an interactive HTML portal:

```bash
./run.ps1 export
```

### 6. Run End-to-End Pipeline
Executes download, processing, and upload sequentially:

```bash
# End-to-end dry run:
./run.ps1 run --dry-run

# Live end-to-end run:
./run.ps1 run
```

### 7. Preview Generated Maps
Inspect the full JSON payload of any generated map:

```bash
./run.ps1 preview stan1295
./run.ps1 preview English
```

---

## Manual Upload Mode & Portal

If you prefer to review everything offline or upload maps manually through the Wikimedia Commons web interface without configuring bot credentials, the `export` command builds a dedicated package in `data/export/`:

```
data/export/
├── index.html                   # Interactive browser dashboard (1-click copy & direct links)
├── manifest.md                  # Markdown summary table
├── manifest.csv                 # CSV spreadsheet with all URLs and metadata
├── maps/                        # Valid .map JSON files ready for Commons
│   ├── Glottography_-_English_(stan1295).map
│   └── ...
└── descriptions/                # Human-readable provenance & upload sheets
    ├── Glottography_-_English_(stan1295)_provenance.txt
    └── ...
```

### Interactive HTML Portal (`data/export/index.html`)
Open `data/export/index.html` in any web browser to:
- **Search & Filter:** Instantly filter languages by name, Glottocode, dataset, or DOI.
- **1-Click Direct Edit Links:** Click **"Open on Commons"** to open Wikimedia Commons with the target page title (`Data:Glottography - <Language> (<Glottocode>).map`) pre-filled in the editor.
- **1-Click Copy:** Click **"Copy JSON"** to copy the valid `.map` payload directly to your clipboard, ready to paste into Commons.
- **Copy Wikitext Sources:** Copies the formatted provenance wikitext for citation.
- **Category Helper:** One-click copy for the wikitext needed to create `Category:Map data by language` under `Category:Map data by object`.

### Standalone Provenance Sheets (`descriptions/*_provenance.txt`)
Each language map receives its own text file containing:
- Direct 1-click URL to edit/create on Commons
- Target title, namespace (`Data:`), and content model (`JsonConfig`)
- Target category (`Category:Map data by language`)
- Original license terms and link (e.g. CC-BY-4.0)
- Zenodo DOI and resolver URL (`https://doi.org/...`)
- Creators, publication date, Glottolog URL, SIL ISO 639-3 URL
- Complete wikitext provenance block and step-by-step upload instructions.

---

## State Tracking & Resumability

All pipeline operations are tracked in an SQLite database located at `data/pipeline_state.db`:

- **Idempotency:** Re-running the pipeline will not re-download already downloaded datasets or re-upload identical map files.
- **Resumability:** If an upload process is interrupted (e.g. by network timeout or rate limit backoff), running `./run.ps1 upload` resumes exactly where it stopped.
- **Auditing:** Every map record retains its SHA-256 hash, upload timestamp, and Wikimedia URL.

---

## Testing

The test suite includes unit tests for geometry validation, license mapping, CLDF parsing, MediaWiki schema building, and rate limiting, as well as an end-to-end integration test:

```bash
# Run test suite:
python -m unittest discover tests -v
```

---

## License

This project is licensed under the [MIT License](LICENSE). The underlying Glottography geolinguistic datasets are released under their respective open licenses (predominantly **CC-BY-4.0** and **CC0-1.0**) as curated by the Glottography Consortium.

