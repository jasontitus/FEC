#!/usr/bin/env python3
"""
California Campaign Contributions Web Application
Based on the FEC app.py but adapted for California CalAccess data
"""

from flask import Flask, request, render_template_string, jsonify
import sqlite3
import pprint
import time
import math
from urllib.parse import urlencode, quote_plus
import argparse

app = Flask(__name__)
DB_PATH = "ca_contributions.db"
PAGE_SIZE = 50
PERSON_SEARCH_PAGE_SIZE = 10

# Custom Jinja2 filters
def format_currency(value):
    if value is None:
        return "$0.00"
    return "${:,.2f}".format(value)

def format_comma(value):
    if value is None:
        return "0"
    return "{:,}".format(int(value))

app.jinja_env.filters['currency'] = format_currency
app.jinja_env.filters['comma'] = format_comma
app.jinja_env.globals['min'] = min
app.jinja_env.globals['max'] = max
app.jinja_env.filters['quote_plus'] = quote_plus

# Helper Functions
def normalize_and_format_phone(phone_string):
    """Cleans and formats a phone number string."""
    if not phone_string:
        return None
        
    digits = ''.join(filter(str.isdigit, phone_string))
    
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
        
    if len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    else:
        return None

# Security Headers
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Content-Security-Policy'] = "default-src 'self'; frame-src https://www.google.com/; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none';"
    return response

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
    # Get search parameters
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

    # Validate year format
    year_filter = None
    if original_params["year"] and original_params["year"].isdigit() and len(original_params["year"]) == 4:
        year_filter = original_params["year"]
    else:
        original_params["year"] = ""

    # Determine if search should be performed
    search_criteria_provided = bool(
        original_params["first_name"] or original_params["last_name"] or 
        original_params["zip_code"] or original_params["city"] or 
        original_params["state"] or year_filter
    )

    results = []
    total_results = 0
    total_pages = 0
    effective_params = {}
    cascade_message = ""
    no_results_detail_message = None

    if search_criteria_provided:
        conn = get_db()
        cursor = conn.cursor()

        # Cascading search logic (same as FEC app)
        search_attempts = []
        base_attempt_params = original_params.copy()
        
        search_attempts.append({"params": base_attempt_params.copy(), "level": "All filters"})

        if base_attempt_params["zip_code"]:
            attempt_2_params = base_attempt_params.copy()
            attempt_2_params["zip_code"] = ""
            search_attempts.append({"params": attempt_2_params, "level": "Dropped ZIP Code"})

        if base_attempt_params["city"]:
            attempt_3_params = base_attempt_params.copy()
            attempt_3_params["zip_code"] = ""
            attempt_3_params["city"] = ""
            search_attempts.append({"params": attempt_3_params, "level": "Dropped City & ZIP Code"})
        
        found_results = False
        for attempt in search_attempts:
            current_params = attempt["params"]
            level = attempt["level"]

            # Build WHERE clauses
            where_clauses = []
            query_params_list = []

            if current_params["first_name"]:
                where_clauses.append("c.first_name = ?")
                query_params_list.append(current_params["first_name"])
            if current_params["last_name"]:
                where_clauses.append("c.last_name = ?")
                query_params_list.append(current_params["last_name"])
            if current_params["zip_code"]:
                where_clauses.append("c.zip_code LIKE ?")
                query_params_list.append(current_params["zip_code"] + "%")
            if current_params["city"]:
                where_clauses.append("c.city = ?")
                query_params_list.append(current_params["city"])
            if current_params["state"]:
                where_clauses.append("c.state = ?")
                query_params_list.append(current_params["state"])

            if year_filter:
                start_date = f"{year_filter}-01-01"
                end_date = f"{year_filter}-12-31"
                where_clauses.append("c.contribution_date >= ? AND c.contribution_date <= ?")
                query_params_list.extend([start_date, end_date])

            if not where_clauses:
                continue

            where_string = " WHERE " + " AND ".join(where_clauses)

            # Execute COUNT query
            count_query_sql = f"SELECT COUNT(*) FROM contributions c {where_string}" 
            print(f"\n📋 Executing CA SQL (Count - Attempt: {level}):")
            print(count_query_sql)
            print("📎 With params:")
            pprint.pprint(query_params_list)
            
            cursor.execute(count_query_sql, query_params_list)
            current_total_results = cursor.fetchone()[0]

            if current_total_results > 0:
                total_results = current_total_results
                total_pages = math.ceil(total_results / PAGE_SIZE)
                effective_params = current_params
                found_results = True
                
                # Set cascade message
                if level == "Dropped ZIP Code":
                    cascade_message = "(Results found after dropping ZIP Code filter)"
                elif level == "Dropped City & ZIP Code":
                    cascade_message = "(Results found after dropping City & ZIP Code filters)"
                else:
                    cascade_message = ""
                
                # Calculate offset
                offset = (page - 1) * PAGE_SIZE

                # Main data query
                base_select_columns = """
                    c.first_name, c.last_name, c.contribution_date,
                    COALESCE(cm.name, c.recipient_committee_id), c.amount, 
                    COALESCE(cm.committee_type, ''), c.recipient_committee_id,
                    c.city, c.state, c.zip_code
                """
                from_join_clause = "FROM contributions c LEFT JOIN committees cm ON c.recipient_committee_id = cm.committee_id"
                
                data_query_sql = (
                    f"SELECT {base_select_columns} {from_join_clause}{where_string} "
                    f"ORDER BY c.{effective_params['sort_by']} {effective_params['order']} LIMIT ? OFFSET ?"
                )
                paged_data_params = query_params_list + [PAGE_SIZE, offset]

                print(f"\n📋 Executing CA SQL (Data - Effective Level: {level}):")
                print(data_query_sql)
                print("📎 With params:")
                pprint.pprint(paged_data_params)
                
                start_time = time.time()
                cursor.execute(data_query_sql, paged_data_params)
                results = cursor.fetchall()
                end_time = time.time()
                print(f"⏱️ CA Query executed in {end_time - start_time:.4f} seconds")
                
                break
        
        conn.close()
        
        if search_criteria_provided and not found_results:
            print("\nℹ️ No CA results found after all cascade attempts.")
            initial_criteria_list = []
            if original_params["first_name"]: initial_criteria_list.append(f"First Name: {original_params['first_name']}")
            if original_params["last_name"]: initial_criteria_list.append(f"Last Name: {original_params['last_name']}")
            if original_params["city"]: initial_criteria_list.append(f"City: {original_params['city']}")
            if original_params["state"]: initial_criteria_list.append(f"State: {original_params['state']}")
            if original_params["zip_code"]: initial_criteria_list.append(f"ZIP: {original_params['zip_code']}")
            if year_filter: initial_criteria_list.append(f"Year: {year_filter}")
            
            no_results_detail_message = f"No contributions found matching: { ', '.join(initial_criteria_list) }."
            
            # Add cascade details
            original_had_zip = original_params["zip_code"]
            original_had_city = original_params["city"]
            
            cascade_attempts_failed_msg = ""
            if original_had_zip and original_had_city:
                cascade_attempts_failed_msg = " Also tried searching without ZIP, and without City & ZIP."
            elif original_had_zip:
                cascade_attempts_failed_msg = " Also tried searching without ZIP."
            
            no_results_detail_message += cascade_attempts_failed_msg
            no_results_detail_message += " Consider broadening your search criteria."

    # Generate pagination params
    pagination_params = {k: v for k, v in effective_params.items() if k not in ['page'] and v}
    
    return render_template_string(get_search_template(), 
       results=results, page=page, total_pages=total_pages, total_results=total_results, 
       PAGE_SIZE=PAGE_SIZE, original_params=original_params,
       pagination_params=pagination_params,
       urlencode=urlencode,
       cascade_message=cascade_message,
       search_criteria_provided=search_criteria_provided,
       no_results_detail_message=no_results_detail_message
   )

def get_search_template():
    """Returns the search page HTML template."""
    return """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>California Campaign Contribution Search</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        h1 { background: linear-gradient(135deg, #ff6b35, #f7931e); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .ca-badge { background-color: #ff6b35; color: white; padding: 4px 8px; border-radius: 4px; font-size: 0.8em; margin-left: 10px; }
        a { color: #ff6b35; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { margin-bottom: 20px; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; }
        form { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; display: flex; flex-wrap: wrap; gap: 15px; align-items: center; }
        form input[type="text"], form input[type="date"], form select { padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 1em; flex-grow: 1; min-width: 120px; }
        form input[type="submit"], button { background-color: #ff6b35; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        form input[type="submit"]:hover, button:hover { background-color: #e55a30; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #fff3e0; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .form-group { display: flex; flex-direction: column; }
        .form-group label { margin-bottom: 5px; font-weight: 500; }
        .pagination { margin: 25px 0; text-align: center; clear: both; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 14px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; color: #ff6b35; text-decoration: none; font-size: 0.95em; }
        .pagination a:hover { background-color: #fff3e0; border-color: #ffcc80; }
        .pagination .current-page { background-color: #ff6b35; color: white; border-color: #ff6b35; font-weight: bold; }
        .results-summary { margin: 20px 0 5px 0; font-size: 0.9em; color: #555; }
        .cascade-info { margin: 0 0 10px 0; font-size: 0.85em; color: #ff6b35; font-style: italic; }
        .info-link { text-decoration: none; margin-left: 5px; font-size: 0.9em; color: #7f8c8d; }
        .info-link:hover { color: #ff6b35; }
    </style>
</head>
<body>
    <h1>California Campaign Contribution Search <span class="ca-badge">CA</span></h1>
    <div class="nav-links">
        <a href="/">🔍 New Search</a>
        <a href="/search_recipients">👥 Search Recipients by Name</a>
        <a href="/personsearch">👤 Person Search</a>
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
        <span id="mainLoadingIndicator" style="display:none; color: #ff6b35; font-weight: bold; margin-left: 10px; align-self: flex-end;">Searching...</span>
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
            <td><a href="/contributor?first={{ fn }}&last={{ ln }}&city={{ city|urlencode }}&state={{ state|urlencode }}&zip={{ zip|urlencode }}">{{ fn }}</a></td>
            <td><a href="/contributor?first={{ fn }}&last={{ ln }}&city={{ city|urlencode }}&state={{ state|urlencode }}&zip={{ zip|urlencode }}">{{ ln }}</a></td>
            <td>{{ date }}</td>
            <td>
                <a href="/recipient?committee_id={{ cmte_id }}">{{ recip }}</a>
                <a href="https://www.google.com/search?q={{ recip|quote_plus }}" class="info-link" target="_blank" title="Search Google for {{ recip }}">&#x24D8;</a>
            </td>
            <td>{{ amt|currency }}</td>
            <td>{{ typ|default("Unknown") }}</td>
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
        <h2>No results found.</h2>
        <p>{{ no_results_detail_message }}</p>
    {% endif %}
</body>
</html>
"""

# Add other routes (contributor, recipient, etc.) with CA adaptations
# For brevity, I'll include just the key routes here

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run California Campaign Contribution Search Flask App')
    parser.add_argument('--public', action='store_true',
                        help='Run on 0.0.0.0. WARNING: For TESTING ON TRUSTED NETWORKS ONLY.')
    args = parser.parse_args()

    host_ip = '0.0.0.0' if args.public else '127.0.0.1'
    current_debug_mode = False if args.public else True 

    print(f"🚀 Starting California Flask server on http://{host_ip}:5001 (Debug mode: {current_debug_mode})")
    if args.public:
        print("⚠️  WARNING: Server is running on 0.0.0.0 with debug=False.")
        print("         This is for testing on TRUSTED networks only.")
    
    app.run(debug=current_debug_mode, host=host_ip, port=5001)
