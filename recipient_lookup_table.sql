-- Recipient lookup table for fast fuzzy search
-- This table pre-aggregates recipient data for quick searches

CREATE TABLE IF NOT EXISTS recipient_lookup (
    recipient_name TEXT PRIMARY KEY,          -- committee_id from contributions
    display_name TEXT,                        -- resolved name from committees table
    committee_type TEXT,                      -- from committees table
    total_contributions INTEGER DEFAULT 0,    -- total number of contributions ever
    total_amount REAL DEFAULT 0.0,           -- total amount ever received
    recent_contributions INTEGER DEFAULT 0,   -- contributions in last 365 days
    recent_amount REAL DEFAULT 0.0,          -- amount in last 365 days
    first_contribution_date TEXT,             -- earliest contribution date
    last_contribution_date TEXT,              -- most recent contribution date
    contributor_count INTEGER DEFAULT 0,      -- unique contributor count (first+last+zip)
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for fast searching and sorting
CREATE INDEX IF NOT EXISTS idx_recipient_lookup_display_name ON recipient_lookup (display_name);
CREATE INDEX IF NOT EXISTS idx_recipient_lookup_recent_activity ON recipient_lookup (recent_contributions DESC, recent_amount DESC);
CREATE INDEX IF NOT EXISTS idx_recipient_lookup_total_activity ON recipient_lookup (total_contributions DESC, total_amount DESC);
CREATE INDEX IF NOT EXISTS idx_recipient_lookup_updated ON recipient_lookup (updated_at);

-- FTS table for fuzzy text search
CREATE VIRTUAL TABLE IF NOT EXISTS recipient_lookup_fts USING fts5(
    recipient_name,
    display_name,
    content='recipient_lookup'
);

-- Trigger to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS recipient_lookup_fts_insert AFTER INSERT ON recipient_lookup BEGIN
    INSERT INTO recipient_lookup_fts(recipient_name, display_name) 
    VALUES (new.recipient_name, new.display_name);
END;

CREATE TRIGGER IF NOT EXISTS recipient_lookup_fts_update AFTER UPDATE ON recipient_lookup BEGIN
    UPDATE recipient_lookup_fts 
    SET display_name = new.display_name 
    WHERE recipient_name = new.recipient_name;
END;

CREATE TRIGGER IF NOT EXISTS recipient_lookup_fts_delete AFTER DELETE ON recipient_lookup BEGIN
    DELETE FROM recipient_lookup_fts WHERE recipient_name = old.recipient_name;
END;
