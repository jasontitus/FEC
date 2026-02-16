#!/usr/bin/env python3
"""
Fix committee mapping by creating a FILING_ID to committee name lookup
"""

import os
import sqlite3
import csv
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zstd_utils import open_readable

# Increase CSV field size limit
csv.field_size_limit(sys.maxsize)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "ca_contributions.db")
CVR_FILE = os.path.join(SCRIPT_DIR, "CalAccess", "DATA", "CVR_CAMPAIGN_DISCLOSURE_CD.TSV")

def create_filing_committee_mapping():
    """Create a mapping table from FILING_ID to committee information."""
    print("🔧 Creating FILING_ID to committee mapping...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create the mapping table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS filing_committee_mapping (
            filing_id TEXT PRIMARY KEY,
            filer_id TEXT,
            committee_name TEXT,
            entity_code TEXT,
            committee_type TEXT
        )
    """)
    
    # Clear existing data
    cursor.execute("DELETE FROM filing_committee_mapping")
    
    print("📋 Processing CVR_CAMPAIGN_DISCLOSURE_CD.TSV...")
    
    with open_readable(CVR_FILE, encoding='utf-8', errors='replace', null_clean=True) as f:
        reader = csv.DictReader(f, delimiter='\t')
        batch = []
        
        for row in reader:
            try:
                filing_id = row.get('FILING_ID', '').strip()
                filer_id = row.get('FILER_ID', '').strip()
                
                if not filing_id:
                    continue
                
                # Build committee name
                name_parts = []
                for part in ['FILER_NAML', 'FILER_NAMF', 'FILER_NAMT', 'FILER_NAMS']:
                    if row.get(part, '').strip():
                        name_parts.append(row.get(part, '').strip())
                
                committee_name = ' '.join(name_parts) if name_parts else f"Filing ID: {filing_id}"
                
                # Map entity and committee types
                entity_code = row.get('ENTITY_CD', '').strip()
                committee_type = row.get('CMTTE_TYPE', '').strip()
                
                batch.append((filing_id, filer_id, committee_name, entity_code, committee_type))
                
                if len(batch) >= 1000:
                    cursor.executemany("""
                        INSERT OR REPLACE INTO filing_committee_mapping 
                        (filing_id, filer_id, committee_name, entity_code, committee_type)
                        VALUES (?, ?, ?, ?, ?)
                    """, batch)
                    conn.commit()
                    batch = []
                    
            except Exception as e:
                print(f"Warning: Error processing row: {e}")
                continue
        
        # Final commit
        if batch:
            cursor.executemany("""
                INSERT OR REPLACE INTO filing_committee_mapping 
                (filing_id, filer_id, committee_name, entity_code, committee_type)
                VALUES (?, ?, ?, ?, ?)
            """, batch)
            conn.commit()
    
    # Create index
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_filing_committee_mapping ON filing_committee_mapping (filing_id)")
    
    # Show statistics
    cursor.execute("SELECT COUNT(*) FROM filing_committee_mapping")
    mapping_count = cursor.fetchone()[0]
    print(f"✅ Created {mapping_count:,} filing-to-committee mappings")
    
    # Test the mapping with Jason Gardner's data
    print("\n🧪 Testing mapping with Jason Gardner's contributions:")
    cursor.execute("""
        SELECT c.recipient_committee_id, fc.committee_name 
        FROM contributions c 
        LEFT JOIN filing_committee_mapping fc ON c.recipient_committee_id = fc.filing_id
        WHERE c.first_name = 'JASON' AND c.last_name = 'GARDNER' 
        LIMIT 5
    """)
    
    for filing_id, committee_name in cursor.fetchall():
        print(f"   {filing_id} -> {committee_name}")
    
    conn.close()
    print("\n🎉 Committee mapping fixed!")

if __name__ == "__main__":
    create_filing_committee_mapping()
