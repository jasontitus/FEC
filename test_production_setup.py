#!/usr/bin/env python3
"""
Test script to verify production setup is working correctly
"""

import os
import sqlite3
import sys

def test_database_connection(db_path, db_name):
    """Test database connection and basic queries"""
    print(f"🔍 Testing {db_name} database...")
    
    if not os.path.exists(db_path):
        print(f"❌ {db_name} database not found at {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Test basic query
        cursor.execute("SELECT COUNT(*) FROM contributions")
        count = cursor.fetchone()[0]
        print(f"✅ {db_name} database connected - {count:,} contributions")
        
        # Test recent data
        cursor.execute("SELECT MAX(contribution_date) FROM contributions WHERE contribution_date IS NOT NULL")
        latest_date = cursor.fetchone()[0]
        print(f"   📅 Latest contribution date: {latest_date}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error testing {db_name} database: {e}")
        return False

def test_file_exists(file_path, description):
    """Test if a file exists"""
    if os.path.exists(file_path):
        print(f"✅ {description}: {file_path}")
        return True
    else:
        print(f"❌ {description} not found: {file_path}")
        return False

def main():
    print("🧪 Testing Production Setup")
    print("=" * 40)
    
    # Test main FEC database
    fec_db_ok = test_database_connection("fec_contributions.db", "FEC")
    
    # Test CA database
    ca_db_ok = test_database_connection("CA/ca_contributions.db", "CA")
    
    # Test key files
    files_to_test = [
        ("app.py", "Main FEC application"),
        ("unified_app.py", "Unified application"),
        ("process_incremental.py", "Incremental processing script"),
        ("start_apps.sh", "Startup script"),
        ("CA/ca_app_simple.py", "CA application"),
        ("2025/indiv26/itcont.txt", "2025 data file")
    ]
    
    files_ok = True
    for file_path, description in files_to_test:
        if not test_file_exists(file_path, description):
            files_ok = False
    
    print("\n📊 Summary:")
    print(f"   FEC Database: {'✅ OK' if fec_db_ok else '❌ Issues'}")
    print(f"   CA Database: {'✅ OK' if ca_db_ok else '❌ Issues'}")
    print(f"   Key Files: {'✅ OK' if files_ok else '❌ Issues'}")
    
    if fec_db_ok and files_ok:
        print("\n🎉 Production setup looks good!")
        print("   You can now run: ./start_apps.sh")
    else:
        print("\n⚠️  Some issues found. Please check the errors above.")
    
    return fec_db_ok and files_ok

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
