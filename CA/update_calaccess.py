#!/usr/bin/env python3
"""
Automated CalAccess data download and full rebuild pipeline.
Downloads the CalAccess raw data export, rebuilds the database from scratch,
and performs an atomic swap to avoid downtime for the Flask app.
"""

import os
import sys
import json
import time
import shutil
import logging
import argparse
import tempfile
import zipfile
import subprocess
from datetime import datetime
from logging.handlers import RotatingFileHandler

try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CALACCESS_URL = "https://campaignfinance.cdn.sos.ca.gov/dbwebexport.zip"
METADATA_FILE = os.path.join(SCRIPT_DIR, ".update_metadata.json")
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
DB_FILE = os.path.join(SCRIPT_DIR, "ca_contributions.db")
DB_NEW_FILE = os.path.join(SCRIPT_DIR, "ca_contributions.db.new")
DATA_DIR = os.path.join(SCRIPT_DIR, "CalAccess", "DATA")
ZIP_PATH = os.path.join(SCRIPT_DIR, "dbwebexport.zip")


def setup_logging():
    """Configure rotating file and console logging."""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("update_calaccess")
    logger.setLevel(logging.INFO)

    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "update_calaccess.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(ch)

    return logger


def load_metadata():
    """Load stored metadata about previous downloads."""
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "r") as f:
            return json.load(f)
    return {}


def save_metadata(metadata):
    """Save metadata about downloads."""
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)


def check_for_updates(metadata, logger):
    """Check if the CalAccess export has changed."""
    stored = metadata.get(CALACCESS_URL, {})
    try:
        resp = requests.head(CALACCESS_URL, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"HEAD request failed: {e}")
        return False, {}

    remote_info = {
        "content_length": resp.headers.get("Content-Length"),
        "last_modified": resp.headers.get("Last-Modified"),
        "etag": resp.headers.get("ETag"),
    }

    changed = (
        remote_info["content_length"] != stored.get("content_length")
        or remote_info["last_modified"] != stored.get("last_modified")
        or remote_info["etag"] != stored.get("etag")
    )

    if changed:
        logger.info("Change detected for CalAccess export")
        logger.info(f"  Remote: size={remote_info['content_length']}, modified={remote_info['last_modified']}")
        logger.info(f"  Stored: size={stored.get('content_length')}, modified={stored.get('last_modified')}")
    else:
        logger.info("No change detected for CalAccess export")

    return changed, remote_info


def download_file(logger, max_retries=5, retry_delay=10):
    """Stream-download the CalAccess ZIP with retry and resume support."""
    dest_dir = SCRIPT_DIR
    tmp_path = os.path.join(dest_dir, "dbwebexport.zip.partial")

    start_time = time.time()

    for attempt in range(1, max_retries + 1):
        downloaded = 0
        headers = {}
        mode = "wb"

        # Resume from partial download if it exists
        if os.path.exists(tmp_path):
            downloaded = os.path.getsize(tmp_path)
            headers["Range"] = f"bytes={downloaded}-"
            mode = "ab"
            logger.info(f"Attempt {attempt}/{max_retries}: resuming from {downloaded / 1024 / 1024:.0f}MB")
        else:
            logger.info(f"Attempt {attempt}/{max_retries}: starting download of {CALACCESS_URL}")

        try:
            resp = requests.get(CALACCESS_URL, stream=True, timeout=(30, 120),
                                headers=headers)

            # If server doesn't support range requests, start over
            if downloaded > 0 and resp.status_code == 200:
                logger.info("Server does not support resume, restarting download")
                downloaded = 0
                mode = "wb"
            elif downloaded > 0 and resp.status_code == 206:
                logger.info(f"Server supports resume, continuing from {downloaded / 1024 / 1024:.0f}MB")
            elif resp.status_code == 416:
                # Range not satisfiable — file may already be complete
                logger.info("Range not satisfiable, file may be complete already")
                shutil.move(tmp_path, ZIP_PATH)
                elapsed = time.time() - start_time
                logger.info(f"Download complete: {ZIP_PATH} ({downloaded / 1024 / 1024:.0f}MB in {elapsed:.0f}s)")
                return True

            resp.raise_for_status()

            total_size = int(resp.headers.get("Content-Length", 0))
            if resp.status_code == 200:
                total_size_full = total_size
            else:
                total_size_full = downloaded + total_size

            with open(tmp_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=8192 * 16):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size_full > 0 and downloaded % (50 * 1024 * 1024) < 8192 * 16:
                        pct = (downloaded / total_size_full) * 100
                        elapsed = time.time() - start_time
                        rate = downloaded / elapsed / 1024 / 1024 if elapsed > 0 else 0
                        logger.info(f"  Progress: {pct:.1f}% ({downloaded / 1024 / 1024:.0f}MB) at {rate:.1f} MB/s")

            # Download finished successfully
            shutil.move(tmp_path, ZIP_PATH)
            elapsed = time.time() - start_time
            logger.info(f"Download complete: {ZIP_PATH} ({downloaded / 1024 / 1024:.0f}MB in {elapsed:.0f}s)")
            return True

        except Exception as e:
            logger.warning(f"Attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                wait = retry_delay * attempt
                logger.info(f"Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"Download failed after {max_retries} attempts")
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                return False


def extract_zip(logger):
    """Extract the CalAccess ZIP to DATA directory."""
    logger.info(f"Extracting {ZIP_PATH} ...")
    extract_dir = SCRIPT_DIR
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            zf.extractall(extract_dir)
        logger.info("Extraction complete")
        return True
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return False


def build_new_database(logger):
    """Build a new CA database from scratch into a .new file."""
    # Remove any leftover .new file
    if os.path.exists(DB_NEW_FILE):
        os.unlink(DB_NEW_FILE)

    logger.info("Building new CalAccess database...")

    # Temporarily set DB_FILE in environment for subprocesses
    env = os.environ.copy()

    # Step 1: Process CA data (committees + contributions + indexes)
    logger.info("Step 1/3: Processing CalAccess data...")
    try:
        # We need to temporarily point process_ca.py at the new DB file
        # Import and call directly with patched DB_FILE
        sys.path.insert(0, SCRIPT_DIR)

        import process_ca
        # Save original and patch
        orig_db = process_ca.DB_FILE
        process_ca.DB_FILE = DB_NEW_FILE

        # Clear processed_files state so it processes fresh
        conn = process_ca.create_database()
        process_ca.process_committees(conn)
        process_ca.process_contributions(conn)
        process_ca.create_indexes(conn)
        conn.close()

        process_ca.DB_FILE = orig_db
        logger.info("CalAccess data processing complete")
    except Exception as e:
        logger.error(f"CalAccess data processing failed: {e}")
        return False

    # Step 2: Build recipient lookup
    logger.info("Step 2/3: Building recipient lookup table...")
    try:
        import build_ca_recipient_lookup
        orig_db = build_ca_recipient_lookup.DB_PATH
        build_ca_recipient_lookup.DB_PATH = DB_NEW_FILE
        build_ca_recipient_lookup.build_ca_recipient_lookup()
        build_ca_recipient_lookup.DB_PATH = orig_db
        logger.info("Recipient lookup complete")
    except Exception as e:
        logger.error(f"Recipient lookup build failed: {e}")
        return False

    # Step 3: Build percentile tables
    logger.info("Step 3/3: Building percentile tables...")
    try:
        import build_ca_percentile_tables
        orig_db = build_ca_percentile_tables.DB_FILE
        build_ca_percentile_tables.DB_FILE = DB_NEW_FILE
        build_ca_percentile_tables.build_ca_donor_totals_by_year()
        build_ca_percentile_tables.build_ca_percentile_thresholds()
        build_ca_percentile_tables.DB_FILE = orig_db
        logger.info("Percentile tables complete")
    except Exception as e:
        logger.error(f"Percentile build failed: {e}")
        return False

    return True


def atomic_swap(logger):
    """Atomically swap the new database over the live one."""
    if not os.path.exists(DB_NEW_FILE):
        logger.error("New database file not found, cannot swap")
        return False

    # Check new DB is valid
    try:
        import sqlite3
        conn = sqlite3.connect(DB_NEW_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM contributions")
        count = cursor.fetchone()[0]
        conn.close()
        if count == 0:
            logger.error("New database has 0 contributions, aborting swap")
            return False
        logger.info(f"New database validated: {count:,} contributions")
    except Exception as e:
        logger.error(f"Database validation failed: {e}")
        return False

    # Atomic rename
    backup_path = DB_FILE + ".prev"
    try:
        if os.path.exists(DB_FILE):
            shutil.move(DB_FILE, backup_path)
        shutil.move(DB_NEW_FILE, DB_FILE)
        # Clean up WAL/SHM files from old DB
        for ext in ["-wal", "-shm", "-journal"]:
            old_file = backup_path + ext
            if os.path.exists(old_file):
                os.unlink(old_file)
        logger.info(f"Database swap complete. Previous DB backed up to {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Database swap failed: {e}")
        # Try to restore
        if not os.path.exists(DB_FILE) and os.path.exists(backup_path):
            shutil.move(backup_path, DB_FILE)
            logger.info("Restored previous database after swap failure")
        return False


def main():
    parser = argparse.ArgumentParser(description="Automated CalAccess data download and rebuild")
    parser.add_argument("--force", action="store_true", help="Skip change detection, download regardless")
    parser.add_argument("--dry-run", action="store_true", help="Check for updates without downloading")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=" * 60)
    logger.info(f"CalAccess Update started at {datetime.now().isoformat()}")
    logger.info(f"  Force: {args.force}, Dry run: {args.dry_run}")

    start_time = time.time()
    metadata = load_metadata()

    # Check for updates
    changed, remote_info = check_for_updates(metadata, logger)

    if not args.force and not changed:
        logger.info("No updates available")
        elapsed = time.time() - start_time
        logger.info(f"CalAccess Update completed in {elapsed:.0f}s (no changes)")
        logger.info("=" * 60)
        return 0

    if args.dry_run:
        if changed:
            logger.info("[DRY RUN] Would download and rebuild CalAccess database")
        elapsed = time.time() - start_time
        logger.info(f"CalAccess Update dry run completed in {elapsed:.0f}s")
        logger.info("=" * 60)
        return 0

    success = True

    # Download
    if not download_file(logger):
        success = False
    else:
        # Update metadata
        metadata[CALACCESS_URL] = remote_info
        metadata[CALACCESS_URL]["last_downloaded"] = datetime.now().isoformat()
        save_metadata(metadata)

        # Extract
        if not extract_zip(logger):
            success = False
        else:
            # Build new database
            if not build_new_database(logger):
                success = False
            else:
                # Atomic swap
                if not atomic_swap(logger):
                    success = False

    elapsed = time.time() - start_time
    status = "SUCCESS" if success else "COMPLETED WITH ERRORS"
    logger.info(f"CalAccess Update {status} in {elapsed:.0f}s")
    logger.info("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
