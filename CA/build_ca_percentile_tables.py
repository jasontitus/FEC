#!/usr/bin/env python3
"""
Build California donor percentile lookup tables.
This script calculates donor totals by year and pre-computes percentile thresholds.
"""

import os
import sqlite3
import time

# Optional progress bar
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, desc="Processing"):
        print(f"{desc}...")
        return iterable

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "ca_contributions.db")

def build_ca_donor_totals_by_year():
    """Build the ca_donor_totals_by_year table with aggregated contributions."""
    print("🔄 Building California donor totals by year...")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Conservative pragmas for low-memory environments
    cursor.execute('PRAGMA cache_size = -8000;')
    cursor.execute('PRAGMA temp_store = DEFAULT;')

    # Create the tables
    with open(os.path.join(SCRIPT_DIR, 'ca_percentile_tables.sql'), 'r') as f:
        cursor.executescript(f.read())

    # Clear existing data
    cursor.execute("DELETE FROM ca_donor_totals_by_year")
    cursor.execute("DELETE FROM ca_percentile_thresholds_by_year")

    # Build donor totals year-by-year to reduce working set size
    print("📊 Calculating California donor totals by year...")
    start_time = time.time()
    total_records = 0

    for year in range(2000, 2026):
        year_start = f"{year}-01-01"
        year_end = f"{year}-12-31"

        insert_query = """
        INSERT INTO ca_donor_totals_by_year (donor_key, year, total_amount, contribution_count, first_name, last_name, zip5)
        SELECT
            first_name || '|' || last_name || '|' || substr(zip_code, 1, 5) as donor_key,
            CAST(strftime('%Y', contribution_date) as INTEGER) as year,
            SUM(amount) as total_amount,
            COUNT(*) as contribution_count,
            first_name,
            last_name,
            substr(zip_code, 1, 5) as zip5
        FROM contributions
        WHERE contribution_date IS NOT NULL
          AND contribution_date >= ?
          AND contribution_date <= ?
          AND first_name IS NOT NULL
          AND last_name IS NOT NULL
          AND zip_code IS NOT NULL
          AND length(zip_code) >= 5
        GROUP BY donor_key, year
        HAVING total_amount > 0
        """

        cursor.execute(insert_query, (year_start, year_end))
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM ca_donor_totals_by_year WHERE year = ?", (year,))
        year_count = cursor.fetchone()[0]
        total_records += year_count
        if year_count > 0:
            print(f"   {year}: {year_count:,} donor records")

    end_time = time.time()

    print(f"✅ Inserted {total_records:,} California donor-year records in {end_time - start_time:.2f} seconds")
    conn.close()

def build_ca_percentile_thresholds():
    """Calculate and store percentile thresholds for each year."""
    print("🔄 Building California percentile thresholds...")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Get all years with data
    cursor.execute("SELECT DISTINCT year FROM ca_donor_totals_by_year ORDER BY year")
    years = [row[0] for row in cursor.fetchall()]
    
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    
    for year in tqdm(years, desc="Processing years"):
        # Get total donor count for this year
        cursor.execute("SELECT COUNT(*) FROM ca_donor_totals_by_year WHERE year = ?", (year,))
        total_donors = cursor.fetchone()[0]
        
        for percentile in percentiles:
            # Calculate the position for this percentile
            position = int((percentile / 100.0) * total_donors)
            if position == 0:
                position = 1
            
            # Get the amount at this percentile position
            cursor.execute("""
                SELECT total_amount 
                FROM ca_donor_totals_by_year 
                WHERE year = ? 
                ORDER BY total_amount DESC 
                LIMIT 1 OFFSET ?
            """, (year, position - 1))
            
            result = cursor.fetchone()
            if result:
                threshold_amount = result[0]
                
                # Insert the threshold
                cursor.execute("""
                    INSERT OR REPLACE INTO ca_percentile_thresholds_by_year 
                    (year, percentile, amount_threshold, donor_count_at_threshold)
                    VALUES (?, ?, ?, ?)
                """, (year, percentile, threshold_amount, position))
    
    conn.commit()
    
    # Show some sample results
    print("\n📈 Sample California percentile thresholds for 2024:")
    cursor.execute("""
        SELECT percentile, amount_threshold, donor_count_at_threshold 
        FROM ca_percentile_thresholds_by_year 
        WHERE year = 2024 
        ORDER BY percentile
    """)
    
    results = cursor.fetchall()
    if results:
        for row in results:
            percentile, amount, count = row
            print(f"   {percentile:2d}th percentile: ${amount:,.2f} (rank {count:,})")
    else:
        # Try another recent year
        cursor.execute("""
            SELECT year, percentile, amount_threshold, donor_count_at_threshold 
            FROM ca_percentile_thresholds_by_year 
            ORDER BY year DESC, percentile
            LIMIT 10
        """)
        recent_results = cursor.fetchall()
        if recent_results:
            latest_year = recent_results[0][0]
            print(f"\n📈 Sample California percentile thresholds for {latest_year} (latest available):")
            for row in recent_results:
                if row[0] == latest_year:
                    year, percentile, amount, count = row
                    print(f"   {percentile:2d}th percentile: ${amount:,.2f} (rank {count:,})")
        else:
            print("   No percentile data available")
    
    conn.close()

def get_ca_donor_percentile(first_name, last_name, zip5, year):
    """
    Get the percentile ranking for a specific California donor in a given year.
    Returns the percentile (1-100) where higher = better rank.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    donor_key = f"{first_name}|{last_name}|{zip5}"
    
    # Get this donor's total for the year
    cursor.execute("""
        SELECT total_amount 
        FROM ca_donor_totals_by_year 
        WHERE donor_key = ? AND year = ?
    """, (donor_key, year))
    
    result = cursor.fetchone()
    if not result:
        conn.close()
        return None, None
    
    donor_amount = result[0]
    
    # Count how many donors gave more than this donor
    cursor.execute("""
        SELECT COUNT(*) 
        FROM ca_donor_totals_by_year 
        WHERE year = ? AND total_amount > ?
    """, (year, donor_amount))
    
    donors_above = cursor.fetchone()[0]
    
    # Get total donor count for the year
    cursor.execute("""
        SELECT COUNT(*) 
        FROM ca_donor_totals_by_year 
        WHERE year = ?
    """, (year,))
    
    total_donors = cursor.fetchone()[0]
    
    conn.close()
    
    # Calculate percentile (higher percentile = better rank)
    percentile = ((total_donors - donors_above) / total_donors) * 100
    rank = donors_above + 1
    
    return percentile, rank

def show_ca_database_stats():
    """Show statistics about the California database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    print("\n📊 California Database Statistics:")
    
    # Check if main tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contributions'")
    if not cursor.fetchone():
        print("   ❌ Main contributions table not found. Run process_ca.py first.")
        conn.close()
        return
    
    # Total contributions
    cursor.execute("SELECT COUNT(*), SUM(amount) FROM contributions")
    contrib_count, total_amount = cursor.fetchone()
    print(f"   Total contributions: {contrib_count:,}")
    print(f"   Total amount: ${total_amount:,.2f}")
    
    # Date range
    cursor.execute("SELECT MIN(contribution_date), MAX(contribution_date) FROM contributions WHERE contribution_date IS NOT NULL")
    min_date, max_date = cursor.fetchone()
    print(f"   Date range: {min_date} to {max_date}")
    
    # Unique donors
    cursor.execute("""
        SELECT COUNT(DISTINCT first_name || '|' || last_name || '|' || substr(zip_code, 1, 5))
        FROM contributions 
        WHERE first_name IS NOT NULL AND last_name IS NOT NULL AND zip_code IS NOT NULL
    """)
    unique_donors = cursor.fetchone()[0]
    print(f"   Unique donors: {unique_donors:,}")
    
    # Years with data
    cursor.execute("""
        SELECT COUNT(DISTINCT strftime('%Y', contribution_date))
        FROM contributions 
        WHERE contribution_date IS NOT NULL
    """)
    years_count = cursor.fetchone()[0]
    print(f"   Years with data: {years_count}")
    
    conn.close()

if __name__ == "__main__":
    print("🚀 Building California donor percentile lookup tables...")
    
    # Show current database stats
    show_ca_database_stats()
    
    # Step 1: Build donor totals
    build_ca_donor_totals_by_year()
    
    # Step 2: Calculate percentile thresholds
    build_ca_percentile_thresholds()
    
    print("\n🎉 California percentile tables built successfully!")
    
    # Test the lookup function
    print("\n🧪 Testing lookup function...")
    test_percentile, test_rank = get_ca_donor_percentile("JOHN", "SMITH", "90210", 2024)
    if test_percentile:
        print(f"   Sample: JOHN SMITH (90210) in 2024: {test_percentile:.1f}th percentile (rank #{test_rank:,})")
    else:
        print("   No test data found for JOHN SMITH in 90210 for 2024")
