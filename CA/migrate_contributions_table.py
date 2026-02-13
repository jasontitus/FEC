#!/usr/bin/env python3
"""
Migration script to add candidate columns to existing contributions table
"""

import sqlite3

def migrate_contributions_table():
    """Add new candidate columns to existing contributions table."""
    
    conn = sqlite3.connect('ca_contributions.db')
    cursor = conn.cursor()
    
    # Check if columns already exist
    cursor.execute("PRAGMA table_info(contributions)")
    columns = [column[1] for column in cursor.fetchall()]
    
    # Add missing columns one by one
    new_columns = [
        'candidate_last_name TEXT',
        'candidate_first_name TEXT', 
        'office_description TEXT',
        'jurisdiction_description TEXT'
    ]
    
    for column_def in new_columns:
        column_name = column_def.split()[0]
        if column_name not in columns:
            try:
                cursor.execute(f"ALTER TABLE contributions ADD COLUMN {column_def}")
                print(f"✅ Added column: {column_name}")
            except Exception as e:
                print(f"❌ Error adding column {column_name}: {e}")
        else:
            print(f"⏩ Column {column_name} already exists")
    
    conn.commit()
    conn.close()
    print("🎯 Migration complete!")

if __name__ == "__main__":
    migrate_contributions_table()
