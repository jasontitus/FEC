# California Campaign Contributions Database

This directory contains a parallel system to the federal FEC contribution tracking, but designed specifically for California campaign finance data from the CalAccess system.

## Overview

The California system processes data from the CalAccess database export and provides the same functionality as the federal system:
- Search contributors by name, location, and other criteria
- View contributor profiles with percentile rankings
- Search recipients/committees by name
- Fast fuzzy search capabilities

## Files

### Data Processing
- `process_ca.py` - Main data processor for CalAccess TSV files
- `build_ca_percentile_tables.py` - Builds donor percentile lookup tables
- `build_ca_recipient_lookup.py` - Builds recipient search tables

### SQL Schemas
- `ca_percentile_tables.sql` - Percentile table schemas
- `ca_recipient_lookup_table.sql` - Recipient lookup table schemas

### Web Application
- `ca_app.py` - Full-featured web application (similar to main FEC app)
- `ca_app_simple.py` - Simplified web application for basic functionality

### Database
- `ca_contributions.db` - SQLite database (created by processing scripts)

## Setup Instructions

### 1. Extract CalAccess Data
First, ensure the CalAccess data has been extracted:
```bash
cd /Users/jasontitus/experiments/FEC/CA
unzip -q dbwebexport.zip
# This creates CalAccess/DATA/ directory with TSV files
```

### 2. Process the Data
Run the main data processor:
```bash
python3 process_ca.py
```

This will:
- Parse `CVR_CAMPAIGN_DISCLOSURE_CD.TSV` for committee information
- Parse `RCPT_CD.TSV` for individual contribution records
- Create and populate the main database tables
- Create indexes for optimal performance

### 3. Build Lookup Tables (Optional but Recommended)

Build percentile tables for donor rankings:
```bash
python3 build_ca_percentile_tables.py
```

Build recipient lookup tables for fast search:
```bash
python3 build_ca_recipient_lookup.py
```

### 4. Run the Web Application

For simple functionality:
```bash
python3 ca_app_simple.py
```

For full functionality (if ca_app.py is complete):
```bash
python3 ca_app.py
```

The app will run on http://127.0.0.1:5001 by default.

## Data Structure

### Contributions Table
- `filing_id` - CalAccess filing ID
- `first_name`, `last_name` - Contributor name
- `city`, `state`, `zip_code` - Contributor location
- `employer`, `occupation` - Contributor employment info
- `contribution_date` - Date of contribution (YYYY-MM-DD format)
- `amount` - Contribution amount
- `recipient_committee_id` - ID of receiving committee

### Committees Table
- `committee_id` - Unique committee identifier
- `name` - Committee/candidate name
- `committee_type` - Type (Candidate, PAC, etc.)
- `entity_code` - CalAccess entity code
- Location and contact information
- Candidate information (if applicable)

## Key Differences from Federal System

1. **Data Source**: CalAccess instead of FEC bulk downloads
2. **File Format**: Tab-separated values (.TSV) instead of pipe-delimited
3. **Date Format**: MM/DD/YYYY format instead of MMDDYYYY
4. **Committee Structure**: California-specific committee types and codes
5. **Default State**: No default state (vs CA default in federal system)

## Performance Notes

- The initial data processing can take 10-30 minutes depending on data size
- Building percentile and lookup tables adds another 5-15 minutes
- The database will be several GB in size for complete CalAccess data
- Indexes are crucial for search performance

## Web Interface Features

- **Contribution Search**: Search by contributor name, location, year
- **Cascading Search**: Automatically drops filters if no results found
- **Contributor Profiles**: View all contributions by an individual
- **Percentile Rankings**: See how donors rank against others (when tables built)
- **Recipient Search**: Find committees and candidates by name
- **Full-Text Search**: Fast fuzzy matching for recipient names

## Color Scheme

The CA web interface uses an orange/red color scheme to distinguish it from the blue federal interface:
- Primary color: `#ff6b35` (Orange-red)
- Accent color: `#f7931e` (Orange)
- Background: Similar neutral grays as federal system

## Port Configuration

- Federal app runs on port 5000
- California app runs on port 5001
- Both can run simultaneously for comparison
