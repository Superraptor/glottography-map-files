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
from glottography_to_commons.state_tracker import StateTracker
from glottography_to_commons.utils import slugify

logger = logging.getLogger(__name__)


@dataclass
class ExportItem:
    """Metadata item for manual upload packaging."""
    page_title: str
    commons_edit_url: str
    commons_view_url: str
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
                lang_name=lang_name,
                glottocode=glottocode,
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
                glottocode=glottocode or "unknown",
                language_name=lang_name or base_name,
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
            f" Target Category           : Category:{self.config.category_name}",
            f" Parent Category           : Category:{self.config.parent_category}",
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
            " 1. Ensure 'Category:Map data by language' exists on Commons.",
            "    If it does not exist yet, visit:",
            f"    https://commons.wikimedia.org/w/index.php?title=Category:{urllib.parse.quote(self.config.category_name)}&action=edit",
            "    and save the following wikitext:",
            "",
            "    {{CategoryMapData}}",
            "    Map data files (.map) in the Data: namespace representing geographical speaker areas.",
            "",
            f"    [[Category:{self.config.parent_category}|{self.config.parent_sortkey}]]",
            "",
            " 2. Click the 1-Click Direct Edit URL above (or navigate to the target title on Commons).",
            " 3. Paste the contents of the corresponding .map file into the editor.",
            " 4. In the 'Summary' box, enter:",
            f"    Importing Glottography speaker area map for {lang_name} ({glottocode}) from Zenodo {doi}",
            " 5. Click 'Save changes'.",
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
            f"Target Category: **`Category:{self.config.category_name}`** (subcategorized under `Category:{self.config.parent_category}`)  ",
            "",
            "### Instructions for Manual Upload",
            "1. First, verify **[Category:Map data by language](https://commons.wikimedia.org/wiki/Category:Map_data_by_language)** exists on Commons.",
            "2. Click the **[Create/Edit on Commons]** link in the table below for any language.",
            "3. Paste the `.map` JSON payload into the editor and save.",
            "",
            "---",
            "",
            "| Language | Glottocode | Target Page Title | Dataset & DOI | License | Actions |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for item in sorted(items, key=lambda x: x.language_name):
            doi_link = f"[{item.doi}](https://doi.org/{item.doi})" if item.doi != "N/A" else "N/A"
            glotto_link = (
                f"[{item.glottocode}](https://glottolog.org/resource/languoid/id/{item.glottocode})"
                if item.glottocode != "unknown" else "unknown"
            )
            row = (
                f"| **{item.language_name}** | {glotto_link} | `{item.page_title}` "
                f"| {item.dataset_title[:40]} ({doi_link}) | `{item.license}` "
                f"| [Create/Edit on Commons]({item.commons_edit_url}) &bull; [Map File]({item.map_rel_path}) |"
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

        # Prepare JSON payload embedded for quick filtering and clipboard copying
        embedded_items = []
        for it in items:
            embedded_items.append({
                "page_title": it.page_title,
                "edit_url": it.commons_edit_url,
                "view_url": it.commons_view_url,
                "lang": it.language_name,
                "glottocode": it.glottocode,
                "license": it.license,
                "doi": it.doi,
                "dataset": it.dataset_title,
                "creators": it.creators,
                "features": it.features_count,
                "map_rel": it.map_rel_path,
                "desc_rel": it.description_rel_path,
                "json_str": it.json_content,
                "sources": it.sources_wikitext,
            })

        items_json = json.dumps(embedded_items, ensure_ascii=False)

        cat_name = self.config.category_name
        parent_cat = self.config.parent_category
        parent_key = self.config.parent_sortkey

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
    .container {{ max-width: 1300px; margin: 0 auto; }}
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
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 5px;
      font-size: 13px;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.15s ease-in-out;
    }}
    .btn-primary {{ background: var(--primary); color: #fff; }}
    .btn-primary:hover {{ background: var(--primary-hover); }}
    .btn-outline {{ background: #fff; border-color: var(--border); color: var(--text); }}
    .btn-outline:hover {{ background: #f1f3f5; }}
    .btn-success {{ background: var(--success); color: #fff; }}
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
      padding: 12px 14px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      font-size: 14px;
    }}
    th {{
      background: #f1f3f5;
      font-weight: 600;
      color: #495057;
    }}
    tr:hover {{ background-color: #fbfcfd; }}
    .btn-group {{ display: flex; gap: 6px; }}
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
        <strong>Target Subcategory:</strong> 
        <a href="https://commons.wikimedia.org/wiki/Category:{urllib.parse.quote(cat_name)}" target="_blank">Category:{cat_name}</a> 
        (subcategorized under <a href="https://commons.wikimedia.org/wiki/Category:{urllib.parse.quote(parent_cat)}" target="_blank">Category:{parent_cat}</a>).
        <br><br>
        If the category does not exist yet on Commons:
        <a href="https://commons.wikimedia.org/w/index.php?title=Category:{urllib.parse.quote(cat_name)}&action=edit" target="_blank" class="btn btn-primary" style="margin-left: 8px; font-size: 12px;">Create Category on Commons</a>
        <button class="btn btn-outline" style="margin-left: 6px; font-size: 12px;" onclick="copyCategoryWikitext()">Copy Category Wikitext</button>
      </div>
    </header>

    <div class="toolbar">
      <input type="text" id="searchInput" class="search-input" placeholder="Search by language name, glottocode, dataset, or DOI..." oninput="filterTable()">
      <span class="badge" id="countBadge">Loading...</span>
    </div>

    <table>
      <thead>
        <tr>
          <th>Language</th>
          <th>Glottocode</th>
          <th>Page Title</th>
          <th>Dataset / DOI</th>
          <th>License</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>

  <div id="toast" class="toast">Copied to clipboard!</div>

  <script>
    const items = {items_json};

    function showToast(msg) {{
      const t = document.getElementById("toast");
      t.textContent = msg;
      t.classList.add("show");
      setTimeout(() => t.classList.remove("show"), 2200);
    }}

    function copyToClipboard(text, successMsg) {{
      navigator.clipboard.writeText(text).then(() => {{
        showToast(successMsg);
      }}).catch(err => {{
        alert("Failed to copy: " + err);
      }});
    }}

    function copyCategoryWikitext() {{
      const wiki = "{{{{CategoryMapData}}}}\\nMap data files (.map) in the Data: namespace representing geographical speaker areas.\\n\\n[[Category:{parent_cat}|{parent_key}]]";
      copyToClipboard(wiki, "Category wikitext copied to clipboard!");
    }}

    function copyMapJson(index) {{
      const it = items[index];
      copyToClipboard(it.json_str, `Copied JSON for ${{it.lang}} (${{it.glottocode}}) to clipboard!`);
    }}

    function copySourcesWikitext(index) {{
      const it = items[index];
      copyToClipboard(it.sources, `Copied sources wikitext for ${{it.lang}} to clipboard!`);
    }}

    function renderTable(list) {{
      const tbody = document.getElementById("tableBody");
      tbody.innerHTML = "";
      document.getElementById("countBadge").textContent = `${{list.length}} maps`;

      if (list.length === 0) {{
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #6c757d; padding: 24px;">No matching maps found.</td></tr>`;
        return;
      }}

      list.forEach((it, idx) => {{
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${{it.lang}}</strong></td>
          <td>
            ${{it.glottocode !== "unknown" ? `<a href="https://glottolog.org/resource/languoid/id/${{it.glottocode}}" target="_blank">${{it.glottocode}}</a>` : '<span style="color:#888;">unknown</span>'}}
          </td>
          <td><code style="font-size:12px;">${{it.page_title}}</code></td>
          <td>
            ${{it.dataset}}<br>
            <small>${{it.doi !== "N/A" ? `<a href="https://doi.org/${{it.doi}}" target="_blank">${{it.doi}}</a>` : ''}}</small>
          </td>
          <td><span class="badge badge-license">${{it.license}}</span></td>
          <td>
            <div class="btn-group">
              <a href="${{it.edit_url}}" target="_blank" class="btn btn-primary" title="Open editor on Wikimedia Commons">Open on Commons</a>
              <button class="btn btn-outline" onclick="copyMapJson(${{idx}})" title="Copy valid .map JSON to clipboard">Copy JSON</button>
              <button class="btn btn-outline" onclick="copySourcesWikitext(${{idx}})" title="Copy sources citation wikitext">Copy Sources</button>
            </div>
          </td>
        `;
        tbody.appendChild(tr);
      }});
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

