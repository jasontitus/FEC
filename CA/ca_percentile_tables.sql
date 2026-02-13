-- California Donor Percentile Tables
-- Creates tables for tracking donor contribution totals and percentile rankings

-- Main table for donor totals by year
CREATE TABLE IF NOT EXISTS ca_donor_totals_by_year (
    donor_key TEXT NOT NULL,  -- "first_name|last_name|zip5"
    year INTEGER NOT NULL,
    total_amount REAL NOT NULL,
    contribution_count INTEGER NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    zip5 TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (donor_key, year)
);

-- Table for pre-computed percentile thresholds by year
CREATE TABLE IF NOT EXISTS ca_percentile_thresholds_by_year (
    year INTEGER NOT NULL,
    percentile INTEGER NOT NULL,  -- 1, 5, 10, 25, 50, 75, 90, 95, 99
    amount_threshold REAL NOT NULL,
    donor_count_at_threshold INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (year, percentile)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_ca_donor_totals_year ON ca_donor_totals_by_year (year);
CREATE INDEX IF NOT EXISTS idx_ca_donor_totals_amount ON ca_donor_totals_by_year (year, total_amount DESC);
CREATE INDEX IF NOT EXISTS idx_ca_donor_totals_donor_key ON ca_donor_totals_by_year (donor_key);
CREATE INDEX IF NOT EXISTS idx_ca_percentile_thresholds_year ON ca_percentile_thresholds_by_year (year);
