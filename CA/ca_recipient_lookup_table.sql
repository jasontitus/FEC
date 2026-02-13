-- California Recipient Lookup Tables
-- Creates tables for fast recipient/committee search with pre-computed statistics

-- Main recipient lookup table with aggregated statistics
CREATE TABLE IF NOT EXISTS ca_recipient_lookup (
    recipient_name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    committee_type TEXT,
    entity_code TEXT,
    
    -- Location information
    city TEXT,
    state TEXT,
    zip_code TEXT,
    
    -- Contact information
    phone TEXT,
    email TEXT,
    
    -- Candidate information (if applicable)
    candidate_last_name TEXT,
    candidate_first_name TEXT,
    office_description TEXT,
    jurisdiction_description TEXT,
    
    -- Aggregated statistics
    total_contributions INTEGER DEFAULT 0,
    total_amount REAL DEFAULT 0.0,
    recent_contributions INTEGER DEFAULT 0,  -- Last 365 days
    recent_amount REAL DEFAULT 0.0,         -- Last 365 days
    first_contribution_date TEXT,
    last_contribution_date TEXT,
    contributor_count INTEGER DEFAULT 0,    -- Unique contributors
    
    -- Metadata
    updated_at TEXT DEFAULT (datetime('now'))
);

-- FTS (Full Text Search) table for fast name searching
CREATE VIRTUAL TABLE IF NOT EXISTS ca_recipient_lookup_fts USING fts5(
    recipient_name,
    display_name,
    candidate_last_name,
    candidate_first_name,
    content='ca_recipient_lookup',
    content_rowid='rowid'
);

-- Triggers to keep FTS table in sync with main table
CREATE TRIGGER IF NOT EXISTS ca_recipient_lookup_fts_insert AFTER INSERT ON ca_recipient_lookup 
BEGIN
    INSERT INTO ca_recipient_lookup_fts(
        recipient_name, display_name, candidate_last_name, candidate_first_name
    ) VALUES (
        new.recipient_name, new.display_name, new.candidate_last_name, new.candidate_first_name
    );
END;

CREATE TRIGGER IF NOT EXISTS ca_recipient_lookup_fts_delete AFTER DELETE ON ca_recipient_lookup 
BEGIN
    DELETE FROM ca_recipient_lookup_fts WHERE recipient_name = old.recipient_name;
END;

CREATE TRIGGER IF NOT EXISTS ca_recipient_lookup_fts_update AFTER UPDATE ON ca_recipient_lookup 
BEGIN
    DELETE FROM ca_recipient_lookup_fts WHERE recipient_name = old.recipient_name;
    INSERT INTO ca_recipient_lookup_fts(
        recipient_name, display_name, candidate_last_name, candidate_first_name
    ) VALUES (
        new.recipient_name, new.display_name, new.candidate_last_name, new.candidate_first_name
    );
END;

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_ca_recipient_lookup_name ON ca_recipient_lookup (display_name);
CREATE INDEX IF NOT EXISTS idx_ca_recipient_lookup_type ON ca_recipient_lookup (committee_type);
CREATE INDEX IF NOT EXISTS idx_ca_recipient_lookup_recent ON ca_recipient_lookup (recent_contributions DESC);
CREATE INDEX IF NOT EXISTS idx_ca_recipient_lookup_total ON ca_recipient_lookup (total_contributions DESC);
CREATE INDEX IF NOT EXISTS idx_ca_recipient_lookup_candidate ON ca_recipient_lookup (candidate_last_name, candidate_first_name);
