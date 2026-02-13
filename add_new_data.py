#!/usr/bin/env python3
"""
Add new FEC data to an existing database.
This script will:
1. Process any new contribution files
2. Update committee information
3. Rebuild lookup tables with new data
4. Update indexes as needed

Usage: python3 add_new_data.py [--rebuild-all]
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
            # Show last few lines of output
            lines = result.stdout.strip().split('\n')
            for line in lines[-3:]:
                if line.strip():
                    print(f"   {line}")
        return True
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - start_time
        print(f"❌ {description} failed after {elapsed:.1f} seconds")
        print(f"   Error: {e.stderr.strip() if e.stderr else str(e)}")
        return False
    except FileNotFoundError:
        print(f"❌ Script {script_name} not found")
        return False

def check_for_new_files():
    """Check if there are any new files to process"""
    print("\n🔍 Checking for new files to process...")
    
    if not Path("fec_data").exists():
        print("❌ Data directory 'fec_data' not found")
        return False, 0
    
    # Count total files vs processed files
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get list of processed files
    cursor.execute("SELECT filename FROM processed_files")
    processed_files = {row[0] for row in cursor.fetchall()}
    
    # Count all data files
    total_files = 0
    new_files = 0
    
    for root, dirs, files in os.walk("fec_data"):
        for file in files:
            if file.endswith(('.txt', '.txt.zst')):
                total_files += 1
                if file not in processed_files:
                    new_files += 1
    
    conn.close()
    
    print(f"   📊 Total data files: {total_files}")
    print(f"   🆕 New files to process: {new_files}")
    print(f"   ✅ Already processed: {len(processed_files)}")
    
    return new_files > 0, new_files

def get_database_stats():
    """Get current database statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    stats = {}
    
    # Contributions count
    cursor.execute("SELECT COUNT(*) FROM contributions")
    stats['contributions'] = cursor.fetchone()[0]
    
    # Committees count
    cursor.execute("SELECT COUNT(*) FROM committees")
    stats['committees'] = cursor.fetchone()[0]
    
    # Recent contributions (last 365 days)
    cursor.execute("""
        SELECT COUNT(*) FROM contributions 
        WHERE contribution_date >= date('now', '-365 days')
    """)
    stats['recent_contributions'] = cursor.fetchone()[0]
    
    # Latest contribution date
    cursor.execute("SELECT MAX(contribution_date) FROM contributions")
    stats['latest_date'] = cursor.fetchone()[0]
    
    # Check if lookup tables exist
    cursor.execute("""
        SELECT COUNT(*) FROM sqlite_master 
        WHERE type='table' AND name='recipient_lookup'
    """)
    stats['has_recipient_lookup'] = cursor.fetchone()[0] > 0
    
    cursor.execute("""
        SELECT COUNT(*) FROM sqlite_master 
        WHERE type='table' AND name='donor_totals_by_year'
    """)
    stats['has_percentile_tables'] = cursor.fetchone()[0] > 0
    
    conn.close()
    return stats

def main():
    """Main update process"""
    parser = argparse.ArgumentParser(description='Add new data to existing FEC database')
    parser.add_argument('--rebuild-all', action='store_true', 
                       help='Rebuild all lookup tables even if no new data')
    parser.add_argument('--force-process', action='store_true',
                       help='Process data even if no new files detected')
    args = parser.parse_args()
    
    print("🔄 FEC Contributions Database - Add New Data")
    print("=" * 50)
    
    # Check if database exists
    if not Path(DB_PATH).exists():
        print(f"❌ Database {DB_PATH} not found")
        print("   Run setup_from_scratch.py first to create the database")
        return 1
    
    # Get initial stats
    print("📊 Current database statistics:")
    initial_stats = get_database_stats()
    print(f"   Contributions: {initial_stats['contributions']:,}")
    print(f"   Committees: {initial_stats['committees']:,}")
    print(f"   Recent contributions (365 days): {initial_stats['recent_contributions']:,}")
    print(f"   Latest contribution date: {initial_stats['latest_date']}")
    
    # Check for new files
    has_new_files, new_file_count = check_for_new_files()
    
    if not has_new_files and not args.force_process:
        print("ℹ️  No new files to process")
        
        if not args.rebuild_all:
            print("   Use --rebuild-all to rebuild lookup tables anyway")
            return 0
        else:
            print("   Proceeding with lookup table rebuild...")
    
    # Process new data if available
    if has_new_files or args.force_process:
        print(f"\n🚀 Processing {new_file_count} new files...")
        
        # Use process_incremental.py if it exists, otherwise process.py
        process_script = "process_incremental.py" if Path("process_incremental.py").exists() else "process.py"
        
        if not run_script(process_script, "Processing new contribution data"):
            print("❌ Failed to process new contribution data")
            return 1
        
        # Update committee information
        if not run_script("committee.py", "Updating committee information"):
            print("⚠️  Committee update failed - continuing anyway")
    
    # Rebuild lookup tables
    needs_rebuild = args.rebuild_all or has_new_files or not initial_stats['has_recipient_lookup']
    
    if needs_rebuild:
        print("\n🔧 Rebuilding lookup tables...")
        
        # Rebuild recipient lookup
        if not run_script("build_recipient_lookup.py", "Rebuilding recipient lookup table"):
            print("⚠️  Recipient lookup rebuild failed")
        
        # Rebuild percentile tables if they exist or if requested
        if initial_stats['has_percentile_tables'] or args.rebuild_all:
            if not run_script("build_percentile_tables.py", "Rebuilding percentile tables"):
                print("⚠️  Percentile tables rebuild failed")
    
    # Get final stats
    print("\n📊 Updated database statistics:")
    final_stats = get_database_stats()
    
    contrib_diff = final_stats['contributions'] - initial_stats['contributions']
    committee_diff = final_stats['committees'] - initial_stats['committees']
    
    print(f"   Contributions: {final_stats['contributions']:,} (+{contrib_diff:,})")
    print(f"   Committees: {final_stats['committees']:,} (+{committee_diff:,})")
    print(f"   Recent contributions (365 days): {final_stats['recent_contributions']:,}")
    print(f"   Latest contribution date: {final_stats['latest_date']}")
    
    if contrib_diff > 0:
        print(f"\n✅ Successfully added {contrib_diff:,} new contributions!")
    
    print("\n🎉 Update complete!")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
