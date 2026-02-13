#!/usr/bin/env python3
"""
Complete setup script for FEC Contributions Database from scratch.
This script will:
1. Create all necessary tables
2. Process all contribution data
3. Load committee information
4. Build lookup tables for fast searching
5. Create indexes for performance

Usage: python3 setup_from_scratch.py [--public]
"""

import os
import sys
import sqlite3
import subprocess
import time
import argparse
from pathlib import Path

DB_PATH = "fec_contributions.db"

def run_script(script_name, description, *args):
    """Run a Python script and handle errors"""
    print(f"\n🚀 {description}")
    print(f"   Running: python3 {script_name} {' '.join(args)}")
    
    start_time = time.time()
    try:
        result = subprocess.run([sys.executable, script_name] + list(args), 
                              check=True, capture_output=True, text=True)
        elapsed = time.time() - start_time
        print(f"✅ {description} completed in {elapsed:.1f} seconds")
        if result.stdout:
            print(f"   Output: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - start_time
        print(f"❌ {description} failed after {elapsed:.1f} seconds")
        print(f"   Error: {e.stderr.strip() if e.stderr else str(e)}")
        return False
    except FileNotFoundError:
        print(f"❌ Script {script_name} not found")
        return False

def create_base_tables():
    """Create the base database tables"""
    print("\n📋 Creating base database tables...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create contributions table
    print("   Creating contributions table...")
    with open("table.sql", 'r') as f:
        cursor.executescript(f.read())
    
    # Create processed_files table for tracking
    print("   Creating processed files tracking table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_files (
            filename TEXT PRIMARY KEY,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    print("✅ Base tables created")

def create_lookup_tables():
    """Create all lookup and performance tables"""
    print("\n📋 Creating lookup and performance tables...")
    
    # Create percentile tables
    print("   Creating percentile tables...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    with open("percentile_tables.sql", 'r') as f:
        cursor.executescript(f.read())
    conn.commit()
    conn.close()
    
    # Create recipient lookup tables
    print("   Creating recipient lookup tables...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    with open("recipient_lookup_table.sql", 'r') as f:
        cursor.executescript(f.read())
    conn.commit()
    conn.close()
    
    print("✅ Lookup tables created")

def create_indexes():
    """Create database indexes for performance"""
    print("\n📋 Creating database indexes...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    with open("indexes.sql", 'r') as f:
        cursor.executescript(f.read())
    conn.commit()
    conn.close()
    
    print("✅ Indexes created")

def main():
    """Main setup process"""
    parser = argparse.ArgumentParser(description='Set up FEC database from scratch')
    parser.add_argument('--skip-data', action='store_true', help='Skip data processing (tables only)')
    args = parser.parse_args()
    
    print("🎯 FEC Contributions Database - Complete Setup")
    print("=" * 50)
    
    # Check for required files
    required_files = [
        "table.sql", "percentile_tables.sql", "recipient_lookup_table.sql", 
        "indexes.sql", "process.py", "committee.py", 
        "build_percentile_tables.py", "build_recipient_lookup.py"
    ]
    
    missing_files = [f for f in required_files if not Path(f).exists()]
    if missing_files:
        print(f"❌ Missing required files: {', '.join(missing_files)}")
        return 1
    
    # Check for data directory
    if not args.skip_data and not Path("fec_data").exists():
        print("❌ Data directory 'fec_data' not found")
        print("   Please download and extract FEC data to the 'fec_data' directory")
        return 1
    
    # Remove existing database
    if Path(DB_PATH).exists():
        print(f"🗑️  Removing existing database: {DB_PATH}")
        os.remove(DB_PATH)
    
    # Step 1: Create base tables
    create_base_tables()
    
    if not args.skip_data:
        # Step 2: Process contribution data
        if not run_script("process.py", "Processing contribution data"):
            print("❌ Failed to process contribution data")
            return 1
        
        # Step 3: Load committee information
        if not run_script("committee.py", "Loading committee information"):
            print("❌ Failed to load committee data")
            return 1
    
    # Step 4: Create lookup tables
    create_lookup_tables()
    
    # Step 5: Create indexes
    create_indexes()
    
    if not args.skip_data:
        # Step 6: Build percentile tables
        if not run_script("build_percentile_tables.py", "Building percentile lookup tables"):
            print("⚠️  Percentile tables failed - you can build them later")
        
        # Step 7: Build recipient lookup
        if not run_script("build_recipient_lookup.py", "Building recipient lookup tables"):
            print("⚠️  Recipient lookup failed - you can build it later")
    
    print("\n🎉 Setup complete!")
    print(f"   Database: {DB_PATH}")
    
    if not args.skip_data:
        # Show database stats
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM contributions")
        contrib_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM committees")
        committee_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM recipient_lookup")
        recipient_lookup_count = cursor.fetchone()[0]
        
        print(f"   📊 {contrib_count:,} contributions loaded")
        print(f"   🏛️  {committee_count:,} committees loaded")
        print(f"   🔍 {recipient_lookup_count:,} recipients in lookup table")
        
        conn.close()
    
    print("\n🚀 You can now run the web app with: python3 app.py")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
