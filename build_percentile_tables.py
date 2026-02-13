#!/usr/bin/env python3
"""
Build percentile lookup tables for donor contributions.
This script calculates donor totals by year and pre-computes percentile thresholds.
"""

import sqlite3
import time

# Optional progress bar
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, desc="Processing"):
        print(f"{desc}...")
        return iterable

DB_FILE = "fec_contributions.db"

def build_donor_totals_by_year():
    """Build the donor_totals_by_year table with aggregated contributions."""
    print("🔄 Building donor totals by year...")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create the tables
    with open('percentile_tables.sql', 'r') as f:
        cursor.executescript(f.read())
    
    # Clear existing data
    cursor.execute("DELETE FROM donor_totals_by_year")
    cursor.execute("DELETE FROM percentile_thresholds_by_year")
    
    # Build donor totals - this groups by proper donor identification
    print("📊 Calculating donor totals by year...")
    insert_query = """
    INSERT INTO donor_totals_by_year (donor_key, year, total_amount, contribution_count, first_name, last_name, zip5)
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
      AND contribution_date >= '2015-01-01' 
      AND contribution_date <= '2025-12-31'
      AND first_name IS NOT NULL 
      AND last_name IS NOT NULL
      AND zip_code IS NOT NULL
    GROUP BY donor_key, year
    HAVING total_amount > 0  -- Only positive total contributions
    """
    
    start_time = time.time()
    cursor.execute(insert_query)
    conn.commit()
    end_time = time.time()
    
    # Get count of records inserted
    cursor.execute("SELECT COUNT(*) FROM donor_totals_by_year")
    total_records = cursor.fetchone()[0]
    
    print(f"✅ Inserted {total_records:,} donor-year records in {end_time - start_time:.2f} seconds")
    conn.close()

def build_percentile_thresholds():
    """Calculate and store percentile thresholds for each year."""
    print("🔄 Building percentile thresholds...")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Get all years with data
    cursor.execute("SELECT DISTINCT year FROM donor_totals_by_year ORDER BY year")
    years = [row[0] for row in cursor.fetchall()]
    
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    
    for year in tqdm(years, desc="Processing years"):
        # Get total donor count for this year
        cursor.execute("SELECT COUNT(*) FROM donor_totals_by_year WHERE year = ?", (year,))
        total_donors = cursor.fetchone()[0]
        
        for percentile in percentiles:
            # Calculate the position for this percentile
            position = int((percentile / 100.0) * total_donors)
            if position == 0:
                position = 1
            
            # Get the amount at this percentile position
            cursor.execute("""
                SELECT total_amount 
                FROM donor_totals_by_year 
                WHERE year = ? 
                ORDER BY total_amount DESC 
                LIMIT 1 OFFSET ?
            """, (year, position - 1))
            
            result = cursor.fetchone()
            if result:
                threshold_amount = result[0]
                
                # Insert the threshold
                cursor.execute("""
                    INSERT OR REPLACE INTO percentile_thresholds_by_year 
                    (year, percentile, amount_threshold, donor_count_at_threshold)
                    VALUES (?, ?, ?, ?)
                """, (year, percentile, threshold_amount, position))
    
    conn.commit()
    
    # Show some sample results
    print("\n📈 Sample percentile thresholds for 2024:")
    cursor.execute("""
        SELECT percentile, amount_threshold, donor_count_at_threshold 
        FROM percentile_thresholds_by_year 
        WHERE year = 2024 
        ORDER BY percentile
    """)
    
    for row in cursor.fetchall():
        percentile, amount, count = row
        print(f"   {percentile:2d}th percentile: ${amount:,.2f} (rank {count:,})")
    
    conn.close()

def get_donor_percentile(first_name, last_name, zip5, year):
    """
    Get the percentile ranking for a specific donor in a given year.
    Returns the percentile (1-100) where higher = better rank.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    donor_key = f"{first_name}|{last_name}|{zip5}"
    
    # Get this donor's total for the year
    cursor.execute("""
        SELECT total_amount 
        FROM donor_totals_by_year 
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
        FROM donor_totals_by_year 
        WHERE year = ? AND total_amount > ?
    """, (year, donor_amount))
    
    donors_above = cursor.fetchone()[0]
    
    # Get total donor count for the year
    cursor.execute("""
        SELECT COUNT(*) 
        FROM donor_totals_by_year 
        WHERE year = ?
    """, (year,))
    
    total_donors = cursor.fetchone()[0]
    
    conn.close()
    
    # Calculate percentile (higher percentile = better rank)
    percentile = ((total_donors - donors_above) / total_donors) * 100
    rank = donors_above + 1
    
    return percentile, rank

if __name__ == "__main__":
    print("🚀 Building FEC donor percentile lookup tables...")
    
    # Step 1: Build donor totals
    build_donor_totals_by_year()
    
    # Step 2: Calculate percentile thresholds
    build_percentile_thresholds()
    
    print("\n🎉 Percentile tables built successfully!")
    
    # Test the lookup function
    print("\n🧪 Testing lookup function...")
    test_percentile, test_rank = get_donor_percentile("JOHN", "SMITH", "90210", 2024)
    if test_percentile:
        print(f"   Sample: JOHN SMITH (90210) in 2024: {test_percentile:.1f}th percentile (rank #{test_rank:,})")
    else:
        print("   No test data found for JOHN SMITH in 90210")
