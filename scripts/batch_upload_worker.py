"""
Continuous batch upload worker for Glottography language maps to Wikimedia Commons.

Strictly adheres to Commons:Bots policies:
- Enforces user-configured rate limit delay (6.0 seconds per request/edit)
- Identifies as bot (bot=1, assert=user)
- Respects server load (maxlag=5 with exponential backoff)
- Periodically checks bot user talk page for emergency shutoff messages
- Halts immediately on any warnings, abuse filters, or validation errors
- Fully resumable and tracks state in SQLite database
"""

import os
import sys
import time
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from glottography_to_commons.config import Config
from glottography_to_commons.wikimedia_client import WikimediaClient
from glottography_to_commons.state_tracker import StateTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/batch_upload.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("batch_upload_worker")

PROGRESS_FILE = Path("data/upload_progress.json")

def check_emergency_shutoff(client: WikimediaClient, bot_name: str) -> None:
    """Checks if there are any messages on the bot's talk page requesting shutoff."""
    logger.info("Performing emergency shutoff check on User talk:%s...", bot_name)
    exists, talk_content = client.page_exists(f"User talk:{bot_name}")
    if exists and talk_content:
        clean_talk = talk_content.replace(f"{{{{Welcome|name={bot_name}}}}}", "").strip()
        lines = [
            line.strip() for line in clean_talk.split("\n")
            if line.strip() and not line.strip().startswith("-- [[User:Wikimedia Commons Welcome")
        ]
        if lines:
            logger.critical("EMERGENCY SHUTOFF TRIGGERED! Found message on User talk:%s: %s", bot_name, clean_talk)
            raise RuntimeError(f"EMERGENCY SHUTOFF TRIGGERED via talk page message on User talk:{bot_name}")
    logger.info("Emergency shutoff check passed.")

def save_progress(current_idx: int, total_count: int, current_title: str, successes: int, errors: int, start_time: float) -> None:
    elapsed = time.time() - start_time
    rate = (successes / elapsed) * 60.0 if elapsed > 0 else 0.0
    progress_data = {
        "current_index": current_idx,
        "total_count": total_count,
        "current_title": current_title,
        "successes": successes,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
        "edits_per_minute": round(rate, 2),
        "last_updated": datetime.now(timezone.utc).isoformat()
    }
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress_data, f, indent=2)
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="Glottography Commons batch upload worker")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of maps to upload in this run")
    parser.add_argument("--delay", type=float, default=6.0, help="Delay in seconds between requests (default: 6.0)")
    args = parser.parse_args()

    cfg = Config.from_env()
    # Enforce at least 6.0s delay as requested
    cfg.request_delay = max(args.delay, 6.0)

    logger.info("Initializing Batch Upload Worker with request delay: %.1f seconds", cfg.request_delay)
    client = WikimediaClient(cfg)
    tracker = StateTracker(cfg.state_db_path)

    logger.info("Authenticating bot as %s...", cfg.commons_username)
    if not client.login():
        logger.critical("Bot authentication failed! Exiting.")
        sys.exit(1)

    bot_name = cfg.commons_username.split("@")[0] if cfg.commons_username else ""
    check_emergency_shutoff(client, bot_name)

    # Ensure base parent category and bot user page
    client.ensure_category()
    client.ensure_bot_userpage()

    # Get pending uploads
    pending_maps = tracker.get_pending_uploads()
    logger.info("Found %d pending records in database.", len(pending_maps))

    # Deduplicate by unique page title
    seen_titles = set()
    unique_pending = []
    for m in pending_maps:
        title = m["page_title"]
        if title not in seen_titles:
            seen_titles.add(title)
            unique_pending.append(m)

    logger.info("Found %d distinct pending map page titles to upload.", len(unique_pending))

    if args.limit and args.limit > 0:
        logger.info("Limiting upload run to first %d maps as requested.", args.limit)
        unique_pending = unique_pending[:args.limit]

    total_to_process = len(unique_pending)
    logger.info("=== STARTING BATCH UPLOAD: %d MAPS (delay: %.1fs per request) ===", total_to_process, cfg.request_delay)

    success_count = 0
    error_count = 0
    start_time = time.time()

    for idx, item in enumerate(unique_pending, 1):
        map_id = item["map_id"]
        page_title = item["page_title"]
        map_path = Path(item["map_path"])
        glottocode = item["glottocode"]
        language_name = item["language_name"]
        record_id = item["record_id"]

        # Emergency shutoff check every 25 maps
        if idx % 25 == 0:
            check_emergency_shutoff(client, bot_name)

        logger.info("[%d/%d] Processing %s (%s, %s)...", idx, total_to_process, language_name, glottocode, page_title)

        if not map_path.exists():
            logger.error("Map file %s does not exist on disk! Skipping.", map_path)
            tracker.record_map_upload_failed(map_id, f"File not found: {map_path}")
            error_count += 1
            continue

        with open(map_path, "r", encoding="utf-8") as f:
            content_json = f.read()

        summary = f"Importing Glottography speaker area map for {language_name} ({glottocode}) from Zenodo record {record_id}"

        try:
            res = client.upload_map(
                page_title=page_title,
                content_json=content_json,
                summary=summary,
                language_name=language_name,
                recreate=False
            )

            result_status = res.get("result")
            if result_status in ("Success", "Skipped"):
                url = res.get("url", f"https://commons.wikimedia.org/wiki/{page_title}")
                tracker.record_map_uploaded(map_id, url)
                success_count += 1
                logger.info(
                    "[%d/%d] SUCCESS for %s (rev %s): %s",
                    idx, total_to_process, language_name, res.get("newrevid", "existing"), url
                )
            else:
                raise RuntimeError(f"Unexpected upload result: {res}")

        except Exception as e:
            logger.critical(
                "CRITICAL ERROR on [%d/%d] %s: %s. HALTING IMMEDIATELY.",
                idx, total_to_process, page_title, e, exc_info=True
            )
            tracker.record_map_upload_failed(map_id, str(e))
            save_progress(idx, total_to_process, page_title, success_count, error_count + 1, start_time)
            sys.exit(2)

        save_progress(idx, total_to_process, page_title, success_count, error_count, start_time)

    elapsed_total = time.time() - start_time
    logger.info(
        "=== BATCH UPLOAD FINISHED: %d SUCCESSFUL, %d ERRORS in %.1f seconds ===",
        success_count, error_count, elapsed_total
    )

if __name__ == "__main__":
    main()
