#!/usr/bin/env python3
"""
One-time migration: Fix recipient_committee_id in contributions table.

Problem: When CMTE_ID was empty in RCPT_CD.TSV (~90% of rows), process_ca.py
stored FILING_ID as recipient_committee_id. But FILING_ID is a filing number,
not a committee identifier. The committees table is keyed by FILER_ID.

Fix: Use CVR_CAMPAIGN_DISCLOSURE_CD.TSV to map FILING_ID -> FILER_ID, then
update contributions.recipient_committee_id accordingly.
"""

import os
import sqlite3
import csv
import sys
import time

csv.field_size_limit(sys.maxsize)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "ca_contributions.db")
DATA_DIR = os.path.join(SCRIPT_DIR, "CalAccess", "DATA")


def migrate():
    cvr_file = os.path.join(DATA_DIR, "CVR_CAMPAIGN_DISCLOSURE_CD.TSV")
    if not os.path.exists(cvr_file):
        print(f"Error: CVR file not found: {cvr_file}")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Optimize for bulk update
    cursor.execute('PRAGMA journal_mode = WAL;')
    cursor.execute('PRAGMA synchronous = NORMAL;')
    cursor.execute('PRAGMA cache_size = -8000;')
    cursor.execute('PRAGMA temp_store = DEFAULT;')

    # Step 1: Create temp mapping table
    print("Step 1: Creating filing_to_filer mapping table...")
    cursor.execute("DROP TABLE IF EXISTS filing_to_filer")
    cursor.execute("""
        CREATE TABLE filing_to_filer (
            filing_id TEXT PRIMARY KEY,
            filer_id TEXT NOT NULL
        )
    """)

    # Load mapping from CVR file
    batch = []
    count = 0
    with open(cvr_file, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader((line.replace('\0', '') for line in f), delimiter='\t')
        for row in reader:
            filing_id = row.get('FILING_ID', '').strip()
            filer_id = row.get('FILER_ID', '').strip()
            if filing_id and filer_id:
                batch.append((filing_id, filer_id))
                count += 1
                if len(batch) >= 10000:
                    cursor.executemany(
                        "INSERT OR REPLACE INTO filing_to_filer (filing_id, filer_id) VALUES (?, ?)",
                        batch
                    )
                    conn.commit()
                    batch = []
    if batch:
        cursor.executemany(
            "INSERT OR REPLACE INTO filing_to_filer (filing_id, filer_id) VALUES (?, ?)",
            batch
        )
        conn.commit()
    print(f"   Loaded {count:,} filing-to-filer mappings")

    # Step 2: Check how many contributions will be updated
    cursor.execute("""
        SELECT COUNT(*) FROM contributions c
        INNER JOIN filing_to_filer ftf ON c.recipient_committee_id = ftf.filing_id
        WHERE c.recipient_committee_id != ftf.filer_id
    """)
    to_update = cursor.fetchone()[0]
    print(f"Step 2: {to_update:,} contributions need updating")

    if to_update == 0:
        print("   Nothing to update, migration already applied or no mismatches.")
        cursor.execute("DROP TABLE IF EXISTS filing_to_filer")
        conn.close()
        return

    # Step 3: Update in batches using rowid ranges to avoid memory issues
    print("Step 3: Updating contributions in batches...")
    start_time = time.time()

    cursor.execute("SELECT MIN(rowid), MAX(rowid) FROM contributions")
    min_id, max_id = cursor.fetchone()

    batch_size = 500000
    total_updated = 0
    current = min_id
    prev_total_changes = conn.total_changes

    while current <= max_id:
        batch_end = current + batch_size - 1
        cursor.execute("""
            UPDATE contributions
            SET recipient_committee_id = (
                SELECT ftf.filer_id FROM filing_to_filer ftf
                WHERE ftf.filing_id = contributions.recipient_committee_id
            )
            WHERE rowid >= ? AND rowid <= ?
              AND EXISTS (
                SELECT 1 FROM filing_to_filer ftf
                WHERE ftf.filing_id = contributions.recipient_committee_id
                  AND ftf.filer_id != contributions.recipient_committee_id
              )
        """, (current, batch_end))
        new_total_changes = conn.total_changes
        batch_updated = new_total_changes - prev_total_changes
        prev_total_changes = new_total_changes
        total_updated += batch_updated
        conn.commit()
        if batch_updated > 0:
            print(f"   Rows {current:,}-{batch_end:,}: updated {batch_updated:,}")
        current = batch_end + 1

    elapsed = time.time() - start_time
    print(f"   Total updated: {total_updated:,} in {elapsed:.1f}s")

    # Step 4: Verify
    cursor.execute("""
        SELECT COUNT(*) FROM contributions c
        INNER JOIN committees cm ON c.recipient_committee_id = cm.committee_id
    """)
    matched = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM contributions")
    total = cursor.fetchone()[0]
    print(f"Step 4: Verification - {matched:,} / {total:,} contributions now match committees ({100*matched/total:.1f}%)")

    # Step 5: Cleanup
    cursor.execute("DROP TABLE IF EXISTS filing_to_filer")
    conn.commit()
    conn.close()
    print("Done! Temp table dropped.")


if __name__ == "__main__":
    migrate()
