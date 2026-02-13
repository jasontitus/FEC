#!/usr/bin/env python3
"""
Build recipient lookup table for fast fuzzy search.
This script creates a pre-aggregated table of recipient statistics.
"""

import sqlite3
import time
from datetime import datetime, timedelta

DB_PATH = "fec_contributions.db"

def get_recent_date_cutoff():
    """Get date cutoff for 'recent' contributions (365 days ago)"""
    cutoff = datetime.now() - timedelta(days=365)
    return cutoff.strftime('%Y-%m-%d')

def build_recipient_lookup():
    """Build the recipient lookup table with aggregated statistics"""
    print("🚀 Building recipient lookup table...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # First, create the tables
    print("📋 Creating recipient lookup table...")
    with open("recipient_lookup_table.sql", 'r') as f:
        cursor.executescript(f.read())
    
    # Clear existing data
    cursor.execute("DELETE FROM recipient_lookup")
    cursor.execute("DELETE FROM recipient_lookup_fts")
    
    # Get recent date cutoff
    recent_cutoff = get_recent_date_cutoff()
    print(f"📅 Using recent activity cutoff: {recent_cutoff}")
    
    # Build the aggregated data
    print("📊 Aggregating recipient statistics...")
    
    # This query aggregates all the statistics we need in one pass
    aggregation_query = """
        WITH recipient_stats AS (
            SELECT 
                c.recipient_name,
                COALESCE(m.name, c.recipient_name) as display_name,
                COALESCE(m.type, '') as committee_type,
                COUNT(*) as total_contributions,
                SUM(c.amount) as total_amount,
                SUM(CASE WHEN c.contribution_date >= ? THEN 1 ELSE 0 END) as recent_contributions,
                SUM(CASE WHEN c.contribution_date >= ? THEN c.amount ELSE 0 END) as recent_amount,
                MIN(c.contribution_date) as first_contribution_date,
                MAX(c.contribution_date) as last_contribution_date,
                COUNT(DISTINCT c.first_name || '|' || c.last_name || '|' || substr(c.zip_code, 1, 5)) as contributor_count
            FROM contributions c
            LEFT JOIN committees m ON c.recipient_name = m.committee_id
            WHERE c.recipient_name NOT IN (
                'C00401224',  -- ACTBLUE
                'C00694323',  -- WINRED  
                'C00708504',  -- NATIONBUILDER
                'C00580100'   -- REPUBLICAN PLATFORM FUND
            )
            GROUP BY c.recipient_name, display_name, committee_type
        )
        INSERT INTO recipient_lookup (
            recipient_name, display_name, committee_type,
            total_contributions, total_amount,
            recent_contributions, recent_amount,
            first_contribution_date, last_contribution_date,
            contributor_count, updated_at
        )
        SELECT 
            recipient_name, display_name, committee_type,
            total_contributions, total_amount,
            recent_contributions, recent_amount,
            first_contribution_date, last_contribution_date,
            contributor_count, datetime('now')
        FROM recipient_stats
    """
    
    print("⏱️  Executing aggregation query (this may take several minutes)...")
    start_time = time.time()
    
    cursor.execute(aggregation_query, (recent_cutoff, recent_cutoff))
    
    end_time = time.time()
    elapsed = end_time - start_time
    
    # Get count of records inserted
    cursor.execute("SELECT COUNT(*) FROM recipient_lookup")
    record_count = cursor.fetchone()[0]
    
    print(f"✅ Aggregated {record_count:,} recipients in {elapsed:.2f} seconds")
    
    # Commit the changes
    conn.commit()
    
    # Show some sample data
    print("\n📋 Sample recipient data:")
    cursor.execute("""
        SELECT display_name, total_contributions, total_amount, recent_contributions, recent_amount
        FROM recipient_lookup 
        ORDER BY recent_contributions DESC 
        LIMIT 5
    """)
    
    for row in cursor.fetchall():
        display_name, total_contrib, total_amt, recent_contrib, recent_amt = row
        print(f"  {display_name[:50]:<50} | Total: {total_contrib:,} contrib, ${total_amt:,.2f} | Recent: {recent_contrib:,} contrib, ${recent_amt:,.2f}")
    
    print("\n🔍 Top recipients by recent activity:")
    cursor.execute("""
        SELECT display_name, recent_contributions, recent_amount
        FROM recipient_lookup 
        WHERE recent_contributions > 0
        ORDER BY recent_contributions DESC 
        LIMIT 10
    """)
    
    for row in cursor.fetchall():
        display_name, recent_contrib, recent_amt = row
        print(f"  {display_name[:60]:<60} | {recent_contrib:,} contrib, ${recent_amt:,.2f}")
    
    conn.close()
    print("\n🎉 Recipient lookup table built successfully!")

if __name__ == "__main__":
    build_recipient_lookup()
