# FEC & California Campaign Finance Database & Search Applications

A comprehensive suite of web applications for searching and analyzing campaign contribution data from both Federal Election Commission (FEC) and California CalAccess systems. These applications process raw data files and provide fast, user-friendly search capabilities with advanced features like donor percentile rankings, recipient lookup tables, and integrated Google search.

## Applications Overview

### 🇺🇸 **National FEC Application** (`app.py`)
- **Database**: `fec_contributions.db` - Federal campaign contributions
- **URL**: `http://localhost:5000`
- **Data Source**: FEC bulk downloads (all US federal campaigns)
- **Coverage**: Presidential, Senate, House campaigns and PACs nationwide

### 🏛️ **California Application** (`CA/ca_app_simple.py`)
- **Database**: `CA/ca_contributions.db` - California state campaigns  
- **URL**: `http://localhost:5001`
- **Data Source**: CalAccess database exports
- **Coverage**: California state and local campaigns

## Core Features (Both Applications)

### 🔍 **Advanced Search Capabilities**
- **Contribution Search**: Search by contributor name, location, year, amount ranges
- **Cascading Search Logic**: Automatically relaxes filters to find results (drops ZIP, then City+ZIP)
- **Recipient Search**: Fuzzy text search of donation recipients with activity sorting
- **Real-time Filtering**: Sort by date, amount, recent activity
- **Smart Defaults**: CA app defaults to California when no state specified

### 👤 **Comprehensive Person Profiles**
- **Recent Contributions**: Latest donation activity with recipient details
- **Total Giving**: Lifetime contribution totals with filtering options
- **Donor Percentile Rankings**: Annual rankings among all donors (when percentile tables built)
- **Multiple Google Search Integration**: 
  - Name + Address search
  - Name + Phone search (with number normalization)
  - Name + Email search
  - Name + City search
- **Cascade Messaging**: Clear explanation of which search filters were relaxed

### 🏢 **Recipient Analysis**
- **Top Contributors**: Ranked list of biggest donors to any committee/campaign
- **Activity Statistics**: Recent (365 days) vs all-time contribution totals
- **Committee Details**: Type, total raised, contributor counts
- **Pagination**: Handle large contributor lists efficiently

### 🎯 **Smart Search Features**
- **Conduit Filtering**: Excludes passthrough platforms (ActBlue, WinRed) from searches
- **Address Normalization**: ZIP code standardization and partial matching  
- **State Defaults**: Intelligent state filtering (CA default for California app)
- **Cross-References**: Links between contributors, recipients, and detailed profiles

### ⚡ **Performance Optimization**
- **Lookup Tables**: Pre-aggregated recipient statistics for instant search
- **Database Indexes**: Optimized for name, location, date, and amount queries
- **Percentile Pre-calculation**: Annual donor rankings computed in advance
- **Sub-second Response Times**: Most queries complete in under 500ms

## Quick Start

### 🇺🇸 **National FEC Application Setup**

```bash
# Clone or download this repository
# Ensure you have the FEC data files in a 'fec_data' directory

# Run the complete setup (this will take several hours)
python3 setup_from_scratch.py

# Build percentile tables for donor rankings
python3 build_percentile_tables.py

# Start the web application
python3 app.py
# Open http://localhost:5000
```

### 🏛️ **California Application Setup**

```bash
# Ensure you have CalAccess data in 'CA/' directory
cd CA

# Process California data (see CA setup instructions below)
python3 process_ca.py

# Build CA-specific lookup tables
python3 build_ca_recipient_lookup.py

# Build CA percentile tables for donor rankings  
python3 build_ca_percentile_tables.py

# Start the California web application
python3 ca_app_simple.py
# Open http://localhost:5001
```

### 🔄 **Adding New Data to Existing Databases**

```bash
# National FEC data updates
python3 add_new_data.py

# California data updates (from CA/ directory)
cd CA && python3 process_ca.py --incremental

# Force rebuild all lookup tables
python3 add_new_data.py --rebuild-all
```

## Detailed Setup Instructions

### Prerequisites

- Python 3.6 or later
- SQLite3
- Required Python packages: `flask`, `sqlite3` (built-in)
- Sufficient disk space (see requirements below)

### System Requirements

- **Disk Space**: 50-100GB minimum (depends on data coverage)
- **RAM**: 8GB+ recommended for processing
- **Time**: Initial setup takes 3-8 hours depending on system and data size

### Data Download

1. Download FEC data from the official bulk download site:
   ```bash
   # Individual contributions data by election cycle
   wget https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74.s3-us-gov-west-1.amazonaws.com/bulk-downloads/2024/indiv24.zip
   wget https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74.s3-us-gov-west-1.amazonaws.com/bulk-downloads/2022/indiv22.zip
   # ... download other years as needed
   
   # Committee data by election cycle
   wget https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74.s3-us-gov-west-1.amazonaws.com/bulk-downloads/2024/cm24.zip
   wget https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74.s3-us-gov-west-1.amazonaws.com/bulk-downloads/2022/cm22.zip
   # ... download other years as needed
   ```

2. Extract data files and organize by election cycle:
   ```
   fec_data/
   ├── 2023-2024/
   │   ├── indiv24.zip
   │   ├── cm24.zip
   │   └── by_date/
   │       └── (extracted contribution files)
   ├── 2021-2022/
   │   ├── indiv22.zip
   │   ├── cm22.zip
   │   └── by_date/
   │       └── (extracted contribution files)
   └── ... (other election cycles)
   ```

### Setup Process

The setup process involves several stages:

1. **Database Creation**: Creates SQLite database with optimized schema
2. **Data Processing**: Imports contribution data from all FEC files
3. **Committee Loading**: Imports committee/recipient information
4. **Index Creation**: Creates database indexes for fast querying
5. **Lookup Tables**: Builds pre-aggregated tables for instant search
6. **Percentile Calculation**: Calculates donor ranking statistics

#### Stage 1: Create Tables Only (Quick)

```bash
# Create just the database structure without data
python3 setup_from_scratch.py --skip-data
```

#### Stage 2: Full Setup with Data

```bash
# Complete setup including all data processing
python3 setup_from_scratch.py
```

## Database Schema

### Core Tables

- **`contributions`**: Main contribution records
  - Individual contributor information (name, address)
  - Recipient information (committee ID)
  - Contribution details (amount, date)

- **`committees`**: Committee/recipient lookup
  - Committee ID, name, and type
  - Enables recipient name resolution

### Performance Tables

- **`recipient_lookup`**: Fast recipient search
  - Pre-aggregated recipient statistics
  - Total and recent contribution counts/amounts
  - Full-text search capabilities

- **`donor_totals_by_year`**: Donor percentile rankings
  - Annual contribution totals by donor
  - Enables percentile calculations

- **`percentile_thresholds_by_year`**: Percentile lookup
  - Pre-calculated percentile thresholds
  - Fast donor ranking queries

## Application Usage

### Starting the Applications

```bash
# National FEC Application (Port 5000)
python3 app.py                    # Local: http://127.0.0.1:5000
python3 app.py --public          # Network: http://0.0.0.0:5000 (⚠️ Testing only)

# Unified Application (Port 5000, recommended)
python3 unified_app.py                          # Local, default FEC
python3 unified_app.py --default-db ca          # Local, default CA
python3 unified_app.py --public --port 8080     # Network, custom port

# California Application (Port 5001)
cd CA
python3 ca_app_simple.py         # Local: http://127.0.0.1:5001
python3 ca_app_simple.py --public # Network: http://0.0.0.0:5001 (⚠️ Testing only)
```

### Debug Mode

Debug mode is **on by default** when running on localhost (i.e. without `--public`). When debug mode is active, every incoming HTTP request is logged to the console with its method, path, status code, and response time in milliseconds. Flask's auto-reloader is also enabled so code changes take effect without restarting.

```bash
# Debug mode is on automatically for localhost
python3 app.py
python3 unified_app.py
python3 CA/ca_app_simple.py

# Explicitly enable debug mode (useful combined with --public for network debugging)
python3 app.py --debug
python3 unified_app.py --debug --public
python3 CA/ca_app_simple.py --debug --public
```

Example log output in debug mode:
```
2026-02-15 10:23:01,234 [DEBUG] fec.requests: GET / 200 - 142.3ms
2026-02-15 10:23:05,678 [DEBUG] fec.requests: GET /api/search?last_name=SMITH 200 - 87.1ms
2026-02-15 10:23:06,012 [DEBUG] fec.requests: GET /contributor?first_name=JOHN&last_name=SMITH 200 - 203.5ms
```

| Flag | Effect |
|------|--------|
| *(no flags)* | Localhost, debug **on** (logging + auto-reload) |
| `--public` | Network-accessible, debug **off** |
| `--debug` | Force debug **on** (even with `--public`) |
| `--public --debug` | Network-accessible with debug logging enabled |

### 🔍 **Main Search Features (Both Apps)**

#### 1. **Contribution Search** (`/`)
**Search individual contributions with smart filtering**
- **Name Search**: First name, last name (exact match, case-insensitive)
- **Location Filtering**: ZIP code (prefix matching), city, state  
- **Temporal Filtering**: Year (4-digit format)
- **Sorting Options**: Date (newest/oldest), Amount (highest/lowest)
- **Cascading Logic**: 
  1. Try all provided filters
  2. If no results, drop ZIP code
  3. If still no results, drop City + ZIP code
- **State Defaults**: CA app defaults to California when no state specified
- **Results Display**: Contributor name (linked to profile), date, recipient (linked to details), amount, location

#### 2. **Recipient Search** (`/search_recipients`)
**Find committees, campaigns, and organizations**
- **Fuzzy Text Search**: Partial name matching with FTS (Full-Text Search)
- **Smart Sorting**: Recent Activity (365 days), Total Activity (all-time), Alphabetical
- **Activity Metrics**: 
  - Recent contributions count and total amount
  - All-time contributions count and total amount  
  - Last contribution date
- **Fast Performance**: Uses pre-built lookup tables for instant results
- **Google Integration**: Info links for additional research

#### 3. **Person Search** (`/personsearch`)
**Comprehensive individual lookup with multiple data sources**
- **Required Fields**: First name, last name
- **Optional Enhancement**: Street address, city, state, ZIP, phone, email
- **Contribution Analysis**: 
  - Recent contributions with recipient details
  - Total giving amounts with cascade messaging
  - Location-based filtering for disambiguation
- **Integrated Google Search**: 
  - **Name + Address**: Real estate, public records
  - **Name + Phone**: Social media, business listings (auto-formats phone numbers)
  - **Name + Email**: Professional profiles, social networks
  - **Name + City**: Local news, community involvement
- **Embedded Results**: Google search results displayed in iframes

### 📊 **Profile Pages**

#### 4. **Contributor View** (`/contributor`)
**Detailed donor analysis and history**
- **Contribution History**: Paginated list of all donations (50 per page)
- **Total Giving**: Lifetime contribution amounts with filter respect
- **Percentile Rankings**: Annual donor rankings when percentile tables available
  - Percentile score (higher = better rank)
  - Exact rank among all donors  
  - Contribution count and totals by year
- **Location Filtering**: Filter by city, state, ZIP for disambiguation
- **Advanced Sorting**: Date, amount (ascending/descending)
- **Recipient Links**: Direct access to committee/campaign details

#### 5. **Recipient View** (`/recipient`)
**Committee/campaign contributor analysis**
- **Top Contributors**: Ranked list of biggest individual donors
- **All-time Totals**: Lifetime giving to this recipient
- **Pagination**: Handle large contributor lists (50 per page)
- **Contributor Profiles**: Links to individual donor histories
- **Google Research**: Info links for additional recipient information

### 🎯 **Advanced Features**

#### **Cascading Search Logic**
Both applications implement intelligent search relaxation:
1. **Initial Search**: All provided criteria (name, city, state, ZIP, year)
2. **ZIP Relaxation**: If no results, remove ZIP code filter  
3. **Location Relaxation**: If still no results, remove city and ZIP filters
4. **Clear Messaging**: Users see which filters were relaxed to find results

#### **Conduit Filtering**
- **Automatic Exclusion**: ActBlue, WinRed, and other passthrough platforms
- **Ultimate Recipients**: Shows final destination of contributions
- **Toggle Option**: Some views allow including/excluding passthroughs

#### **State Intelligence**  
- **National App**: No state defaults, searches all states
- **California App**: Defaults to CA when no state specified
- **Override Capability**: Users can specify any state in both apps

#### **Phone Number Normalization**
- **Input Flexibility**: Accepts various formats (spaces, dashes, parentheses)
- **11-digit Handling**: Removes leading "1" from US numbers
- **Standardization**: Converts to XXX-XXX-XXXX format for search

## Performance & Optimization

### Query Performance

- **Recipient Search**: ~50ms (uses pre-aggregated lookup table)
- **Contribution Search**: 100-500ms (indexed queries)
- **Contributor Analysis**: 200-1000ms (depends on result size)

### Database Indexes

Key indexes for performance:
- Contributor name + location combinations
- Recipient ID lookups
- Date range queries
- Amount-based sorting

### Lookup Tables

Pre-computed aggregations eliminate expensive queries:
- **Recipient statistics**: Avoids scanning 200M+ contribution records
- **Donor percentiles**: Pre-calculated rankings for instant display

## Automated Updates

Both data sources are updated automatically via weekly cron jobs. Updates check for changes before downloading.

### Update Scripts

| Script | Purpose | Schedule |
|--------|---------|----------|
| `update_fec.py` | Download & process FEC bulk data | Sundays 2:00 AM |
| `CA/update_calaccess.py` | Download & rebuild CalAccess DB | Wednesdays 3:00 AM |
| `update_all.py` | Run both updates sequentially | Manual |

### Usage

```bash
# Check for FEC updates (no download)
python3 update_fec.py --dry-run

# Force FEC update regardless of change detection
python3 update_fec.py --force

# Update all cycles (not just current)
python3 update_fec.py --all-cycles

# Check for CalAccess updates
python3 CA/update_calaccess.py --dry-run

# Run both updates
python3 update_all.py --dry-run
python3 update_all.py --force

# Run only one source
python3 update_all.py --fec-only
python3 update_all.py --ca-only
```

### Cron Setup

Install automated weekly cron jobs:

```bash
bash setup_cron.sh
```

This installs:
- **FEC:** Sundays at 2:00 AM
- **CalAccess:** Wednesdays at 3:00 AM

Verify with `crontab -l`. Logs are written to `logs/`.

### Manual Data Updates

```bash
# Rebuild lookup and performance tables manually
python3 build_recipient_lookup.py
python3 build_percentile_tables.py
```

## JSON API

All UI search features are available as JSON API endpoints for programmatic access. See **[API_REFERENCE.md](API_REFERENCE.md)** for complete documentation.

### Quick Examples

```bash
# Search contributions
curl "http://localhost:5000/api/search?last_name=SMITH&state=CA&year=2024"

# Get contributor profile with percentiles
curl "http://localhost:5000/api/contributor?first_name=JOHN&last_name=SMITH&zip_code=90210"

# Search recipients by name
curl "http://localhost:5000/api/search_recipients?q=democratic"

# Get recipient details and top contributors
curl "http://localhost:5000/api/recipient?committee_id=C00703975"

# Person search
curl "http://localhost:5000/api/person?first_name=JOHN&last_name=SMITH"
```

### Available Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/search` | Search contributions by name, location, year |
| `/api/contributor` | Contributor profile with percentile rankings |
| `/api/recipient` | Recipient details with top contributors |
| `/api/search_recipients` | Search committees/campaigns by name |
| `/api/person` | Person search with cascading logic |
| `/api/contributions_by_person` | Quick person lookup (legacy) |

## Maintenance

### Database Maintenance

```bash
# Check database size and statistics
sqlite3 fec_contributions.db "SELECT COUNT(*) FROM contributions"
sqlite3 fec_contributions.db "SELECT COUNT(*) FROM committees"
sqlite3 fec_contributions.db "SELECT COUNT(*) FROM recipient_lookup"

# Vacuum database to reclaim space
sqlite3 fec_contributions.db "VACUUM"
```

## Application Cross-Links

The applications include intelligent cross-linking that preserves search parameters when switching between federal and California data:

### 🔗 **Preserved Search Context**
- **Name searches**: First/last name parameters automatically mapped
- **Location filters**: City, state, ZIP code preserved where applicable  
- **Sort preferences**: Date/amount sorting maintained across apps
- **Recipient searches**: Name queries and sort preferences carried over

### 🎯 **Smart Parameter Mapping**
- **National → CA**: Searches default to CA state when unspecified
- **CA → National**: State restriction removed for broader federal search
- **Phone/Email**: Person search parameters maintained for consistency

### 🌐 **Cross-App Navigation Links**
Each page includes links to equivalent functionality in the other app:
- **Main Search**: "🏛️ Search CA Data" / "🇺🇸 Search Federal Data"
- **Person Profiles**: Direct links with preserved name/location context
- **Recipient Search**: Cross-reference federal vs state committee databases

## File Structure

```
FEC/
├── app.py                          # 🇺🇸 National FEC Flask application (Port 5000)
├── update_fec.py                   # Automated FEC data download & processing
├── update_all.py                   # Master update orchestrator
├── setup_cron.sh                   # Cron job installer
├── setup_from_scratch.py           # Complete FEC setup script
├── add_new_data.py                 # Incremental FEC update script
├── process.py                      # FEC data processing engine
├── process_incremental.py          # FEC incremental processing
├── committee.py                    # FEC committee data loader
├── build_recipient_lookup.py       # FEC recipient lookup builder
├── build_percentile_tables.py      # FEC percentile calculator
├── table.sql                       # FEC core table schema
├── recipient_lookup_table.sql      # FEC recipient lookup schema
├── percentile_tables.sql           # FEC percentile table schema
├── indexes.sql                     # FEC database indexes
├── API_REFERENCE.md                # JSON API documentation
├── README.md                       # This documentation
├── requirements.txt                # Python dependencies
├── .gitignore                      # Git exclusions
├── logs/                           # Log files (generated)
├── fec_data/                       # Raw FEC data files (gitignored)
│   ├── 2023-2024/
│   ├── 2021-2022/
│   └── ...
├── fec_contributions.db           # 🇺🇸 National SQLite database (generated)
└── CA/                            # 🏛️ California Application Directory
    ├── ca_app_simple.py           # CA Flask application (Port 5001)
    ├── update_calaccess.py        # Automated CalAccess download & rebuild
    ├── process_ca.py              # CA data processing engine
    ├── build_ca_recipient_lookup.py # CA recipient lookup builder
    ├── build_ca_percentile_tables.py # CA percentile calculator
    ├── ca_percentile_tables.sql   # CA percentile table schema
    ├── ca_recipient_lookup_table.sql # CA recipient lookup schema
    ├── README_CA.md               # CA-specific documentation
    ├── CalAccess/                 # Raw CalAccess data files (gitignored)
    │   ├── DATA/
    │   │   └── *.TSV              # CalAccess TSV exports
    │   └── ...
    └── ca_contributions.db        # 🏛️ California SQLite database (generated)
```

## Cross-Database Comparison

| Feature | National FEC App | California App |
|---------|------------------|----------------|
| **Data Source** | FEC bulk downloads | CalAccess exports |
| **Coverage** | All federal campaigns | CA state/local campaigns |
| **Time Span** | 2015-2025+ | Variable (depends on CalAccess data) |
| **Contributors** | ~200M+ records | ~13M+ records |
| **Unique Donors** | ~50M+ | ~1.9M+ |
| **Port** | 5000 | 5001 |
| **Default State** | None (national search) | CA (California default) |
| **Conduit Filtering** | ActBlue, WinRed, etc. | ActBlue, WinRed (CA-specific) |
| **Percentile Tables** | ✅ Available | ✅ Available |
| **Recipient Lookup** | ✅ FTS enabled | ✅ FTS enabled |
| **Person Search** | ✅ Google integration | ✅ Google integration |
| **Cross-Links** | → CA App | → National App |

## Troubleshooting

### Common Issues

1. **Out of Disk Space**
   - The database can grow to 50-100GB with full data
   - Ensure adequate free space before starting

2. **Memory Issues During Processing**
   - Processing large files requires 4-8GB RAM
   - Consider processing smaller date ranges if needed

3. **Slow Performance**
   - Ensure indexes are created: `python3 -c "exec(open('indexes.sql').read())"`
   - Rebuild lookup tables: `python3 build_recipient_lookup.py`

4. **Database Corruption**
   - Run integrity check: `sqlite3 fec_contributions.db "PRAGMA integrity_check"`
   - If corrupted, restore from backup or rebuild from source data

### Performance Tuning

1. **SQLite Configuration**
   ```sql
   PRAGMA journal_mode = WAL;
   PRAGMA synchronous = NORMAL;
   PRAGMA cache_size = -64000;  -- 64MB cache
   PRAGMA temp_store = MEMORY;
   ```

2. **System Optimization**
   - Use SSD storage for better I/O performance
   - Increase system memory for larger caches
   - Monitor disk space during processing

## Security Notes

- **Development Only**: This application is designed for local development and analysis
- **Data Privacy**: Contains personal contribution information - handle responsibly
- **Network Security**: Do not expose to the internet without proper security measures
- **Access Control**: No built-in authentication - implement if needed for production use

## Legal & Compliance

- **Data Source**: All data from official FEC public records
- **Usage Rights**: FEC data is public domain
- **Privacy**: Contributors' information is already public record
- **Terms**: Follow FEC guidelines for data usage and redistribution

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with a subset of data
5. Submit a pull request

## License

This project is released under the MIT License. See LICENSE file for details.

## Support

For issues and questions:
1. Check the troubleshooting section above
2. Review the FEC data documentation
3. Open an issue on the project repository

---

*Last updated: February 2026*
