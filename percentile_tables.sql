-- Create percentile lookup tables for donor rankings
-- These tables will be pre-computed for fast lookups

-- Table to store donor total contributions by year
CREATE TABLE IF NOT EXISTS donor_totals_by_year (
    donor_key TEXT,           -- first_name|last_name|zip5
    year INTEGER,
    total_amount REAL,
    contribution_count INTEGER,
    first_name TEXT,          -- Store for easy display
    last_name TEXT,
    zip5 TEXT,
    PRIMARY KEY (donor_key, year)
);

-- Table to store percentile thresholds by year
CREATE TABLE IF NOT EXISTS percentile_thresholds_by_year (
    year INTEGER,
    percentile INTEGER,       -- 1, 5, 10, 25, 50, 75, 90, 95, 99
    amount_threshold REAL,
    donor_count_at_threshold INTEGER,
    PRIMARY KEY (year, percentile)
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_donor_totals_amount ON donor_totals_by_year (year, total_amount);
CREATE INDEX IF NOT EXISTS idx_donor_totals_key ON donor_totals_by_year (donor_key);
CREATE INDEX IF NOT EXISTS idx_percentile_lookup ON percentile_thresholds_by_year (year, amount_threshold);


