import os
import sqlite3
import sys
import csv
import argparse
from tqdm import tqdm

csv.field_size_limit(sys.maxsize)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "fec_contributions.db")

def get_connection(db_path=None):
    """Create and return a database connection with optimized settings."""
    path = db_path or DB_FILE
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute('PRAGMA journal_mode = WAL;')
    cursor.execute('PRAGMA synchronous = NORMAL;')
    return conn, cursor

conn, cursor = get_connection()

# Create main contributions table (if it doesn't exist)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS contributions (
        first_name TEXT,
        last_name  TEXT,
        city       TEXT,
        state      TEXT,
        zip_code   TEXT,
        contribution_date TEXT,
        recipient_name   TEXT,
        amount           REAL,
        recipient_type   TEXT
    )
''')

# Create a temporary table to help with duplicate detection (for performance)
cursor.execute('''
    CREATE TEMPORARY TABLE temp_contribution_hashes (
        record_hash TEXT PRIMARY KEY
    )
''')

# Export existing 2023+ records and build hash table in Python (much faster)
print("🔍 Exporting existing 2023+ records for duplicate detection...")
cursor.execute('''
    SELECT first_name, last_name, city, state, zip_code, contribution_date, 
           recipient_name, amount, recipient_type
    FROM contributions
    WHERE contribution_date >= '2023-01-01'
''')

print("⚡ Building hash table in Python...")
existing_hashes = set()
batch_hashes = []

for row in tqdm(cursor.fetchall(), desc="Processing existing records"):
    record_hash = '|'.join([
        str(row[0] or ''),  # first_name
        str(row[1] or ''),  # last_name  
        str(row[2] or ''),  # city
        str(row[3] or ''),  # state
        str(row[4] or ''),  # zip_code
        str(row[5] or ''),  # contribution_date
        str(row[6] or ''),  # recipient_name
        str(row[7] or ''),  # amount
        str(row[8] or '')   # recipient_type
    ])
    existing_hashes.add(record_hash)
    batch_hashes.append((record_hash,))

# Bulk insert the hashes into temp table
print("💾 Bulk loading hash table...")
cursor.executemany('INSERT OR IGNORE INTO temp_contribution_hashes (record_hash) VALUES (?)', batch_hashes)
print(f"✅ Indexed {len(existing_hashes):,} existing 2023+ records for duplicate detection")

conn.commit()

def record_exists(first_name, last_name, city, state, zip_code, contribution_date, recipient_name, amount, recipient_type):
    """Check if a record already exists in the database (using fast Python set lookup)"""
    record_hash = '|'.join([
        str(first_name or ''),
        str(last_name or ''),
        str(city or ''),
        str(state or ''),
        str(zip_code or ''),
        str(contribution_date or ''),
        str(recipient_name or ''),
        str(amount or ''),
        str(recipient_type or '')
    ])
    return record_hash in existing_hashes

def add_record_to_temp_table(first_name, last_name, city, state, zip_code, contribution_date, recipient_name, amount, recipient_type):
    """Add a record hash to track newly inserted records (both Python set and temp table)"""
    record_hash = '|'.join([
        str(first_name or ''),
        str(last_name or ''),
        str(city or ''),
        str(state or ''),
        str(zip_code or ''),
        str(contribution_date or ''),
        str(recipient_name or ''),
        str(amount or ''),
        str(recipient_type or '')
    ])
    existing_hashes.add(record_hash)  # Add to Python set for future lookups

def process_file_incrementally(file_path, description="Processing"):
    """Process a single file and add only new records"""
    print(f"\n📄 Processing {file_path}")
    
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return
    
    with open(file_path, 'r', encoding='latin-1') as f:
        reader = csv.reader(f, delimiter='|')
        batch = []
        new_records = 0
        duplicate_records = 0
        error_records = 0
        
        for row in tqdm(reader, desc=description):
            try:
                # Parse the record (same logic as original script)
                name = row[7].strip().split(', ')
                last_name = name[0] if len(name) > 0 else ''
                first_name = name[1] if len(name) > 1 else ''
                city = row[8].strip()
                state = row[9].strip()
                zip_code = row[10].strip()
                raw_date = row[13].strip()
                contribution_date = (
                    f"{raw_date[4:8]}-{raw_date[0:2]}-{raw_date[2:4]}"
                    if len(raw_date) == 8 else None
                )
                amount = float(row[14].strip())
                
                # Determine the recipient name
                other_id = row[15].strip()
                cmte_id = row[0].strip()
                recipient_name = other_id if other_id else cmte_id
                
                recipient_type = row[16].strip()

                # Check if this record already exists
                if record_exists(first_name, last_name, city, state, zip_code, 
                               contribution_date, recipient_name, amount, recipient_type):
                    duplicate_records += 1
                    continue
                
                # Add to batch for insertion
                batch.append((
                    first_name, last_name, city, state, zip_code,
                    contribution_date, recipient_name, amount, recipient_type
                ))
                
                # Also add to our temporary tracking table
                add_record_to_temp_table(first_name, last_name, city, state, zip_code,
                                       contribution_date, recipient_name, amount, recipient_type)
                
                new_records += 1

                # Process batch when it gets large enough
                if len(batch) >= 1000:
                    cursor.executemany('''
                        INSERT INTO contributions (
                            first_name, last_name, city, state, zip_code,
                            contribution_date, recipient_name, amount, recipient_type
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', batch)
                    conn.commit()
                    batch = []

            except Exception as e:
                error_records += 1
                continue

        # Final commit for remaining records
        if batch:
            cursor.executemany('''
                INSERT INTO contributions (
                    first_name, last_name, city, state, zip_code,
                    contribution_date, recipient_name, amount, recipient_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()

    print(f"✅ Completed processing {file_path}")
    print(f"   📊 New records added: {new_records:,}")
    print(f"   🔄 Duplicates skipped: {duplicate_records:,}")
    print(f"   ❌ Errors skipped: {error_records:,}")
    
    return new_records, duplicate_records, error_records

# Main execution
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Incrementally process FEC contribution data")
    parser.add_argument("file_path", nargs="?",
                        default=os.path.join(SCRIPT_DIR, "fec_data", "2025-2026", "itcont.txt"),
                        help="Path to the itcont.txt file to process")
    args = parser.parse_args()

    print("🚀 Starting incremental FEC data processing with duplicate detection...")

    file_path = args.file_path

    total_new, total_duplicates, total_errors = process_file_incrementally(
        file_path,
        f"Adding records from {os.path.basename(file_path)}"
    )
    
    print(f"\n🎉 Processing complete!")
    print(f"📊 Summary:")
    print(f"   ✅ New records added: {total_new:,}")
    print(f"   🔄 Duplicates skipped: {total_duplicates:,}")
    print(f"   ❌ Errors skipped: {total_errors:,}")
    
    # Update indexes to maintain performance
    print("\n🔧 Updating indexes...")
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_name ON contributions (first_name, last_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_location ON contributions (city, state, zip_code)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_contrib_date ON contributions (contribution_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_contrib_recipient ON contributions (recipient_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_contrib_flz_plus_date ON contributions (first_name, last_name, zip_code, contribution_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_contrib_flz_plus_amount ON contributions (first_name, last_name, zip_code, amount)')
    conn.commit()
    
    conn.close()
    print(f"✅ Database updated: {DB_FILE}")
