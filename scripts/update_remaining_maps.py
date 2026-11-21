"""
Batch updater for the 7 remaining Wikimedia Commons language maps
that have not yet been updated with the new zoom and framing coordinates:

1. English (stan1293) -> zoom 1
2. Abau (abau1245) -> zoom 9
3. Mungbam (abar1238) -> zoom 12
4. Abai Sungai (abai1240) -> zoom 11
5. Abaga (abag1245) -> zoom 12
6. Abadi (abad1241) -> zoom 10
7. Aari (aari1239) -> zoom 9

Strictly follows Commons:Bots guidelines:
- bot=1
- maxlag=5
- assert=user
- 3.0s delay between edits
- Emergency shutoff check on bot talk page
- Immediate halt on ANY warning or error
"""

import os
import sys
import time
import json
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from glottography_to_commons.config import Config
from glottography_to_commons.wikimedia_client import WikimediaClient
from glottography_to_commons.state_tracker import StateTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("update_remaining_maps")

TARGETS = [
    {
        "language_name": "English",
        "glottocode": "stan1293",
        "page_title": "Data:Glottography English (stan1293) - glottography dataset deri.map",
        "file_path": "data/generated_maps/glottography_english_stan1293_glottography_dataset_deri.map",
        "expected_zoom": 1,
    },
    {
        "language_name": "Abau",
        "glottocode": "abau1245",
        "page_title": "Data:Glottography Abau (abau1245) - glottography dataset deri.map",
        "file_path": "data/generated_maps/glottography_abau_abau1245_glottography_dataset_deri.map",
        "expected_zoom": 9,
    },
    {
        "language_name": "Mungbam",
        "glottocode": "abar1238",
        "page_title": "Data:Glottography Mungbam (abar1238) - glottography dataset deri.map",
        "file_path": "data/generated_maps/glottography_mungbam_abar1238_glottography_dataset_deri.map",
        "expected_zoom": 12,
    },
    {
        "language_name": "Abai Sungai",
        "glottocode": "abai1240",
        "page_title": "Data:Glottography Abai Sungai (abai1240) - glottography dataset deri.map",
        "file_path": "data/generated_maps/glottography_abai_sungai_abai1240_glottography_dataset_deri.map",
        "expected_zoom": 11,
    },
    {
        "language_name": "Abaga",
        "glottocode": "abag1245",
        "page_title": "Data:Glottography Abaga (abag1245) - glottography dataset deri.map",
        "file_path": "data/generated_maps/glottography_abaga_abag1245_glottography_dataset_deri.map",
        "expected_zoom": 12,
    },
    {
        "language_name": "Abadi",
        "glottocode": "abad1241",
        "page_title": "Data:Glottography Abadi (abad1241) - glottography dataset deri.map",
        "file_path": "data/generated_maps/glottography_abadi_abad1241_glottography_dataset_deri.map",
        "expected_zoom": 10,
    },
    {
        "language_name": "Aari",
        "glottocode": "aari1239",
        "page_title": "Data:Glottography Aari (aari1239) - glottography dataset deri.map",
        "file_path": "data/generated_maps/glottography_aari_aari1239_glottography_dataset_deri.map",
        "expected_zoom": 9,
    },
]

def main():
    cfg = Config.from_env()
    client = WikimediaClient(cfg)
    tracker = StateTracker(cfg.state_db_path)
    
    logger.info("Authenticating bot on Wikimedia Commons...")
    if not client.login():
        logger.error("Bot authentication failed! Halting.")
        sys.exit(1)
        
    bot_name = cfg.commons_username.split('@')[0] if cfg.commons_username else ''
    
    # Emergency shutoff check
    logger.info("Performing emergency shutoff check on User talk:%s...", bot_name)
    exists, talk_content = client.page_exists(f"User talk:{bot_name}")
    if exists and talk_content:
        # Check if talk page has anything other than standard welcome template
        clean_talk = talk_content.replace("{{Welcome|name=" + bot_name + "}}", "").strip()
        # If there are user signatures or messages beyond welcome:
        lines = [line.strip() for line in clean_talk.split("\n") if line.strip() and not line.strip().startswith("-- [[User:Wikimedia Commons Welcome")]
        if lines:
            logger.critical("EMERGENCY SHUTOFF: Found unexpected text on bot talk page: %s", clean_talk)
            sys.exit(1)
    logger.info("Talk page check passed. No emergency messages.")
    
    results = []
    edit_delay = max(cfg.request_delay, 3.0)  # At least 3.0 seconds between edits
    
    for idx, item in enumerate(TARGETS):
        if idx > 0:
            logger.info("Sleeping %.1f seconds to respect Wikimedia Commons rate limits...", edit_delay)
            time.sleep(edit_delay)
            
        logger.info(
            "[%d/%d] Preparing update for %s (%s) from %s...",
            idx + 1, len(TARGETS), item["language_name"], item["glottocode"], item["file_path"]
        )
        
        with open(item["file_path"], "r", encoding="utf-8") as f:
            content = f.read()
            
        payload = json.loads(content)
        actual_zoom = payload.get("zoom")
        actual_lat = payload.get("latitude")
        actual_lon = payload.get("longitude")
        
        logger.info(
            "Payload framing: zoom=%s, lat=%s, lon=%s (expected zoom: %s)",
            actual_zoom, actual_lat, actual_lon, item["expected_zoom"]
        )
        
        summary = f"Adjusting map framing zoom to {actual_zoom} and center coordinates for optimal view"
        
        try:
            res = client.upload_map(
                page_title=item["page_title"],
                content_json=content,
                summary=summary,
                language_name=item["language_name"],
                recreate=True
            )
            
            if res.get("result") != "Success":
                logger.critical("CRITICAL HALT: Result was not 'Success': %s", res)
                sys.exit(2)
                
            logger.info("SUCCESS for %s: revid=%s", item["page_title"], res.get("newrevid"))
            results.append({
                "page_title": item["page_title"],
                "language_name": item["language_name"],
                "glottocode": item["glottocode"],
                "zoom": actual_zoom,
                "lat": actual_lat,
                "lon": actual_lon,
                "newrevid": res.get("newrevid"),
                "url": res.get("url")
            })
            
        except Exception as e:
            logger.critical("CRITICAL HALT: Failed updating %s: %s", item["page_title"], e, exc_info=True)
            sys.exit(2)
            
    print("\n========================================================")
    print("=== ALL 7 REMAINING MAPS UPDATED SUCCESSFULLY ===")
    print("========================================================")
    for r in results:
        print(f"* {r['language_name']} ({r['glottocode']}): zoom={r['zoom']}, lat={r['lat']}, lon={r['lon']}")
        print(f"  URL: {r['url']} (rev {r['newrevid']})")

if __name__ == "__main__":
    main()
