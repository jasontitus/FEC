import os
import sqlite3
import zipfile
import csv

DB_FILE = "fec_contributions.db"
DATA_DIR = "fec_data"

conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Create table for all committee metadata
cursor.execute('''
    CREATE TABLE IF NOT EXISTS committees (
        committee_id TEXT PRIMARY KEY,
        name TEXT,
        type TEXT
    )
''')

# Walk through each election cycle directory
for cycle_dir in os.listdir(DATA_DIR):
    full_path = os.path.join(DATA_DIR, cycle_dir)
    if not os.path.isdir(full_path):
        continue

    # Look for cmXX.zip in this cycle
    for fname in os.listdir(full_path):
        if fname.lower().startswith("cm") and fname.lower().endswith(".zip"):
            zip_path = os.path.join(full_path, fname)
            print(f"📦 Processing {zip_path}")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(full_path)

            txt_name = next((f for f in os.listdir(full_path) if f.lower().startswith("cm") and f.endswith(".txt")), None)
            if not txt_name:
                print(f"❌ No cm.txt file found in {full_path}")
                continue

            cm_path = os.path.join(full_path, txt_name)
            with open(cm_path, 'r', encoding='latin-1') as f:
                reader = csv.reader(f, delimiter='|')
                batch = []
                for row in reader:
                    if len(row) < 5:
                        continue
                    committee_id = row[0].strip()
                    name = row[1].strip()
                    cmte_type = row[3].strip()
                    batch.append((committee_id, name, cmte_type))

                cursor.executemany('''
                    INSERT OR REPLACE INTO committees (committee_id, name, type)
                    VALUES (?, ?, ?)
                ''', batch)
                conn.commit()
            print(f"✅ Loaded {len(batch)} committees from {txt_name}")

print("🎉 All committees loaded into the database.")
conn.close()
