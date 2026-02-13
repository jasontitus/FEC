#!/usr/bin/env python3
"""
California Campaign Contributions Web Application
Simplified version with core functionality
"""

from flask import Flask, request, render_template_string
import sqlite3
import math
from urllib.parse import urlencode, quote_plus
import argparse
import os
import pprint
from datetime import datetime, timedelta

app = Flask(__name__)
# Use DB path relative to this file so the CA app always points to the CA database
DB_PATH = os.path.join(os.path.dirname(__file__), "ca_contributions.db")
PAGE_SIZE = 50
PERSON_SEARCH_PAGE_SIZE = 10  # Specific page size for recent contributions on person page

# Jinja2 filters
def format_currency(value):
    if value is None: return "$0.00"
    return "${:,.2f}".format(value)

def format_comma(value):
    if value is None: return "0"
    return "{:,}".format(int(value))

app.jinja_env.filters['currency'] = format_currency
app.jinja_env.filters['comma'] = format_comma
app.jinja_env.filters['quote_plus'] = quote_plus
app.jinja_env.globals['min'] = min
app.jinja_env.globals['max'] = max

# Helper function to build national FEC app URLs with preserved parameters
def build_fec_app_url(route="/", params=None):
    """Build URL for national FEC app with preserved search parameters."""
    if params is None:
        params = {}
    
    # Map CA parameters to national app parameters
    fec_params = {}
    for key, value in params.items():
        if key in ["first_name", "last_name", "city", "state", "zip_code", "year", "sort_by", "order", "name_query"]:
            fec_params[key] = value
        elif key == "first":
            fec_params["first_name"] = value
        elif key == "last":
            fec_params["last_name"] = value
        elif key == "zip":
            fec_params["zip_code"] = value
    
    base_url = f"http://localhost:5000{route}"
    if fec_params:
        base_url += "?" + urlencode(fec_params)
    
    return base_url

# Add to template globals
app.jinja_env.globals['build_fec_app_url'] = build_fec_app_url

# Helper Functions
def normalize_and_format_phone(phone_string):
    """Cleans and formats a phone number string.
    
    Args:
        phone_string: The raw phone number input.
        
    Returns:
        Formatted phone number as XXX-XXX-XXXX if input results in 10 digits,
        otherwise None.
    """
    if not phone_string:
        return None
        
    # 1. Remove non-digits
    digits = ''.join(filter(str.isdigit, phone_string))
    
    # 2. Handle leading '1' if 11 digits
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
        
    # 3. Check if we have exactly 10 digits
    if len(digits) == 10:
        # 4. Format into XXX-XXX-XXXX
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    else:
        # Return None if not 10 digits after cleaning
        return None

# Known passthrough platforms to filter out (similar to federal FEC system)
KNOWN_CA_CONDUITS = [
    "ActBlue",
    "ActBlue California",
    "WinRed"  # May also appear in CA data
]

def get_db():
    return sqlite3.connect(DB_PATH)

def get_ca_donor_percentiles_by_year(first_name, last_name, zip_code):
    """Get percentile rankings for a CA donor across all years they have data."""
    if not zip_code or len(zip_code) < 5:
        return {}
    
    zip5 = zip_code[:5]
    donor_key = f"{first_name}|{last_name}|{zip5}"
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if percentile tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ca_donor_totals_by_year'")
    if not cursor.fetchone():
        conn.close()
        return {}
    
    cursor.execute("""
        SELECT year, total_amount, contribution_count
        FROM ca_donor_totals_by_year 
        WHERE donor_key = ?
        ORDER BY year DESC
    """, (donor_key,))
    
    donor_years = cursor.fetchall()
    if not donor_years:
        conn.close()
        return {}
    
    percentiles = {}
    
    for year, total_amount, contrib_count in donor_years:
        cursor.execute("""
            SELECT COUNT(*) 
            FROM ca_donor_totals_by_year 
            WHERE year = ? AND total_amount > ?
        """, (year, total_amount))
        
        donors_above = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) 
            FROM ca_donor_totals_by_year 
            WHERE year = ?
        """, (year,))
        
        total_donors = cursor.fetchone()[0]
        
        if total_donors > 0:
            percentile = ((total_donors - donors_above) / total_donors) * 100
            rank = donors_above + 1
            
            percentiles[year] = {
                "percentile": percentile,
                "rank": rank,
                "total_amount": total_amount,
                "contribution_count": contrib_count,
                "total_donors": total_donors
            }
    
    conn.close()
    return percentiles

@app.route("/", methods=["GET"])
def search():
    """Main search page for California contributions."""
    params = {
        "first_name": request.args.get("first_name", "").strip().upper(),
        "last_name": request.args.get("last_name", "").strip().upper(),
        "zip_code": request.args.get("zip_code", "").strip().upper(),
        "city": request.args.get("city", "").strip().upper(),
        "state": request.args.get("state", "").strip().upper(),
        "year": request.args.get("year", "").strip(),
    }
    sort_by = request.args.get("sort_by", "date_desc").strip()
    exclude_passthrough = request.args.get("exclude_passthrough", "").strip().lower() in ("1", "true", "on", "yes")
    # Expose to template via params for checkbox state
    params["exclude_passthrough"] = "1" if exclude_passthrough else ""
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1

    # Validate year
    year_filter = None
    if params["year"] and params["year"].isdigit() and len(params["year"]) == 4:
        year_filter = params["year"]

    # Check if search should be performed
    search_criteria_provided = any([params["first_name"], params["last_name"], 
                                   params["zip_code"], params["city"], 
                                   params["state"], year_filter])

    results = []
    total_results = 0
    total_pages = 0

    if search_criteria_provided:
        conn = get_db()
        cursor = conn.cursor()

        # Build WHERE clause
        where_clauses = []
        query_params = []

        if params["first_name"]:
            where_clauses.append("c.first_name = ? COLLATE NOCASE")
            query_params.append(params["first_name"])
        if params["last_name"]:
            where_clauses.append("c.last_name = ? COLLATE NOCASE")
            query_params.append(params["last_name"])
        if params["zip_code"]:
            # Normalize search ZIP: allow 5 or 9 digit, with/without hyphen/spaces
            zip_digits = "".join(ch for ch in params["zip_code"] if ch.isdigit())
            zip5 = zip_digits[:5]
            # Use materialized normalized ZIP column (zip_norm), falling back is no longer needed on this DB
            where_clauses.append("(c.zip_norm LIKE ? OR substr(c.zip_norm,1,5) = ?)")
            query_params.extend([zip_digits + "%", zip5])
        if params["city"]:
            where_clauses.append("c.city = ? COLLATE NOCASE")
            query_params.append(params["city"])
        if params["state"]:
            where_clauses.append("c.state = ? COLLATE NOCASE")
            query_params.append(params["state"])
        if year_filter:
            start_date = f"{year_filter}-01-01"
            end_date = f"{year_filter}-12-31"
            where_clauses.append("c.contribution_date >= ? AND c.contribution_date <= ?")
            query_params.extend([start_date, end_date])

        # Optionally filter out known passthrough platforms
        if exclude_passthrough and KNOWN_CA_CONDUITS:
            conduit_placeholders = ",".join(["?"] * len(KNOWN_CA_CONDUITS))
            where_clauses.append(f"(fc.committee_name IS NULL OR fc.committee_name NOT IN ({conduit_placeholders}))")
            query_params.extend(KNOWN_CA_CONDUITS)

        if where_clauses:
            where_string = " WHERE " + " AND ".join(where_clauses)

            # Count query - use subquery with DISTINCT to get accurate count
            count_query = f"""
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT c.first_name, c.last_name, c.contribution_date, c.amount, c.recipient_committee_id, c.city, c.state, c.zip_code
                    FROM contributions c 
                    LEFT JOIN filing_committee_mapping fc ON c.recipient_committee_id = fc.filing_id 
                    {where_string}
                )
            """
            cursor.execute(count_query, query_params)
            total_results = cursor.fetchone()[0]
            total_pages = math.ceil(total_results / PAGE_SIZE)

            if total_results > 0:
                # Data query
                offset = (page - 1) * PAGE_SIZE
                if sort_by == "date_asc":
                    order_clause = "c.contribution_date ASC"
                elif sort_by == "amount_desc":
                    order_clause = "c.amount DESC, c.contribution_date DESC"
                elif sort_by == "amount_asc":
                    order_clause = "c.amount ASC, c.contribution_date DESC"
                else:
                    order_clause = "c.contribution_date DESC"
                data_query = f"""
                    SELECT DISTINCT c.first_name, c.last_name, c.contribution_date,
                           COALESCE(fc.committee_name, 'Committee ID: ' || c.recipient_committee_id) as recipient_display,
                           c.amount,
                           COALESCE(fc.committee_type, '') as committee_type, c.recipient_committee_id,
                           c.city, c.state, c.zip_code
                    FROM contributions c 
                    LEFT JOIN filing_committee_mapping fc ON c.recipient_committee_id = fc.filing_id
                    {where_string}
                    ORDER BY {order_clause}
                    LIMIT ? OFFSET ?
                """
                cursor.execute(data_query, query_params + [PAGE_SIZE, offset])
                results = cursor.fetchall()

        conn.close()

    # Pagination params (include sort_by)
    pagination_params = {k: v for k, v in params.items() if v}
    if sort_by:
        pagination_params["sort_by"] = sort_by

    return render_template_string(SEARCH_TEMPLATE,
        results=results, page=page, total_pages=total_pages, total_results=total_results,
        PAGE_SIZE=PAGE_SIZE, params=params, pagination_params=pagination_params,
        urlencode=urlencode, search_criteria_provided=search_criteria_provided,
        sort_by=sort_by,
        query_params_without_page_sort=urlencode({k:v for k,v in request.args.items() if k not in ['page','sort_by']})
    )

@app.route("/contributor")
def contributor_view():
    """Show contributions by a specific contributor."""
    first = request.args.get("first", "").strip()
    last = request.args.get("last", "").strip()
    # Get optional address params
    city = request.args.get("city", "").strip()
    state = request.args.get("state", "").strip() 
    zip_code = request.args.get("zip", "").strip()
    
    debug = request.args.get("debug", "").strip()
    sort_by = request.args.get("sort_by", "date_desc").strip()
    exclude_passthrough = request.args.get("exclude_passthrough", "").strip().lower() in ("1", "true", "on", "yes")
    
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1
    offset = (page - 1) * PAGE_SIZE
    
    if not first or not last:
        return "Missing first and last name", 400

    conn = get_db()
    cursor = conn.cursor()

    # Build base WHERE clause and params
    base_where_clauses = ["c.first_name = ? COLLATE NOCASE", "c.last_name = ? COLLATE NOCASE"]
    query_params = [first, last]

    # Add address filters if provided
    if city:
        base_where_clauses.append("c.city = ? COLLATE NOCASE")
        query_params.append(city)
    if state:
        base_where_clauses.append("c.state = ? COLLATE NOCASE")
        query_params.append(state)
    if zip_code:
        zip_digits = "".join(ch for ch in zip_code if ch.isdigit())
        zip5 = zip_digits[:5]
        base_where_clauses.append("(c.zip_norm LIKE ? OR substr(c.zip_norm,1,5) = ?)")
        query_params.extend([zip_digits + "%", zip5])
    
    # Handle passthrough exclusion
        if exclude_passthrough and KNOWN_CA_CONDUITS:
        conduit_placeholders = ",".join(["?"] * len(KNOWN_CA_CONDUITS))
        base_where_clauses.append(f"(fc.committee_name IS NULL OR fc.committee_name NOT IN ({conduit_placeholders}))")
        final_query_params = query_params + list(KNOWN_CA_CONDUITS)
    else:
        final_query_params = query_params
    
    where_string = " AND ".join(base_where_clauses)
    from_clause = "FROM contributions c LEFT JOIN filing_committee_mapping fc ON c.recipient_committee_id = fc.filing_id"

    # Count Query
    count_query_sql = f"SELECT COUNT(*) {from_clause} WHERE {where_string}"
    
    # Data Query
        if sort_by == "date_asc":
            order_clause = "c.contribution_date ASC"
        elif sort_by == "amount_desc":
            order_clause = "c.amount DESC, c.contribution_date DESC"
        elif sort_by == "amount_asc":
            order_clause = "c.amount ASC, c.contribution_date DESC"
        else:
            order_clause = "c.contribution_date DESC"
        
    data_query_sql = f"""
        SELECT c.contribution_date, 
               COALESCE(fc.committee_name, 'Committee ID: ' || c.recipient_committee_id) as recipient_display,
               c.amount, c.recipient_committee_id,
               c.city, c.state, c.zip_code
        {from_clause}
        WHERE {where_string}
        ORDER BY {order_clause} LIMIT ? OFFSET ?
    """
    paged_data_params = final_query_params + [PAGE_SIZE, offset]

    # Sum Query
    sum_query_sql = f"SELECT SUM(c.amount) {from_clause} WHERE {where_string}"

    # Execute Queries
    print(f"\n📋 Executing CA SQL (/contributor count):")
    print(count_query_sql)
    print("📎 With params:")
    pprint.pprint(final_query_params)
    cursor.execute(count_query_sql, final_query_params)
    total_results = cursor.fetchone()[0]
    total_pages = math.ceil(total_results / PAGE_SIZE)

    print(f"\n📋 Executing CA SQL (/contributor data):")
    print(data_query_sql)
    print("📎 With params:")
    pprint.pprint(paged_data_params)
    cursor.execute(data_query_sql, paged_data_params)
    rows = cursor.fetchall()
    
    print(f"\n📋 Executing CA SQL (/contributor sum):")
    print(sum_query_sql)
    print("📎 With params:")
    pprint.pprint(final_query_params)
    cursor.execute(sum_query_sql, final_query_params)
    total_amount_for_contributor = cursor.fetchone()[0] or 0

    conn.close()
    
    # Get percentile data for this donor
    percentiles_by_year = {}
    if zip_code:  # Only calculate if we have ZIP for proper identification
        percentiles_by_year = get_ca_donor_percentiles_by_year(first, last, zip_code)

    # Prepare Pagination URL
    pagination_params = {"first": first, "last": last}
    if city: pagination_params["city"] = city
    if state: pagination_params["state"] = state
    if zip_code: pagination_params["zip"] = zip_code
    if exclude_passthrough: pagination_params["exclude_passthrough"] = "1"
    base_pagination_url = "/contributor?" + urlencode(pagination_params)

    # Construct a filter description string
    filter_desc = f"{first} {last}"
    location_parts = []
    if city: location_parts.append(city)
    if state: location_parts.append(state)
    if zip_code: location_parts.append(zip_code)
    if location_parts:
        filter_desc += f" from {', '.join(location_parts)}"

    return render_template_string(CONTRIBUTOR_TEMPLATE,
        first=first, last=last, 
        city=city, state=state, zip_code=zip_code,
        filter_desc=filter_desc,
        total_amount_for_contributor=total_amount_for_contributor, rows=rows,
        page=page, total_pages=total_pages, total_results=total_results,
        PAGE_SIZE=PAGE_SIZE,
        base_pagination_url=base_pagination_url,
        percentiles_by_year=percentiles_by_year,
        sort_by=sort_by,
        contributor_query_without_sort=urlencode({k:v for k,v in request.args.items() if k not in ['sort_by']})
    )

@app.route("/search_recipients", methods=["GET"])
def search_recipients_by_name():
    """Search recipients by name."""
    name_query = request.args.get("name_query", "").strip()
    sort_by = request.args.get("sort_by", "recent_activity")
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1
    offset = (page - 1) * PAGE_SIZE
    results = []
    total_pages = 0
    total_results = 0

    if name_query:
        conn = get_db()
        cursor = conn.cursor()
        
        # Check if CA recipient_lookup table exists
        cursor.execute("""
            SELECT COUNT(*) FROM sqlite_master 
            WHERE type='table' AND name='ca_recipient_lookup'
        """)
        has_lookup_table = cursor.fetchone()[0] > 0
        
        if has_lookup_table:
            # Use CA lookup table
            if sort_by == "recent_activity":
                order_clause = "recent_contributions DESC, recent_amount DESC, total_contributions DESC"
            elif sort_by == "total_activity":
                order_clause = "total_contributions DESC, total_amount DESC, recent_contributions DESC"
            else:  # alphabetical
                order_clause = "display_name ASC"
            
            # Try FTS search first
            fts_count_query = """
                SELECT COUNT(*)
                FROM ca_recipient_lookup_fts fts
                JOIN ca_recipient_lookup ON fts.recipient_name = ca_recipient_lookup.recipient_name
                WHERE ca_recipient_lookup_fts MATCH ?
            """
            cursor.execute(fts_count_query, [name_query])
            fts_results = cursor.fetchone()[0]
            
            if fts_results > 0:
                total_results = fts_results
                total_pages = math.ceil(total_results / PAGE_SIZE)
                
                data_query = f"""
                    SELECT ca_recipient_lookup.recipient_name, ca_recipient_lookup.display_name, 
                           ca_recipient_lookup.committee_type, ca_recipient_lookup.total_contributions, 
                           ca_recipient_lookup.total_amount, ca_recipient_lookup.recent_contributions, 
                           ca_recipient_lookup.recent_amount, ca_recipient_lookup.last_contribution_date
                    FROM ca_recipient_lookup_fts fts
                    JOIN ca_recipient_lookup ON fts.recipient_name = ca_recipient_lookup.recipient_name
                    WHERE ca_recipient_lookup_fts MATCH ?
                    ORDER BY {order_clause}
                    LIMIT ? OFFSET ?
                """
                params = [name_query, PAGE_SIZE, offset]
            else:
                # Fall back to LIKE search
                like_count_query = """
                    SELECT COUNT(*) FROM ca_recipient_lookup 
                    WHERE display_name LIKE ? OR recipient_name LIKE ?
                """
                like_params = [f"%{name_query}%", f"%{name_query}%"]
                
                cursor.execute(like_count_query, like_params)
                total_results = cursor.fetchone()[0]
                total_pages = math.ceil(total_results / PAGE_SIZE)
                
                data_query = f"""
                    SELECT recipient_name, display_name, committee_type,
                           total_contributions, total_amount, recent_contributions, 
                           recent_amount, last_contribution_date
                    FROM ca_recipient_lookup 
                    WHERE display_name LIKE ? OR recipient_name LIKE ?
                    ORDER BY {order_clause}
                    LIMIT ? OFFSET ?
                """
                params = like_params + [PAGE_SIZE, offset]
            
            cursor.execute(data_query, params)
            lookup_results = cursor.fetchall()
            
            # Convert to expected format
            results = []
            for row in lookup_results:
                recipient_name, display_name, committee_type, total_contrib, total_amt, recent_contrib, recent_amt, last_date = row
                results.append((recipient_name, display_name, committee_type, 
                              total_contrib, total_amt, recent_contrib, recent_amt, last_date))

            # If the lookup/FTS produced zero results, fall back to filing_committee_mapping
            if total_results == 0:
                # Fallback: use mapping table and compute contribution stats on the fly
                # Count matched committees
                count_sql_query = "SELECT COUNT(*) FROM filing_committee_mapping WHERE committee_name LIKE ?"
                count_params = [f"%{name_query}%"]
                cursor.execute(count_sql_query, count_params)
                total_results = cursor.fetchone()[0]
                total_pages = math.ceil(total_results / PAGE_SIZE)

                # Compute recent cutoff (last 365 days)
                recent_cutoff = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')

                order_clause_mapping = "recent_contributions DESC, recent_amount DESC, total_contributions DESC"
                if sort_by == "total_activity":
                    order_clause_mapping = "total_contributions DESC, total_amount DESC, recent_contributions DESC"
                elif sort_by == "alphabetical":
                    order_clause_mapping = "committee_name ASC"

                sql_query = f"""
                    WITH matched AS (
                        SELECT filing_id, committee_name, committee_type
                        FROM filing_committee_mapping
                        WHERE committee_name LIKE ?
                    ),
                    agg AS (
                        SELECT 
                            c.recipient_committee_id AS filing_id,
                            COUNT(*) AS total_contributions,
                            COALESCE(SUM(c.amount), 0.0) AS total_amount,
                            SUM(CASE WHEN c.contribution_date >= ? THEN 1 ELSE 0 END) AS recent_contributions,
                            COALESCE(SUM(CASE WHEN c.contribution_date >= ? THEN c.amount ELSE 0 END), 0.0) AS recent_amount,
                            MAX(c.contribution_date) AS last_contribution_date
                        FROM contributions c
                        WHERE c.recipient_committee_id IN (SELECT filing_id FROM matched)
                        GROUP BY c.recipient_committee_id
                    )
                    SELECT 
                        m.filing_id,
                        m.committee_name,
                        m.committee_type,
                        COALESCE(a.total_contributions, 0) AS total_contributions,
                        COALESCE(a.total_amount, 0.0) AS total_amount,
                        COALESCE(a.recent_contributions, 0) AS recent_contributions,
                        COALESCE(a.recent_amount, 0.0) AS recent_amount,
                        a.last_contribution_date
                    FROM matched m
                    LEFT JOIN agg a ON a.filing_id = m.filing_id
                    ORDER BY {order_clause_mapping}
                    LIMIT ? OFFSET ?
                """
                params = [f"%{name_query}%", recent_cutoff, recent_cutoff, PAGE_SIZE, offset]

                cursor.execute(sql_query, params)
                committee_results = cursor.fetchall()

                # Convert to expected format
                results = []
                for filing_id, name, cmte_type, total_contrib, total_amt, recent_contrib, recent_amt, last_date in committee_results:
                    results.append((filing_id, name, cmte_type, total_contrib, total_amt, recent_contrib, recent_amt, last_date))
        
        else:
            # Fall back to filing_committee_mapping
            count_sql_query = "SELECT COUNT(*) FROM filing_committee_mapping WHERE committee_name LIKE ?"
            count_params = [f"%{name_query}%"]
            cursor.execute(count_sql_query, count_params)
            total_results = cursor.fetchone()[0]
            total_pages = math.ceil(total_results / PAGE_SIZE)

            sql_query = "SELECT filing_id, committee_name, committee_type FROM filing_committee_mapping WHERE committee_name LIKE ? ORDER BY committee_name LIMIT ? OFFSET ?"
            params = [f"%{name_query}%", PAGE_SIZE, offset]

            cursor.execute(sql_query, params)
            committee_results = cursor.fetchall()
            
            # Convert to expected format
            results = []
            for filing_id, name, cmte_type in committee_results:
                results.append((filing_id, name, cmte_type, 0, 0.0, 0, 0.0, None))
        
        conn.close()

    return render_template_string(SEARCH_RECIPIENTS_TEMPLATE,
       results=results, name_query=name_query, sort_by=sort_by, page=page, 
       total_pages=total_pages, total_results=total_results, PAGE_SIZE=PAGE_SIZE,
       urlencode=urlencode, query_params_without_page=urlencode({k:v for k,v in request.args.items() if k not in ['page']}))

@app.route("/personsearch", methods=["GET"])
def person_search_form():
    """Person search form."""
    return render_template_string(PERSON_SEARCH_TEMPLATE)

@app.route("/person")
def person_view_results():
    """Person search results with contribution data and Google integration."""
    # Get original search parameters from form
    original_form_params = {
        "first_name": request.args.get("first", "").strip().upper(),
        "last_name": request.args.get("last", "").strip().upper(),
        "street": request.args.get("street", "").strip().upper(),
        "city": request.args.get("city", "").strip().upper(),
        "zip_code": request.args.get("zip", "").strip().upper(),
        "phone": request.args.get("phone", "").strip(),
        "email": request.args.get("email", "").strip(),
        "state": request.args.get("state", "").strip().upper()
    }

    if not original_form_params["first_name"] or not original_form_params["last_name"]:
        return "Missing required query parameters: 'first' and 'last'", 400
    
    conn = get_db()
    cursor = conn.cursor()

    # Cascading logic for DB query for contributions
    recent_contributions = []
    total_amount = 0.0
    db_cascade_message = ""
    
    # Define search attempts for DB query
    db_search_attempts = []
    db_base_attempt_params = {
        "first_name": original_form_params["first_name"],
        "last_name": original_form_params["last_name"],
        "city": original_form_params["city"],
        "zip_code": original_form_params["zip_code"],
        "state": original_form_params["state"]
    }

    # Attempt 1: All relevant DB filters
    db_search_attempts.append({"params": db_base_attempt_params.copy(), "level": "All relevant filters"})

    # Attempt 2: Drop ZIP (if ZIP was provided)
    if original_form_params["zip_code"]:
        attempt_2_params = db_base_attempt_params.copy()
        attempt_2_params["zip_code"] = ""
        db_search_attempts.append({"params": attempt_2_params, "level": "Dropped ZIP Code from DB query"})

    # Attempt 3: Drop City & ZIP (if City was provided)
    if original_form_params["city"]:
        attempt_3_params = db_base_attempt_params.copy()
        attempt_3_params["zip_code"] = ""
        attempt_3_params["city"] = ""
        db_search_attempts.append({"params": attempt_3_params, "level": "Dropped City & ZIP Code from DB query"})

    found_db_results = False
    effective_db_params = {}
    last_attempt_db_params = {}

    for attempt in db_search_attempts:
        current_db_params = attempt["params"]
        level = attempt["level"]
        last_attempt_db_params = current_db_params

        db_where_clauses = ["c.first_name = ? COLLATE NOCASE", "c.last_name = ? COLLATE NOCASE"]
        db_query_actual_params = [current_db_params["first_name"], current_db_params["last_name"]]
        state_filter_applied = None

        if current_db_params["city"]:
            db_where_clauses.append("c.city = ? COLLATE NOCASE")
            db_query_actual_params.append(current_db_params["city"])
            
        # State handling: Apply explicit state OR default to CA
        if current_db_params["state"]:
            db_where_clauses.append("c.state = ? COLLATE NOCASE")
            db_query_actual_params.append(current_db_params["state"])
            state_filter_applied = current_db_params["state"]
        else:
            db_where_clauses.append("c.state = ? COLLATE NOCASE")
            db_query_actual_params.append("CA")
            state_filter_applied = "CA (Default)"
            
        if current_db_params["zip_code"]:
            zip_digits = "".join(ch for ch in current_db_params["zip_code"] if ch.isdigit())
            zip5 = zip_digits[:5]
            db_where_clauses.append("(c.zip_norm LIKE ? OR substr(c.zip_norm,1,5) = ?)")
            db_query_actual_params.extend([zip_digits + "%", zip5])

        # Optionally exclude known passthrough platforms
        if KNOWN_CA_CONDUITS:
            conduit_placeholders = ",".join(["?"] * len(KNOWN_CA_CONDUITS))
            db_where_clauses.append(f"(fc.committee_name IS NULL OR fc.committee_name NOT IN ({conduit_placeholders}))")
            db_query_actual_params.extend(KNOWN_CA_CONDUITS)

        db_where_string = " AND ".join(db_where_clauses)

        # Check if any contributions exist with these criteria
        check_existence_sql = f"""SELECT 1 FROM contributions c 
                                LEFT JOIN filing_committee_mapping fc ON c.recipient_committee_id = fc.filing_id 
                                WHERE {db_where_string} LIMIT 1"""
        
        cursor.execute(check_existence_sql, db_query_actual_params)
        if cursor.fetchone():
            found_db_results = True
            effective_db_params = current_db_params
            if level == "Dropped ZIP Code from DB query":
                db_cascade_message = "(Contribution data found after dropping ZIP code filter from DB query)"
            elif level == "Dropped City & ZIP Code from DB query":
                db_cascade_message = "(Contribution data found after dropping City & ZIP code filters from DB query)"
            else:
                db_cascade_message = ""

            # Query for total contribution amount
            sum_query = f"""SELECT SUM(c.amount) FROM contributions c 
                           LEFT JOIN filing_committee_mapping fc ON c.recipient_committee_id = fc.filing_id 
                           WHERE {db_where_string}"""
            cursor.execute(sum_query, db_query_actual_params)
            total_amount_result = cursor.fetchone()
            total_amount = total_amount_result[0] if total_amount_result and total_amount_result[0] is not None else 0.0

            # Query for recent contributions
            recent_query = f"""
                SELECT c.contribution_date, 
                       COALESCE(fc.committee_name, 'Committee ID: ' || c.recipient_committee_id) as recipient_display_name,
                       c.amount, c.recipient_committee_id, c.city, c.state, c.zip_code
                FROM contributions c
                LEFT JOIN filing_committee_mapping fc ON c.recipient_committee_id = fc.filing_id
                WHERE {db_where_string}
                ORDER BY c.contribution_date DESC
                LIMIT ?
            """
            recent_query_final_params = db_query_actual_params + [PERSON_SEARCH_PAGE_SIZE]
            cursor.execute(recent_query, recent_query_final_params)
            recent_contributions = cursor.fetchall()
            break
    
    no_results_message = None
    if not found_db_results:
        no_results_message = f"No recent contributions found for {original_form_params['first_name']} {original_form_params['last_name']}"
        # Clarify the final criteria attempted
        final_attempt_criteria = []
        if last_attempt_db_params.get('city'): 
            final_attempt_criteria.append(f"City: {last_attempt_db_params['city']}")
        if last_attempt_db_params.get('state'): 
            final_attempt_criteria.append(f"State: {last_attempt_db_params['state']}")
        elif original_form_params['first_name'] or original_form_params['last_name']:
            final_attempt_criteria.append("State: CA (Default)")
        if last_attempt_db_params.get('zip_code'): 
            final_attempt_criteria.append(f"ZIP: {last_attempt_db_params['zip_code']}")
        
        if final_attempt_criteria:
            no_results_message += f" matching: { ', '.join(final_attempt_criteria) }."
        else:
            no_results_message += "."
            
        # Add details about failed cascades
        original_had_zip = original_form_params["zip_code"]
        original_had_city = original_form_params["city"]
        if original_had_zip and original_had_city:
            no_results_message += " Also tried searching database without ZIP, and without City & ZIP."
        elif original_had_zip:
            no_results_message += " Also tried searching database without ZIP."
        elif original_had_city:
            no_results_message += " Also tried searching database without City & ZIP."
        no_results_message += " (Excluding passthroughs)."

    conn.close()

    # Prepare Google search URLs (uses original_form_params)
    
    # Address Search
    google_search_query_address_parts = []
    if original_form_params["first_name"]: google_search_query_address_parts.append(original_form_params["first_name"])
    if original_form_params["last_name"]: google_search_query_address_parts.append(original_form_params["last_name"])
    if original_form_params["street"]: google_search_query_address_parts.append(original_form_params["street"])
    if original_form_params["city"]: google_search_query_address_parts.append(original_form_params["city"])
    if original_form_params["state"]: google_search_query_address_parts.append(original_form_params["state"])
    google_search_query_address = " ".join(filter(None, google_search_query_address_parts))
    google_search_url_address = f"https://www.google.com/search?igu=1&q={quote_plus(google_search_query_address)}" if google_search_query_address else None

    # Phone Search (using normalized number)
    formatted_phone = normalize_and_format_phone(original_form_params["phone"])
    google_search_query_phone = None
    google_search_url_phone = None
    if formatted_phone:
        google_search_query_phone_parts = []
        if original_form_params["first_name"]: google_search_query_phone_parts.append(original_form_params["first_name"])
        if original_form_params["last_name"]: google_search_query_phone_parts.append(original_form_params["last_name"])
        google_search_query_phone_parts.append(formatted_phone)
        google_search_query_phone = " ".join(filter(None, google_search_query_phone_parts))
        google_search_url_phone = f"https://www.google.com/search?igu=1&q={quote_plus(google_search_query_phone)}"
        
    # Email Search
    google_search_query_email_parts = []
    if original_form_params["first_name"]: google_search_query_email_parts.append(original_form_params["first_name"])
    if original_form_params["last_name"]: google_search_query_email_parts.append(original_form_params["last_name"])
    if original_form_params["email"]: google_search_query_email_parts.append(original_form_params["email"])
    google_search_query_email = " ".join(filter(None, google_search_query_email_parts))
    google_search_url_email = f"https://www.google.com/search?igu=1&q={quote_plus(google_search_query_email)}" if original_form_params["email"] else None

    # Name + City Search
    google_search_query_name_city_parts = []
    if original_form_params["first_name"]: google_search_query_name_city_parts.append(original_form_params["first_name"])
    if original_form_params["last_name"]: google_search_query_name_city_parts.append(original_form_params["last_name"])
    if original_form_params["city"]: google_search_query_name_city_parts.append(original_form_params["city"])
    google_search_query_name_city = " ".join(filter(None, google_search_query_name_city_parts))
    google_search_url_name_city = None
    if original_form_params["first_name"] and original_form_params["last_name"] and original_form_params["city"]:
        google_search_url_name_city = f"https://www.google.com/search?igu=1&q={quote_plus(google_search_query_name_city)}"
    
    return render_template_string(PERSON_RESULTS_TEMPLATE, 
                                original_form_params=original_form_params,
                                total_amount=total_amount, 
                                recent_contributions=recent_contributions,
                                db_cascade_message=db_cascade_message,
                                no_results_message=no_results_message,
                                google_search_url_address=google_search_url_address,
                                google_search_query_address=google_search_query_address,
                                google_search_url_phone=google_search_url_phone,
                                google_search_query_phone=google_search_query_phone,
                                google_search_url_email=google_search_url_email,
                                google_search_query_email=google_search_query_email,
                                google_search_url_name_city=google_search_url_name_city,
                                google_search_query_name_city=google_search_query_name_city
                                )

@app.route("/recipient")
def recipient_view():
    """Show top contributors to a specific recipient."""
    committee_id = request.args.get("committee_id", "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1
    offset = (page - 1) * PAGE_SIZE

    if not committee_id:
        return "Missing committee_id", 400

    conn = get_db()
    cursor = conn.cursor()

    # Get committee name
    cursor.execute("SELECT committee_name FROM filing_committee_mapping WHERE filing_id = ?", (committee_id,))
    name_row = cursor.fetchone()
    recipient_name = name_row[0] if name_row else committee_id

    # Query for paged results
    data_query_str = """
        SELECT first_name, last_name, SUM(amount) as total
        FROM contributions
        WHERE recipient_committee_id = ?
        GROUP BY first_name, last_name
        ORDER BY total DESC
        LIMIT ? OFFSET ?
    """
    data_params = [committee_id, PAGE_SIZE, offset]

    # Count query for total groups of contributors
    count_query_str = """
        SELECT COUNT(*)
        FROM (
            SELECT 1
            FROM contributions
            WHERE recipient_committee_id = ?
            GROUP BY first_name, last_name
        )
    """
    count_params = [committee_id]

    print(f"\n📋 Executing CA SQL (/recipient count):")
    print(count_query_str)
    print("📎 With params:")
    pprint.pprint(count_params)
    
    cursor.execute(count_query_str, count_params)
    total_results = cursor.fetchone()[0]
    total_pages = math.ceil(total_results / PAGE_SIZE)

    print(f"\n📋 Executing CA SQL (/recipient data):")
    print(data_query_str)
    print("📎 With params:")
    pprint.pprint(data_params)

    cursor.execute(data_query_str, data_params)
    rows = cursor.fetchall()
    conn.close()

    return render_template_string(RECIPIENT_TEMPLATE, 
        recipient_name=recipient_name, rows=rows, committee_id=committee_id, 
        page=page, total_pages=total_pages, total_results=total_results, PAGE_SIZE=PAGE_SIZE,
        query_params_without_page=urlencode({k:v for k,v in request.args.items() if k not in ['page', 'committee_id']}))

@app.route("/committee/<committee_id>")
def committee_view(committee_id):
    """Show all contributions to a specific committee."""
    sort_by = request.args.get("sort_by", "date_desc").strip()
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Get committee information
    cursor.execute("""
        SELECT committee_name, committee_type 
        FROM filing_committee_mapping 
        WHERE filing_id = ?
    """, (committee_id,))
    committee_info = cursor.fetchone()
    
    if not committee_info:
        return f"Committee {committee_id} not found", 404
    
    committee_name, committee_type = committee_info
    
    # Build order clause
    if sort_by == "date_asc":
        order_clause = "c.contribution_date ASC, c.amount DESC"
    elif sort_by == "amount_desc":
        order_clause = "c.amount DESC, c.contribution_date DESC"
    elif sort_by == "amount_asc":
        order_clause = "c.amount ASC, c.contribution_date DESC"
    else:
        order_clause = "c.contribution_date DESC, c.amount DESC"
    
    # Get all contributions to this committee
    cursor.execute(f"""
        SELECT DISTINCT c.contribution_date, c.first_name, c.last_name, c.city, c.state, 
               c.amount, c.employer, c.occupation
        FROM contributions c
        WHERE c.recipient_committee_id = ?
        ORDER BY {order_clause}
        LIMIT 1000
    """, (committee_id,))
    contributions = cursor.fetchall()
    
    # Get total amount and count
    cursor.execute("""
        SELECT COUNT(*), SUM(c.amount)
        FROM contributions c
        WHERE c.recipient_committee_id = ?
    """, (committee_id,))
    total_count, total_amount = cursor.fetchone()
    total_amount = total_amount or 0
    
    conn.close()
    
    return render_template_string(COMMITTEE_TEMPLATE,
        committee_id=committee_id, committee_name=committee_name, committee_type=committee_type,
        contributions=contributions, total_count=total_count, total_amount=total_amount,
        sort_by=sort_by,
        committee_query_without_sort=urlencode({k:v for k,v in request.args.items() if k not in ['sort_by']})
    )

# HTML Templates
SEARCH_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CA Campaign Contribution Search</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { margin-bottom: 20px; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; }
        form { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; display: flex; flex-wrap: wrap; gap: 15px; align-items: center; }
        form input[type="text"], form input[type="date"], form select { padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 1em; flex-grow: 1; min-width: 120px; }
        form input[type="submit"], button { background-color: #e67e22; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        form input[type="submit"]:hover, button:hover { background-color: #d35400; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #fff3e0; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .pagination { text-align: center; margin: 20px 0; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 12px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; }
        .pagination a:hover { background: #f0f0f0; }
        .info-link { text-decoration: none; margin-left: 5px; font-size: 0.9em; color: #7f8c8d; }
        .info-link:hover { color: #3498db; }
    </style>
</head>
<body>
    <h1>🏛️ CA Campaign Contribution Search</h1>
    <div class="nav-links">
        <a href="/">💰 Contribution Search</a>
        <a href="/search_recipients">🏢 Recipient Search</a>
        <a href="/personsearch">👤 Person Search</a>
        <a href="{{ build_fec_app_url('/', params) }}" style="color: #3498db;" target="_blank">🇺🇸 Search Federal Data</a>
    </div>
    <h2>Search Contributions</h2>
        
        <form method="get">
            <input name="first_name" placeholder="First Name" value="{{ params.first_name }}">
            <input name="last_name" placeholder="Last Name" value="{{ params.last_name }}">
            <input name="city" placeholder="City" value="{{ params.city }}">
            <input name="state" placeholder="State" value="{{ params.state }}" maxlength="2">
            <input name="zip_code" placeholder="ZIP Code" value="{{ params.zip_code }}">
            <input name="year" placeholder="Year (YYYY)" value="{{ params.year }}">
            <select name="sort_by">
                <option value="date_desc" {% if sort_by == 'date_desc' %}selected{% endif %}>Date (newest)</option>
                <option value="date_asc" {% if sort_by == 'date_asc' %}selected{% endif %}>Date (oldest)</option>
                <option value="amount_desc" {% if sort_by == 'amount_desc' %}selected{% endif %}>Amount (highest)</option>
                <option value="amount_asc" {% if sort_by == 'amount_asc' %}selected{% endif %}>Amount (lowest)</option>
            </select>
            <label style="margin-left:10px; display:flex; align-items:center; gap:6px;">
                <input type="checkbox" name="exclude_passthrough" value="1" {% if params.exclude_passthrough %}checked{% endif %}>
                Exclude passthroughs (ActBlue/WinRed)
            </label>
            <input type="submit" value="Search">
        </form>

        {% if results %}
            <h2>Results ({{ total_results|comma }} found)</h2>
            <table>
                <tr>
                    <th><a href="/?{{ query_params_without_page_sort }}&sort_by={{ 'date_desc' if sort_by != 'date_desc' else 'date_asc' }}">First</a></th>
                    <th><a href="/?{{ query_params_without_page_sort }}&sort_by={{ 'date_desc' if sort_by != 'date_desc' else 'date_asc' }}">Last</a></th>
                    <th><a href="/?{{ query_params_without_page_sort }}&sort_by={{ 'date_desc' if sort_by != 'date_desc' else 'date_asc' }}">Date</a></th>
                    <th>Recipient</th>
                    <th><a href="/?{{ query_params_without_page_sort }}&sort_by={{ 'amount_desc' if sort_by != 'amount_desc' else 'amount_asc' }}">Amount</a></th>
                    <th>City</th><th>State</th><th>ZIP</th>
                </tr>
                {% for fn, ln, date, recip, amt, typ, cmte_id, city, state, zip in results %}
                <tr>
                    <td><a href="/contributor?first={{ fn }}&last={{ ln }}&city={{ city|urlencode }}&state={{ state|urlencode }}&zip={{ zip|urlencode }}">{{ fn }}</a></td>
                    <td><a href="/contributor?first={{ fn }}&last={{ ln }}&city={{ city|urlencode }}&state={{ state|urlencode }}&zip={{ zip|urlencode }}">{{ ln }}</a></td>
                    <td>{{ date }}</td>
                    <td>
                        <a href="/recipient?committee_id={{ cmte_id }}">{{ recip }}</a>
                        <a href="https://www.google.com/search?q={{ recip|quote_plus }}" class="info-link" target="_blank" title="Search Google for {{ recip }}">&#x24D8;</a>
                    </td>
                    <td>{{ amt|currency }}</td>
                    <td>{{ city }}</td>
                    <td>{{ state }}</td>
                    <td>{{ zip }}</td>
                </tr>
                {% endfor %}
            </table>
            
            {% if total_pages > 1 %}
            <div class="pagination">
                {% set base_url = "/?" + urlencode(pagination_params) %}
                {% if page > 1 %}
                    <a href="{{ base_url }}&page={{ page - 1 }}">&laquo; Previous</a>
                {% endif %}
                <span>Page {{ page }} of {{ total_pages }}</span>
                {% if page < total_pages %}
                    <a href="{{ base_url }}&page={{ page + 1 }}">Next &raquo;</a>
                {% endif %}
            </div>
            {% endif %}
        {% elif search_criteria_provided %}
            <p>No results found. Try broadening your search criteria.</p>
        {% endif %}
    </div>
</body>
</html>
"""

CONTRIBUTOR_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CA Contributions by {{ filter_desc }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 10px; }
        h1 { font-size: 1.8em; }
        h2 { font-size: 1.2em; margin-top: 20px; }
        .filter-info { font-size: 0.95em; color: #555; margin-bottom: 20px; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { margin-bottom: 20px; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .pagination { margin: 25px 0; text-align: center; clear: both; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 14px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; color: #3498db; text-decoration: none; font-size: 0.95em; }
        .pagination a:hover { background-color: #eaf2f8; border-color: #c5ddec; }
        .pagination .current-page { background-color: #3498db; color: white; border-color: #3498db; font-weight: bold; }
        .results-summary { margin: 20px 0 10px 0; font-size: 0.9em; color: #555; }
        .info-link { text-decoration: none; margin-left: 5px; font-size: 0.9em; color: #7f8c8d; }
        .info-link:hover { color: #3498db; }
    </style>
</head>
<body>
    <h1>CA Contributions by {{ first }} {{ last }}</h1>
    <div class="filter-info">Showing contributions matching: {{ filter_desc }}</div>
    <div class="nav-links">
        <a href="/">🔍 New Search</a>
        <a href="/search_recipients">👥 Search Recipients by Name</a>
        <a href="/personsearch">👤 Person Search</a>
        <a href="{{ build_fec_app_url('/contributor', {'first_name': first, 'last_name': last, 'city': city, 'state': state, 'zip_code': zip_code}) }}" style="color: #3498db;" target="_blank">🇺🇸 Search Federal Data</a>
    </div>
    <h2>Total Contributed (matching filter, all pages): {{ total_amount_for_contributor|currency }}</h2>
    
    {% if percentiles_by_year and zip_code %}
    <div style="background-color: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 20px 0;">
        <h3 style="margin-top: 0; color: #2c3e50;">📊 CA Donor Percentile Rankings</h3>
        <p style="font-size: 0.9em; color: #666; margin-bottom: 15px;">
            Based on total annual contributions among all CA donors identified as: {{ first }} {{ last }} ({{ zip_code[:5] }})
        </p>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px;">
            {% for year in percentiles_by_year.keys()|sort(reverse=True) %}
            {% set data = percentiles_by_year[year] %}
            <div style="background-color: #f8f9fa; padding: 10px; border-radius: 4px; border-left: 4px solid #3498db;">
                <strong>{{ year }}</strong><br>
                <span style="font-size: 1.1em; color: #2c3e50;">{{ data.percentile|round(1) }}th percentile</span><br>
                <small style="color: #666;">
                    Rank {{ data.rank|comma }} of {{ data.total_donors|comma }}<br>
                    Total: {{ data.total_amount|currency }}
                </small>
            </div>
            {% endfor %}
        </div>
        <p style="font-size: 0.8em; color: #888; margin-top: 10px; margin-bottom: 0;">
            Higher percentile = higher rank among donors. Rankings based on yearly total contributions.
        </p>
    </div>
    {% elif zip_code %}
    <div style="background-color: #fff3cd; padding: 10px; border-radius: 4px; margin: 15px 0; font-size: 0.9em;">
        📊 Percentile rankings will be available after running the CA percentile table builder script.
    </div>
    {% endif %}
    
    <div class="results-summary">
      Showing {{ (page - 1) * PAGE_SIZE + 1 if total_results > 0 else 0 }} - {{ [page * PAGE_SIZE, total_results]|min }} of {{ total_results }} contributions.
    </div>
        <table>
      <tr><th>Date</th><th>Recipient</th><th>Amount</th><th>Type</th>
          <th>City</th><th>State</th><th>ZIP</th></tr>
      {% for r_date, r_name, r_amt, r_cmte_id, r_city, r_state, r_zip in rows %}
        <tr>
          <td>{{ r_date }}</td>
          <td>
              <a href="/committee/{{ r_cmte_id }}">{{ r_name }}</a>
              <a href="https://www.google.com/search?q={{ r_name|quote_plus }}" class="info-link" target="_blank" title="Search Google for {{ r_name }}">&#x24D8;</a>
          </td>
          <td>{{ r_amt|currency }}</td>
          <td>CA Committee</td>
          <td>{{ r_city }}</td>
          <td>{{ r_state }}</td>
          <td>{{ r_zip }}</td>
            </tr>
            {% endfor %}
        </table>
    {% if total_pages > 1 %}
    <div class="pagination">
        {% if page > 1 %}
            <a href="{{ base_pagination_url }}&page={{ page - 1 }}">&laquo; Previous</a>
        {% endif %}
        <span>Page {{ page }} of {{ total_pages }}</span>
        {% if page < total_pages %}
            <a href="{{ base_pagination_url }}&page={{ page + 1 }}">Next &raquo;</a>
        {% endif %}
    </div>
    {% endif %}
</body>
</html>
"""

SEARCH_RECIPIENTS_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Search CA Recipients</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { margin-bottom: 20px; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; }
        form { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; display: flex; flex-wrap: wrap; gap: 15px; align-items: center; }
        form input[type="text"], form input[type="date"], form select { padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 1em; flex-grow: 1; min-width: 120px; }
        form input[type="submit"], button { background-color: #e67e22; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        form input[type="submit"]:hover, button:hover { background-color: #d35400; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
    </style>
</head>
<body>
    <h1>🏛️ CA Campaign Finance</h1>
    <div class="nav-links">
        <a href="/">💰 Contribution Search</a>
        <a href="/search_recipients">🏢 Recipient Search</a>
        <a href="/personsearch">👤 Person Search</a>
        <a href="{{ build_fec_app_url('/search_recipients', {'name_query': name_query, 'sort_by': sort_by}) }}" style="color: #3498db;" target="_blank">🇺🇸 Search Federal Recipients</a>
    </div>
        <h2>Search Recipients by Name</h2>
        <form method="get">
            <input name="name_query" placeholder="Search recipient names..." value="{{ name_query }}" style="flex-grow: 1;">
            <select name="sort_by">
                <option value="recent_activity" {% if sort_by == 'recent_activity' %}selected{% endif %}>Recent Activity</option>
                <option value="total_activity" {% if sort_by == 'total_activity' %}selected{% endif %}>Total Activity</option>
                <option value="alphabetical" {% if sort_by == 'alphabetical' %}selected{% endif %}>Alphabetical</option>
            </select>
            <input type="submit" value="Search">
        </form>

        {% if name_query %}
            {% if results %}
                <h3>Results</h3>
                <div class="results-summary">
                    Showing {{ (page - 1) * PAGE_SIZE + 1 }} - {{ [page * PAGE_SIZE, total_results]|min }} of {{ total_results }} recipients.
                </div>
                <table>
                    <tr>
                        <th>Recipient</th><th>Type</th><th>Recent Activity</th><th>Total Activity</th><th>Last Contribution</th>
                    </tr>
                    {% for committee_id, name, type, total_contrib, total_amt, recent_contrib, recent_amt, last_date in results %}
                    <tr>
                        <td><a href="/committee/{{ committee_id }}">{{ name }}</a></td>
                        <td>{{ type if type else "Unknown" }}</td>
                        <td>
                            {% if recent_contrib > 0 %}
                                {{ recent_contrib|comma }} contrib<br>
                                <small>{{ recent_amt|currency }}</small>
                            {% else %}
                                <span style="color: #999;">No recent activity</span>
                            {% endif %}
                        </td>
                        <td>
                            {% if total_contrib > 0 %}
                                {{ total_contrib|comma }} contrib<br>
                                <small>{{ total_amt|currency }}</small>
                            {% else %}
                                <span style="color: #999;">No data</span>
                            {% endif %}
                        </td>
                        <td>{{ last_date if last_date else "Unknown" }}</td>
                    </tr>
                    {% endfor %}
                </table>
                {% if total_pages > 1 %}
                <div class="pagination">
                    {% set base_url = "/search_recipients?name_query=" + name_query + "&" + query_params_without_page %}
                    {% if page > 1 %}
                        <a href="{{ base_url }}&page={{ page - 1 }}">&laquo; Previous</a>
                    {% endif %}
                    <span>Page {{ page }} of {{ total_pages }}</span>
                    {% if page < total_pages %}
                        <a href="{{ base_url }}&page={{ page + 1 }}">Next &raquo;</a>
                    {% endif %}
                </div>
                {% endif %}
            {% else %}
                <p>No recipients found for "{{ name_query }}".</p>
            {% endif %}
        {% endif %}
</body>
</html>
"""

PERSON_SEARCH_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CA Person Search</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        h1 { text-align: center; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { text-align: center; margin-bottom: 25px; }
        .nav-links a { margin: 0 10px; font-size: 1.1em; }
        form { background-color: #fff; padding: 25px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); max-width: 600px; margin: 0 auto; }
        .form-group { margin-bottom: 18px; }
        .form-group label { display: block; margin-bottom: 6px; font-weight: 500; color: #333; }
        .form-group input[type="text"], .form-group input[type="email"], .form-group input[type="tel"] { 
            width: 100%; 
            padding: 10px; 
            border: 1px solid #ddd; 
            border-radius: 4px;
            box-sizing: border-box; 
            font-size: 1em; 
        }
        .form-group input:focus { border-color: #3498db; outline: none; box-shadow: 0 0 0 2px rgba(52, 152, 219, 0.2); }
        .required-note { font-size: 0.9em; color: #555; margin-bottom: 20px; }
        input[type="submit"] { background-color: #e67e22; color: white; padding: 12px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 1.1em; width: 100%; margin-top: 10px; transition: background-color 0.2s ease; }
        input[type="submit"]:hover { background-color: #d35400; }
        .optional-fields { border-top: 1px dashed #ccc; margin-top: 20px; padding-top: 20px; }
        .optional-fields h3 { color: #666; margin-bottom: 15px; font-size: 1.1em; }
        .loading-indicator { display: none; color: #e67e22; font-weight: bold; text-align: center; margin-top: 15px; }
    </style>
</head>
<body>
    <h1>CA Person Search</h1>
    <div class="nav-links">
        <a href="/">🔍 Contribution Search</a>
        <a href="/search_recipients">👥 Recipient Search</a>
        <a href="/personsearch">👤 New Person Search</a>
        <a href="http://localhost:5000/personsearch" style="color: #3498db;" target="_blank">🇺🇸 Federal Person Search</a>
    </div>
    <form method="get" action="/person" onsubmit="document.getElementById('searchButton').disabled = true; document.getElementById('loading').style.display = 'block';">
            <div class="form-group">
                <label for="first">First Name:</label>
                <input type="text" id="first" name="first" required>
            </div>
            <div class="form-group">
                <label for="last">Last Name:</label>
                <input type="text" id="last" name="last" required>
            </div>
        <p class="required-note">First and Last Name are required.</p>
            
            <div class="optional-fields">
            <h3>Optional Details (for more specific searches)</h3>
            <div class="form-group">
                <label for="street">Street Address:</label>
                <input type="text" id="street" name="street">
            </div>
                <div class="form-group">
                    <label for="city">City:</label>
                    <input type="text" id="city" name="city">
                </div>
                <div class="form-group">
                    <label for="state">State:</label>
                <input type="text" id="state" name="state" maxlength="2" size="4" style="width: auto;">
                <small>(Defaults to CA if blank)</small>
                </div>
                <div class="form-group">
                    <label for="zip">ZIP Code:</label>
                <input type="text" id="zip" name="zip" pattern="[0-9]{5}(-[0-9]{4})?" title="Enter a 5-digit or 9-digit ZIP code">
                </div>
                <div class="form-group">
                    <label for="phone">Phone Number:</label>
                <input type="tel" id="phone" name="phone" pattern="[0-9\\-\\+\\s\\(\\)]*" title="Enter a valid phone number">
                </div>
                <div class="form-group">
                    <label for="email">Email Address:</label>
                    <input type="email" id="email" name="email">
                </div>
            </div>

        <input type="submit" value="Search Person" id="searchButton">
        <div id="loading" class="loading-indicator">Searching...</div>
        </form>
</body>
</html>
"""

PERSON_RESULTS_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Profile for {{ original_form_params.first_name }} {{ original_form_params.last_name }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f7f6; color: #333; display: flex; flex-direction: column; min-height: 100vh; }
        .container { max-width: 900px; margin: 20px auto; padding: 20px; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; flex-grow: 1; }
        h1, h2 { color: #2c3e50; margin-bottom: 15px; }
        h1 { margin-bottom: 5px; word-break: break-word; }
        .sub-header { font-size: 1.1em; color: #555; margin-bottom: 20px; }
        .search-params { font-size: 0.9em; color: #555; margin-bottom: 10px; padding: 10px; background-color: #f9f9f9; border-radius: 4px; border: 1px solid #eee; word-break: break-word;}
        .search-params strong { color: #333; }
        .db-cascade-info { margin: 0 0 10px 0; font-size: 0.85em; color: #e67e22; font-style: italic; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; margin-bottom: 30px; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .google-search-section { margin-top: 30px; border-top: 2px solid #ddd; padding-top: 20px; }
        .google-search-section h2 { margin-bottom: 15px; font-size: 1.3em; }
        iframe { border: 1px solid #ccc; width: 100%; height: 500px; margin-bottom: 20px; }
        .no-results { color: #e74c3c; font-style: italic; }
        .nav-link { display: inline-block; background-color: #3498db; color: white !important; padding: 10px 15px; border-radius: 4px; font-size: 1em; text-decoration: none; margin-top:20px;}
        .nav-link:hover { background-color: #2980b9; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <a href="/personsearch" class="nav-link" style="float: right; margin-top:0;">↩ New Person Search Form</a>
        <h1>{{ original_form_params.first_name }} {{ original_form_params.last_name }}</h1>
        
        <div class="search-params">
            <strong>Search Parameters (from form):</strong><br>
            First Name: {{ original_form_params.first_name }}<br>
            Last Name: {{ original_form_params.last_name }}<br>
            {% if original_form_params.street %}Street: {{ original_form_params.street }}<br>{% endif %}
            {% if original_form_params.city %}City: {{ original_form_params.city }}<br>{% endif %}
            {% if original_form_params.state %}State: {{ original_form_params.state }}<br>{% endif %}
            {% if original_form_params.zip_code %}ZIP: {{ original_form_params.zip_code }}<br>{% endif %}
            {% if original_form_params.phone %}Phone: {{ original_form_params.phone }}<br>{% endif %}
            {% if original_form_params.email %}Email: {{ original_form_params.email }}{% endif %}
        </div>

        <div class="sub-header">Total CA Contributions (matching DB criteria, excluding passthroughs): <strong>{{ total_amount|currency }}</strong></div>
        {% if db_cascade_message %}<div class="db-cascade-info"><strong>{{ db_cascade_message }}</strong></div>{% endif %}

        <h2>Recent CA Contributions ({{ recent_contributions|length }})</h2>
        {% if recent_contributions %}
            <table>
                <tr><th>Date</th><th>Recipient</th><th>Amount</th><th>Contributor Location</th></tr>
                {% for contrib in recent_contributions %}
                    <tr>
                        <td>{{ contrib[0] }}</td>
                        <td>
                           <a href="/committee/{{ contrib[3] }}" target="_blank" title="View recipient details">{{ contrib[1] }}</a>
                        </td>
                        <td>{{ contrib[2]|currency }}</td>
                        <td>{{ contrib[4] }}, {{ contrib[5] }} {{ contrib[6] }}</td>
                    </tr>
                {% endfor %}
            </table>
        {% else %}
            <p class="no-results">{{ no_results_message }}</p>
        {% endif %}

        {% if google_search_url_address %}
        <div class="google-search-section">
            <h2>Google Search: Name + Address</h2>
            <p><em>Searching for: {{ google_search_query_address }}</em></p>
            <iframe src="{{ google_search_url_address }}" title="Google Search Results for {{ google_search_query_address }}"></iframe>
        </div>
        {% endif %}

        {% if google_search_url_phone %}
        <div class="google-search-section">
            <h2>Google Search: Name + Phone</h2>
            <p><em>Searching for: {{ google_search_query_phone }}</em></p>
            <iframe src="{{ google_search_url_phone }}" title="Google Search Results for {{ google_search_query_phone }}"></iframe>
        </div>
        {% endif %}

        {% if google_search_url_email %}
        <div class="google-search-section">
            <h2>Google Search: Name + Email</h2>
            <p><em>Searching for: {{ google_search_query_email }}</em></p>
            <iframe src="{{ google_search_url_email }}" title="Google Search Results for {{ google_search_query_email }}"></iframe>
        </div>
        {% endif %}

        {% if google_search_url_name_city %}
        <div class="google-search-section">
            <h2>Google Search: Name + City</h2>
            <p><em>Searching for: {{ google_search_query_name_city }}</em></p>
            <iframe src="{{ google_search_url_name_city }}" title="Google Search Results for {{ google_search_query_name_city }}"></iframe>
        </div>
        {% endif %}

    </div>
</body>
</html>
"""

RECIPIENT_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Top Contributors to {{ recipient_name }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; margin-bottom:20px;}
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .pagination { margin: 25px 0; text-align: center; clear: both; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 14px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; color: #3498db; text-decoration: none; font-size: 0.95em; }
        .pagination a:hover { background-color: #eaf2f8; border-color: #c5ddec; }
        .pagination .current-page { background-color: #3498db; color: white; border-color: #3498db; font-weight: bold; }
        .results-summary { margin: 20px 0 10px 0; font-size: 0.9em; color: #555; }
        .info-link { text-decoration: none; margin-left: 5px; font-size: 0.9em; color: #7f8c8d; }
        .info-link:hover { color: #3498db; }
    </style>
</head>
<body>
    <h1>Top Contributors to {{ recipient_name }}</h1>
    <p style="color: #666; margin-bottom: 20px;"><em>Showing all-time contribution totals across all years in database</em></p>
    <div class="nav-links">
        <a href="/">🔍 New Search</a>
        <a href="/search_recipients">👥 Search Recipients by Name</a>
        <a href="/personsearch">👤 Person Search</a>
        <a href="http://localhost:5000/search_recipients" style="color: #3498db;" target="_blank">🇺🇸 Search Federal Recipients</a>
    </div>
    <div class="results-summary">
      Showing top {{ (page - 1) * PAGE_SIZE + 1 if total_results > 0 else 0 }} - {{ [page * PAGE_SIZE, total_results]|min }} of {{ total_results }} contributors.
        </div>
    <table>
      <tr><th>First</th><th>Last</th><th>Total Contributed<br><small>(All Time)</small></th></tr>
      {% for fn, ln, total in rows %}
        <tr>
          <td><a href="/contributor?first={{ fn }}&last={{ ln }}">{{ fn }}</a></td>
          <td><a href="/contributor?first={{ fn }}&last={{ ln }}">{{ ln }}</a></td>
          <td>{{ total|currency }}</td>
        </tr>
      {% endfor %}
    </table>
    {% if total_pages > 1 %}
    <div class="pagination">
        {% set base_url = "/recipient?committee_id=" + committee_id + "&" + query_params_without_page %}
        {% if page > 1 %}
            <a href="{{ base_url }}&page={{ page - 1 }}">&laquo; Previous</a>
        {% endif %}
        <span>Page {{ page }} of {{ total_pages }}</span>
        {% if page < total_pages %}
            <a href="{{ base_url }}&page={{ page + 1 }}">Next &raquo;</a>
        {% endif %}
        </div>
    {% endif %}
</body>
</html>
"""

COMMITTEE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ committee_name }} - CA Committee</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { margin-bottom: 20px; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; }
        .committee-info { background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .stats { background: #e8f5e8; padding: 15px; border-radius: 4px; margin-bottom: 20px; }
    </style>
</head>
<body>
    <h1>🏛️ CA Campaign Finance</h1>
    <div class="nav-links">
        <a href="/">💰 Contribution Search</a>
        <a href="/search_recipients">🏢 Recipient Search</a>
        <a href="/personsearch">👤 Person Search</a>
        <a href="http://localhost:5000/" style="color: #3498db;" target="_blank">🇺🇸 Search Federal Data</a>
    </div>
    
    <div class="committee-info">
        <h1>{{ committee_name }}</h1>
        <p><strong>Committee ID:</strong> {{ committee_id }}</p>
        <p><strong>Type:</strong> {{ committee_type if committee_type else "Unknown" }}</p>
        <p><a href="/">← Back to Search</a></p>
    </div>
    
    <div class="stats">
        <strong>Total Contributions:</strong> {{ total_count|comma }} contributions, {{ total_amount|currency }}
        {% if total_count >= 1000 %}<br><em>Showing most recent 1,000 contributions</em>{% endif %}
    </div>
    
    <form method="get" style="margin-bottom: 20px; background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
        <label for="sort_by" style="margin-right: 10px;">Sort by:</label>
        <select name="sort_by" id="sort_by" onchange="this.form.submit()" style="padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
            <option value="date_desc" {% if sort_by == 'date_desc' %}selected{% endif %}>Date (newest)</option>
            <option value="date_asc" {% if sort_by == 'date_asc' %}selected{% endif %}>Date (oldest)</option>
            <option value="amount_desc" {% if sort_by == 'amount_desc' %}selected{% endif %}>Amount (highest)</option>
            <option value="amount_asc" {% if sort_by == 'amount_asc' %}selected{% endif %}>Amount (lowest)</option>
        </select>
    </form>
    
    {% if contributions %}
        <table>
            <tr>
                <th><a href="/committee/{{ committee_id }}?{{ committee_query_without_sort }}&sort_by={{ 'date_desc' if sort_by != 'date_desc' else 'date_asc' }}">Date</a></th>
                <th>Contributor</th>
                <th>Location</th>
                <th><a href="/committee/{{ committee_id }}?{{ committee_query_without_sort }}&sort_by={{ 'amount_desc' if sort_by != 'amount_desc' else 'amount_asc' }}">Amount</a></th>
                <th>Employer/Occupation</th>
            </tr>
            {% for date, first, last, city, state, amount, employer, occupation in contributions %}
            <tr>
                <td>{{ date }}</td>
                <td><a href="/contributor?first={{ first }}&last={{ last }}">{{ first }} {{ last }}</a></td>
                <td>{{ city }}{% if city and state %}, {% endif %}{{ state }}</td>
                <td>{{ amount|currency }}</td>
                <td>{{ employer }}{% if employer and occupation %} / {% endif %}{{ occupation }}</td>
            </tr>
            {% endfor %}
        </table>
    {% else %}
        <p>No contributions found for this committee.</p>
    {% endif %}
</body>
</html>
"""

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run California Contribution Search App')
    parser.add_argument('--public', action='store_true', 
                        help='Run on 0.0.0.0. WARNING: For testing only.')
    args = parser.parse_args()

    host = '0.0.0.0' if args.public else '127.0.0.1'
    debug = False if args.public else True
    
    print(f"🚀 Starting California app on http://{host}:5001")
    app.run(debug=debug, host=host, port=5001)
