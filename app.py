from flask import Flask, request, render_template_string, jsonify
import sqlite3
import pprint
import time
import math # For math.ceil
from urllib.parse import urlencode, quote_plus # Added quote_plus
import argparse

app = Flask(__name__)
DB_PATH = "fec_contributions.db"
PAGE_SIZE = 50 # Items per page for pagination
PERSON_SEARCH_PAGE_SIZE = 10 # Specific page size for recent contributions on person page

# Custom Jinja2 filter for currency formatting
def format_currency(value):
    # Handle None case explicitly
    if value is None:
        return "$0.00"
    return "${:,.2f}".format(value)

app.jinja_env.filters['currency'] = format_currency

# Custom Jinja2 filter for comma-separated numbers
def format_comma(value):
    if value is None:
        return "0"
    return "{:,}".format(int(value))

app.jinja_env.filters['comma'] = format_comma
app.jinja_env.globals['min'] = min
app.jinja_env.globals['max'] = max
app.jinja_env.filters['quote_plus'] = quote_plus # Changed from globals to filters

# Helper function to build CA app URLs with preserved parameters
def build_ca_app_url(route="/", params=None):
    """Build URL for CA app with preserved search parameters."""
    if params is None:
        params = {}
    
    # Map national parameters to CA app parameters
    ca_params = {}
    for key, value in params.items():
        if key in ["first_name", "last_name", "city", "state", "zip_code", "year", "sort_by", "order"]:
            ca_params[key] = value
    
    base_url = f"http://localhost:5001{route}"
    if ca_params:
        base_url += "?" + urlencode(ca_params)
    
    return base_url

# Add to template globals
app.jinja_env.globals['build_ca_app_url'] = build_ca_app_url

# --- Helper Functions ---
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

# --- Security Headers --- 
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Update CSP to allow Google iframes needed for /person route
    response.headers['Content-Security-Policy'] = "default-src 'self'; frame-src https://www.google.com/; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none';"
    return response

# Known passthrough platforms like ActBlue and WinRed
KNOWN_CONDUITS = {
    "C00401224": "ACTBLUE",
    "C00694323": "WINRED",
    "C00708504": "NATIONBUILDER",
    "C00580100": "REPUBLICAN PLATFORM FUND",
}

def map_cmte_type(code):
    return {
        "H": "Candidate",
        "S": "Candidate",
        "P": "Candidate",
        "X": "Party Committee",
        "Y": "Party Committee",
    }.get(code, "PAC")

def get_db():
    return sqlite3.connect(DB_PATH)

def get_donor_percentiles_by_year(first_name, last_name, zip_code):
    """
    Get percentile rankings for a donor across all years they have data.
    Returns dict: {year: {"percentile": float, "rank": int, "total_amount": float, "total_donors": int}}
    """
    if not zip_code or len(zip_code) < 5:
        return {}
    
    zip5 = zip_code[:5]  # Use first 5 digits
    donor_key = f"{first_name}|{last_name}|{zip5}"
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Get donor's totals by year
    cursor.execute("""
        SELECT year, total_amount, contribution_count
        FROM donor_totals_by_year 
        WHERE donor_key = ?
        ORDER BY year DESC
    """, (donor_key,))
    
    donor_years = cursor.fetchall()
    if not donor_years:
        conn.close()
        return {}
    
    percentiles = {}
    
    for year, total_amount, contrib_count in donor_years:
        # Count donors with higher totals in this year
        cursor.execute("""
            SELECT COUNT(*) 
            FROM donor_totals_by_year 
            WHERE year = ? AND total_amount > ?
        """, (year, total_amount))
        
        donors_above = cursor.fetchone()[0]
        
        # Get total donor count for the year
        cursor.execute("""
            SELECT COUNT(*) 
            FROM donor_totals_by_year 
            WHERE year = ?
        """, (year,))
        
        total_donors = cursor.fetchone()[0]
        
        if total_donors > 0:
            # Calculate percentile (higher percentile = better rank)
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
    # --- Get original search parameters ---
    original_params = {
        "first_name": request.args.get("first_name", "").strip().upper(),
        "last_name": request.args.get("last_name", "").strip().upper(),
        "zip_code": request.args.get("zip_code", "").strip().upper(),
        "year": request.args.get("year", "").strip(),
        "city": request.args.get("city", "").strip().upper(),
        "state": request.args.get("state", "").strip().upper(),
        "sort_by": request.args.get("sort_by", "contribution_date"),
        "order": request.args.get("order", "desc")
    }
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1

    # Validate sort parameters
    if original_params["sort_by"] not in {"contribution_date", "amount"}:
        original_params["sort_by"] = "contribution_date"
    if original_params["order"] not in {"asc", "desc"}:
        original_params["order"] = "desc"

    # Validate year format (only use if 4 digits)
    year_filter = None
    if original_params["year"] and original_params["year"].isdigit() and len(original_params["year"]) == 4:
        year_filter = original_params["year"]
    else:
        original_params["year"] = "" # Clear invalid year from displayed params

    # Determine if a search should be performed
    search_criteria_provided = bool(
        original_params["first_name"] or original_params["last_name"] or 
        original_params["zip_code"] or original_params["city"] or 
        original_params["state"] or year_filter
    )

    results = []
    total_results = 0
    total_pages = 0
    effective_params = {} # To store the params that yielded results
    cascade_message = "" # Message explaining which filters were dropped
    no_results_detail_message = None # Initialize here

    if search_criteria_provided:
        conn = get_db()
        cursor = conn.cursor()

        # --- Define search attempts (cascading logic) ---
        search_attempts = []
        base_attempt_params = original_params.copy()
        
        # Attempt 1: All provided filters
        search_attempts.append({"params": base_attempt_params.copy(), "level": "All filters"})

        # Attempt 2: Drop ZIP (if ZIP was provided)
        if base_attempt_params["zip_code"]:
            attempt_2_params = base_attempt_params.copy()
            attempt_2_params["zip_code"] = "" # Drop zip
            search_attempts.append({"params": attempt_2_params, "level": "Dropped ZIP Code"})

        # Attempt 3: Drop City & ZIP (if City was provided)
        # Note: State remains if it was provided independently
        if base_attempt_params["city"]:
            attempt_3_params = base_attempt_params.copy()
            attempt_3_params["zip_code"] = "" # Drop zip (might have been dropped already, ensures it)
            attempt_3_params["city"] = ""     # Drop city
            search_attempts.append({"params": attempt_3_params, "level": "Dropped City & ZIP Code"})
        
        # --- Loop through attempts --- 
        found_results = False
        for attempt in search_attempts:
            current_params = attempt["params"]
            level = attempt["level"]

            # Build WHERE clauses and params for this attempt
            where_clauses = ["c.recipient_name NOT IN ({})".format(",".join(["?"] * len(KNOWN_CONDUITS)))]
            query_params_list = list(KNOWN_CONDUITS.keys())
            is_name_search = False # Flag to track if name was searched

            if current_params["first_name"]:
                where_clauses.append("c.first_name = ?")
                query_params_list.append(current_params["first_name"])
                is_name_search = True
            if current_params["last_name"]:
                where_clauses.append("c.last_name = ?")
                query_params_list.append(current_params["last_name"])
                is_name_search = True
            if current_params["zip_code"]: # Use the potentially modified zip
                where_clauses.append("c.zip_code LIKE ?")
                query_params_list.append(current_params["zip_code"] + "%")
            if current_params["city"]: # Use the potentially modified city
                where_clauses.append("c.city = ?")
                query_params_list.append(current_params["city"])
            
            # State handling: Only apply if explicitly provided by user
            state_filter_applied = None # Keep track for logging/message clarity if needed
            if current_params["state"]: # User provided state
                where_clauses.append("c.state = ?")
                query_params_list.append(current_params["state"])
                state_filter_applied = current_params["state"]
            # REMOVED: CA default logic

            if year_filter: # Use validated year
                start_date = f"{year_filter}-01-01"
                end_date = f"{year_filter}-12-31"
                where_clauses.append("c.contribution_date >= ? AND c.contribution_date <= ?")
                query_params_list.extend([start_date, end_date])

            where_string = " WHERE " + " AND ".join(where_clauses)

            # Execute COUNT query for this attempt
            count_query_sql = f"SELECT COUNT(*) FROM contributions c {where_string}" 
            print(f"\n📋 Executing SQL (Count - Attempt: {level}):")
            print(count_query_sql)
            print("📎 With params:")
            pprint.pprint(query_params_list)
            
            cursor.execute(count_query_sql, query_params_list)
            current_total_results = cursor.fetchone()[0]

            if current_total_results > 0:
                total_results = current_total_results
                total_pages = math.ceil(total_results / PAGE_SIZE)
                effective_params = current_params # Store the successful parameters
                found_results = True
                
                # Set cascade message if filters were dropped
                cascade_parts = []
                if level == "Dropped ZIP Code":
                    cascade_parts.append("dropped ZIP Code filter")
                elif level == "Dropped City & ZIP Code":
                    cascade_parts.append("dropped City & ZIP Code filters")
                # REMOVED: check for CA default application message
                
                if cascade_parts:
                    cascade_message = f"(Results found after { ' and '.join(cascade_parts) })"
                else:
                    cascade_message = "" # Explicitly clear for 'All filters'
                
                # Calculate offset for pagination
                offset = (page - 1) * PAGE_SIZE

                # Construct and execute the main data query using effective criteria
                base_select_for_data_columns = """
                    c.first_name, c.last_name, c.contribution_date,
                    COALESCE(m.name, c.recipient_name), c.amount, 
                    COALESCE(m.type, ''), c.recipient_name,
                    c.city, c.state, c.zip_code
                """
                from_join_clause = "FROM contributions c LEFT JOIN committees m ON c.recipient_name = m.committee_id"
                
                # Rebuild WHERE clause and params for the successful attempt for data query
                final_where_clauses = ["c.recipient_name NOT IN ({})".format(",".join(["?"] * len(KNOWN_CONDUITS)))]
                final_query_params = list(KNOWN_CONDUITS.keys())
                if effective_params["first_name"]:
                    final_where_clauses.append("c.first_name = ?")
                    final_query_params.append(effective_params["first_name"])
                if effective_params["last_name"]:
                    final_where_clauses.append("c.last_name = ?")
                    final_query_params.append(effective_params["last_name"])
                if effective_params["zip_code"]:
                    final_where_clauses.append("c.zip_code LIKE ?")
                    final_query_params.append(effective_params["zip_code"] + "%")
                if effective_params["city"]:
                    final_where_clauses.append("c.city = ?")
                    final_query_params.append(effective_params["city"])
                if effective_params["state"]: # User provided state in successful attempt
                    final_where_clauses.append("c.state = ?")
                    final_query_params.append(effective_params["state"])
                # REMOVED: CA default application in final query

                if year_filter: # Use validated year
                    start_date = f"{year_filter}-01-01"
                    end_date = f"{year_filter}-12-31"
                    final_where_clauses.append("c.contribution_date >= ? AND c.contribution_date <= ?")
                    final_query_params.extend([start_date, end_date])

                final_where_string = " WHERE " + " AND ".join(final_where_clauses)
                
                data_query_sql = (
                    f"SELECT {base_select_for_data_columns} {from_join_clause}{final_where_string} "
                    f"ORDER BY c.{effective_params['sort_by']} {effective_params['order']} LIMIT ? OFFSET ?"
                )
                paged_data_params = final_query_params + [PAGE_SIZE, offset]

                print(f"\n📋 Executing SQL (Data - Effective Level: {level}, State Filter: {state_filter_applied if state_filter_applied else 'None'}):") # Log state filter used
                print(data_query_sql)
                print("📎 With params:")
                pprint.pprint(paged_data_params)
                
                start_time = time.time()
                cursor.execute(data_query_sql, paged_data_params)
                results = cursor.fetchall()
                end_time = time.time()
                print(f"⏱️ Query executed in {end_time - start_time:.4f} seconds")
                
                break # Exit the loop once results are found
        
        conn.close()
        
        # Construct the final "No results" message if applicable
        # no_results_detail_message = "" # No longer need to initialize here
        if search_criteria_provided and not found_results:
             print("\nℹ️ No results found after all cascade attempts on main search.")
             # Use original_params to describe what was initially searched
             initial_criteria_list = []
             if original_params["first_name"]: initial_criteria_list.append(f"First Name: {original_params['first_name']}")
             if original_params["last_name"]: initial_criteria_list.append(f"Last Name: {original_params['last_name']}")
             if original_params["city"]: initial_criteria_list.append(f"City: {original_params['city']}")
             if original_params["state"]: # State is only the explicitly provided one here
                 initial_criteria_list.append(f"State: {original_params['state']}")
             # REMOVED: Mentioning CA default
             if original_params["zip_code"]: initial_criteria_list.append(f"ZIP: {original_params['zip_code']}")
             if year_filter: initial_criteria_list.append(f"Year: {year_filter}") # Use validated year
             
             no_results_detail_message = f"No contributions found matching: { ', '.join(initial_criteria_list) }."
             
             # Add details about failed cascades
             original_had_zip = original_params["zip_code"]
             original_had_city = original_params["city"]
             
             cascade_attempts_failed_msg = ""
             if original_had_zip and original_had_city:
                 cascade_attempts_failed_msg = " Also tried searching without ZIP, and without City & ZIP."
             elif original_had_zip:
                 cascade_attempts_failed_msg = " Also tried searching without ZIP."
             
             no_results_detail_message += cascade_attempts_failed_msg
             no_results_detail_message += " Consider broadening your search criteria."

    # Render template using original params for form fields, 
    # but effective params for pagination links
    # Also pass cascade_message
    # Regenerate pagination_params using effective_params (no default state logic needed)
    pagination_params = {k: v for k, v in effective_params.items() if k not in ['page'] and v}
    
    return render_template_string("""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FEC Contribution Search</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { margin-bottom: 20px; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; }
        form { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; display: flex; flex-wrap: wrap; gap: 15px; align-items: center; }
        form input[type="text"], form input[type="date"], form select { padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 1em; flex-grow: 1; min-width: 120px; }
        form input[type="submit"], button { background-color: #3498db; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        form input[type="submit"]:hover, button:hover { background-color: #2980b9; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .form-group { display: flex; flex-direction: column; }
        .form-group label { margin-bottom: 5px; font-weight: 500; }
        .form-group small { font-size: 0.8em; color: #777; margin-top: 3px; }
        .pagination { margin: 25px 0; text-align: center; clear: both; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 14px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; color: #3498db; text-decoration: none; font-size: 0.95em; }
        .pagination a:hover { background-color: #eaf2f8; border-color: #c5ddec; }
        .pagination .current-page { background-color: #3498db; color: white; border-color: #3498db; font-weight: bold; }
        .results-summary { margin: 20px 0 5px 0; font-size: 0.9em; color: #555; }
        .cascade-info { margin: 0 0 10px 0; font-size: 0.85em; color: #e67e22; font-style: italic; }
        .info-link { text-decoration: none; margin-left: 5px; font-size: 0.9em; color: #7f8c8d; }
        .info-link:hover { color: #3498db; }
    </style>
</head>
<body>
    <h1>FEC Contribution Search</h1>
    <div class="nav-links">
        <a href="/">🔍 New Search</a>
        <a href="/search_recipients">👥 Search Recipients by Name</a>
        <a href="/personsearch">👤 Person Search</a>
        <a href="{{ build_ca_app_url('/', original_params) }}" style="color: #ff6b35;" target="_blank">🏛️ Search CA Data</a>
    </div>
    <form method="get" onsubmit="document.getElementById('mainSearchButton').disabled = true; document.getElementById('mainLoadingIndicator').style.display = 'block';">
        <div class="form-group">
            <label for="first_name">First Name:</label>
            <input id="first_name" name="first_name" value="{{ original_params.first_name }}">
        </div>
        <div class="form-group">
            <label for="last_name">Last Name:</label>
            <input id="last_name" name="last_name" value="{{ original_params.last_name }}">
        </div>
        <div class="form-group">
            <label for="zip_code">ZIP Code:</label>
            <input id="zip_code" name="zip_code" value="{{ original_params.zip_code }}">
        </div>
        <div class="form-group">
            <label for="city">City:</label>
            <input id="city" name="city" value="{{ original_params.city }}">
        </div>
        <div class="form-group">
            <label for="state">State:</label>
            <input id="state" name="state" value="{{ original_params.state }}" maxlength="2" size="5">
            {# REMOVED default CA helper text #}
        </div>
        <div class="form-group">
            <label for="year">Year:</label>
            <input id="year" type="text" name="year" value="{{ original_params.year }}" pattern="\\d{4}" title="Enter a 4-digit year" size="7">
        </div>
        <div class="form-group">
            <label for="sort_by">Sort By:</label>
            <select id="sort_by" name="sort_by">
                <option value="contribution_date" {% if original_params.sort_by == 'contribution_date' %}selected{% endif %}>Date</option>
                <option value="amount" {% if original_params.sort_by == 'amount' %}selected{% endif %}>Amount</option>
            </select>
        </div>
        <div class="form-group">
            <label for="order">Order:</label>
            <select id="order" name="order">
                <option value="desc" {% if original_params.order == 'desc' %}selected{% endif %}>Desc</option>
                <option value="asc" {% if original_params.order == 'asc' %}selected{% endif %}>Asc</option>
            </select>
        </div>
        <input type="submit" value="Search" id="mainSearchButton" style="align-self: flex-end;">
        <span id="mainLoadingIndicator" style="display:none; color: #e67e22; font-weight: bold; margin-left: 10px; align-self: flex-end;">Searching...</span>
    </form>

    {% if results %}
      <h2>Results</h2>
      <div class="results-summary">
        Showing {{ (page - 1) * PAGE_SIZE + 1 }} - {{ min(page * PAGE_SIZE, total_results) }} of {{ total_results }} contributions.
      </div>
      {% if cascade_message %}<div class="cascade-info"><strong>{{ cascade_message }}</strong></div>{% endif %}
      <table>
        <tr>
          <th>First</th><th>Last</th><th>Date</th><th>Recipient</th><th>Amount</th><th>Type</th>
          <th>City</th><th>State</th><th>ZIP</th>
        </tr>
        {% for fn, ln, date, recip, amt, typ, cmte_id, city, state, zip in results %}
          <tr>
            {# Pass city, state, zip to contributor view #}
            <td><a href="/contributor?first={{ fn }}&last={{ ln }}&city={{ city|urlencode }}&state={{ state|urlencode }}&zip={{ zip|urlencode }}">{{ fn }}</a></td>
            <td><a href="/contributor?first={{ fn }}&last={{ ln }}&city={{ city|urlencode }}&state={{ state|urlencode }}&zip={{ zip|urlencode }}">{{ ln }}</a></td>
            <td>{{ date }}</td>
            <td>
                <a href="/recipient?committee_id={{ cmte_id }}">{{ recip }}</a>
                <a href="https://www.google.com/search?q={{ recip|quote_plus }}" class="info-link" target="_blank" title="Search Google for {{ recip }}">&#x24D8;</a>
            </td>
            <td>{{ amt|currency }}</td>
            <td>{{ typ|default("PAC") }}</td>
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
        {# Only show 'No results' if a search was actually attempted #}
        <h2>No results found.</h2>
        {# Display the detailed message constructed in the backend #}
        <p>{{ no_results_detail_message }}</p>
    {% endif %}
</body>
</html>
""", 
       results=results, page=page, total_pages=total_pages, total_results=total_results, 
       PAGE_SIZE=PAGE_SIZE, original_params=original_params, # Pass original params for form repopulation
       pagination_params=pagination_params, # Pass effective params for pagination
       urlencode=urlencode, # Pass urlencode for use in template
       cascade_message=cascade_message, # Pass cascade message
       search_criteria_provided=search_criteria_provided, # To control display of 'No results' message
       no_results_detail_message=no_results_detail_message # Pass detailed no results message
   )

@app.route("/contributor")
def contributor_view():
    first = request.args.get("first", "").strip()
    last = request.args.get("last", "").strip()
    # Get optional address params, keep original case if needed for display
    city = request.args.get("city", "").strip()
    state = request.args.get("state", "").strip()
    zip_code = request.args.get("zip", "").strip()
    
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1
    offset = (page - 1) * PAGE_SIZE

    if not first or not last:
        return "Missing first and last name", 400

    conn = get_db()
    cursor = conn.cursor()

    # --- Build base WHERE clause and params ---
    base_where_clauses = ["c.first_name = ?", "c.last_name = ?"]
    query_params = [first, last]

    # Add address filters if provided
    if city:
        base_where_clauses.append("c.city = ?")
        query_params.append(city)
    if state:
        base_where_clauses.append("c.state = ?")
        query_params.append(state)
    if zip_code:
        base_where_clauses.append("c.zip_code LIKE ?")
        query_params.append(zip_code + "%")
        
    # Exclude known conduits
    conduit_placeholders = ",".join(["?"] * len(KNOWN_CONDUITS))
    base_where_clauses.append(f"c.recipient_name NOT IN ({conduit_placeholders})")
    final_query_params = query_params + list(KNOWN_CONDUITS.keys())
    
    where_string = " AND ".join(base_where_clauses)
    from_clause = "FROM contributions c LEFT JOIN committees m ON c.recipient_name = m.committee_id"

    # --- Count Query --- 
    count_query_sql = f"SELECT COUNT(*) {from_clause} WHERE {where_string}"
    
    # --- Data Query --- 
    data_query_sql = f"""
        SELECT c.contribution_date, COALESCE(m.name, c.recipient_name) as recipient_name,
               c.amount, COALESCE(m.type, '') as recipient_type, c.recipient_name as committee_id,
               c.city, c.state, c.zip_code
        {from_clause}
        WHERE {where_string}
        ORDER BY c.contribution_date DESC LIMIT ? OFFSET ?
    """
    paged_data_params = final_query_params + [PAGE_SIZE, offset]

    # --- Sum Query --- 
    sum_query_sql = f"SELECT SUM(c.amount) {from_clause} WHERE {where_string}"

    # --- Execute Queries --- 
    print("\n📋 Executing SQL (/contributor count):")
    print(count_query_sql)
    print("📎 With params:")
    pprint.pprint(final_query_params)
    cursor.execute(count_query_sql, final_query_params) # Use final params for count
    total_results = cursor.fetchone()[0]
    total_pages = math.ceil(total_results / PAGE_SIZE)

    print("\n📋 Executing SQL (/contributor data):")
    print(data_query_sql)
    print("📎 With params:")
    pprint.pprint(paged_data_params)
    start_time = time.time()
    cursor.execute(data_query_sql, paged_data_params)
    rows = cursor.fetchall()
    end_time = time.time()
    print(f"⏱️ Query executed in {end_time - start_time:.4f} seconds")
    
    print("\n📋 Executing SQL (/contributor sum):")
    print(sum_query_sql)
    print("📎 With params:")
    pprint.pprint(final_query_params)
    cursor.execute(sum_query_sql, final_query_params) # Use final params for sum query
    total_amount_for_contributor = cursor.fetchone()[0] or 0
    conn.close()
    
    # Get percentile data for this donor
    percentiles_by_year = {}
    if zip_code:  # Only calculate if we have ZIP for proper identification
        percentiles_by_year = get_donor_percentiles_by_year(first, last, zip_code)

    # --- Prepare Pagination URL --- 
    pagination_params = {"first": first, "last": last}
    if city: pagination_params["city"] = city
    if state: pagination_params["state"] = state
    if zip_code: pagination_params["zip"] = zip_code
    # Add any other persistent query params if needed (e.g., sort order from original search? - Not currently passed)
    base_pagination_url = "/contributor?" + urlencode(pagination_params)

    # --- Render Template --- 
    # Construct a filter description string
    filter_desc = f"{first} {last}"
    location_parts = []
    if city: location_parts.append(city)
    if state: location_parts.append(state)
    if zip_code: location_parts.append(zip_code)
    if location_parts:
        # Corrected f-string concatenation
        filter_desc += f" from {', '.join(location_parts)}"
        
    return render_template_string("""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Contributions by {{ filter_desc }}</title>
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
    <h1>Contributions by {{ first }} {{ last }}</h1>
    <div class="filter-info">Showing contributions matching: {{ filter_desc }}</div>
    <div class="nav-links">
        <a href="/">🔍 New Search</a>
        <a href="/search_recipients">👥 Search Recipients by Name</a>
        <a href="/personsearch">👤 Person Search</a>
        <a href="{{ build_ca_app_url('/contributor', {'first': first, 'last': last, 'city': city, 'state': state, 'zip': zip_code}) }}" style="color: #ff6b35;" target="_blank">🏛️ Search CA Data</a>
    </div>
    <h2>Total Contributed (matching filter, all pages): {{ total_amount_for_contributor|currency }}</h2>
    
    {% if percentiles_by_year and zip_code %}
    <div style="background-color: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 20px 0;">
        <h3 style="margin-top: 0; color: #2c3e50;">📊 Donor Percentile Rankings</h3>
        <p style="font-size: 0.9em; color: #666; margin-bottom: 15px;">
            Based on total annual contributions among all donors identified as: {{ first }} {{ last }} ({{ zip_code[:5] }})
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
        📊 Percentile rankings will be available after running the percentile table builder script.
    </div>
    {% endif %}
    
    <div class="results-summary">
      Showing {{ (page - 1) * PAGE_SIZE + 1 if total_results > 0 else 0 }} - {{ min(page * PAGE_SIZE, total_results) }} of {{ total_results }} contributions.
    </div>
    <table>
      <tr><th>Date</th><th>Recipient</th><th>Amount</th><th>Type</th>
          <th>City</th><th>State</th><th>ZIP</th></tr>
      {# Loop variable names adjusted slightly for clarity #}
      {% for r_date, r_name, r_amt, r_type, r_cmte_id, r_city, r_state, r_zip in rows %}
        <tr>
          <td>{{ r_date }}</td>
          <td>
              <a href="/recipient?committee_id={{ r_cmte_id }}">{{ r_name }}</a>
              <a href="https://www.google.com/search?q={{ r_name|quote_plus }}" class="info-link" target="_blank" title="Search Google for {{ r_name }}">&#x24D8;</a>
          </td>
          <td>{{ r_amt|currency }}</td>
          <td>{{ r_type|default("PAC") }}</td>
          {# Display the location from the contribution record itself #}
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
""", 
       first=first, last=last, 
       city=city, state=state, zip_code=zip_code, # Pass params for display/debug
       filter_desc=filter_desc, # Pass constructed filter description
       total_amount_for_contributor=total_amount_for_contributor, rows=rows, 
       page=page, total_pages=total_pages, total_results=total_results, 
       PAGE_SIZE=PAGE_SIZE,
       base_pagination_url=base_pagination_url, # Use pre-built base URL for pagination
       percentiles_by_year=percentiles_by_year # Pass percentile data
   )

@app.route("/recipient")
def recipient_view():
    committee_id = request.args.get("committee_id", "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1
    offset = (page - 1) * PAGE_SIZE

    if not committee_id:
        return "Missing committee_id", 400

    if committee_id in KNOWN_CONDUITS:
        return render_template_string(f"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{KNOWN_CONDUITS[committee_id]} - Passthrough</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }}
        h1 {{ color: #2c3e50; margin-bottom: 20px; }}
        a {{ color: #3498db; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .nav-links a {{ margin-right: 15px; font-size: 1.1em; display: inline-block; margin-bottom:20px;}}
    </style>
</head>
<body>
    <h1>{KNOWN_CONDUITS[committee_id]} is a passthrough platform. No direct contributors shown.</h1>
    <div class="nav-links">
        <a href='/'>🔍 Back to search</a>
        <a href="/personsearch">👤 Person Search</a>
    </div>
</body>
</html>
""")

    conn = get_db()
    cursor = conn.cursor()

    start_time_initial = time.time()
    cursor.execute("SELECT name FROM committees WHERE committee_id = ?", (committee_id,))
    name_row = cursor.fetchone()
    end_time_initial = time.time()
    print(f"⏱️ Initial committee lookup executed in {end_time_initial - start_time_initial:.4f} seconds")

    recipient_name = name_row[0] if name_row else committee_id

    # Query for paged results
    data_query_str = """
        SELECT first_name, last_name, SUM(amount) as total
        FROM contributions
        WHERE recipient_name = ?
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
            WHERE recipient_name = ?
            GROUP BY first_name, last_name
        )
    """
    count_params = [committee_id]

    print("\n📋 Executing SQL (/recipient count):")
    print(count_query_str)
    print("📎 With params:")
    pprint.pprint(count_params)
    
    cursor.execute(count_query_str, count_params)
    total_results = cursor.fetchone()[0]
    total_pages = math.ceil(total_results / PAGE_SIZE)

    print("\n📋 Executing SQL (/recipient data):")
    print(data_query_str)
    print("📎 With params:")
    pprint.pprint(data_params)

    start_time = time.time()
    cursor.execute(data_query_str, data_params)
    rows = cursor.fetchall()
    end_time = time.time()
    print(f"⏱️ Query executed in {end_time - start_time:.4f} seconds")
    conn.close()

    return render_template_string("""
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
        .pagination {
            margin: 25px 0;
            text-align: center;
            clear: both;
        }
        .pagination a, .pagination span {
            display: inline-block;
            padding: 8px 14px;
            margin: 0 4px;
            border: 1px solid #ddd;
            border-radius: 4px;
            color: #3498db;
            text-decoration: none;
            font-size: 0.95em;
        }
        .pagination a:hover {
            background-color: #eaf2f8;
            border-color: #c5ddec;
        }
        .pagination .current-page {
            background-color: #3498db;
            color: white;
            border-color: #3498db;
            font-weight: bold;
        }
        .results-summary {
            margin: 20px 0 10px 0;
            font-size: 0.9em;
            color: #555;
        }
        .info-link {
            text-decoration: none;
            margin-left: 5px;
            font-size: 0.9em;
            color: #7f8c8d;
        }
        .info-link:hover {
            color: #3498db;
        }
    </style>
</head>
<body>
    <h1>Top Contributors to {{ recipient_name }}</h1>
    <p style="color: #666; margin-bottom: 20px;"><em>Showing all-time contribution totals across all years in database</em></p>
    <div class="nav-links">
        <a href="/">🔍 New Search</a>
        <a href="/search_recipients">👥 Search Recipients by Name</a>
        <a href="/personsearch">👤 Person Search</a>
    </div>
    <div class="results-summary">
      Showing top {{ (page - 1) * PAGE_SIZE + 1 if total_results > 0 else 0 }} - {{ min(page * PAGE_SIZE, total_results) }} of {{ total_results }} contributors.
    </div>
    <table>
      <tr><th>First</th><th>Last</th><th>Total Contributed<br><small>(All Time)</small></th></tr>
      {% for fn, ln, total in rows %}
        <tr>
          {# Pass first/last name, but address info isn't available here to add #}
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
""", recipient_name=recipient_name, rows=rows, committee_id=committee_id, 
   page=page, total_pages=total_pages, total_results=total_results, PAGE_SIZE=PAGE_SIZE,
   query_params_without_page=urlencode({k:v for k,v in request.args.items() if k not in ['page', 'committee_id']}))

@app.route("/search_recipients", methods=["GET"])
def search_recipients_by_name():
    name_query = request.args.get("name_query", "").strip()
    sort_by = request.args.get("sort_by", "recent_activity")  # recent_activity, total_activity, alphabetical
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1
    offset = (page - 1) * PAGE_SIZE
    results = []
    total_pages = 0
    total_results = 0

    if name_query:
        conn = get_db()
        cursor = conn.cursor()
        
        # Check if recipient_lookup table exists
        cursor.execute("""
            SELECT COUNT(*) FROM sqlite_master 
            WHERE type='table' AND name='recipient_lookup'
        """)
        has_lookup_table = cursor.fetchone()[0] > 0
        
        if has_lookup_table:
            # Use fast lookup table with FTS search
            print(f"\n🔍 Using recipient lookup table for fuzzy search: '{name_query}'")
            
            # Determine sort order
            if sort_by == "recent_activity":
                order_clause = "recipient_lookup.recent_contributions DESC, recipient_lookup.recent_amount DESC, recipient_lookup.total_contributions DESC"
                order_clause_simple = "recent_contributions DESC, recent_amount DESC, total_contributions DESC"
            elif sort_by == "total_activity":
                order_clause = "recipient_lookup.total_contributions DESC, recipient_lookup.total_amount DESC, recipient_lookup.recent_contributions DESC"
                order_clause_simple = "total_contributions DESC, total_amount DESC, recent_contributions DESC"
            else:  # alphabetical
                order_clause = "recipient_lookup.display_name ASC"
                order_clause_simple = "display_name ASC"
            
            # Try FTS search first, then fall back to LIKE search
            fts_count_query = """
                SELECT COUNT(*)
                FROM recipient_lookup_fts fts
                JOIN recipient_lookup ON fts.recipient_name = recipient_lookup.recipient_name
                WHERE recipient_lookup_fts MATCH ?
            """
            fts_params = [name_query]
            
            cursor.execute(fts_count_query, fts_params)
            fts_results = cursor.fetchone()[0]
            
            if fts_results > 0:
                # Use FTS search
                total_results = fts_results
                total_pages = math.ceil(total_results / PAGE_SIZE)
                
                data_query = f"""
                    SELECT recipient_lookup.recipient_name, recipient_lookup.display_name, recipient_lookup.committee_type,
                           recipient_lookup.total_contributions, recipient_lookup.total_amount,
                           recipient_lookup.recent_contributions, recipient_lookup.recent_amount,
                           recipient_lookup.last_contribution_date
                    FROM recipient_lookup_fts fts
                    JOIN recipient_lookup ON fts.recipient_name = recipient_lookup.recipient_name
                    WHERE recipient_lookup_fts MATCH ?
                    ORDER BY {order_clause}
                    LIMIT ? OFFSET ?
                """
                params = fts_params + [PAGE_SIZE, offset]
                print("   Using FTS search")
            else:
                # Fall back to LIKE search on display names
                like_count_query = """
                    SELECT COUNT(*) FROM recipient_lookup 
                    WHERE display_name LIKE ? OR recipient_name LIKE ?
                """
                like_params = [f"%{name_query}%", f"%{name_query}%"]
                
                cursor.execute(like_count_query, like_params)
                total_results = cursor.fetchone()[0]
                total_pages = math.ceil(total_results / PAGE_SIZE)
                
                data_query = f"""
                    SELECT recipient_name, display_name, committee_type,
                           total_contributions, total_amount,
                           recent_contributions, recent_amount,
                           last_contribution_date
                    FROM recipient_lookup 
                    WHERE display_name LIKE ? OR recipient_name LIKE ?
                    ORDER BY {order_clause_simple}
                    LIMIT ? OFFSET ?
                """
                params = like_params + [PAGE_SIZE, offset]
                print("   Using LIKE search (FTS had no results)")
            
            print(f"\n📋 Executing SQL (/search_recipients - lookup table):")
            print(data_query)
            print("📎 With params:")
            pprint.pprint(params)

            start_time = time.time()
            cursor.execute(data_query, params)
            lookup_results = cursor.fetchall()
            end_time = time.time()
            print(f"⏱️ Query executed in {end_time - start_time:.4f} seconds")
            
            # Convert lookup results to format expected by template
            results = []
            for row in lookup_results:
                recipient_name, display_name, committee_type, total_contrib, total_amt, recent_contrib, recent_amt, last_date = row
                results.append((recipient_name, display_name, committee_type, 
                              total_contrib, total_amt, recent_contrib, recent_amt, last_date))
        
        else:
            # Fall back to original committees table search
            print(f"\n⚠️  Recipient lookup table not found, using committees table")
            
            count_sql_query = "SELECT COUNT(*) FROM committees WHERE name LIKE ?"
            count_params = [f"%{name_query}%"]
            cursor.execute(count_sql_query, count_params)
            total_results = cursor.fetchone()[0]
            total_pages = math.ceil(total_results / PAGE_SIZE)

            sql_query = "SELECT committee_id, name, type FROM committees WHERE name LIKE ? ORDER BY name LIMIT ? OFFSET ?"
            params = [f"%{name_query}%", PAGE_SIZE, offset]

            print("\n📋 Executing SQL (/search_recipients - committees fallback):")
            print(sql_query)
            print("📎 With params:")
            pprint.pprint(params)

            start_time = time.time()
            cursor.execute(sql_query, params)
            committee_results = cursor.fetchall()
            end_time = time.time()
            print(f"⏱️ Query executed in {end_time - start_time:.4f} seconds")
            
            # Convert committee results to expected format (with placeholder stats)
            results = []
            for committee_id, name, cmte_type in committee_results:
                results.append((committee_id, name, cmte_type, 0, 0.0, 0, 0.0, None))
        
        conn.close()

    return render_template_string("""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Search Recipients</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; margin-bottom:20px;}
        form { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; display: flex; flex-wrap: wrap; gap: 15px; align-items: center; }
        form input[type=\"text\"], form input[type=\"date\"], form select { padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 1em; flex-grow: 1; min-width: 120px; }
        form input[type=\"submit\"], button { background-color: #3498db; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        form input[type=\"submit\"]:hover, button:hover { background-color: #2980b9; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .form-group { display: flex; flex-direction: column; }
        .form-group label { margin-bottom: 5px; font-weight: 500; }
        .pagination {
            margin: 25px 0;
            text-align: center;
            clear: both;
        }
        .pagination a, .pagination span {
            display: inline-block;
            padding: 8px 14px;
            margin: 0 4px;
            border: 1px solid #ddd;
            border-radius: 4px;
            color: #3498db;
            text-decoration: none;
            font-size: 0.95em;
        }
        .pagination a:hover {
            background-color: #eaf2f8;
            border-color: #c5ddec;
        }
        .pagination .current-page {
            background-color: #3498db;
            color: white;
            border-color: #3498db;
            font-weight: bold;
        }
        .results-summary {
            margin: 20px 0 10px 0;
            font-size: 0.9em;
            color: #555;
        }
        .info-link {
            text-decoration: none;
            margin-left: 5px;
            font-size: 0.9em;
            color: #7f8c8d;
        }
        .info-link:hover {
            color: #3498db;
        }
    </style>
</head>
<body>
    <h1>Search Recipients by Name</h1>
    <div class="nav-links">
        <a href="/">🔍 Contribution Search</a>
        <a href="/search_recipients">👥 New Recipient Search</a>
        <a href="/personsearch">👤 Person Search</a>
        <a href="{{ build_ca_app_url('/search_recipients', {'name_query': request.args.get('name_query', ''), 'sort_by': request.args.get('sort_by', '')}) }}" style="color: #ff6b35;" target="_blank">🏛️ Search CA Recipients</a>
    </div>
    <form method="get" onsubmit="document.getElementById('recipientSearchButton').disabled = true; document.getElementById('recipientLoadingIndicator').style.display = 'inline';">
        <div class="form-group" style="flex-grow: 3;"> 
            <label for="name_query">Name:</label>
            <input id="name_query" name="name_query" value="{{ request.args.get('name_query', '') }}" placeholder="Search recipient names...">
        </div>
        <div class="form-group">
            <label for="sort_by">Sort By:</label>
            <select id="sort_by" name="sort_by">
                <option value="recent_activity" {% if request.args.get('sort_by', 'recent_activity') == 'recent_activity' %}selected{% endif %}>Recent Activity</option>
                <option value="total_activity" {% if request.args.get('sort_by') == 'total_activity' %}selected{% endif %}>Total Activity</option>
                <option value="alphabetical" {% if request.args.get('sort_by') == 'alphabetical' %}selected{% endif %}>Alphabetical</option>
            </select>
        </div>
        <input type="submit" value="Search Recipients" id="recipientSearchButton" style="align-self: flex-end;">
        <span id="recipientLoadingIndicator" style="display:none; color: #e67e22; font-weight: bold; margin-left: 10px; align-self: flex-end;">Searching...</span>
    </form>

    {% if name_query %}
        {% if results %}
          <h2>Results</h2>
          <div class="results-summary">
            Showing {{ (page - 1) * PAGE_SIZE + 1 }} - {{ min(page * PAGE_SIZE, total_results) }} of {{ total_results }} committees.
          </div>
          <table>
            <tr>
              <th>Committee ID</th>
              <th>Name</th>
              <th>Type</th>
              <th>Recent Activity<br><small>(365 days)</small></th>
              <th>Total Activity<br><small>(all time)</small></th>
              <th>Last Contribution</th>
            </tr>
            {% for committee_id, name, type, total_contrib, total_amt, recent_contrib, recent_amt, last_date in results %}
              <tr>
                <td><a href="/recipient?committee_id={{ committee_id }}">{{ committee_id }}</a></td>
                <td>
                    <a href="/recipient?committee_id={{ committee_id }}">{{ name }}</a>
                    <a href="https://www.google.com/search?q={{ name|quote_plus }}" class="info-link" target="_blank" title="Search Google for {{ name }}">&#x24D8;</a>
                </td>
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
                <td>
                    {% if last_date %}
                        {{ last_date }}
                    {% else %}
                        <span style="color: #999;">Unknown</span>
                    {% endif %}
                </td>
              </tr>
            {% endfor %}
          </table>
          {% if total_pages > 1 %}
          <div class="pagination">
              {% set base_url = "/search_recipients?name_query=" + request.args.get('name_query', '') + "&" + query_params_without_page %}
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
          <h2>No results found for "{{ request.args.get('name_query') }}".</h2>
        {% endif %}
    {% endif %}
</body>
</html>
""", results=results, name_query=name_query, sort_by=sort_by, page=page, total_pages=total_pages, total_results=total_results, PAGE_SIZE=PAGE_SIZE,
   query_params_without_page=urlencode({k:v for k,v in request.args.items() if k not in ['page']}))

# --- API Endpoint --- 
@app.route("/api/contributions_by_person", methods=["GET"])
def api_contributions_by_person():
    first_name = request.args.get("first_name", "").strip().upper()
    last_name = request.args.get("last_name", "").strip().upper()
    zip_code = request.args.get("zip_code", "").strip().upper()

    if not all([first_name, last_name, zip_code]):
        return jsonify({"error": "Missing required parameters: first_name, last_name, and zip_code"}), 400

    query = """
        SELECT c.first_name, c.last_name, c.contribution_date,
               COALESCE(m.name, c.recipient_name) as recipient_name_resolved,
               c.amount, COALESCE(m.type, '') as recipient_type_resolved,
               c.recipient_name as recipient_committee_id,
               c.city, c.state, c.zip_code
        FROM contributions c
        LEFT JOIN committees m ON c.recipient_name = m.committee_id
        WHERE c.first_name = ? AND c.last_name = ? AND c.zip_code LIKE ?
          AND c.recipient_name NOT IN ({})
        ORDER BY c.contribution_date DESC
        LIMIT 20
    """.format(",".join(["?"] * len(KNOWN_CONDUITS)))
    
    params = [first_name, last_name, zip_code + "%"] + list(KNOWN_CONDUITS.keys())

    conn = get_db()
    conn.row_factory = sqlite3.Row # To get results as dict-like objects
    cursor = conn.cursor()

    print("\n📋 Executing SQL (/api/contributions_by_person):")
    print(query)
    print("📎 With params:")
    pprint.pprint(params)

    start_time = time.time()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    end_time = time.time()
    print(f"⏱️ Query executed in {end_time - start_time:.4f} seconds")
    conn.close()

    # Convert Row objects to dictionaries for JSON serialization
    contributions = [dict(row) for row in rows]

    return jsonify(contributions)

# --- Person Search Routes (Merged from person_search_app.py) ---

@app.route("/personsearch", methods=["GET"])
def person_search_form():
    # Form submits to /person using GET
    # Added links back to other app sections
    return '''
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Person Search</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
            h1 { color: #2c3e50; margin-bottom: 25px; text-align: center; }
            .nav-links { text-align: center; margin-bottom: 25px; }
            .nav-links a { margin: 0 10px; font-size: 1.1em; color: #3498db; text-decoration: none; }
            .nav-links a:hover { text-decoration: underline; }
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
            .optional-fields { border-top: 1px dashed #ccc; margin-top: 20px; padding-top: 20px; }
            .optional-fields h2 { font-size: 1.1em; color: #555; margin-bottom: 15px; }
            input[type="submit"] { 
                background-color: #3498db; 
                color: white; 
                padding: 12px 20px; 
                border: none; 
                border-radius: 4px; 
                cursor: pointer; 
                font-size: 1.1em; 
                width: 100%; 
                margin-top: 10px; 
                transition: background-color 0.2s ease;
            }
            input[type="submit"]:hover { background-color: #2980b9; }
            .loading-indicator { display: none; color: #e67e22; font-weight: bold; text-align: center; margin-top: 15px; }
        </style>
    </head>
    <body>
        <h1>Person Search</h1>
         <div class="nav-links">
            <a href="/">🔍 Contribution Search</a>
            <a href="/search_recipients">👥 Recipient Search</a>
            <a href="/personsearch">👤 New Person Search</a>
            <a href="http://localhost:5001/personsearch" style="color: #ff6b35;" target="_blank">🏛️ CA Person Search</a>
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
                <h2>Optional Details (for more specific searches)</h2>
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
    '''

@app.route("/person")
def person_view_results(): # Renamed function slightly to avoid potential conflicts
    # --- Get original search parameters from form ---
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
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # --- Cascading logic for DB query for contributions ---
    recent_contributions = []
    total_amount = 0.0
    db_cascade_message = ""
    
    # Define search attempts for DB query
    db_search_attempts = []
    # Base params for DB attempts (only first, last, city, zip are relevant for DB contribution query here)
    db_base_attempt_params = {
        "first_name": original_form_params["first_name"],
        "last_name": original_form_params["last_name"],
        "city": original_form_params["city"],
        "zip_code": original_form_params["zip_code"],
        "state": original_form_params["state"]
    }

    # Attempt 1: All relevant DB filters (FN, LN, City, Zip, State)
    db_search_attempts.append({"params": db_base_attempt_params.copy(), "level": "All relevant filters"})

    # Attempt 2: Drop ZIP (if ZIP was provided in original form)
    if original_form_params["zip_code"]:
        attempt_2_params = db_base_attempt_params.copy()
        attempt_2_params["zip_code"] = ""
        db_search_attempts.append({"params": attempt_2_params, "level": "Dropped ZIP Code from DB query"})

    # Attempt 3: Drop City & ZIP (if City was provided in original form)
    if original_form_params["city"]:
        attempt_3_params = db_base_attempt_params.copy()
        attempt_3_params["zip_code"] = ""
        attempt_3_params["city"] = ""
        db_search_attempts.append({"params": attempt_3_params, "level": "Dropped City & ZIP Code from DB query"})

    found_db_results = False
    effective_db_params = {}
    last_attempt_db_params = {} # Store params of the last attempt

    for attempt in db_search_attempts:
        current_db_params = attempt["params"]
        level = attempt["level"]
        last_attempt_db_params = current_db_params # Update on each iteration

        db_where_clauses = ["c.first_name = ?", "c.last_name = ?"]
        db_query_actual_params = [current_db_params["first_name"], current_db_params["last_name"]]
        state_filter_applied = None # Track state filter for this attempt

        if current_db_params["city"]:
            db_where_clauses.append("c.city = ?")
            db_query_actual_params.append(current_db_params["city"])
            
        # State handling: Apply explicit state OR default to CA (since FN/LN are always present here)
        if current_db_params["state"]:
            db_where_clauses.append("c.state = ?")
            db_query_actual_params.append(current_db_params["state"])
            state_filter_applied = current_db_params["state"]
        else: # Default to CA if no state provided
            db_where_clauses.append("c.state = ?")
            db_query_actual_params.append("CA")
            state_filter_applied = "CA (Default)"
            
        if current_db_params["zip_code"]:
            db_where_clauses.append("c.zip_code LIKE ?")
            db_query_actual_params.append(current_db_params["zip_code"] + "%")

        conduit_placeholders = ",".join(["?"] * len(KNOWN_CONDUITS))
        db_where_clauses.append(f"c.recipient_name NOT IN ({conduit_placeholders})")
        final_db_query_params = db_query_actual_params + list(KNOWN_CONDUITS.keys())
        db_where_string = " AND ".join(db_where_clauses)

        # Check if any contributions exist with these criteria (Count for sum_query logic)
        # For this page, we primarily care if *any* contributions exist for the sum and recent list.
        # We can simplify the count to just check for existence to decide to proceed.
        check_existence_sql = f"SELECT 1 FROM contributions c WHERE {db_where_string} LIMIT 1"
        print(f"\n📋 Executing SQL (/person - DB Check - Attempt: {level}):")
        print(check_existence_sql)
        print("📎 With params:")
        pprint.pprint(final_db_query_params)

        cursor.execute(check_existence_sql, final_db_query_params)
        if cursor.fetchone(): # If any row exists
            found_db_results = True
            effective_db_params = current_db_params
            if level == "Dropped ZIP Code from DB query":
                db_cascade_message = "(Contribution data found after dropping ZIP code filter from DB query)"
            elif level == "Dropped City & ZIP Code from DB query":
                db_cascade_message = "(Contribution data found after dropping City & ZIP code filters from DB query)"
            else:
                 db_cascade_message = "" # Explicitly clear for 'All relevant filters'

            # Query for total contribution amount with these effective DB params
            sum_query = f"SELECT SUM(c.amount) FROM contributions c WHERE {db_where_string}"
            cursor.execute(sum_query, final_db_query_params)
            total_amount_result = cursor.fetchone()
            total_amount = total_amount_result[0] if total_amount_result and total_amount_result[0] is not None else 0.0

            # Query for recent contributions with these effective DB params
            recent_query = f"""
                SELECT c.contribution_date, COALESCE(m.name, c.recipient_name) as recipient_display_name,
                       c.amount, c.recipient_name as committee_id, c.city, c.state, c.zip_code
                FROM contributions c
                LEFT JOIN committees m ON c.recipient_name = m.committee_id
                WHERE {db_where_string}
                ORDER BY c.contribution_date DESC
                LIMIT ?
            """
            recent_query_final_params = final_db_query_params + [PERSON_SEARCH_PAGE_SIZE]
            cursor.execute(recent_query, recent_query_final_params)
            recent_contributions = cursor.fetchall()
            break # Found results, stop cascading for DB queries
    
    no_results_message = None # Initialize
    if not found_db_results:
        print("\nℹ️ No DB results found for contributions after all cascade attempts on /person page.")
        # Construct specific message indicating cascade failure
        no_results_message = f"No recent contributions found for {original_form_params['first_name']} {original_form_params['last_name']}"
        # Clarify the final criteria attempted
        final_attempt_criteria = []
        if last_attempt_db_params.get('city'): final_attempt_criteria.append(f"City: {last_attempt_db_params['city']}")
        # State explanation for final attempt
        if last_attempt_db_params.get('state'): 
            final_attempt_criteria.append(f"State: {last_attempt_db_params['state']}")
        elif original_form_params['first_name'] or original_form_params['last_name']: # Check if name was searched
            final_attempt_criteria.append("State: CA (Default)")
            
        if last_attempt_db_params.get('zip_code'): final_attempt_criteria.append(f"ZIP: {last_attempt_db_params['zip_code']}")
        if final_attempt_criteria:
            no_results_message += f" matching: { ', '.join(final_attempt_criteria) }."
        else:
            no_results_message += "." # Should include CA default if no state/city/zip provided
            
        # Add details about failed cascades
        original_had_zip = original_form_params["zip_code"]
        original_had_city = original_form_params["city"]
        if original_had_zip and original_had_city:
            no_results_message += " Also tried searching database without ZIP, and without City & ZIP."
        elif original_had_zip:
            no_results_message += " Also tried searching database without ZIP."
        elif original_had_city: # Only city was provided initially, so only city/zip drop was relevant
             no_results_message += " Also tried searching database without City & ZIP."
        no_results_message += " (Excluding passthroughs)."
        # The Google searches below might still yield results based on original form input.

    conn.close()

    # --- Prepare Google search URLs (uses original_form_params) ---
    
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
    if formatted_phone: # Only create search if phone was valid and formatted
        google_search_query_phone_parts = []
        if original_form_params["first_name"]: google_search_query_phone_parts.append(original_form_params["first_name"])
        if original_form_params["last_name"]: google_search_query_phone_parts.append(original_form_params["last_name"])
        google_search_query_phone_parts.append(formatted_phone) # Use formatted number
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
    # Only generate this search if all three parts (FN, LN, City) are present
    google_search_url_name_city = None
    if original_form_params["first_name"] and original_form_params["last_name"] and original_form_params["city"]:
        google_search_url_name_city = f"https://www.google.com/search?igu=1&q={quote_plus(google_search_query_name_city)}"

    # Render the template
    html_template = '''
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
            {# Display original phone input #}
            {% if original_form_params.phone %}Phone: {{ original_form_params.phone }}<br>{% endif %}
            {% if original_form_params.email %}Email: {{ original_form_params.email }}{% endif %}
        </div>

        <div class="sub-header">Total Contributions (matching DB criteria, excluding passthroughs): <strong>{{ total_amount|currency }}</strong></div>
        {% if db_cascade_message %}<div class="db-cascade-info"><strong>{{ db_cascade_message }}</strong></div>{% endif %}

        <h2>Recent Contributions ({{ recent_contributions|length }})</h2>
        {% if recent_contributions %}
            <table>
                <tr><th>Date</th><th>Recipient</th><th>Amount</th><th>Contributor Location</th></tr>
                {% for contrib in recent_contributions %}
                    <tr>
                        <td>{{ contrib['contribution_date'] }}</td>
                        <td>
                           <a href="/recipient?committee_id={{ contrib['committee_id'] }}" target="_blank" title="View recipient details">{{ contrib['recipient_display_name'] }}</a>
                        </td>
                        <td>{{ contrib['amount']|currency }}</td>
                        <td>{{ contrib['city'] }}, {{ contrib['state'] }} {{ contrib['zip_code'] }}</td>
                    </tr>
                {% endfor %}
            </table>
        {% else %}
            {# Display the specific message constructed in the backend #}
            <p class="no-results">{{ no_results_message }}</p>
        {% endif %}

        {% if google_search_url_address %}
        <div class="google-search-section">
            <h2>Google Search: Name + Address</h2>
            <p><em>Searching for: {{ google_search_query_address }}</em></p>
            <iframe src="{{ google_search_url_address }}" title="Google Search Results for {{ google_search_query_address }}"></iframe>
        </div>
        {% endif %}

        {# Only show phone search if formatting was successful #}
        {% if google_search_url_phone %}
        <div class="google-search-section">
            <h2>Google Search: Name + Phone</h2>
            {# Use the formatted phone number in the display query #}
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
    '''
    return render_template_string(html_template, 
                                original_form_params=original_form_params,
                                total_amount=total_amount, 
                                recent_contributions=recent_contributions,
                                db_cascade_message=db_cascade_message,
                                no_results_message=no_results_message, # Pass specific no results message
                                google_search_url_address=google_search_url_address,
                                google_search_query_address=google_search_query_address,
                                google_search_url_phone=google_search_url_phone,
                                google_search_query_phone=google_search_query_phone, # Pass formatted query
                                google_search_url_email=google_search_url_email,
                                google_search_query_email=google_search_query_email,
                                google_search_url_name_city=google_search_url_name_city, # Pass Name+City URL
                                google_search_query_name_city=google_search_query_name_city # Pass Name+City Query
                                )

# --- JSON API Endpoints ---

@app.route("/api/search", methods=["GET"])
def api_search():
    """JSON API: Search contributions. Mirrors / route logic."""
    params = {
        "first_name": request.args.get("first_name", "").strip().upper(),
        "last_name": request.args.get("last_name", "").strip().upper(),
        "zip_code": request.args.get("zip_code", "").strip().upper(),
        "year": request.args.get("year", "").strip(),
        "city": request.args.get("city", "").strip().upper(),
        "state": request.args.get("state", "").strip().upper(),
        "sort_by": request.args.get("sort_by", "contribution_date"),
        "order": request.args.get("order", "desc"),
    }
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    if params["sort_by"] not in {"contribution_date", "amount"}:
        params["sort_by"] = "contribution_date"
    if params["order"] not in {"asc", "desc"}:
        params["order"] = "desc"

    year_filter = None
    if params["year"] and params["year"].isdigit() and len(params["year"]) == 4:
        year_filter = params["year"]

    search_provided = bool(
        params["first_name"] or params["last_name"] or params["zip_code"]
        or params["city"] or params["state"] or year_filter
    )
    if not search_provided:
        return jsonify({"error": "At least one search parameter required"}), 400

    conn = get_db()
    cursor = conn.cursor()

    # Cascading search (same logic as HTML route)
    search_attempts = [{"params": params.copy(), "level": "All filters"}]
    if params["zip_code"]:
        a2 = params.copy(); a2["zip_code"] = ""
        search_attempts.append({"params": a2, "level": "Dropped ZIP Code"})
    if params["city"]:
        a3 = params.copy(); a3["zip_code"] = ""; a3["city"] = ""
        search_attempts.append({"params": a3, "level": "Dropped City & ZIP Code"})

    results = []
    total_results = 0
    total_pages = 0
    cascade_message = ""

    for attempt in search_attempts:
        cp = attempt["params"]
        where_clauses = ["c.recipient_name NOT IN ({})".format(",".join(["?"] * len(KNOWN_CONDUITS)))]
        qp = list(KNOWN_CONDUITS.keys())

        if cp["first_name"]: where_clauses.append("c.first_name = ?"); qp.append(cp["first_name"])
        if cp["last_name"]: where_clauses.append("c.last_name = ?"); qp.append(cp["last_name"])
        if cp["zip_code"]: where_clauses.append("c.zip_code LIKE ?"); qp.append(cp["zip_code"] + "%")
        if cp["city"]: where_clauses.append("c.city = ?"); qp.append(cp["city"])
        if cp["state"]: where_clauses.append("c.state = ?"); qp.append(cp["state"])
        if year_filter:
            where_clauses.append("c.contribution_date >= ? AND c.contribution_date <= ?")
            qp.extend([f"{year_filter}-01-01", f"{year_filter}-12-31"])

        where = " WHERE " + " AND ".join(where_clauses)
        cursor.execute(f"SELECT COUNT(*) FROM contributions c {where}", qp)
        count = cursor.fetchone()[0]

        if count > 0:
            total_results = count
            total_pages = math.ceil(total_results / PAGE_SIZE)
            if attempt["level"] != "All filters":
                cascade_message = attempt["level"]
            offset = (page - 1) * PAGE_SIZE
            cursor.execute(
                f"""SELECT c.first_name, c.last_name, c.contribution_date,
                       COALESCE(m.name, c.recipient_name), c.amount,
                       COALESCE(m.type, ''), c.recipient_name,
                       c.city, c.state, c.zip_code
                FROM contributions c LEFT JOIN committees m ON c.recipient_name = m.committee_id
                {where} ORDER BY c.{cp['sort_by']} {cp['order']} LIMIT ? OFFSET ?""",
                qp + [PAGE_SIZE, offset],
            )
            results = [
                {"first_name": r[0], "last_name": r[1], "contribution_date": r[2],
                 "recipient_name": r[3], "amount": r[4], "recipient_type": r[5],
                 "committee_id": r[6], "city": r[7], "state": r[8], "zip_code": r[9]}
                for r in cursor.fetchall()
            ]
            break

    conn.close()
    resp = {
        "results": results, "total_results": total_results,
        "page": page, "total_pages": total_pages,
    }
    if cascade_message:
        resp["cascade_message"] = cascade_message
    return jsonify(resp)


@app.route("/api/contributor", methods=["GET"])
def api_contributor():
    """JSON API: Contributor detail. Mirrors /contributor route logic."""
    first = request.args.get("first_name", "").strip().upper()
    last = request.args.get("last_name", "").strip().upper()
    city = request.args.get("city", "").strip().upper()
    state = request.args.get("state", "").strip().upper()
    zip_code = request.args.get("zip_code", "").strip().upper()
    sort_by = request.args.get("sort_by", "contribution_date")
    order = request.args.get("order", "desc")
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    if not first or not last:
        return jsonify({"error": "first_name and last_name are required"}), 400

    if sort_by not in {"contribution_date", "amount"}:
        sort_by = "contribution_date"
    if order not in {"asc", "desc"}:
        order = "desc"

    offset = (page - 1) * PAGE_SIZE
    conn = get_db()
    cursor = conn.cursor()

    where_clauses = ["c.first_name = ?", "c.last_name = ?"]
    qp = [first, last]
    if city: where_clauses.append("c.city = ?"); qp.append(city)
    if state: where_clauses.append("c.state = ?"); qp.append(state)
    if zip_code: where_clauses.append("c.zip_code LIKE ?"); qp.append(zip_code + "%")

    conduit_ph = ",".join(["?"] * len(KNOWN_CONDUITS))
    where_clauses.append(f"c.recipient_name NOT IN ({conduit_ph})")
    final_qp = qp + list(KNOWN_CONDUITS.keys())
    where = " AND ".join(where_clauses)
    from_cl = "FROM contributions c LEFT JOIN committees m ON c.recipient_name = m.committee_id"

    cursor.execute(f"SELECT COUNT(*) {from_cl} WHERE {where}", final_qp)
    total_results = cursor.fetchone()[0]
    total_pages = math.ceil(total_results / PAGE_SIZE)

    cursor.execute(
        f"""SELECT c.contribution_date, COALESCE(m.name, c.recipient_name),
               c.amount, COALESCE(m.type, ''), c.recipient_name,
               c.city, c.state, c.zip_code
        {from_cl} WHERE {where} ORDER BY c.{sort_by} {order} LIMIT ? OFFSET ?""",
        final_qp + [PAGE_SIZE, offset],
    )
    contributions = [
        {"contribution_date": r[0], "recipient_name": r[1], "amount": r[2],
         "recipient_type": r[3], "committee_id": r[4], "city": r[5],
         "state": r[6], "zip_code": r[7]}
        for r in cursor.fetchall()
    ]

    cursor.execute(f"SELECT SUM(c.amount) {from_cl} WHERE {where}", final_qp)
    total_amount = cursor.fetchone()[0] or 0
    conn.close()

    percentiles = {}
    if zip_code:
        percentiles = get_donor_percentiles_by_year(first, last, zip_code)

    return jsonify({
        "contributor": {"first_name": first, "last_name": last,
                        "city": city, "state": state, "zip_code": zip_code},
        "contributions": contributions, "total_amount": total_amount,
        "percentiles": {str(k): v for k, v in percentiles.items()},
        "page": page, "total_pages": total_pages, "total_results": total_results,
    })


@app.route("/api/recipient", methods=["GET"])
def api_recipient():
    """JSON API: Recipient detail. Mirrors /recipient route logic."""
    committee_id = request.args.get("committee_id", "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    if not committee_id:
        return jsonify({"error": "committee_id is required"}), 400

    if committee_id in KNOWN_CONDUITS:
        return jsonify({"name": KNOWN_CONDUITS[committee_id], "type": "passthrough",
                        "message": "This is a passthrough platform. No direct contributors shown.",
                        "contributors": [], "total_amount": 0, "page": 1, "total_pages": 0})

    offset = (page - 1) * PAGE_SIZE
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT name, type FROM committees WHERE committee_id = ?", (committee_id,))
    name_row = cursor.fetchone()
    recipient_name = name_row[0] if name_row else committee_id
    recipient_type = name_row[1] if name_row else ""

    cursor.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM contributions WHERE recipient_name = ? GROUP BY first_name, last_name)",
        (committee_id,),
    )
    total_results = cursor.fetchone()[0]
    total_pages = math.ceil(total_results / PAGE_SIZE)

    cursor.execute(
        """SELECT first_name, last_name, SUM(amount) as total
        FROM contributions WHERE recipient_name = ?
        GROUP BY first_name, last_name ORDER BY total DESC LIMIT ? OFFSET ?""",
        (committee_id, PAGE_SIZE, offset),
    )
    contributors = [
        {"first_name": r[0], "last_name": r[1], "total_amount": r[2]}
        for r in cursor.fetchall()
    ]

    cursor.execute("SELECT SUM(amount) FROM contributions WHERE recipient_name = ?", (committee_id,))
    total_amount = cursor.fetchone()[0] or 0
    conn.close()

    return jsonify({
        "name": recipient_name, "type": recipient_type, "committee_id": committee_id,
        "contributors": contributors, "total_amount": total_amount,
        "page": page, "total_pages": total_pages, "total_results": total_results,
    })


@app.route("/api/search_recipients", methods=["GET"])
def api_search_recipients():
    """JSON API: Search recipients. Mirrors /search_recipients route logic."""
    q = request.args.get("q", "").strip()
    sort_by = request.args.get("sort_by", "recent_activity")
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    if not q:
        return jsonify({"error": "q (search query) is required"}), 400

    offset = (page - 1) * PAGE_SIZE
    conn = get_db()
    cursor = conn.cursor()

    # Check if lookup table exists
    cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='recipient_lookup'")
    has_lookup = cursor.fetchone()[0] > 0

    results = []
    total_results = 0
    total_pages = 0

    if has_lookup:
        if sort_by == "recent_activity":
            order_clause = "recipient_lookup.recent_contributions DESC, recipient_lookup.recent_amount DESC"
            order_simple = "recent_contributions DESC, recent_amount DESC"
        elif sort_by == "total_activity":
            order_clause = "recipient_lookup.total_contributions DESC, recipient_lookup.total_amount DESC"
            order_simple = "total_contributions DESC, total_amount DESC"
        else:
            order_clause = "recipient_lookup.display_name ASC"
            order_simple = "display_name ASC"

        # Try FTS first
        cursor.execute(
            "SELECT COUNT(*) FROM recipient_lookup_fts fts JOIN recipient_lookup ON fts.recipient_name = recipient_lookup.recipient_name WHERE recipient_lookup_fts MATCH ?",
            (q,),
        )
        fts_count = cursor.fetchone()[0]

        if fts_count > 0:
            total_results = fts_count
            total_pages = math.ceil(total_results / PAGE_SIZE)
            cursor.execute(
                f"""SELECT recipient_lookup.recipient_name, recipient_lookup.display_name, recipient_lookup.committee_type,
                       recipient_lookup.total_contributions, recipient_lookup.total_amount,
                       recipient_lookup.recent_contributions, recipient_lookup.recent_amount,
                       recipient_lookup.last_contribution_date
                FROM recipient_lookup_fts fts
                JOIN recipient_lookup ON fts.recipient_name = recipient_lookup.recipient_name
                WHERE recipient_lookup_fts MATCH ?
                ORDER BY {order_clause} LIMIT ? OFFSET ?""",
                (q, PAGE_SIZE, offset),
            )
        else:
            # LIKE fallback
            like_params = [f"%{q}%", f"%{q}%"]
            cursor.execute(
                "SELECT COUNT(*) FROM recipient_lookup WHERE display_name LIKE ? OR recipient_name LIKE ?",
                like_params,
            )
            total_results = cursor.fetchone()[0]
            total_pages = math.ceil(total_results / PAGE_SIZE)
            cursor.execute(
                f"""SELECT recipient_name, display_name, committee_type,
                       total_contributions, total_amount, recent_contributions, recent_amount,
                       last_contribution_date
                FROM recipient_lookup WHERE display_name LIKE ? OR recipient_name LIKE ?
                ORDER BY {order_simple} LIMIT ? OFFSET ?""",
                like_params + [PAGE_SIZE, offset],
            )

        results = [
            {"committee_id": r[0], "name": r[1], "type": r[2],
             "total_contributions": r[3], "total_amount": r[4],
             "recent_contributions": r[5], "recent_amount": r[6],
             "last_contribution_date": r[7]}
            for r in cursor.fetchall()
        ]
    else:
        # Fallback to committees table
        cursor.execute("SELECT COUNT(*) FROM committees WHERE name LIKE ?", (f"%{q}%",))
        total_results = cursor.fetchone()[0]
        total_pages = math.ceil(total_results / PAGE_SIZE)
        cursor.execute(
            "SELECT committee_id, name, type FROM committees WHERE name LIKE ? ORDER BY name LIMIT ? OFFSET ?",
            (f"%{q}%", PAGE_SIZE, offset),
        )
        results = [
            {"committee_id": r[0], "name": r[1], "type": r[2],
             "total_contributions": 0, "total_amount": 0,
             "recent_contributions": 0, "recent_amount": 0,
             "last_contribution_date": None}
            for r in cursor.fetchall()
        ]

    conn.close()
    return jsonify({
        "results": results, "total_results": total_results,
        "page": page, "total_pages": total_pages,
    })


@app.route("/api/person", methods=["GET"])
def api_person():
    """JSON API: Person search. Mirrors /person route logic."""
    first_name = request.args.get("first_name", "").strip().upper()
    last_name = request.args.get("last_name", "").strip().upper()
    city = request.args.get("city", "").strip().upper()
    state = request.args.get("state", "").strip().upper()
    zip_code = request.args.get("zip_code", "").strip().upper()

    if not first_name or not last_name:
        return jsonify({"error": "first_name and last_name are required"}), 400

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Cascading DB search (same as /person)
    db_attempts = []
    base = {"first_name": first_name, "last_name": last_name, "city": city, "zip_code": zip_code, "state": state}
    db_attempts.append({"params": base.copy(), "level": "All filters"})
    if zip_code:
        a2 = base.copy(); a2["zip_code"] = ""
        db_attempts.append({"params": a2, "level": "Dropped ZIP"})
    if city:
        a3 = base.copy(); a3["zip_code"] = ""; a3["city"] = ""
        db_attempts.append({"params": a3, "level": "Dropped City & ZIP"})

    contributions = []
    total_amount = 0.0
    cascade_message = ""

    for attempt in db_attempts:
        cp = attempt["params"]
        wc = ["c.first_name = ?", "c.last_name = ?"]
        qp = [cp["first_name"], cp["last_name"]]

        if cp["city"]: wc.append("c.city = ?"); qp.append(cp["city"])
        effective_state = cp["state"] if cp["state"] else "CA"
        wc.append("c.state = ?"); qp.append(effective_state)
        if cp["zip_code"]: wc.append("c.zip_code LIKE ?"); qp.append(cp["zip_code"] + "%")

        conduit_ph = ",".join(["?"] * len(KNOWN_CONDUITS))
        wc.append(f"c.recipient_name NOT IN ({conduit_ph})")
        final_qp = qp + list(KNOWN_CONDUITS.keys())
        where = " AND ".join(wc)

        cursor.execute(f"SELECT 1 FROM contributions c WHERE {where} LIMIT 1", final_qp)
        if cursor.fetchone():
            if attempt["level"] != "All filters":
                cascade_message = attempt["level"]

            cursor.execute(f"SELECT SUM(c.amount) FROM contributions c WHERE {where}", final_qp)
            result = cursor.fetchone()
            total_amount = result[0] if result and result[0] else 0.0

            cursor.execute(
                f"""SELECT c.contribution_date, COALESCE(m.name, c.recipient_name),
                       c.amount, c.recipient_name, c.city, c.state, c.zip_code
                FROM contributions c LEFT JOIN committees m ON c.recipient_name = m.committee_id
                WHERE {where} ORDER BY c.contribution_date DESC LIMIT ?""",
                final_qp + [PERSON_SEARCH_PAGE_SIZE],
            )
            contributions = [
                {"contribution_date": r[0], "recipient_name": r[1], "amount": r[2],
                 "committee_id": r[3], "city": r[4], "state": r[5], "zip_code": r[6]}
                for r in cursor.fetchall()
            ]
            break

    conn.close()

    resp = {
        "person": {"first_name": first_name, "last_name": last_name,
                    "city": city, "state": state, "zip_code": zip_code},
        "contributions": contributions, "total_giving": total_amount,
    }
    if cascade_message:
        resp["cascade_message"] = cascade_message

    # Percentile info
    if zip_code:
        percentiles = get_donor_percentiles_by_year(first_name, last_name, zip_code)
        resp["percentiles"] = {str(k): v for k, v in percentiles.items()}

    return jsonify(resp)


# --- Main Application Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run FEC Contribution Search Flask App')
    parser.add_argument('--public', action='store_true',
                        help='Run on 0.0.0.0. WARNING: For TESTING ON TRUSTED NETWORKS ONLY. NOT FOR PRODUCTION.')
    args = parser.parse_args()

    host_ip = '0.0.0.0' if args.public else '127.0.0.1'
    # CRITICAL: debug must be False if potentially exposed, even for testing on 0.0.0.0
    # For true production, use a WSGI server like Gunicorn, not app.run().
    current_debug_mode = False if args.public else True 

    print(f"🚀 Starting Flask server on http://{host_ip}:5000 (Debug mode: {current_debug_mode})")
    if args.public:
        print("**************************************************************************************")
        print("⚠️  WARNING: Server is running on 0.0.0.0 with debug=False.")
        print("         This is for testing on TRUSTED networks only.")
        print("         DO NOT expose this directly to the internet for production.")
        print("         Use a proper WSGI server (e.g., Gunicorn) and a reverse proxy (e.g., Nginx).")
        print("**************************************************************************************")
    
    # Note: For Gunicorn, you would typically run: gunicorn -w 4 -b 0.0.0.0:5000 app:app
    # And this __main__ block might not even call app.run() in that scenario.
    app.run(debug=current_debug_mode, host=host_ip)
