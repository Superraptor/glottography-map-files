"""
Safe update script for Wikimedia Commons map pages:
1. Data:Glottography Northern Ju (vase1234) - glottography dataset deri.map
2. Data:Glottography Standard Arabic (stan1318) - glottography dataset deri.map

Strictly follows Commons:Bots guidelines:
- bot=1
- maxlag=5
- assert=user
- delay between edits
- immediate halt on any warning or error
"""

import os
import sys
import time
import json
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from glottography_to_commons.config import Config
from glottography_to_commons.wikimedia_client import WikimediaClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("update_maps")

def main():
    cfg = Config.from_env()
    client = WikimediaClient(cfg)
    
    logger.info("Authenticating bot on Wikimedia Commons...")
    if not client.login():
        logger.error("Bot authentication failed! Halting.")
        sys.exit(1)
    
    tasks = [
        {
            "name": "Northern Ju",
            "page_title": "Data:Glottography Northern Ju (vase1234) - glottography dataset deri.map",
            "file_path": "data/generated_maps/glottography_northern_ju_vase1234_glottography_dataset_deri.map",
            "summary": "Updating map data with framed zoom and center coordinates, and normalized sort key",
            "language_name": "Northern Ju"
        },
        {
            "name": "Standard Arabic",
            "page_title": "Data:Glottography Standard Arabic (stan1318) - glottography dataset deri.map",
            "file_path": "data/generated_maps/glottography_standard_arabic_stan1318_glottography_dataset_deri.map",
            "summary": "Updating map data with framed zoom and center coordinates",
            "language_name": "Standard Arabic"
        }
    ]
    
    results = []
    
    for idx, item in enumerate(tasks):
        if idx > 0:
            logger.info("Sleeping %.1f seconds between edits to respect rate limits...", cfg.request_delay)
            time.sleep(cfg.request_delay)
            
        logger.info("Reading payload for %s from %s...", item["name"], item["file_path"])
        with open(item["file_path"], "r", encoding="utf-8") as f:
            content = f.read()
            
        logger.info("Uploading update for %s (length: %d bytes)...", item["page_title"], len(content))
        try:
            res = client.upload_map(
                page_title=item["page_title"],
                content_json=content,
                summary=item["summary"],
                language_name=item["language_name"],
                recreate=True
            )
            logger.info("SUCCESS for %s: %s", item["name"], res)
            results.append(res)
        except Exception as e:
            logger.critical("CRITICAL HALT: Error updating %s: %s", item["page_title"], e, exc_info=True)
            sys.exit(2)
            
    print("\n=== ALL UPDATES COMPLETED SUCCESSFULLY ===")
    for r in results:
        print(f"* {r['page_title']}: revid={r.get('newrevid')}, url={r.get('url')}")

if __name__ == "__main__":
    main()
