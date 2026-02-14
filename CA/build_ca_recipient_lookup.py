#!/usr/bin/env python3
"""
Build California recipient lookup table for fast fuzzy search.
This script creates a pre-aggregated table of California recipient statistics.
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "ca_contributions.db")

def get_recent_date_cutoff():
    """Get date cutoff for 'recent' contributions (365 days ago)"""
    cutoff = datetime.now() - timedelta(days=365)
    return cutoff.strftime('%Y-%m-%d')

def build_ca_recipient_lookup():
    """Build the California recipient lookup table with aggregated statistics"""
    print("🚀 Building California recipient lookup table...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Conservative pragmas for low-memory environments
    cursor.execute('PRAGMA cache_size = -8000;')
    cursor.execute('PRAGMA temp_store = DEFAULT;')

    # First, create the tables
    print("📋 Creating California recipient lookup table...")
    with open(os.path.join(SCRIPT_DIR, "ca_recipient_lookup_table.sql"), 'r') as f:
        cursor.executescript(f.read())

    # Clear existing data
    cursor.execute("DELETE FROM ca_recipient_lookup")
    cursor.execute("DELETE FROM ca_recipient_lookup_fts")

    # Get recent date cutoff
    recent_cutoff = get_recent_date_cutoff()
    print(f"📅 Using recent activity cutoff: {recent_cutoff}")

    # Build the aggregated data in batches by recipient_committee_id prefix
    print("📊 Aggregating California recipient statistics in batches...")

    # CA recipient_committee_id values are numeric filer IDs, so batch by first digit
    # Also handle alphabetic IDs just in case
    prefix_ranges = [
        ('0', '2'),  # 0-1
        ('2', '4'),  # 2-3
        ('4', '6'),  # 4-5
        ('6', '8'),  # 6-7
        ('8', 'A'),  # 8-9
        ('A', 'N'),  # A-M
        ('N', '['),  # N-Z
    ]

    start_time = time.time()
    total_inserted = 0

    for range_start, range_end in prefix_ranges:
        aggregation_query = """
            WITH recipient_stats AS (
                SELECT
                    c.recipient_committee_id as recipient_name,
                    COALESCE(cm.name, c.recipient_committee_id) as display_name,
                    COALESCE(cm.committee_type, '') as committee_type,
                    COALESCE(cm.entity_code, '') as entity_code,
                    cm.city,
                    cm.state,
                    cm.zip_code,
                    cm.phone,
                    cm.email,
                    cm.candidate_last_name,
                    cm.candidate_first_name,
                    cm.office_description,
                    cm.jurisdiction_description,
                    COUNT(*) as total_contributions,
                    SUM(c.amount) as total_amount,
                    SUM(CASE WHEN c.contribution_date >= ? THEN 1 ELSE 0 END) as recent_contributions,
                    SUM(CASE WHEN c.contribution_date >= ? THEN c.amount ELSE 0 END) as recent_amount,
                    MIN(c.contribution_date) as first_contribution_date,
                    MAX(c.contribution_date) as last_contribution_date,
                    COUNT(DISTINCT c.first_name || '|' || c.last_name || '|' || substr(c.zip_code, 1, 5)) as contributor_count
                FROM contributions c
                LEFT JOIN committees cm ON c.recipient_committee_id = cm.committee_id
                WHERE c.recipient_committee_id IS NOT NULL
                  AND c.recipient_committee_id != ''
                  AND c.recipient_committee_id >= ? AND c.recipient_committee_id < ?
                GROUP BY c.recipient_committee_id, display_name, committee_type, cm.entity_code,
                         cm.city, cm.state, cm.zip_code, cm.phone, cm.email,
                         cm.candidate_last_name, cm.candidate_first_name,
                         cm.office_description, cm.jurisdiction_description
            )
            INSERT INTO ca_recipient_lookup (
                recipient_name, display_name, committee_type, entity_code,
                city, state, zip_code, phone, email,
                candidate_last_name, candidate_first_name,
                office_description, jurisdiction_description,
                total_contributions, total_amount,
                recent_contributions, recent_amount,
                first_contribution_date, last_contribution_date,
                contributor_count, updated_at
            )
            SELECT
                recipient_name, display_name, committee_type, entity_code,
                city, state, zip_code, phone, email,
                candidate_last_name, candidate_first_name,
                office_description, jurisdiction_description,
                total_contributions, total_amount,
                recent_contributions, recent_amount,
                first_contribution_date, last_contribution_date,
                contributor_count, datetime('now')
            FROM recipient_stats
        """

        cursor.execute(aggregation_query, (recent_cutoff, recent_cutoff, range_start, range_end))
        conn.commit()
        batch_count = cursor.rowcount
        total_inserted += batch_count
        print(f"   Batch {range_start}-{chr(ord(range_end)-1)}: {batch_count:,} recipients")

    end_time = time.time()
    elapsed = end_time - start_time

    # Get count of records inserted
    cursor.execute("SELECT COUNT(*) FROM ca_recipient_lookup")
    record_count = cursor.fetchone()[0]

    print(f"✅ Aggregated {record_count:,} California recipients in {elapsed:.2f} seconds")

    # Commit the changes
    conn.commit()
    
    # Show some sample data
    print("\n📋 Sample California recipient data:")
    cursor.execute("""
        SELECT display_name, committee_type, total_contributions, total_amount, 
               recent_contributions, recent_amount
        FROM ca_recipient_lookup 
        ORDER BY recent_contributions DESC 
        LIMIT 5
    """)
    
    for row in cursor.fetchall():
        display_name, cmte_type, total_contrib, total_amt, recent_contrib, recent_amt = row
        print(f"  {display_name[:45]:<45} | {cmte_type[:12]:<12} | Total: {total_contrib:,} contrib, ${total_amt:,.2f} | Recent: {recent_contrib:,} contrib, ${recent_amt:,.2f}")
    
    print("\n🔍 Top California recipients by recent activity:")
    cursor.execute("""
        SELECT display_name, committee_type, recent_contributions, recent_amount
        FROM ca_recipient_lookup 
        WHERE recent_contributions > 0
        ORDER BY recent_contributions DESC 
        LIMIT 10
    """)
    
    for row in cursor.fetchall():
        display_name, cmte_type, recent_contrib, recent_amt = row
        print(f"  {display_name[:50]:<50} | {cmte_type[:15]:<15} | {recent_contrib:,} contrib, ${recent_amt:,.2f}")
    
    print("\n🏛️  Top California candidates by total contributions:")
    cursor.execute("""
        SELECT display_name, candidate_first_name || ' ' || candidate_last_name as candidate_name,
               office_description, total_contributions, total_amount
        FROM ca_recipient_lookup 
        WHERE candidate_last_name IS NOT NULL AND candidate_last_name != ''
        ORDER BY total_contributions DESC 
        LIMIT 10
    """)
    
    for row in cursor.fetchall():
        display_name, candidate_name, office, total_contrib, total_amt = row
        print(f"  {candidate_name[:25]:<25} | {office[:20]:<20} | {total_contrib:,} contrib, ${total_amt:,.2f}")
    
    conn.close()
    print("\n🎉 California recipient lookup table built successfully!")

def show_ca_recipient_stats():
    """Show statistics about the California recipient lookup table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("\n📊 California Recipient Lookup Statistics:")
    
    # Check if lookup table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ca_recipient_lookup'")
    if not cursor.fetchone():
        print("   ❌ Recipient lookup table not found. Run this script first.")
        conn.close()
        return
    
    # Total recipients
    cursor.execute("SELECT COUNT(*) FROM ca_recipient_lookup")
    total_recipients = cursor.fetchone()[0]
    print(f"   Total recipients: {total_recipients:,}")
    
    # Recipients with recent activity
    cursor.execute("SELECT COUNT(*) FROM ca_recipient_lookup WHERE recent_contributions > 0")
    active_recipients = cursor.fetchone()[0]
    print(f"   Recipients with recent activity: {active_recipients:,}")
    
    # Breakdown by committee type
    print("\n   📋 Breakdown by committee type:")
    cursor.execute("""
        SELECT committee_type, COUNT(*), SUM(total_contributions), SUM(total_amount)
        FROM ca_recipient_lookup 
        WHERE committee_type IS NOT NULL AND committee_type != ''
        GROUP BY committee_type 
        ORDER BY COUNT(*) DESC
    """)
    
    for row in cursor.fetchall():
        cmte_type, count, total_contribs, total_amount = row
        print(f"     {cmte_type[:20]:<20} | {count:>6,} recipients | {total_contribs:>8,} contrib | ${total_amount:>15,.2f}")
    
    # Check FTS table
    cursor.execute("SELECT COUNT(*) FROM ca_recipient_lookup_fts")
    fts_count = cursor.fetchone()[0]
    print(f"\n   Full-text search entries: {fts_count:,}")
    
    conn.close()

if __name__ == "__main__":
    print("🚀 Building California recipient lookup tables...")
    
    # Show current database stats first
    show_ca_recipient_stats()
    
    # Build the lookup table
    build_ca_recipient_lookup()
    
    # Show final stats
    show_ca_recipient_stats()
