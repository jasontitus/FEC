#!/usr/bin/env python3
"""
Master orchestrator for all data updates.
Runs FEC and CalAccess updates sequentially. If one fails, still attempts the other.
"""

import os
import sys
import time
import logging
import argparse
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")


def setup_logging():
    """Configure console logging for the orchestrator."""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("update_all")
    logger.setLevel(logging.INFO)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(ch)

    return logger


def run_update(script_path, label, args_list, logger):
    """Run an update script and return success status."""
    logger.info(f"--- Starting {label} ---")
    cmd = [sys.executable, script_path] + args_list

    try:
        result = subprocess.run(
            cmd,
            cwd=os.path.dirname(script_path),
            timeout=14400,  # 4 hour timeout
        )
        if result.returncode == 0:
            logger.info(f"{label}: SUCCESS")
            return True
        else:
            logger.error(f"{label}: FAILED (exit code {result.returncode})")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"{label}: TIMEOUT (exceeded 4 hours)")
        return False
    except Exception as e:
        logger.error(f"{label}: ERROR ({e})")
        return False


def main():
    parser = argparse.ArgumentParser(description="Master orchestrator for FEC and CalAccess updates")
    parser.add_argument("--fec-only", action="store_true", help="Only run FEC update")
    parser.add_argument("--ca-only", action="store_true", help="Only run CalAccess update")
    parser.add_argument("--force", action="store_true", help="Force download regardless of change detection")
    parser.add_argument("--dry-run", action="store_true", help="Check for updates without downloading")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=" * 60)
    logger.info(f"Update All started at {datetime.now().isoformat()}")

    start_time = time.time()
    results = {}

    # Build common args
    extra_args = []
    if args.force:
        extra_args.append("--force")
    if args.dry_run:
        extra_args.append("--dry-run")

    # FEC Update
    if not args.ca_only:
        fec_script = os.path.join(SCRIPT_DIR, "update_fec.py")
        results["FEC"] = run_update(fec_script, "FEC Update", extra_args, logger)

    # CalAccess Update
    if not args.fec_only:
        ca_script = os.path.join(SCRIPT_DIR, "CA", "update_calaccess.py")
        results["CalAccess"] = run_update(ca_script, "CalAccess Update", extra_args, logger)

    # Summary
    elapsed = time.time() - start_time
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY:")
    all_success = True
    for name, success in results.items():
        status = "SUCCESS" if success else "FAILED"
        logger.info(f"  {name}: {status}")
        if not success:
            all_success = False

    logger.info(f"Total time: {elapsed:.0f}s")
    logger.info("=" * 60)

    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
