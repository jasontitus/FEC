#!/usr/bin/env python3
"""
Automated FEC data download and processing pipeline.
Checks for new bulk download data, downloads if changed, and runs the full processing pipeline.
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
from datetime import datetime
from logging.handlers import RotatingFileHandler

try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FEC_DATA_DIR = os.path.join(SCRIPT_DIR, "fec_data")
METADATA_FILE = os.path.join(FEC_DATA_DIR, ".update_metadata.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")

# Current election cycle
CURRENT_CYCLE_YEAR = 2026
CURRENT_CYCLE_DIR = "2025-2026"
CURRENT_CYCLE_YY = str(CURRENT_CYCLE_YEAR)[-2:]

# FEC bulk download URLs
FEC_BASE_URL = "https://www.fec.gov/files/bulk-downloads"
INDIV_URL = f"{FEC_BASE_URL}/{CURRENT_CYCLE_YEAR}/indiv{CURRENT_CYCLE_YY}.zip"
CM_URL = f"{FEC_BASE_URL}/{CURRENT_CYCLE_YEAR}/cm{CURRENT_CYCLE_YY}.zip"

# All cycles for --all-cycles mode
ALL_CYCLES = [
    {"year": 2016, "dir": "2015-2016"},
    {"year": 2018, "dir": "2017-2018"},
    {"year": 2020, "dir": "2019-2020"},
    {"year": 2022, "dir": "2021-2022"},
    {"year": 2024, "dir": "2023-2024"},
    {"year": 2026, "dir": "2025-2026"},
]


def setup_logging():
    """Configure rotating file and console logging."""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("update_fec")
    logger.setLevel(logging.INFO)

    # Rotating file handler: 5MB max, 3 backups
    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "update_fec.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    # Console handler
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
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)


def check_for_updates(url, metadata, logger):
    """Check if a remote file has changed using HEAD request."""
    stored = metadata.get(url, {})
    try:
        resp = requests.head(url, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"HEAD request failed for {url}: {e}")
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
        logger.info(f"Change detected for {url}")
        logger.info(f"  Remote: size={remote_info['content_length']}, modified={remote_info['last_modified']}")
        logger.info(f"  Stored: size={stored.get('content_length')}, modified={stored.get('last_modified')}")
    else:
        logger.info(f"No change detected for {url}")

    return changed, remote_info


def download_file(url, dest_path, logger):
    """Stream-download a file to a temp location, then atomic rename."""
    dest_dir = os.path.dirname(dest_path)
    os.makedirs(dest_dir, exist_ok=True)

    # Download to temp file in same directory for atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=dest_dir, suffix=".tmp")
    os.close(fd)

    try:
        logger.info(f"Downloading {url} ...")
        resp = requests.get(url, stream=True, timeout=600)
        resp.raise_for_status()

        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        start_time = time.time()

        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192 * 16):
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = (downloaded / total_size) * 100
                    elapsed = time.time() - start_time
                    rate = downloaded / elapsed / 1024 / 1024 if elapsed > 0 else 0
                    if downloaded % (50 * 1024 * 1024) < 8192 * 16:  # Log every ~50MB
                        logger.info(f"  Progress: {pct:.1f}% ({downloaded / 1024 / 1024:.0f}MB) at {rate:.1f} MB/s")

        # Atomic rename
        shutil.move(tmp_path, dest_path)
        elapsed = time.time() - start_time
        logger.info(f"Download complete: {dest_path} ({downloaded / 1024 / 1024:.0f}MB in {elapsed:.0f}s)")
        return True

    except Exception as e:
        logger.error(f"Download failed: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return False


def extract_zip(zip_path, extract_dir, logger):
    """Extract a ZIP file to the specified directory."""
    logger.info(f"Extracting {zip_path} to {extract_dir}")
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        logger.info(f"Extraction complete")
        return True
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return False


def run_processing_pipeline(cycle_dir, logger):
    """Run the full processing pipeline for a given cycle."""
    cycle_path = os.path.join(FEC_DATA_DIR, cycle_dir)

    # Find the itcont.txt file
    itcont_path = None
    for root, dirs, files in os.walk(cycle_path):
        for f in files:
            if f.lower() == "itcont.txt":
                itcont_path = os.path.join(root, f)
                break
        if itcont_path:
            break

    if not itcont_path:
        logger.warning(f"No itcont.txt found in {cycle_path}")
        return False

    logger.info(f"Processing contributions from {itcont_path}")

    # Import and run incremental processing
    sys.path.insert(0, SCRIPT_DIR)
    try:
        # Run process_incremental on the file
        import process_incremental
        result = process_incremental.process_file_incrementally(itcont_path, f"Processing {cycle_dir}")
        if result:
            new, dupes, errors = result
            logger.info(f"Processing complete: {new:,} new, {dupes:,} duplicates, {errors:,} errors")
        return True
    except Exception as e:
        logger.error(f"Processing failed: {e}")
        return False


def run_committee_update(logger):
    """Update committee data."""
    logger.info("Updating committee data...")
    try:
        # Run committee.py as a subprocess to avoid module-level side effects
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, "committee.py")],
            capture_output=True, text=True, cwd=SCRIPT_DIR, timeout=3600
        )
        if result.returncode != 0:
            logger.error(f"Committee update failed: {result.stderr}")
            return False
        logger.info("Committee update complete")
        return True
    except Exception as e:
        logger.error(f"Committee update failed: {e}")
        return False


def run_recipient_lookup_rebuild(logger):
    """Rebuild the recipient lookup table."""
    logger.info("Rebuilding recipient lookup table...")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, "build_recipient_lookup.py")],
            capture_output=True, text=True, cwd=SCRIPT_DIR, timeout=7200
        )
        if result.returncode != 0:
            logger.error(f"Recipient lookup rebuild failed: {result.stderr}")
            return False
        logger.info("Recipient lookup rebuild complete")
        return True
    except Exception as e:
        logger.error(f"Recipient lookup rebuild failed: {e}")
        return False


def run_percentile_rebuild(logger):
    """Rebuild the percentile tables."""
    logger.info("Rebuilding percentile tables...")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, "build_percentile_tables.py")],
            capture_output=True, text=True, cwd=SCRIPT_DIR, timeout=7200
        )
        if result.returncode != 0:
            logger.error(f"Percentile rebuild failed: {result.stderr}")
            return False
        logger.info("Percentile rebuild complete")
        return True
    except Exception as e:
        logger.error(f"Percentile rebuild failed: {e}")
        return False


def update_cycle(cycle_year, cycle_dir, force, dry_run, logger):
    """Update a single FEC cycle."""
    yy = str(cycle_year)[-2:]
    indiv_url = f"{FEC_BASE_URL}/{cycle_year}/indiv{yy}.zip"
    cm_url = f"{FEC_BASE_URL}/{cycle_year}/cm{yy}.zip"

    metadata = load_metadata()

    # Check for changes
    indiv_changed, indiv_info = check_for_updates(indiv_url, metadata, logger)
    cm_changed, cm_info = check_for_updates(cm_url, metadata, logger)

    if not force and not indiv_changed and not cm_changed:
        logger.info(f"No updates available for {cycle_dir}")
        return True

    if dry_run:
        if indiv_changed:
            logger.info(f"[DRY RUN] Would download individual contributions for {cycle_dir}")
        if cm_changed:
            logger.info(f"[DRY RUN] Would download committee data for {cycle_dir}")
        return True

    cycle_path = os.path.join(FEC_DATA_DIR, cycle_dir)
    os.makedirs(cycle_path, exist_ok=True)
    success = True

    # Download and extract individual contributions
    if force or indiv_changed:
        indiv_zip = os.path.join(cycle_path, f"indiv{yy}.zip")
        if download_file(indiv_url, indiv_zip, logger):
            if extract_zip(indiv_zip, cycle_path, logger):
                metadata[indiv_url] = indiv_info
                metadata[indiv_url]["last_downloaded"] = datetime.now().isoformat()
            else:
                success = False
        else:
            success = False

    # Download and extract committee data
    if force or cm_changed:
        cm_zip = os.path.join(cycle_path, f"cm{yy}.zip")
        if download_file(cm_url, cm_zip, logger):
            if extract_zip(cm_zip, cycle_path, logger):
                metadata[cm_url] = cm_info
                metadata[cm_url]["last_downloaded"] = datetime.now().isoformat()
            else:
                success = False
        else:
            success = False

    save_metadata(metadata)
    return success


def main():
    parser = argparse.ArgumentParser(description="Automated FEC data download and processing")
    parser.add_argument("--force", action="store_true", help="Skip change detection, download regardless")
    parser.add_argument("--dry-run", action="store_true", help="Check for updates without downloading")
    parser.add_argument("--all-cycles", action="store_true", help="Update all cycles, not just current")
    parser.add_argument("--skip-processing", action="store_true", help="Download only, skip database processing")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=" * 60)
    logger.info(f"FEC Update started at {datetime.now().isoformat()}")
    logger.info(f"  Force: {args.force}, Dry run: {args.dry_run}, All cycles: {args.all_cycles}")

    start_time = time.time()
    success = True

    if args.all_cycles:
        cycles = ALL_CYCLES
    else:
        cycles = [{"year": CURRENT_CYCLE_YEAR, "dir": CURRENT_CYCLE_DIR}]

    # Download phase
    for cycle in cycles:
        logger.info(f"--- Checking cycle: {cycle['dir']} ---")
        if not update_cycle(cycle["year"], cycle["dir"], args.force, args.dry_run, logger):
            success = False

    # Processing phase (skip if dry run or skip-processing)
    if not args.dry_run and not args.skip_processing:
        for cycle in cycles:
            if not run_processing_pipeline(cycle["dir"], logger):
                success = False

        # Post-processing: committee update, lookup tables, percentiles
        if not run_committee_update(logger):
            success = False
        if not run_recipient_lookup_rebuild(logger):
            success = False
        if not run_percentile_rebuild(logger):
            success = False

    elapsed = time.time() - start_time
    status = "SUCCESS" if success else "COMPLETED WITH ERRORS"
    logger.info(f"FEC Update {status} in {elapsed:.0f}s")
    logger.info("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
