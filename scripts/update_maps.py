"""
Safe update script to update Wikimedia Commons map for Standard Arabic:
Data:Glottography Standard Arabic (stan1318) - glottography dataset deri.map

Strictly follows Commons:Bots guidelines:
- bot=1
- maxlag=5
- assert=user
- immediate halt on any warning or error
"""

import os
import sys
import json
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from glottography_to_commons.config import Config
from glottography_to_commons.wikimedia_client import WikimediaClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("update_arabic")

def main():
    cfg = Config.from_env()
    client = WikimediaClient(cfg)
    
    file_path = "data/generated_maps/glottography_standard_arabic_stan1318_glottography_dataset_deri.map"
    page_title = "Data:Glottography Standard Arabic (stan1318) - glottography dataset deri.map"
    summary = "Clean up duplicated compression provenance documentation section"
    
    logger.info("Reading payload from %s...", file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    logger.info("Authenticating bot on Wikimedia Commons...")
    if not client.login():
        logger.error("Bot authentication failed! Halting.")
        sys.exit(1)
        
    logger.info("Uploading update for %s (length: %d bytes)...", page_title, len(content))
    try:
        res = client.upload_map(
            page_title=page_title,
            content_json=content,
            summary=summary,
            language_name="Standard Arabic",
            recreate=True
        )
        logger.info("SUCCESS: %s", res)
        print(f"\n=== UPDATE COMPLETED SUCCESSFULLY ===")
        print(f"* {res['page_title']}: revid={res.get('newrevid')}, url={res.get('url')}")
    except Exception as e:
        logger.critical("CRITICAL HALT: Error updating %s: %s", page_title, e, exc_info=True)
        sys.exit(2)

if __name__ == "__main__":
    main()
