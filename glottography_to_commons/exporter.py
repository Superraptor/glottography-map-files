"""
Manual upload exporter for Glottography language maps.
Generates organized packages with .map files, standalone provenance descriptions,
direct Wikimedia Commons 1-click edit links, CSV/Markdown manifests, and an interactive HTML portal.
"""

import csv
import json
import logging
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from glottography_to_commons.config import Config
from glottography_to_commons.map_builder import MapBuilder
from glottography_to_commons.state_tracker import StateTracker
from glottography_to_commons.utils import slugify

logger = logging.getLogger(__name__)


@dataclass
class ExportItem:
    """Metadata item for manual upload packaging."""
    page_title: str
    commons_edit_url: str
    commons_view_url: str
    talk_page_title: str
    talk_edit_url: str
    talk_wikitext: str
    lang_category: str
    lang_cat_edit_url: str
    lang_cat_wikitext: str
    glottocode: str
    language_name: str
    iso639_3: Optional[str]
    license: str
    doi: str
    dataset_title: str
    creators: str
    publication_date: str
    features_count: int
    map_filename: str
    map_rel_path: str
    description_filename: str
    description_rel_path: str
    sources_wikitext: str
    description_en: str
    json_content: str


class ManualUploadExporter:
    """Exports generated map data and provenance files for manual upload to Commons."""

    def __init__(self, config: Config):
        self.config = config
        self.tracker = StateTracker(config.state_db_path)
        self.export_dir = config.export_dir
        self.maps_dir = self.export_dir / "maps"
        self.desc_dir = self.export_dir / "descriptions"

    def export(self) -> List[ExportItem]:
        """
        Gathers all generated maps and exports standalone packages, manifests, and HTML dashboard.
        """
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.maps_dir.mkdir(parents=True, exist_ok=True)
        self.desc_dir.mkdir(parents=True, exist_ok=True)

        items: List[ExportItem] = []

        # Find all generated map files
        map_files = list(self.config.maps_output_dir.glob("*.map"))
        logger.info("Found %d generated .map files to prepare for manual upload.", len(map_files))

        # Lookup datasets and maps from tracker for rich metadata
        datasets_lookup = {d["record_id"]: d for d in self.tracker.get_all_datasets()}
        maps_lookup = {}
        with self.tracker._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.map_path, m.page_title, m.glottocode, m.language_name, m.dataset_slug, d.doi, d.title as ds_title
                FROM maps m
                LEFT JOIN datasets d ON m.record_id = d.record_id
            """)
            for row in cursor.fetchall():
                if row["map_path"]:
                    maps_lookup[Path(row["map_path"]).name] = dict(row)

        for mf in map_files:
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    map_json = json.load(f)
            except Exception as e:
                logger.error("Failed to read map file %s: %s", mf.name, e)
                continue

            db_info = maps_lookup.get(mf.name, {})

            # Extract fields
            lic = map_json.get("license", "CC-BY-4.0")
            desc_en = map_json.get("description", {}).get("en", "")
            sources = map_json.get("sources", "")
            features = map_json.get("data", {}).get("features", [])

            # Language name and glottocode
            lang_name = db_info.get("language_name")
            glottocode = db_info.get("glottocode")

            if not lang_name and features:
                lang_name = features[0].get("properties", {}).get("title", "")
            if not glottocode and features:
                desc_text = features[0].get("properties", {}).get("description", "")
                if "Glottocode:" in desc_text:
                    glottocode = desc_text.split("Glottocode:")[-1].split("|")[0].strip()

            base_name = mf.stem
            # Page title
            page_title = db_info.get("page_title")
            if not page_title:
                clean_lang = lang_name or base_name
                gc_part = f" ({glottocode})" if glottocode and glottocode != "unknown" else ""
                page_title = f"Data:Glottography - {clean_lang}{gc_part}.map"

            # 1-Click URLs
            quoted_title = urllib.parse.quote(page_title.replace(" ", "_"))
            edit_url = f"https://commons.wikimedia.org/w/index.php?title={quoted_title}&action=edit"
            view_url = f"https://commons.wikimedia.org/wiki/{quoted_title}"

            # Extract DOI and dataset info
            doi = db_info.get("doi") or ""
            if not doi:
                for line in sources.splitlines():
                    if "DOI" in line and "10.5281/zenodo." in line:
                        parts = line.split("10.5281/zenodo.")
                        if len(parts) > 1:
                            doi = "10.5281/zenodo." + parts[1].split()[0].rstrip("]")

            dataset_title = db_info.get("ds_title") or ""
            creators = ""
            pub_date = ""
            for line in sources.splitlines():
                if not dataset_title and "'''Dataset''':" in line:
                    dataset_title = line.replace("* '''Dataset''':", "").strip().strip("''")
                elif "'''Creators / Cartographers''':" in line:
                    creators = line.replace("* '''Creators / Cartographers''':", "").strip()
                elif "'''Publication Date''':" in line:
                    pub_date = line.replace("* '''Publication Date''':", "").strip()

            # Shorten base name to stay well within Windows MAX_PATH (260 chars) limit
            short_base = base_name[:75]
            if len(base_name) > 75:
                import hashlib
                short_base = f"{short_base}_{hashlib.md5(base_name.encode()).hexdigest()[:6]}"

            lang_clean = lang_name or base_name
            lang_cat = MapBuilder.get_language_map_category(lang_clean)
            talk_title = page_title.replace("Data:", "Data talk:")
            talk_edit_url = f"https://commons.wikimedia.org/w/index.php?title={urllib.parse.quote(talk_title.replace(' ', '_'))}&action=edit"
            sort_char = lang_clean[0].upper() if lang_clean else ""
            talk_wikitext = f"[[Category:{self.config.talk_category}|{sort_char}]]"
            lang_cat_edit_url = f"https://commons.wikimedia.org/w/index.php?title=Category:{urllib.parse.quote(lang_cat.replace(' ', '_'))}&action=edit"
            lang_cat_wikitext = f"[[Category:{self.config.parent_linguistic_category}]]"

            # Copy/Save .map file into export directory
            map_filename = f"{short_base}.map"
            target_map_path = self.maps_dir / map_filename
            json_str = json.dumps(map_json, indent=2, ensure_ascii=False)
            with open(target_map_path, "w", encoding="utf-8") as f:
                f.write(json_str)

            # Build standalone human-readable description & instructions file
            desc_filename = f"{short_base}_provenance.txt"
            target_desc_path = self.desc_dir / desc_filename
            desc_text = self._build_standalone_description(
                page_title=page_title,
                edit_url=edit_url,
                view_url=view_url,
                talk_page_title=talk_title,
                talk_edit_url=talk_edit_url,
                talk_wikitext=talk_wikitext,
                lang_category=lang_cat,
                lang_cat_edit_url=lang_cat_edit_url,
                lang_cat_wikitext=lang_cat_wikitext,
                lang_name=lang_clean,
                glottocode=glottocode or "unknown",
                license=lic,
                dataset_title=dataset_title,
                doi=doi,
                creators=creators,
                pub_date=pub_date,
                features_count=len(features),
                sources_wikitext=sources,
                description_en=desc_en,
            )
            with open(target_desc_path, "w", encoding="utf-8") as f:
                f.write(desc_text)

            item = ExportItem(
                page_title=page_title,
                commons_edit_url=edit_url,
                commons_view_url=view_url,
                talk_page_title=talk_title,
                talk_edit_url=talk_edit_url,
                talk_wikitext=talk_wikitext,
                lang_category=lang_cat,
                lang_cat_edit_url=lang_cat_edit_url,
                lang_cat_wikitext=lang_cat_wikitext,
                glottocode=glottocode or "unknown",
                language_name=lang_clean,
                iso639_3=None,
                license=lic,
                doi=doi or "N/A",
                dataset_title=dataset_title or "Glottography Dataset",
                creators=creators or "The Glottography Consortium",
                publication_date=pub_date or "N/A",
                features_count=len(features),
                map_filename=map_filename,
                map_rel_path=f"maps/{map_filename}",
                description_filename=desc_filename,
                description_rel_path=f"descriptions/{desc_filename}",
                sources_wikitext=sources,
                description_en=desc_en,
                json_content=json_str,
            )
            items.append(item)

        # Generate Master Manifests & HTML Portal
        self._write_markdown_manifest(items)
        self._write_csv_manifest(items)
        self._write_html_portal(items)

        logger.info("Manual upload package successfully generated in %s (%d maps ready)", self.export_dir, len(items))
        return items

    def _build_standalone_description(
        self,
        page_title: str,
        edit_url: str,
        view_url: str,
        talk_page_title: str,
        talk_edit_url: str,
        talk_wikitext: str,
        lang_category: str,
        lang_cat_edit_url: str,
        lang_cat_wikitext: str,
        lang_name: str,
        glottocode: str,
        license: str,
        dataset_title: str,
        doi: str,
        creators: str,
        pub_date: str,
        features_count: int,
        sources_wikitext: str,
        description_en: str,
    ) -> str:
        """Constructs human-readable manual upload provenance sheet."""
        lines = [
            "=" * 70,
            " WIKIMEDIA COMMONS MAP DATA - MANUAL UPLOAD SHEET",
            "=" * 70,
            f" Target Commons Page Title : {page_title}",
            f" 1-Click Direct Edit URL   : {edit_url}",
            f" View URL                  : {view_url}",
            f" Discussion / Talk Page    : {talk_page_title}",
            f" Talk Page Edit URL        : {talk_edit_url}",
            f" Map Data Category         : Category:{lang_category}",
            f" Parent Linguistic Cat     : Category:{self.config.parent_linguistic_category}",
            f" Talk Page Category        : Category:{self.config.talk_category}",
            f" Content Model             : JsonConfig (.map)",
            f" License                   : {license}",
            "-" * 70,
            " LANGUAGE & DATASET METADATA",
            "-" * 70,
            f" Language Name             : {lang_name}",
            f" Glottocode                : {glottocode}",
            f" Glottolog Link            : https://glottolog.org/resource/languoid/id/{glottocode}" if glottocode else "",
            f" Dataset Title             : {dataset_title}",
            f" Zenodo DOI                : {doi}",
            f" DOI Resolver Link         : https://doi.org/{doi}" if doi else "",
            f" Creators / Cartographers  : {creators}",
            f" Publication Date          : {pub_date}",
            f" Total Polygon Features    : {features_count}",
            f" English Description       : {description_en}",
            "-" * 70,
            " HOW TO UPLOAD MANUALLY TO WIKIMEDIA COMMONS:",
            "-" * 70,
            " STEP 1: Upload the .map file into the Data: namespace",
            f" 1. Click: {edit_url}",
            " 2. Paste the contents of the corresponding .map file into the editor.",
            f" 3. Summary: Importing Glottography speaker area map for {lang_name} ({glottocode}) from Zenodo {doi}",
            " 4. Click 'Save changes'.",
            f"    (This categorizes the map under Category:{lang_category})",
            "",
            " STEP 2: Categorize the discussion / talk page",
            f" 1. Click: {talk_edit_url}",
            f" 2. Paste the following wikitext into the talk page editor:",
            "",
            f"    {talk_wikitext}",
            "",
            f" 3. Summary: Categorizing map data under Category:{self.config.talk_category}",
            " 4. Click 'Save changes'.",
            f"    (This catalogs the map data in Category:{self.config.talk_category})",
            "",
            f" STEP 3: If Category:{lang_category} does not exist yet on Commons:",
            f" Visit: {lang_cat_edit_url}",
            " and save the following wikitext:",
            "",
            f"    {lang_cat_wikitext}",
            "",
            "=" * 70,
            " COMPLETE SOURCES & PROVENANCE WIKITEXT:",
            "=" * 70,
            sources_wikitext,
            "=" * 70,
        ]
        return "\n".join(lines)

    def _write_markdown_manifest(self, items: List[ExportItem]) -> None:
        """Write master Markdown manifest table."""
        manifest_path = self.export_dir / "manifest.md"
        lines = [
            "# Glottography Manual Upload Manifest",
            "",
            f"Total language maps prepared: **{len(items)}**  ",
            f"- **Map Data Categories:** `Category:<Language>-language linguistic maps` under **`Category:{self.config.parent_linguistic_category}`**  ",
            f"- **Map Data Talk Catalog:** **`Category:{self.config.talk_category}`**  ",
            "",
            "### 3-Step Instructions for Manual Upload",
            "1. **Upload Map Data**: Click **[Upload .map]**, paste the `.map` JSON payload into the Commons editor, and save.",
            f"2. **Categorize Discussion Page**: Click **[Edit Talk Page]**, paste `[[Category:{self.config.talk_category}|<SortKey>]]`, and save.",
            f"3. **Ensure Category**: If `Category:<Language>-language linguistic maps` does not exist yet, click **[Create Category]** and save `[[Category:{self.config.parent_linguistic_category}]]`.",
            "",
            "---",
            "",
            "| Language | Glottocode | Target .map Title | Category | Talk Page | Dataset & DOI | Actions |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for item in sorted(items, key=lambda x: x.language_name):
            doi_link = f"[{item.doi}](https://doi.org/{item.doi})" if item.doi != "N/A" else "N/A"
            glotto_link = (
                f"[{item.glottocode}](https://glottolog.org/resource/languoid/id/{item.glottocode})"
                if item.glottocode != "unknown" else "unknown"
            )
            cat_link = f"[{item.lang_category}](https://commons.wikimedia.org/wiki/Category:{urllib.parse.quote(item.lang_category.replace(' ', '_'))})"
            row = (
                f"| **{item.language_name}** | {glotto_link} | `{item.page_title}` "
                f"| {cat_link} | [Talk]({item.talk_edit_url}) "
                f"| {item.dataset_title[:35]} ({doi_link}) "
                f"| [Upload .map]({item.commons_edit_url}) &bull; [Edit Talk]({item.talk_edit_url}) &bull; [Map File]({item.map_rel_path}) |"
            )
            lines.append(row)

        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info("Saved Markdown manifest: %s", manifest_path.name)

    def _write_csv_manifest(self, items: List[ExportItem]) -> None:
        """Write master CSV spreadsheet."""
        csv_path = self.export_dir / "manifest.csv"
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Language_Name",
                "Glottocode",
                "Commons_Page_Title",
                "Commons_Edit_URL",
                "Talk_Page_Title",
                "Talk_Edit_URL",
                "Talk_Wikitext",
                "Language_Category",
                "Language_Category_Edit_URL",
                "Language_Category_Wikitext",
                "Zenodo_DOI",
                "Dataset_Title",
                "Creators",
                "Publication_Date",
                "License",
                "Features_Count",
                "Map_Relative_Path",
                "Description_Relative_Path",
            ])
            for item in sorted(items, key=lambda x: x.language_name):
                writer.writerow([
                    item.language_name,
                    item.glottocode,
                    item.page_title,
                    item.commons_edit_url,
                    item.talk_page_title,
                    item.talk_edit_url,
                    item.talk_wikitext,
                    item.lang_category,
                    item.lang_cat_edit_url,
                    item.lang_cat_wikitext,
                    item.doi,
                    item.dataset_title,
                    item.creators,
                    item.publication_date,
                    item.license,
                    item.features_count,
                    item.map_rel_path,
                    item.description_rel_path,
                ])
        logger.info("Saved CSV manifest: %s", csv_path.name)

    def _write_html_portal(self, items: List[ExportItem]) -> None:
        """Generate interactive single-file HTML manual upload portal."""
        html_path = self.export_dir / "index.html"

        # Prepare JSON metadata payload embedded for quick filtering and clipboard copying
        embedded_items = []
        for it in items:
            embedded_items.append({
                "page_title": it.page_title,
                "edit_url": it.commons_edit_url,
                "view_url": it.commons_view_url,
                "talk_title": it.talk_page_title,
                "talk_url": it.talk_edit_url,
                "talk_wiki": it.talk_wikitext,
                "lang_cat": it.lang_category,
                "lang_cat_url": it.lang_cat_edit_url,
                "lang_cat_wiki": it.lang_cat_wikitext,
                "lang": it.language_name,
                "glottocode": it.glottocode,
                "license": it.license,
                "doi": it.doi,
                "dataset": it.dataset_title,
                "creators": it.creators,
                "features": it.features_count,
                "map_rel": it.map_rel_path,
                "desc_rel": it.description_rel_path,
                "sources": it.sources_wikitext,
            })

        items_json = json.dumps(embedded_items, ensure_ascii=False)

        cat_parent = self.config.parent_linguistic_category
        cat_talk = self.config.talk_category

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Glottography &rarr; Wikimedia Commons Manual Upload Portal</title>
  <style>
    :root {{
      --primary: #2a4b8d;
      --primary-hover: #1b356d;
      --secondary: #5a6268;
      --success: #198754;
      --bg: #f8f9fa;
      --card-bg: #ffffff;
      --border: #dee2e6;
      --text: #212529;
      --text-muted: #6c757d;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    header {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.04);
    }}
    h1 {{ color: var(--primary); font-size: 26px; margin-bottom: 8px; }}
    p.subtitle {{ color: var(--text-muted); font-size: 15px; margin-bottom: 16px; }}
    .alert-box {{
      background: #eef4fc;
      border-left: 4px solid var(--primary);
      padding: 14px 18px;
      border-radius: 4px;
      font-size: 14px;
      margin-top: 12px;
    }}
    .toolbar {{
      display: flex;
      gap: 12px;
      align-items: center;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }}
    .search-input {{
      flex: 1;
      min-width: 280px;
      padding: 10px 14px;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 15px;
    }}
    .badge {{
      display: inline-block;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 4px;
      background: #e9ecef;
      color: #495057;
    }}
    .badge-license {{ background: #d1e7dd; color: #0f5132; }}
    .badge-cat {{ background: #cfe2ff; color: #084298; }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 5px 10px;
      border-radius: 4px;
      font-size: 12px;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.15s ease-in-out;
      white-space: nowrap;
    }}
    .btn-primary {{ background: var(--primary); color: #fff; }}
    .btn-primary:hover {{ background: var(--primary-hover); }}
    .btn-outline {{ background: #fff; border-color: var(--border); color: var(--text); }}
    .btn-outline:hover {{ background: #f1f3f5; }}
    .btn-talk {{ background: #f8f9fa; border-color: #6c757d; color: #495057; }}
    .btn-talk:hover {{ background: #e2e6ea; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 2px 4px rgba(0,0,0,0.04);
    }}
    th, td {{
      padding: 10px 12px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      font-size: 13px;
    }}
    th {{
      background: #f1f3f5;
      font-weight: 600;
      color: #495057;
    }}
    tr:hover {{ background-color: #fbfcfd; }}
    .btn-group {{ display: flex; gap: 4px; flex-wrap: wrap; }}
    .pagination-bar {{
      display: flex;
      justify-content: center;
      margin-top: 20px;
    }}
    .toast {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #333;
      color: #fff;
      padding: 12px 20px;
      border-radius: 6px;
      font-size: 14px;
      opacity: 0;
      transition: opacity 0.25s ease;
      z-index: 1000;
      pointer-events: none;
    }}
    .toast.show {{ opacity: 1; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Glottography &rarr; Wikimedia Commons Manual Upload Portal</h1>
      <p class="subtitle">Browse, inspect, and manually upload speaker-area map data pages into Commons <code>Data:*.map</code>.</p>
      
      <div class="alert-box">
        <strong>Wikimedia Commons Categorization Architecture:</strong>
        <ul style="margin-left: 20px; margin-top: 6px;">
          <li><strong>Map Data Files (.map)</strong> belong in individual language categories under <a href="https://commons.wikimedia.org/wiki/Category:{urllib.parse.quote(cat_parent)}" target="_blank">Category:{cat_parent}</a> (e.g. <code>Category:Northern Ju-language linguistic maps</code>).</li>
          <li><strong>Discussion Pages (Data talk:)</strong> are cataloged under <a href="https://commons.wikimedia.org/wiki/Category:{urllib.parse.quote(cat_talk)}" target="_blank">Category:{cat_talk}</a>.</li>
        </ul>
      </div>
    </header>

    <div class="toolbar">
      <input type="text" id="searchInput" class="search-input" placeholder="Search by language name, glottocode, category, dataset, or DOI..." oninput="filterTable()">
      <span class="badge" id="countBadge">Loading...</span>
    </div>

    <table>
      <thead>
        <tr>
          <th>Language</th>
          <th>Glottocode</th>
          <th>Page Title</th>
          <th>Linguistic Map Category</th>
          <th>Dataset / DOI</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="tableBody"></tbody>
    </table>

    <div class="pagination-bar">
      <button id="loadMoreBtn" class="btn btn-outline" style="display:none; padding: 10px 24px; font-size: 14px;" onclick="loadMore()">Load More</button>
    </div>
  </div>

  <div id="toast" class="toast">Copied to clipboard!</div>

  <script>
    const items = {items_json};
    let currentList = items;
    let displayedCount = 100;
    const PAGE_SIZE = 100;

    function showToast(msg) {{
      const t = document.getElementById("toast");
      t.textContent = msg;
      t.classList.add("show");
      setTimeout(() => t.classList.remove("show"), 2500);
    }}

    function copyToClipboard(text, successMsg) {{
      navigator.clipboard.writeText(text).then(() => {{
        showToast(successMsg);
      }}).catch(err => {{
        alert("Failed to copy: " + err);
      }});
    }}

    async function copyMapJson(index) {{
      const it = currentList[index];
      try {{
        const res = await fetch(it.map_rel);
        if (!res.ok) throw new Error("Status " + res.status);
        const text = await res.text();
        copyToClipboard(text, `Copied JSON for ${{it.lang}} (${{it.glottocode}}) to clipboard!`);
      }} catch (err) {{
        window.open(it.map_rel, '_blank');
        showToast(`Opened ${{it.lang}} .map in new tab`);
      }}
    }}

    function copyTalkWikitext(index) {{
      const it = currentList[index];
      copyToClipboard(it.talk_wiki, `Copied talk page category wikitext for ${{it.lang}}!`);
    }}

    function copySourcesWikitext(index) {{
      const it = currentList[index];
      copyToClipboard(it.sources, `Copied sources citation wikitext for ${{it.lang}} to clipboard!`);
    }}

    function renderRows(start, count) {{
      const tbody = document.getElementById("tableBody");
      const end = Math.min(start + count, currentList.length);
      const fragment = document.createDocumentFragment();

      for (let idx = start; idx < end; idx++) {{
        const it = currentList[idx];
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${{it.lang}}</strong></td>
          <td>
            ${{it.glottocode !== "unknown" ? `<a href="https://glottolog.org/resource/languoid/id/${{it.glottocode}}" target="_blank">${{it.glottocode}}</a>` : '<span style="color:#888;">unknown</span>'}}
          </td>
          <td><code style="font-size:11px;">${{it.page_title}}</code></td>
          <td>
            <a href="https://commons.wikimedia.org/wiki/Category:${{encodeURIComponent(it.lang_cat.replace(/ /g, '_'))}}" target="_blank" class="badge badge-cat">${{it.lang_cat}}</a>
          </td>
          <td>
            ${{it.dataset}}<br>
            <small>${{it.doi !== "N/A" ? `<a href="https://doi.org/${{it.doi}}" target="_blank">${{it.doi}}</a>` : ''}}</small>
          </td>
          <td>
            <div class="btn-group">
              <a href="${{it.edit_url}}" target="_blank" class="btn btn-primary" title="Open .map editor on Commons">Upload .map</a>
              <button class="btn btn-outline" onclick="copyMapJson(${{idx}})" title="Copy .map JSON to clipboard">Copy JSON</button>
              <a href="${{it.talk_url}}" target="_blank" class="btn btn-talk" title="Open talk page editor to add Category:Map data of languages">Edit Talk</a>
              <button class="btn btn-outline" onclick="copyTalkWikitext(${{idx}})" title="Copy talk page wikitext">Copy Talk</button>
              <a href="${{it.map_rel}}" target="_blank" class="btn btn-outline" title="View / download raw .map file">.map</a>
              <a href="${{it.desc_rel}}" target="_blank" class="btn btn-outline" title="Open provenance sheet">Provenance</a>
            </div>
          </td>
        `;
        fragment.appendChild(tr);
      }}
      tbody.appendChild(fragment);

      const loadMoreBtn = document.getElementById("loadMoreBtn");
      if (end < currentList.length) {{
        loadMoreBtn.style.display = "inline-block";
        loadMoreBtn.textContent = `Load More (${{currentList.length - end}} remaining)`;
      }} else {{
        loadMoreBtn.style.display = "none";
      }}
    }}

    function renderTable(list) {{
      currentList = list;
      displayedCount = PAGE_SIZE;
      const tbody = document.getElementById("tableBody");
      tbody.innerHTML = "";
      document.getElementById("countBadge").textContent = `${{list.length}} maps`;

      if (list.length === 0) {{
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #6c757d; padding: 24px;">No matching maps found.</td></tr>`;
        document.getElementById("loadMoreBtn").style.display = "none";
        return;
      }}

      renderRows(0, displayedCount);
    }}

    function loadMore() {{
      const start = displayedCount;
      displayedCount += PAGE_SIZE;
      renderRows(start, PAGE_SIZE);
    }}

    function filterTable() {{
      const q = document.getElementById("searchInput").value.toLowerCase().trim();
      if (!q) {{
        renderTable(items);
        return;
      }}
      const filtered = items.filter(it => 
        it.lang.toLowerCase().includes(q) ||
        it.glottocode.toLowerCase().includes(q) ||
        it.page_title.toLowerCase().includes(q) ||
        it.lang_cat.toLowerCase().includes(q) ||
        it.dataset.toLowerCase().includes(q) ||
        it.doi.toLowerCase().includes(q)
      );
      renderTable(filtered);
    }}

    // Initial render
    renderTable(items);
  </script>
</body>
</html>
"""
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info("Saved Interactive HTML Portal: %s", html_path.name)

