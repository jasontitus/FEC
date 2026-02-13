#!/usr/bin/env python3
"""
California Campaign Contributions Database Processor
Processes CalAccess data from RCPT_CD.TSV and CVR_CAMPAIGN_DISCLOSURE_CD.TSV
"""

import os
import sqlite3
import csv
import sys
from datetime import datetime

# Optional progress bar
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, desc="Processing"):
        print(f"{desc}...")
        return iterable

# Increase CSV field size limit for large data
csv.field_size_limit(sys.maxsize)

# Database configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "ca_contributions.db")
DATA_DIR = os.path.join(SCRIPT_DIR, "CalAccess", "DATA")

def create_database():
    """Create the SQLite database with optimized settings."""
    print("🔧 Creating database...")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Optimize SQLite for bulk insertion
    cursor.execute('PRAGMA journal_mode = OFF;')
    cursor.execute('PRAGMA synchronous = OFF;')
    cursor.execute('PRAGMA cache_size = 100000;')
    cursor.execute('PRAGMA temp_store = MEMORY;')

    # Create main contributions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contributions (
            filing_id TEXT,
            amend_id INTEGER,
            line_item INTEGER,
            first_name TEXT,
            last_name TEXT,
            city TEXT,
            state TEXT,
            zip_code TEXT,
            employer TEXT,
            occupation TEXT,
            contribution_date TEXT,
            amount REAL,
            recipient_committee_id TEXT,
            recipient_type TEXT,
            entity_code TEXT,
            transaction_type TEXT,
            cumulative_ytd REAL,
            transaction_id TEXT,
            candidate_last_name TEXT,
            candidate_first_name TEXT,
            office_description TEXT,
            jurisdiction_description TEXT
        )
    ''')

    # Create committees table for recipient information
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS committees (
            committee_id TEXT PRIMARY KEY,
            name TEXT,
            committee_type TEXT,
            entity_code TEXT,
            city TEXT,
            state TEXT,
            zip_code TEXT,
            phone TEXT,
            email TEXT,
            candidate_last_name TEXT,
            candidate_first_name TEXT,
            office_description TEXT,
            jurisdiction_description TEXT
        )
    ''')

    # Metadata table to track processed files
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_files (
            filename TEXT PRIMARY KEY,
            processed_at TEXT
        )
    ''')

    conn.commit()
    return conn

def parse_ca_date(date_str):
    """
    Parse California date format which appears to be 'M/D/YYYY HH:MM:SS AM/PM'
    Returns YYYY-MM-DD format or None if invalid
    """
    if not date_str or date_str.strip() == "":
        return None
    
    try:
        # Handle different date formats that might appear
        date_str = date_str.strip()
        
        # Try parsing with time component first
        if " " in date_str:
            date_part = date_str.split(" ")[0]
        else:
            date_part = date_str
            
        # Split by slash
        parts = date_part.split("/")
        if len(parts) == 3:
            month, day, year = parts
            month = month.zfill(2)
            day = day.zfill(2)
            return f"{year}-{month}-{day}"
            
    except Exception:
        pass
    
    return None

def already_processed(filename, cursor):
    """Check if a file has already been processed."""
    cursor.execute("SELECT 1 FROM processed_files WHERE filename = ?", (filename,))
    return cursor.fetchone() is not None

def mark_processed(filename, cursor, conn):
    """Mark a file as processed."""
    cursor.execute("INSERT OR REPLACE INTO processed_files (filename, processed_at) VALUES (?, ?)", 
                   (filename, datetime.now().isoformat()))
    conn.commit()

def process_committees(conn):
    """Process committee information from CVR_CAMPAIGN_DISCLOSURE_CD.TSV"""
    cursor = conn.cursor()
    committees_file = os.path.join(DATA_DIR, "CVR_CAMPAIGN_DISCLOSURE_CD.TSV")
    
    if already_processed("CVR_CAMPAIGN_DISCLOSURE_CD.TSV", cursor):
        print("⏩ Committees already processed, skipping.")
        return
        
    if not os.path.exists(committees_file):
        print(f"❌ Committee file not found: {committees_file}")
        return

    print("📋 Processing committees...")
    
    with open(committees_file, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f, delimiter='\t')
        batch = []
        
        for row in tqdm(reader, desc="Processing committees"):
            try:
                # Extract committee information
                committee_id = row.get('FILER_ID', '').strip()
                if not committee_id:
                    continue
                    
                # Get the full committee name
                name_parts = []
                if row.get('FILER_NAML', '').strip():
                    name_parts.append(row.get('FILER_NAML', '').strip())
                if row.get('FILER_NAMF', '').strip():
                    name_parts.append(row.get('FILER_NAMF', '').strip())
                if row.get('FILER_NAMT', '').strip():
                    name_parts.append(row.get('FILER_NAMT', '').strip())
                if row.get('FILER_NAMS', '').strip():
                    name_parts.append(row.get('FILER_NAMS', '').strip())
                
                committee_name = ' '.join(name_parts)
                
                # Map committee type
                committee_type_mapping = {
                    'CAO': 'Candidate',
                    'CTL': 'Candidate Committee', 
                    'RCP': 'Recipient Committee',
                    'SMO': 'Slate Mailer Organization',
                    'BMC': 'Ballot Measure Committee'
                }
                
                committee_type = committee_type_mapping.get(
                    row.get('ENTITY_CD', '').strip(), 
                    row.get('CMTTE_TYPE', '').strip()
                )
                
                # Get candidate name if available
                cand_name_parts = []
                if row.get('CAND_NAML', '').strip():
                    cand_name_parts.append(row.get('CAND_NAML', '').strip())
                if row.get('CAND_NAMF', '').strip():
                    cand_name_parts.append(row.get('CAND_NAMF', '').strip())
                
                batch.append((
                    committee_id,
                    committee_name,
                    committee_type,
                    row.get('ENTITY_CD', '').strip(),
                    row.get('FILER_CITY', '').strip(),
                    row.get('FILER_ST', '').strip(),
                    row.get('FILER_ZIP4', '').strip(),
                    row.get('FILER_PHON', '').strip(),
                    row.get('FILE_EMAIL', '').strip(),
                    row.get('CAND_NAML', '').strip(),
                    row.get('CAND_NAMF', '').strip(),
                    row.get('OFFIC_DSCR', '').strip(),
                    row.get('JURIS_DSCR', '').strip()
                ))

                if len(batch) >= 1000:
                    cursor.executemany('''
                        INSERT OR REPLACE INTO committees (
                            committee_id, name, committee_type, entity_code,
                            city, state, zip_code, phone, email,
                            candidate_last_name, candidate_first_name,
                            office_description, jurisdiction_description
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', batch)
                    conn.commit()
                    batch = []

            except Exception as e:
                print(f"Warning: Error processing committee row: {e}")
                continue

        # Final commit for remaining records
        if batch:
            cursor.executemany('''
                INSERT OR REPLACE INTO committees (
                    committee_id, name, committee_type, entity_code,
                    city, state, zip_code, phone, email,
                    candidate_last_name, candidate_first_name,
                    office_description, jurisdiction_description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()

    mark_processed("CVR_CAMPAIGN_DISCLOSURE_CD.TSV", cursor, conn)
    print("✅ Committees processing complete")

def process_contributions(conn):
    """Process contributions from RCPT_CD.TSV"""
    cursor = conn.cursor()
    contributions_file = os.path.join(DATA_DIR, "RCPT_CD.TSV")
    
    if already_processed("RCPT_CD.TSV", cursor):
        print("⏩ Contributions already processed, skipping.")
        return
        
    if not os.path.exists(contributions_file):
        print(f"❌ Contributions file not found: {contributions_file}")
        return

    print("💰 Processing contributions...")
    
    with open(contributions_file, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f, delimiter='\t')
        batch = []
        processed_count = 0
        
        for row in tqdm(reader, desc="Processing contributions"):
            try:
                # Skip if not an individual contribution (IND = Individual)
                entity_code = row.get('ENTITY_CD', '').strip()
                if entity_code != 'IND':
                    continue
                
                # Parse contributor name
                last_name = row.get('CTRIB_NAML', '').strip()
                first_name = row.get('CTRIB_NAMF', '').strip()
                
                # Skip if no name
                if not last_name and not first_name:
                    continue
                
                # Parse date
                contribution_date = parse_ca_date(row.get('RCPT_DATE', ''))
                if not contribution_date:
                    continue
                
                # Parse amount
                try:
                    amount = float(row.get('AMOUNT', '0') or '0')
                    if amount <= 0:
                        continue
                except (ValueError, TypeError):
                    continue
                
                # Get recipient committee ID - try CMTE_ID first, then FILING_ID
                recipient_committee_id = row.get('CMTE_ID', '').strip()
                if not recipient_committee_id:
                    # Use the filing committee as recipient
                    filing_id = row.get('FILING_ID', '').strip()
                    # We'll need to map filing_id to committee_id later
                    recipient_committee_id = filing_id
                
                # Parse other fields
                try:
                    cumulative_ytd = float(row.get('CUM_YTD', '0') or '0')
                except (ValueError, TypeError):
                    cumulative_ytd = 0.0

                batch.append((
                    row.get('FILING_ID', '').strip(),
                    int(row.get('AMEND_ID', '0') or '0'),
                    int(row.get('LINE_ITEM', '0') or '0'),
                    first_name,
                    last_name,
                    row.get('CTRIB_CITY', '').strip(),
                    row.get('CTRIB_ST', '').strip(),
                    row.get('CTRIB_ZIP4', '').strip(),
                    row.get('CTRIB_EMP', '').strip(),
                    row.get('CTRIB_OCC', '').strip(),
                    contribution_date,
                    amount,
                    recipient_committee_id,
                    row.get('REC_TYPE', '').strip(),
                    entity_code,
                    row.get('TRAN_TYPE', '').strip(),
                    cumulative_ytd,
                    row.get('TRAN_ID', '').strip(),
                    row.get('CAND_NAML', '').strip(),  # Candidate last name
                    row.get('CAND_NAMF', '').strip(),  # Candidate first name
                    row.get('OFFIC_DSCR', '').strip(), # Office description
                    row.get('JURIS_DSCR', '').strip()  # Jurisdiction description
                ))

                processed_count += 1

                if len(batch) >= 1000:
                    cursor.executemany('''
                        INSERT INTO contributions (
                            filing_id, amend_id, line_item, first_name, last_name,
                            city, state, zip_code, employer, occupation,
                            contribution_date, amount, recipient_committee_id,
                            recipient_type, entity_code, transaction_type,
                            cumulative_ytd, transaction_id, candidate_last_name,
                            candidate_first_name, office_description, jurisdiction_description
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', batch)
                    conn.commit()
                    batch = []

            except Exception as e:
                print(f"Warning: Error processing contribution row: {e}")
                continue

        # Final commit for remaining records
        if batch:
            cursor.executemany('''
                INSERT INTO contributions (
                    filing_id, amend_id, line_item, first_name, last_name,
                    city, state, zip_code, employer, occupation,
                    contribution_date, amount, recipient_committee_id,
                    recipient_type, entity_code, transaction_type,
                    cumulative_ytd, transaction_id, candidate_last_name,
                    candidate_first_name, office_description, jurisdiction_description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()

    mark_processed("RCPT_CD.TSV", cursor, conn)
    print(f"✅ Contributions processing complete. Processed {processed_count:,} individual contributions")

def create_indexes(conn):
    """Create database indexes for optimal query performance."""
    print("🔧 Creating indexes...")
    cursor = conn.cursor()
    
    indexes = [
        'CREATE INDEX IF NOT EXISTS idx_ca_name ON contributions (first_name, last_name)',
        'CREATE INDEX IF NOT EXISTS idx_ca_location ON contributions (city, state, zip_code)',
        'CREATE INDEX IF NOT EXISTS idx_ca_contrib_date ON contributions (contribution_date)',
        'CREATE INDEX IF NOT EXISTS idx_ca_recipient ON contributions (recipient_committee_id)',
        'CREATE INDEX IF NOT EXISTS idx_ca_flz_plus_date ON contributions (first_name, last_name, zip_code, contribution_date)',
        'CREATE INDEX IF NOT EXISTS idx_ca_flz_plus_amount ON contributions (first_name, last_name, zip_code, amount)',
        'CREATE INDEX IF NOT EXISTS idx_ca_committee_id ON committees (committee_id)',
        'CREATE INDEX IF NOT EXISTS idx_ca_committee_name ON committees (name)'
    ]
    
    for idx_sql in indexes:
        cursor.execute(idx_sql)
    
    conn.commit()
    print("✅ Indexes created")

def show_statistics(conn):
    """Display database statistics."""
    cursor = conn.cursor()
    
    print("\n📊 Database Statistics:")
    
    # Total contributions
    cursor.execute("SELECT COUNT(*), SUM(amount) FROM contributions")
    contrib_count, total_amount = cursor.fetchone()
    print(f"   Total contributions: {contrib_count:,}")
    print(f"   Total amount: ${total_amount:,.2f}")
    
    # Date range
    cursor.execute("SELECT MIN(contribution_date), MAX(contribution_date) FROM contributions WHERE contribution_date IS NOT NULL")
    min_date, max_date = cursor.fetchone()
    print(f"   Date range: {min_date} to {max_date}")
    
    # Total committees
    cursor.execute("SELECT COUNT(*) FROM committees")
    committee_count = cursor.fetchone()[0]
    print(f"   Total committees: {committee_count:,}")
    
    # Top recipients
    print("\n🏆 Top 5 recipients by contribution count:")
    cursor.execute("""
        SELECT c.recipient_committee_id, COALESCE(cm.name, c.recipient_committee_id) as name, 
               COUNT(*) as contrib_count, SUM(c.amount) as total_amount
        FROM contributions c
        LEFT JOIN committees cm ON c.recipient_committee_id = cm.committee_id
        GROUP BY c.recipient_committee_id
        ORDER BY contrib_count DESC
        LIMIT 5
    """)
    
    for row in cursor.fetchall():
        committee_id, name, count, amount = row
        print(f"   {name[:50]:<50} {count:>6,} contrib  ${amount:>12,.2f}")

def main():
    """Main processing function."""
    print("🚀 Starting California Campaign Contributions Database Processing")
    
    if not os.path.exists(DATA_DIR):
        print(f"❌ Data directory not found: {DATA_DIR}")
        print("   Please ensure the CalAccess data has been extracted to CalAccess/DATA/")
        return
    
    # Create database and connection
    conn = create_database()
    
    try:
        # Process data
        process_committees(conn)
        process_contributions(conn)
        
        # Create indexes
        create_indexes(conn)
        
        # Show statistics
        show_statistics(conn)
        
        print(f"\n🎉 Processing complete! Database saved as {DB_FILE}")
        
    finally:
        conn.close()

if __name__ == "__main__":
    main()
