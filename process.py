import os
import sqlite3
import requests
import zipfile
import sys
import csv
from tqdm import tqdm

csv.field_size_limit(sys.maxsize)

# Election cycles and ZIP name mapping
ELECTION_CYCLES = {
    "2015-2016": "2016",
    "2017-2018": "2018",
    "2019-2020": "2020",
    "2021-2022": "2022",
    "2023-2024": "2024",
    "2025-2026": "2026",
}

BASE_URL = "https://www.fec.gov/files/bulk-downloads"
DATA_DIR = "fec_data"
os.makedirs(DATA_DIR, exist_ok=True)

DB_FILE = "fec_contributions.db"
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Optimize SQLite for bulk insertion
cursor.execute('PRAGMA journal_mode = OFF;')
cursor.execute('PRAGMA synchronous = OFF;')

# Create main contributions table
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

# Metadata table to track processed cycles
cursor.execute('''
    CREATE TABLE IF NOT EXISTS processed_cycles (
        label TEXT PRIMARY KEY
    )
''')
conn.commit()

def already_processed(label):
    cursor.execute("SELECT 1 FROM processed_cycles WHERE label = ?", (label,))
    return cursor.fetchone() is not None

def mark_processed(label):
    cursor.execute("INSERT INTO processed_cycles (label) VALUES (?)", (label,))
    conn.commit()

def process_cycle(label, cycle_code):
    if already_processed(label):
        print(f"⏩ Skipping {label}, already processed.")
        return

    print(f"\n🔄 Processing cycle: {label}")
    cycle_dir = os.path.join(DATA_DIR, label)
    os.makedirs(cycle_dir, exist_ok=True)

    zip_filename = f"indiv{cycle_code[-2:]}.zip"
    zip_path = os.path.join(cycle_dir, zip_filename)
    url = f"{BASE_URL}/{cycle_code}/{zip_filename}"

    if not os.path.exists(zip_path):
        print(f"⬇️  Downloading {url}")
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            with open(zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"✅ Downloaded to {zip_path}")
        else:
            print(f"❌ Failed to download: {url}")
            return
    else:
        print(f"✅ ZIP already downloaded: {zip_filename}")

    # --- Determine the target data file path ---
    # The primary individual contributions file is 'itcont.txt' or 'itcont'
    expected_data_file_primary = "itcont.txt"
    expected_data_file_fallback = "itcont" # If .txt extension is missing in zip/extraction
    
    path_to_data_file_primary = os.path.join(cycle_dir, expected_data_file_primary)
    path_to_data_file_fallback = os.path.join(cycle_dir, expected_data_file_fallback)

    # --- Revised Extraction Logic ---
    # Extract if the specific data file (itcont.txt or itcont) is not present.
    if not os.path.exists(path_to_data_file_primary) and not os.path.exists(path_to_data_file_fallback):
        print(f"📦 Data file ({expected_data_file_primary} or {expected_data_file_fallback}) not found in {cycle_dir}. Extracting ZIP {zip_filename}...")
        
        if not os.path.exists(zip_path):
            print(f"❌ ZIP file {zip_path} does not exist. Cannot extract. Please check download process or manually place it.")
            return # Stop processing this cycle

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                print(f"    Extracting all files from {zip_filename} to {cycle_dir}...")
                zip_ref.extractall(cycle_dir)
            print(f"✅ Extraction complete from {zip_filename} to {cycle_dir}")

            # After extraction, verify that one of our target files exists
            if not os.path.exists(path_to_data_file_primary) and not os.path.exists(path_to_data_file_fallback):
                print(f"❌ CRITICAL: Neither {expected_data_file_primary} nor {expected_data_file_fallback} found in {cycle_dir} even after extraction.")
                print(f"   Files in ZIP {zip_filename} were: {zip_ref.namelist() if 'zip_ref' in locals() and hasattr(zip_ref, 'namelist') else 'Unknown (zip error or not read)'}")
                print(f"   Files currently in directory {cycle_dir} are: {os.listdir(cycle_dir)}")
                return # Stop processing this cycle
        except zipfile.BadZipFile:
            print(f"❌ Bad ZIP file: {zip_path}. Consider deleting it and re-running to download a fresh copy.")
            return
        except Exception as e:
            print(f"❌ An error occurred during extraction of {zip_path}: {e}")
            return
    else:
        if os.path.exists(path_to_data_file_primary):
            print(f"✅ Data file already exists: {path_to_data_file_primary}")
        else: # This means path_to_data_file_fallback exists
            print(f"✅ Data file already exists: {path_to_data_file_fallback}")

    # --- Determine which specific data file to use for parsing ---
    txt_file_to_parse = ""
    if os.path.exists(path_to_data_file_primary):
        txt_file_to_parse = path_to_data_file_primary
    elif os.path.exists(path_to_data_file_fallback):
        txt_file_to_parse = path_to_data_file_fallback
    else:
        # This case should have been caught by the extraction logic and returned.
        print(f"❌ FATAL ERROR: Could not determine a valid data file ({expected_data_file_primary} or {expected_data_file_fallback}) in {cycle_dir}. Cannot parse.")
        return

    print(f"📄 Parsing {txt_file_to_parse}")
    with open(txt_file_to_parse, 'r', encoding='latin-1') as f:
        reader = csv.reader(f, delimiter='|')
        batch = []
        for row in tqdm(reader, desc=f"Inserting {label}"):
            try:
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
                # row[15] is OTHER_ID, row[0] is CMTE_ID (the reporting committee)
                other_id = row[15].strip()
                cmte_id = row[0].strip()
                recipient_name = other_id if other_id else cmte_id
                
                recipient_type = row[16].strip() # Note: As per FEC spec, row[5] is often TRANSACTION_TP (e.g. '15E') and row[16] is TRAN_ID.

                batch.append((
                    first_name, last_name, city, state, zip_code,
                    contribution_date, recipient_name, amount, recipient_type
                ))

                if len(batch) >= 1000:
                    cursor.executemany('''
                        INSERT INTO contributions (
                            first_name, last_name, city, state, zip_code,
                            contribution_date, recipient_name, amount, recipient_type
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', batch)
                    conn.commit()
                    batch = []

            except Exception:
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

    mark_processed(label)
    print(f"✅ Finished processing {label}")

# Main loop
for label, code in ELECTION_CYCLES.items():
    process_cycle(label, code)

# Indexing
print("\n🔧 Creating indexes...")
cursor.execute('CREATE INDEX IF NOT EXISTS idx_name ON contributions (first_name, last_name)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_location ON contributions (city, state, zip_code)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_contrib_date ON contributions (contribution_date)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_contrib_recipient ON contributions (recipient_name)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_contrib_flz_plus_date ON contributions (first_name, last_name, zip_code, contribution_date)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_contrib_flz_plus_amount ON contributions (first_name, last_name, zip_code, amount)')
conn.commit()
conn.close()

print(f"\n🎉 All done! Database saved as {DB_FILE}")
