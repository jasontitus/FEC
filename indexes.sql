-- Accelerates search filters
CREATE INDEX IF NOT EXISTS idx_contrib_name_zip ON contributions (first_name, last_name, zip_code);
CREATE INDEX IF NOT EXISTS idx_contrib_zip ON contributions (zip_code);
CREATE INDEX IF NOT EXISTS idx_contrib_date ON contributions (contribution_date);

-- Accelerates recipient lookup and joins
CREATE INDEX IF NOT EXISTS idx_contrib_recipient ON contributions (recipient_name);
CREATE INDEX IF NOT EXISTS idx_cmte_id ON committees (committee_id);
