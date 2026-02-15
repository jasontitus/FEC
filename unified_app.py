#!/usr/bin/env python3
"""
Unified Campaign Finance Search Application
Supports both FEC (Federal) and CalAccess (California) databases
"""

from flask import Flask, request, render_template_string, jsonify, redirect, url_for, make_response
import sqlite3
import pprint
import time
import math
import os
import requests
import json
from urllib.parse import urlencode, quote_plus
import argparse

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, that's okay

app = Flask(__name__)
URL_PREFIX = os.environ.get('URL_PREFIX', '')

# Database paths
FEC_DB_PATH = "fec_contributions.db"
CA_DB_PATH = "CA/ca_contributions.db"
PAGE_SIZE = 50
PERSON_SEARCH_PAGE_SIZE = 10

# Default database (can be overridden via --default-db CLI arg)
DEFAULT_DB = "fec"

def get_current_db():
    """Get current database selection from cookie, falling back to default."""
    return request.cookies.get('db', DEFAULT_DB)

# Jinja2 filters
def format_currency(value):
    if value is None: return "$0.00"
    return "${:,.2f}".format(value)

def format_comma(value):
    if value is None: return "0"
    return "{:,}".format(int(value))

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

app.jinja_env.filters['currency'] = format_currency
app.jinja_env.filters['comma'] = format_comma
app.jinja_env.filters['quote_plus'] = quote_plus

# Helper function for sortable column headers
def build_sort_url(column, current_params):
    """Build URL for sorting by a specific column."""
    from urllib.parse import urlencode
    
    # Get current sort parameters
    current_sort = current_params.get('sort_by', 'contribution_date')
    current_order = current_params.get('order', 'desc')
    
    # Toggle order if same column, otherwise default to desc
    if column == current_sort:
        new_order = 'asc' if current_order == 'desc' else 'desc'
    else:
        new_order = 'desc'
    
    # Build new parameters
    new_params = current_params.copy()
    new_params['sort_by'] = column
    new_params['order'] = new_order
    new_params.pop('page', None)  # Reset to first page when sorting
    
    return urlencode(new_params)

app.jinja_env.globals['build_sort_url'] = build_sort_url
app.jinja_env.globals['min'] = min
app.jinja_env.globals['max'] = max
app.jinja_env.globals['PREFIX'] = URL_PREFIX

# Database connection helper
def get_db(db_type=None):
    """Get database connection based on current context or specified type."""
    if db_type:
        db_to_use = db_type
    else:
        db_to_use = get_current_db()
    
    if db_to_use == "ca":
        return sqlite3.connect(CA_DB_PATH)
    else:
        return sqlite3.connect(FEC_DB_PATH)

# Database toggle route
@app.route("/toggle_db")
def toggle_database():
    """Toggle between FEC and CA databases."""
    current = request.cookies.get('db', DEFAULT_DB)
    new_db = "ca" if current == "fec" else "fec"

    # Preserve current search parameters
    preserved_params = {k: v for k, v in request.args.items() if k != 'db'}

    # Redirect to main search with preserved parameters
    if preserved_params:
        resp = make_response(redirect(f"{URL_PREFIX}/?{urlencode(preserved_params)}"))
    else:
        resp = make_response(redirect(f"{URL_PREFIX}/"))
    resp.set_cookie('db', new_db, max_age=60*60*24*365, path='/')
    return resp

# Get current database info for templates
def get_db_info():
    """Get current database information for display."""
    if get_current_db() == "ca":
        return {
            "name": "California",
            "emoji": "🏛️",
            "description": "CA State & Local Campaigns",
            "color": "#ff6b35",
            "toggle_text": "Switch to Federal Data",
            "toggle_emoji": "🇺🇸"
        }
    else:
        return {
            "name": "Federal",
            "emoji": "🇺🇸", 
            "description": "US Federal Campaigns",
            "color": "#3498db",
            "toggle_text": "Switch to California Data",
            "toggle_emoji": "🏛️"
        }

# Add to template globals
app.jinja_env.globals['get_db_info'] = get_db_info
app.jinja_env.globals['current_db'] = lambda: get_current_db()

# Search API functions
def _safe_excerpt(text, limit=500):
    """Return a short, safe excerpt for debugging."""
    if not text:
        return ""
    text = text.strip()
    if len(text) > limit:
        return text[:limit] + "... [truncated]"
    return text


def _subset_headers(headers, interesting=None, max_items=8):
    """Return a small subset of response headers for debug output."""
    if interesting is None:
        interesting = ["content-type", "cf-ray", "server", "cache-control", "x-request-id", "x-frame-options"]
    subset = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in interesting:
            subset[lk] = v
    # If still empty, just take first few headers
    if not subset:
        for idx, (k, v) in enumerate(headers.items()):
            if idx >= max_items:
                break
            subset[k] = v
    return subset


def search_duckduckgo(query, timeout=5, include_debug=False):
    """Search DuckDuckGo for instant answers and related topics."""
    debug = {
        "query": query,
        "url": None,
        "status_code": None,
        "elapsed_ms": None,
        "result_keys": None,
        "has_result": False,
        "error": None,
        "response_excerpt": None,
        "content_type": None,
        "json_error": None,
        "message": None,
        "response_text_length": None,
        "headers": None,
    }
    response_text = None
    try:
        url = SEARCH_APIS['duckduckgo']['instant_answer_url'].format(quote_plus(query))
        debug["url"] = url
        print(f"🦆 DuckDuckGo API call: {url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
        start_time = time.time()
        response = requests.get(url, timeout=timeout, headers=headers)
        response_text = response.text
        debug["status_code"] = response.status_code
        debug["elapsed_ms"] = round((time.time() - start_time) * 1000, 2)
        debug["content_type"] = response.headers.get("Content-Type")
        debug["response_text_length"] = len(response_text or "")
        debug["headers"] = _subset_headers(response.headers)
        print(f"🦆 DuckDuckGo response status: {response.status_code} (elapsed {debug['elapsed_ms']} ms)")
        if response.status_code in [200, 202]:
            if not response_text:
                debug["json_error"] = "empty_body"
                debug["response_excerpt"] = ""
                debug["message"] = "empty_body"
                return ({}, debug) if include_debug else {}
            try:
                data = response.json()
            except Exception as json_err:
                debug["json_error"] = str(json_err)
                debug["response_excerpt"] = _safe_excerpt(response_text)
                debug["message"] = "json_parse_failure"
                return ({}, debug) if include_debug else {}
            debug["result_keys"] = list(data.keys())
            result = {
                'abstract': data.get('Abstract', ''),
                'abstract_text': data.get('AbstractText', ''),
                'abstract_url': data.get('AbstractURL', ''),
                'related_topics': data.get('RelatedTopics', [])[:3],
                'definition': data.get('Definition', ''),
                'answer': data.get('Answer', '')
            }
            debug["has_result"] = bool(any(result.values()))
            if not debug["has_result"]:
                debug["message"] = "no_instant_answer_fields"
                debug["response_excerpt"] = _safe_excerpt(response_text)
            print(f"🦆 DuckDuckGo result keys: {debug['result_keys']}")
            return (result, debug) if include_debug else result
        debug["response_excerpt"] = _safe_excerpt(response_text)
    except Exception as e:
        debug["error"] = str(e)
        print(f"DuckDuckGo search error: {e}")
    return ({}, debug) if include_debug else {}

def search_wikipedia(query, timeout=5, include_debug=False):
    """Search Wikipedia for information about the query."""
    debug = {
        "query": query,
        "url": None,
        "status_code": None,
        "elapsed_ms": None,
        "result_keys": None,
        "has_result": False,
        "error": None,
        "response_excerpt": None,
        "content_type": None,
        "json_error": None,
        "message": None,
        "response_text_length": None,
        "headers": None,
        "suggestion_used": None,
    }
    response_text = None
    try:
        url = SEARCH_APIS['wikipedia']['search_url'].format(quote_plus(query))
        debug["url"] = url
        print(f"📚 Wikipedia API call: {url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
        start_time = time.time()
        response = requests.get(url, timeout=timeout, headers=headers)
        response_text = response.text
        debug["status_code"] = response.status_code
        debug["elapsed_ms"] = round((time.time() - start_time) * 1000, 2)
        debug["content_type"] = response.headers.get("Content-Type")
        debug["response_text_length"] = len(response_text or "")
        debug["headers"] = _subset_headers(response.headers)
        print(f"📚 Wikipedia response status: {response.status_code} (elapsed {debug['elapsed_ms']} ms)")
        def parse_summary(json_text, dbg):
            try:
                data = json.loads(json_text)
            except Exception as json_err_inner:
                dbg["json_error"] = str(json_err_inner)
                dbg["response_excerpt"] = _safe_excerpt(json_text)
                dbg["message"] = "json_parse_failure"
                return {}, dbg
            dbg["result_keys"] = list(data.keys())
            result_local = {
                'title': data.get('title', ''),
                'extract': data.get('extract', ''),
                'url': data.get('content_urls', {}).get('desktop', {}).get('page', ''),
                'thumbnail': data.get('thumbnail', {}).get('source', '') if data.get('thumbnail') else ''
            }
            dbg["has_result"] = bool(result_local.get('extract') or result_local.get('title'))
            if not dbg["has_result"]:
                dbg["message"] = "no_summary_fields"
                dbg["response_excerpt"] = _safe_excerpt(json_text)
            return result_local, dbg

        if response.status_code == 200:
            result, debug = parse_summary(response_text, debug)
            if debug.get("json_error"):
                return ({}, debug) if include_debug else {}
            return (result, debug) if include_debug else result

        # If summary endpoint 404, try suggestions to find a likely page
        if response.status_code == 404 and SEARCH_APIS['wikipedia'].get('search_suggestions'):
            suggestion_url = SEARCH_APIS['wikipedia']['search_suggestions'].format(quote_plus(query))
            try:
                sug_resp = requests.get(suggestion_url, timeout=timeout, headers=headers)
                debug["suggestion_status"] = sug_resp.status_code
                debug["suggestion_url"] = suggestion_url
                if sug_resp.status_code == 200:
                    suggestions = sug_resp.json()
                    if suggestions and len(suggestions) >= 2 and suggestions[1]:
                        suggestion = suggestions[1][0]
                        debug["suggestion_used"] = suggestion
                        alt_url = SEARCH_APIS['wikipedia']['search_url'].format(quote_plus(suggestion))
                        alt_resp = requests.get(alt_url, timeout=timeout, headers=headers)
                        debug["alt_url"] = alt_url
                        debug["alt_status"] = alt_resp.status_code
                        debug["alt_content_type"] = alt_resp.headers.get("Content-Type")
                        debug["alt_response_text_length"] = len(alt_resp.text or "")
                        if alt_resp.status_code == 200:
                            alt_result, alt_debug = parse_summary(alt_resp.text, debug)
                            if alt_debug.get("json_error"):
                                return ({}, alt_debug) if include_debug else {}
                            return (alt_result, alt_debug) if include_debug else alt_result
            except Exception as sug_err:
                debug["error"] = f"suggestion_error: {sug_err}"

        debug["response_excerpt"] = _safe_excerpt(response_text)
    except Exception as e:
        debug["error"] = str(e)
        print(f"Wikipedia search error: {e}")
    return ({}, debug) if include_debug else {}

def search_news(query, timeout=5, include_debug=False):
    """Search for news articles (if API key is available)."""
    debug = {
        "query": query,
        "url": None,
        "status_code": None,
        "elapsed_ms": None,
        "total_results": None,
        "error": None,
        "enabled": SEARCH_APIS['news_api']['enabled'] and bool(SEARCH_APIS['news_api']['api_key']),
        "response_excerpt": None,
        "content_type": None,
        "json_error": None,
        "params": None,
        "message": None,
    }
    if not debug["enabled"]:
        debug["error"] = "News API disabled or missing api_key"
        return ({}, debug) if include_debug else {}
    
    response_text = None
    try:
        url = f"{SEARCH_APIS['news_api']['base_url']}everything"
        debug["url"] = url
        params = {
            'q': query,
            'apiKey': SEARCH_APIS['news_api']['api_key'],
            'pageSize': 5,
            'sortBy': 'publishedAt'
        }
        debug["params"] = {"q": query, "pageSize": 5, "sortBy": "publishedAt", "apiKey_present": bool(SEARCH_APIS['news_api']['api_key'])}
        start_time = time.time()
        response = requests.get(url, params=params, timeout=timeout)
        response_text = response.text
        debug["status_code"] = response.status_code
        debug["elapsed_ms"] = round((time.time() - start_time) * 1000, 2)
        debug["content_type"] = response.headers.get("Content-Type")
        if response.status_code == 200:
            try:
                data = response.json()
            except Exception as json_err:
                debug["json_error"] = str(json_err)
                debug["response_excerpt"] = _safe_excerpt(response_text)
                debug["message"] = "json_parse_failure"
                return ({}, debug) if include_debug else {}
            debug["total_results"] = data.get('totalResults', 0)
            result = {
                'articles': data.get('articles', [])[:3],
                'total_results': data.get('totalResults', 0)
            }
            if not result['articles']:
                debug["message"] = "no_articles_returned"
                debug["response_excerpt"] = _safe_excerpt(response_text)
            return (result, debug) if include_debug else result
        else:
            debug["error"] = f"Status {response.status_code}"
            debug["response_excerpt"] = _safe_excerpt(response_text)
    except Exception as e:
        debug["error"] = str(e)
        print(f"News API search error: {e}")
    return ({}, debug) if include_debug else {}

def search_alternative(query, timeout=5, include_debug=False):
    """Alternative search using DuckDuckGo HTML search as fallback."""
    debug = {
        "query": query,
        "url": None,
        "status_code": None,
        "elapsed_ms": None,
        "message": None,
        "error": None,
        "response_excerpt": None,
        "content_type": None,
    }
    response_text = None
    try:
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        debug["url"] = search_url
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        start_time = time.time()
        response = requests.get(search_url, headers=headers, timeout=timeout)
        response_text = response.text
        debug["status_code"] = response.status_code
        debug["elapsed_ms"] = round((time.time() - start_time) * 1000, 2)
        debug["content_type"] = response.headers.get("Content-Type")
        if response.status_code == 200:
            debug["message"] = "html_search_available"
            result = {
                'search_url': search_url,
                'status': 'html_search_available',
                'message': 'Search results available via HTML interface'
            }
            return (result, debug) if include_debug else result
        debug["response_excerpt"] = _safe_excerpt(response_text)
    except Exception as e:
        debug["error"] = str(e)
        print(f"Alternative search error: {e}")
    return ({}, debug) if include_debug else {}

def perform_comprehensive_search(search_queries):
    """Perform comprehensive search across multiple APIs."""
    results = {}
    
    for search_name, query in search_queries.items():
        if not query:
            continue
            
        duck_result, duck_debug = search_duckduckgo(query, include_debug=True)
        wiki_result, wiki_debug = search_wikipedia(query, include_debug=True)
        news_result, news_debug = search_news(query, include_debug=True)
        alt_result, alt_debug = search_alternative(query, include_debug=True)

        search_results = {
            'duckduckgo': duck_result,
            'wikipedia': wiki_result,
            'news': news_result,
            'alternative': alt_result
        }
        search_debug = {
            'duckduckgo': duck_debug,
            'wikipedia': wiki_debug,
            'news': news_debug,
            'alternative': alt_debug
        }
        
        # Filter out empty results, but keep debug info
        search_results = {k: v for k, v in search_results.items() if v}

        results[search_name] = {
            'query': query,
            'results': search_results,
            'debug': search_debug,
            'had_results': bool(search_results)
        }
        print(f"\n🔎 External search debug summary for '{search_name}':")
        pprint.pprint(search_debug)
        if not search_results:
            print(f"⚠️ No parsed results returned for '{search_name}' (query='{query}'). Inspect debug above.")
    
    return results

# Known conduits by database
FEC_CONDUITS = {
    "C00401224": "ACTBLUE",
    "C00694323": "WINRED", 
    "C00708504": "NATIONBUILDER", 
    "C00580100": "REPUBLICAN PLATFORM FUND",
    "C00904466": "ACTBLUE",
}

CA_CONDUITS = [
    "ActBlue",
    "ActBlue California", 
    "WinRed"
]

def get_conduits():
    """Get conduits for current database."""
    if get_current_db() == "ca":
        return CA_CONDUITS
    else:
        return FEC_CONDUITS

# Search API configuration
SEARCH_APIS = {
    'duckduckgo': {
        'enabled': True,
        'base_url': 'https://api.duckduckgo.com/',
        'instant_answer_url': 'https://api.duckduckgo.com/?q={}&format=json&no_html=1&skip_disambig=1'
    },
    'wikipedia': {
        'enabled': True,
        'search_url': 'https://en.wikipedia.org/api/rest_v1/page/summary/{}',
        'search_suggestions': 'https://en.wikipedia.org/w/api.php?action=opensearch&search={}&limit=5&namespace=0&format=json'
    },
    'news_api': {
        'enabled': True,  # Set to True if you have an API key
        'base_url': 'https://newsapi.org/v2/',
        'api_key': os.getenv('NEWS_API_KEY', '')
    }
}

# Security headers
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Updated CSP to allow API calls and remove iframe restrictions
    response.headers['Content-Security-Policy'] = "default-src 'self'; connect-src 'self' https://api.duckduckgo.com https://en.wikipedia.org https://newsapi.org; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; object-src 'none';"
    return response

@app.route("/", methods=["GET"])
def search():
    """Unified search page that works with both databases."""
    db_info = get_db_info()
    
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

    # Validate parameters
    if original_params["sort_by"] not in {"contribution_date", "amount"}:
        original_params["sort_by"] = "contribution_date"
    if original_params["order"] not in {"asc", "desc"}:
        original_params["order"] = "desc"

    # Validate year
    year_filter = None
    if original_params["year"] and original_params["year"].isdigit() and len(original_params["year"]) == 4:
        year_filter = original_params["year"]
    else:
        original_params["year"] = ""

    # Check if search should be performed
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

        # Database-specific query logic
        if get_current_db() == "ca":
            # California query logic (no conduit filtering in WHERE)
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

                where_clauses = []
                query_params_list = []

                if current_params["first_name"]:
                    where_clauses.append("c.first_name = ? COLLATE NOCASE")
                    query_params_list.append(current_params["first_name"])
                if current_params["last_name"]:
                    where_clauses.append("c.last_name = ? COLLATE NOCASE")
                    query_params_list.append(current_params["last_name"])
                if current_params["zip_code"]:
                    zip_digits = "".join(ch for ch in current_params["zip_code"] if ch.isdigit())
                    zip5 = zip_digits[:5]
                    where_clauses.append("(c.zip_code LIKE ? OR substr(c.zip_code,1,5) = ?)")
                    query_params_list.extend([zip_digits + "%", zip5])
                if current_params["city"]:
                    where_clauses.append("c.city = ? COLLATE NOCASE")
                    query_params_list.append(current_params["city"])
                if current_params["state"]:
                    where_clauses.append("c.state = ? COLLATE NOCASE")
                    query_params_list.append(current_params["state"])
                elif not current_params["state"] and (current_params["first_name"] or current_params["last_name"]):
                    # Default to CA for California database
                    where_clauses.append("c.state = ? COLLATE NOCASE")
                    query_params_list.append("CA")

                if year_filter:
                    start_date = f"{year_filter}-01-01"
                    end_date = f"{year_filter}-12-31"
                    where_clauses.append("c.contribution_date >= ? AND c.contribution_date <= ?")
                    query_params_list.extend([start_date, end_date])

                if not where_clauses:
                    continue

                where_string = " WHERE " + " AND ".join(where_clauses)

                # Count query
                count_query_sql = f"SELECT COUNT(*) FROM contributions c {where_string}"
                cursor.execute(count_query_sql, query_params_list)
                current_total_results = cursor.fetchone()[0]

                if current_total_results > 0:
                    total_results = current_total_results
                    total_pages = math.ceil(total_results / PAGE_SIZE)
                    effective_params = current_params
                    found_results = True
                    
                    if level == "Dropped ZIP Code":
                        cascade_message = "(Results found after dropping ZIP Code filter)"
                    elif level == "Dropped City & ZIP Code":
                        cascade_message = "(Results found after dropping City & ZIP Code filters)"
                    else:
                        cascade_message = ""
                    
                    offset = (page - 1) * PAGE_SIZE

                    # Data query for CA
                    base_select_columns = """
                        c.first_name, c.last_name, c.contribution_date,
                        COALESCE(cm.name, 'Committee ID: ' || c.recipient_committee_id), c.amount,
                        COALESCE(cm.committee_type, ''), c.recipient_committee_id,
                        c.city, c.state, c.zip_code
                    """
                    from_join_clause = "FROM contributions c LEFT JOIN committees cm ON c.recipient_committee_id = cm.committee_id"
                    
                    data_query_sql = (
                        f"SELECT {base_select_columns} {from_join_clause}{where_string} "
                        f"ORDER BY c.{effective_params['sort_by']} {effective_params['order']} LIMIT ? OFFSET ?"
                    )
                    paged_data_params = query_params_list + [PAGE_SIZE, offset]

                    cursor.execute(data_query_sql, paged_data_params)
                    results = cursor.fetchall()
                    break

        else:
            # Federal FEC query logic (with conduit filtering)
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

                where_clauses = ["c.recipient_name NOT IN ({})".format(",".join(["?"] * len(FEC_CONDUITS)))]
                query_params_list = list(FEC_CONDUITS.keys())

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

                where_string = " WHERE " + " AND ".join(where_clauses)

                # Count query
                count_query_sql = f"SELECT COUNT(*) FROM contributions c {where_string}"
                cursor.execute(count_query_sql, query_params_list)
                current_total_results = cursor.fetchone()[0]

                if current_total_results > 0:
                    total_results = current_total_results
                    total_pages = math.ceil(total_results / PAGE_SIZE)
                    effective_params = current_params
                    found_results = True
                    
                    if level == "Dropped ZIP Code":
                        cascade_message = "(Results found after dropping ZIP Code filter)"
                    elif level == "Dropped City & ZIP Code":
                        cascade_message = "(Results found after dropping City & ZIP Code filters)"
                    else:
                        cascade_message = ""
                    
                    offset = (page - 1) * PAGE_SIZE

                    # Data query for FEC
                    base_select_columns = """
                        c.first_name, c.last_name, c.contribution_date,
                        COALESCE(m.name, c.recipient_name), c.amount, 
                        COALESCE(m.type, ''), c.recipient_name,
                        c.city, c.state, c.zip_code
                    """
                    from_join_clause = "FROM contributions c LEFT JOIN committees m ON c.recipient_name = m.committee_id"
                    
                    data_query_sql = (
                        f"SELECT {base_select_columns} {from_join_clause}{where_string} "
                        f"ORDER BY c.{effective_params['sort_by']} {effective_params['order']} LIMIT ? OFFSET ?"
                    )
                    paged_data_params = query_params_list + [PAGE_SIZE, offset]

                    cursor.execute(data_query_sql, paged_data_params)
                    results = cursor.fetchall()
                    break

        conn.close()

        if search_criteria_provided and not found_results:
            initial_criteria_list = []
            if original_params["first_name"]: initial_criteria_list.append(f"First Name: {original_params['first_name']}")
            if original_params["last_name"]: initial_criteria_list.append(f"Last Name: {original_params['last_name']}")
            if original_params["city"]: initial_criteria_list.append(f"City: {original_params['city']}")
            if original_params["state"]: initial_criteria_list.append(f"State: {original_params['state']}")
            if original_params["zip_code"]: initial_criteria_list.append(f"ZIP: {original_params['zip_code']}")
            if year_filter: initial_criteria_list.append(f"Year: {year_filter}")
            
            no_results_detail_message = f"No contributions found matching: { ', '.join(initial_criteria_list) }."

    # Generate pagination params
    pagination_params = {k: v for k, v in effective_params.items() if k not in ['page'] and v}
    
    return render_template_string(UNIFIED_SEARCH_TEMPLATE, 
       results=results, page=page, total_pages=total_pages, total_results=total_results, 
       PAGE_SIZE=PAGE_SIZE, original_params=original_params,
       current_params=original_params,
       sort_by=original_params["sort_by"],
       order=original_params["order"],
       pagination_params=pagination_params,
       urlencode=urlencode,
       cascade_message=cascade_message,
       search_criteria_provided=search_criteria_provided,
       no_results_detail_message=no_results_detail_message,
       db_info=db_info
   )

@app.route("/personsearch", methods=["GET"])
def person_search_form():
    """Person search form."""
    return render_template_string(PERSON_SEARCH_TEMPLATE)

@app.route("/person")
def person_view_results():
    """Person search results showing both Federal and State contributions with Google integration."""
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

    # Search FEC database
    import time as _time
    _t0 = _time.time()
    fec_results = []
    fec_total = 0
    try:
        fec_conn = get_db("fec")
        fec_cursor = fec_conn.cursor()
        fec_query = """
            SELECT c.first_name, c.last_name, c.city, c.state, c.zip_code, c.contribution_date, 
                   COALESCE(m.name, c.recipient_name) as recipient_display, c.amount, 
                   COALESCE(m.type, '') as recipient_type, c.recipient_name
            FROM contributions c 
            LEFT JOIN committees m ON c.recipient_name = m.committee_id
            WHERE c.first_name = ? AND c.last_name = ?
              AND c.recipient_name NOT IN ({})
        """.format(",".join(["?"] * len(FEC_CONDUITS)))
        fec_query_params = [original_form_params["first_name"], original_form_params["last_name"]] + list(FEC_CONDUITS.keys())
        
        # Add optional filters for FEC
        if original_form_params["city"]:
            fec_query += " AND c.city = ?"
            fec_query_params.append(original_form_params["city"])
        if original_form_params["state"]:
            fec_query += " AND c.state = ?"
            fec_query_params.append(original_form_params["state"])
        if original_form_params["zip_code"]:
            fec_zip = "".join(ch for ch in original_form_params["zip_code"] if ch.isdigit())
            fec_query += " AND c.zip_code LIKE ?"
            fec_query_params.append(fec_zip[:5] + "%")
            
        fec_query += " ORDER BY c.contribution_date DESC LIMIT 50"
        fec_cursor.execute(fec_query, fec_query_params)
        fec_results = fec_cursor.fetchall()
        fec_total = sum(float(row[7]) for row in fec_results if row[7])
        fec_conn.close()
    except Exception as e:
        print(f"FEC search error: {e}")
    print(f"⏱️ FEC query: {_time.time() - _t0:.2f}s")

    _t1 = _time.time()
    # Search CA database
    ca_results = []
    ca_total = 0
    try:
        ca_conn = get_db("ca")
        ca_cursor = ca_conn.cursor()
        ca_query = """
            SELECT DISTINCT c.first_name, c.last_name, c.city, c.state, c.zip_code, c.contribution_date,
                   COALESCE(fc.name, 'Committee ID: ' || c.recipient_committee_id) as recipient_display,
                   c.amount, 'CA Committee' as recipient_type
            FROM contributions c
            LEFT JOIN committees fc ON c.recipient_committee_id = fc.committee_id
            WHERE c.first_name = ? COLLATE NOCASE AND c.last_name = ? COLLATE NOCASE
        """
        ca_query_params = [original_form_params["first_name"], original_form_params["last_name"]]
        
        # Add optional filters for CA using correct column names
        if original_form_params["city"]:
            ca_query += " AND c.city = ? COLLATE NOCASE"
            ca_query_params.append(original_form_params["city"])
        if original_form_params["state"]:
            ca_query += " AND c.state = ? COLLATE NOCASE"
            ca_query_params.append(original_form_params["state"])
        else:
            # Default to CA state if no state specified
            ca_query += " AND c.state = ? COLLATE NOCASE"
            ca_query_params.append("CA")
            
        if original_form_params["zip_code"]:
            # Use normalized ZIP column for CA database
            zip_digits = "".join(ch for ch in original_form_params["zip_code"] if ch.isdigit())
            zip5 = zip_digits[:5]
            ca_query += " AND (c.zip_code LIKE ? OR substr(c.zip_code,1,5) = ?)"
            ca_query_params.extend([zip_digits + "%", zip5])
            
        ca_query += " ORDER BY c.contribution_date DESC LIMIT 50"
        ca_cursor.execute(ca_query, ca_query_params)
        ca_results = ca_cursor.fetchall()
        # Calculate total from deduplicated results
        ca_total = sum(float(row[7]) for row in ca_results if row[7])
        ca_conn.close()
    except Exception as e:
        print(f"CA search error: {e}")
    print(f"⏱️ CA query: {_time.time() - _t1:.2f}s")

    # Generate search queries for API-based search
    search_queries = {}
    
    # Address Search
    address_parts = []
    if original_form_params["first_name"]: address_parts.append(original_form_params["first_name"])
    if original_form_params["last_name"]: address_parts.append(original_form_params["last_name"])
    if original_form_params["street"]: address_parts.append(original_form_params["street"])
    if original_form_params["city"]: address_parts.append(original_form_params["city"])
    if original_form_params["state"]: address_parts.append(original_form_params["state"])
    if address_parts:
        search_queries["address"] = " ".join(address_parts)

    # Phone Search (using normalized number)
    formatted_phone = normalize_and_format_phone(original_form_params["phone"])
    if formatted_phone:
        phone_parts = []
        if original_form_params["first_name"]: phone_parts.append(original_form_params["first_name"])
        if original_form_params["last_name"]: phone_parts.append(original_form_params["last_name"])
        phone_parts.append(formatted_phone)
        search_queries["phone"] = " ".join(phone_parts)

    # Email Search
    if original_form_params["email"]:
        email_parts = []
        if original_form_params["first_name"]: email_parts.append(original_form_params["first_name"])
        if original_form_params["last_name"]: email_parts.append(original_form_params["last_name"])
        email_parts.append(original_form_params["email"])
        search_queries["email"] = " ".join(email_parts)

    # Name + City Search
    if original_form_params["first_name"] and original_form_params["last_name"] and original_form_params["city"]:
        city_parts = []
        city_parts.append(original_form_params["first_name"])
        city_parts.append(original_form_params["last_name"])
        city_parts.append(original_form_params["city"])
        search_queries["city"] = " ".join(city_parts)

    # General searches
    full_name = f"{original_form_params['first_name']} {original_form_params['last_name']}"
    search_queries["general"] = full_name
    search_queries["linkedin"] = f"site:linkedin.com {full_name}"
    search_queries["news"] = f"{full_name} news"

    # Perform comprehensive API-based search (only when enrichment is requested)
    api_search_results = {}
    if request.args.get("enrich"):
        print(f"\n🔍 Starting API search with queries: {search_queries}")
        api_search_results = perform_comprehensive_search(search_queries)
        print(f"📊 API search results: {api_search_results}")

    return render_template_string(UNIFIED_PERSON_RESULTS_TEMPLATE,
                                original_params=original_form_params,
                                fec_results=fec_results, fec_total=fec_total,
                                ca_results=ca_results, ca_total=ca_total,
                                api_search_results=api_search_results,
                                search_queries=search_queries)

@app.route("/contributor")
def contributor_view():
    """Show contributions by a specific contributor (unified for both FEC and CA databases)."""
    first = request.args.get("first", "").strip()
    last = request.args.get("last", "").strip()
    city = request.args.get("city", "").strip()
    state = request.args.get("state", "").strip() 
    zip_code = request.args.get("zip", "").strip()
    sort_by = request.args.get("sort_by", "date_desc").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1
    offset = (page - 1) * PAGE_SIZE
    
    if not first or not last:
        return "Missing first and last name", 400

    conn = get_db()
    cursor = conn.cursor()
    
    # Build base WHERE clause and params (use exact case for index efficiency)
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
        if get_current_db() == "ca":
            # Use normalized ZIP for CA
            zip_digits = "".join(ch for ch in zip_code if ch.isdigit())
            zip5 = zip_digits[:5]
            base_where_clauses.append("(c.zip_code LIKE ? OR substr(c.zip_code,1,5) = ?)")
            query_params.extend([zip_digits + "%", zip5])
        else:
            # Use regular zip_code for FEC
            base_where_clauses.append("c.zip_code LIKE ?")
            query_params.append(zip_code + "%")
    
    where_string = " AND ".join(base_where_clauses)
    
    # Database-specific queries
    if get_current_db() == "ca":
        # CA database query
        from_clause = "FROM contributions c LEFT JOIN committees fc ON c.recipient_committee_id = fc.committee_id"

        # Sort order
        if sort_by == "date_asc":
            order_clause = "c.contribution_date ASC"
        elif sort_by == "amount_desc":
            order_clause = "c.amount DESC, c.contribution_date DESC"
        elif sort_by == "amount_asc":
            order_clause = "c.amount ASC, c.contribution_date DESC"
        else:
            order_clause = "c.contribution_date DESC"
        
        count_query_sql = f"SELECT COUNT(*) {from_clause} WHERE {where_string}"
        
        data_query_sql = f"""
            SELECT c.contribution_date, 
                   COALESCE(fc.name, 'Committee ID: ' || c.recipient_committee_id) as recipient_display,
                   c.amount, c.recipient_committee_id,
                   c.city, c.state, c.zip_code
            {from_clause}
            WHERE {where_string}
            ORDER BY {order_clause} LIMIT ? OFFSET ?
        """
        
        sum_query_sql = f"SELECT SUM(c.amount) {from_clause} WHERE {where_string}"
        
    else:
        # FEC database query
        from_clause = "FROM contributions c LEFT JOIN committees m ON c.recipient_name = m.committee_id"
        
        # Sort order  
        if sort_by == "date_asc":
            order_clause = "c.contribution_date ASC"
        elif sort_by == "amount_desc":
            order_clause = "c.amount DESC, c.contribution_date DESC"
        elif sort_by == "amount_asc":
            order_clause = "c.amount ASC, c.contribution_date DESC"
        else:
            order_clause = "c.contribution_date DESC"
        
        count_query_sql = f"SELECT COUNT(*) {from_clause} WHERE {where_string}"
        
        data_query_sql = f"""
            SELECT c.contribution_date, COALESCE(m.name, c.recipient_name) as recipient_display,
                   c.amount, c.recipient_name as committee_id,
                   c.city, c.state, c.zip_code
            {from_clause}
            WHERE {where_string}
            ORDER BY {order_clause} LIMIT ? OFFSET ?
        """
        
        sum_query_sql = f"SELECT SUM(c.amount) {from_clause} WHERE {where_string}"

    # Execute queries
    paged_data_params = query_params + [PAGE_SIZE, offset]
    
    cursor.execute(count_query_sql, query_params)
    total_results = cursor.fetchone()[0]
    total_pages = math.ceil(total_results / PAGE_SIZE)

    cursor.execute(data_query_sql, paged_data_params)
    rows = cursor.fetchall()
    
    cursor.execute(sum_query_sql, query_params)
    total_amount_for_contributor = cursor.fetchone()[0] or 0

    conn.close()
    
    # Prepare pagination URL
    pagination_params = {"first": first, "last": last}
    if city: pagination_params["city"] = city
    if state: pagination_params["state"] = state
    if zip_code: pagination_params["zip"] = zip_code
    if sort_by != "date_desc": pagination_params["sort_by"] = sort_by
    base_pagination_url = URL_PREFIX + "/contributor?" + urlencode(pagination_params)

    # Construct filter description string
    filter_desc = f"{first} {last}"
    location_parts = []
    if city: location_parts.append(city)
    if state: location_parts.append(state)
    if zip_code: location_parts.append(zip_code)
    if location_parts:
        filter_desc += f" from {', '.join(location_parts)}"

    return render_template_string(CONTRIBUTOR_DETAIL_TEMPLATE,
        first=first, last=last, 
        city=city, state=state, zip_code=zip_code,
        filter_desc=filter_desc,
        total_amount_for_contributor=total_amount_for_contributor, 
        rows=rows,
        page=page, total_pages=total_pages, total_results=total_results,
        PAGE_SIZE=PAGE_SIZE,
        base_pagination_url=base_pagination_url,
        sort_by=sort_by,
        get_db_info=get_db_info)

@app.route("/search_recipients", methods=["GET"])
def search_recipients_by_name():
    """Search recipients by name - COPIED FROM WORKING app.py"""
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
        
        if get_current_db() == "ca":
            # CA database - use ca_recipient_lookup table with FTS search
            print(f"\n🔍 Using CA recipient lookup table for fuzzy search: '{name_query}'")
            
            # Determine sort order
            if sort_by == "recent_activity":
                order_clause = "ca_recipient_lookup.recent_contributions DESC, ca_recipient_lookup.recent_amount DESC, ca_recipient_lookup.total_contributions DESC"
                order_clause_simple = "recent_contributions DESC, recent_amount DESC, total_contributions DESC"
            elif sort_by == "total_activity":
                order_clause = "ca_recipient_lookup.total_contributions DESC, ca_recipient_lookup.total_amount DESC, ca_recipient_lookup.recent_contributions DESC"
                order_clause_simple = "total_contributions DESC, total_amount DESC, recent_contributions DESC"
            else:  # alphabetical
                order_clause = "ca_recipient_lookup.display_name ASC"
                order_clause_simple = "display_name ASC"
            
            # Try FTS search first, then fall back to LIKE search
            fts_count_query = """
                SELECT COUNT(*)
                FROM ca_recipient_lookup_fts fts
                JOIN ca_recipient_lookup ON fts.recipient_name = ca_recipient_lookup.recipient_name
                WHERE ca_recipient_lookup_fts MATCH ?
            """
            fts_params = [name_query]
            
            cursor.execute(fts_count_query, fts_params)
            fts_results = cursor.fetchone()[0]
            
            if fts_results > 0:
                # Use FTS search
                total_results = fts_results
                total_pages = math.ceil(total_results / PAGE_SIZE)
                
                data_query = f"""
                    SELECT ca_recipient_lookup.recipient_name, ca_recipient_lookup.display_name, ca_recipient_lookup.committee_type,
                           ca_recipient_lookup.total_contributions, ca_recipient_lookup.total_amount,
                           ca_recipient_lookup.recent_contributions, ca_recipient_lookup.recent_amount,
                           ca_recipient_lookup.last_contribution_date
                    FROM ca_recipient_lookup_fts fts
                    JOIN ca_recipient_lookup ON fts.recipient_name = ca_recipient_lookup.recipient_name
                    WHERE ca_recipient_lookup_fts MATCH ?
                    ORDER BY {order_clause}
                    LIMIT ? OFFSET ?
                """
                params = fts_params + [PAGE_SIZE, offset]
                print("   Using CA FTS search")
            else:
                # Fall back to LIKE search on display names
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
                           total_contributions, total_amount,
                           recent_contributions, recent_amount,
                           last_contribution_date
                    FROM ca_recipient_lookup 
                    WHERE display_name LIKE ? OR recipient_name LIKE ?
                    ORDER BY {order_clause_simple}
                    LIMIT ? OFFSET ?
                """
                params = like_params + [PAGE_SIZE, offset]
                print("   Using CA LIKE search (FTS had no results)")
            
            print(f"\n📋 Executing SQL (/search_recipients - CA lookup table):")
            print(data_query)
            print("📎 With params:")
            print(params)

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
            # FEC database - use the working logic from app.py
            # Check if recipient_lookup table exists
            cursor.execute("""
                SELECT COUNT(*) FROM sqlite_master 
                WHERE type='table' AND name='recipient_lookup'
            """)
            has_lookup_table = cursor.fetchone()[0] > 0
            
            if has_lookup_table:
                # Use fast lookup table with FTS search - EXACTLY like app.py
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
                print(params)

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
                print(params)

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

    # Build current params for sorting
    current_params = {
        'name_query': name_query,
        'sort_by': sort_by,
        'page': page
    }
    
    return render_template_string(RECIPIENT_SEARCH_TEMPLATE,
                                results=results,
                                page=page,
                                total_pages=total_pages,
                                total_results=total_results,
                                PAGE_SIZE=PAGE_SIZE,
                                name_query=name_query,
                                sort_by=sort_by,
                                current_params=current_params,
                                get_db_info=get_db_info)

@app.route("/api/search")
def api_search():
    """API endpoint for testing search functionality."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400
    
    # Test different search APIs
    results = {
        "query": query,
        "duckduckgo": search_duckduckgo(query),
        "wikipedia": search_wikipedia(query),
        "news": search_news(query)
    }
    
    return jsonify(results)


# --- Ported API endpoints from app.py ---

def get_donor_percentiles_by_year(first_name, last_name, zip_code):
    """Get percentile rankings for a donor across all years they have data."""
    if not zip_code or len(zip_code) < 5:
        return {}
    zip5 = zip_code[:5]
    donor_key = f"{first_name}|{last_name}|{zip5}"
    conn = get_db("fec")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT year, total_amount, contribution_count
        FROM donor_totals_by_year WHERE donor_key = ? ORDER BY year DESC
    """, (donor_key,))
    donor_years = cursor.fetchall()
    if not donor_years:
        conn.close()
        return {}
    percentiles = {}
    for year, total_amount, contrib_count in donor_years:
        cursor.execute("SELECT COUNT(*) FROM donor_totals_by_year WHERE year = ? AND total_amount > ?", (year, total_amount))
        donors_above = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM donor_totals_by_year WHERE year = ?", (year,))
        total_donors = cursor.fetchone()[0]
        if total_donors > 0:
            percentiles[year] = {
                "percentile": ((total_donors - donors_above) / total_donors) * 100,
                "rank": donors_above + 1,
                "total_amount": total_amount,
                "contribution_count": contrib_count,
                "total_donors": total_donors,
            }
    conn.close()
    return percentiles


@app.route("/api/person", methods=["GET"])
def api_person():
    """JSON API: Person search returning both FEC and CA contributions."""
    first_name = request.args.get("first_name", "").strip().upper()
    last_name = request.args.get("last_name", "").strip().upper()
    city = request.args.get("city", "").strip().upper()
    state = request.args.get("state", "").strip().upper()
    zip_code = request.args.get("zip_code", "").strip().upper()

    if not first_name or not last_name:
        return jsonify({"error": "first_name and last_name are required"}), 400

    # --- FEC search with cascading logic ---
    fec_contributions = []
    fec_total = 0.0
    cascade_message = ""
    try:
        conn = get_db("fec")
        cursor = conn.cursor()
        base = {"first_name": first_name, "last_name": last_name, "city": city, "zip_code": zip_code, "state": state}
        db_attempts = [{"params": base.copy(), "level": "All filters"}]
        if zip_code:
            a2 = base.copy(); a2["zip_code"] = ""
            db_attempts.append({"params": a2, "level": "Dropped ZIP"})
        if city:
            a3 = base.copy(); a3["zip_code"] = ""; a3["city"] = ""
            db_attempts.append({"params": a3, "level": "Dropped City & ZIP"})

        for attempt in db_attempts:
            cp = attempt["params"]
            wc = ["c.first_name = ?", "c.last_name = ?"]
            qp = [cp["first_name"], cp["last_name"]]
            if cp["city"]: wc.append("c.city = ?"); qp.append(cp["city"])
            if cp["state"]: wc.append("c.state = ?"); qp.append(cp["state"])
            if cp["zip_code"]: wc.append("c.zip_code LIKE ?"); qp.append(cp["zip_code"] + "%")
            conduit_ph = ",".join(["?"] * len(FEC_CONDUITS))
            wc.append(f"c.recipient_name NOT IN ({conduit_ph})")
            final_qp = qp + list(FEC_CONDUITS.keys())
            where = " AND ".join(wc)

            cursor.execute(f"SELECT 1 FROM contributions c WHERE {where} LIMIT 1", final_qp)
            if cursor.fetchone():
                if attempt["level"] != "All filters":
                    cascade_message = attempt["level"]
                cursor.execute(f"SELECT SUM(c.amount) FROM contributions c WHERE {where}", final_qp)
                result = cursor.fetchone()
                fec_total = result[0] if result and result[0] else 0.0
                cursor.execute(
                    f"""SELECT c.contribution_date, COALESCE(m.name, c.recipient_name),
                           c.amount, c.recipient_name, c.city, c.state, c.zip_code
                    FROM contributions c LEFT JOIN committees m ON c.recipient_name = m.committee_id
                    WHERE {where} ORDER BY c.contribution_date DESC LIMIT ?""",
                    final_qp + [PERSON_SEARCH_PAGE_SIZE],
                )
                fec_contributions = [
                    {"contribution_date": r[0], "recipient_name": r[1], "amount": r[2],
                     "committee_id": r[3], "city": r[4], "state": r[5], "zip_code": r[6]}
                    for r in cursor.fetchall()
                ]
                break
        conn.close()
    except Exception as e:
        print(f"API FEC search error: {e}")

    # --- CA search ---
    ca_contributions = []
    ca_total = 0.0
    try:
        ca_conn = get_db("ca")
        ca_cursor = ca_conn.cursor()
        ca_where = ["c.first_name = ? COLLATE NOCASE", "c.last_name = ? COLLATE NOCASE"]
        ca_params = [first_name, last_name]
        if city:
            ca_where.append("c.city = ? COLLATE NOCASE")
            ca_params.append(city)
        if state:
            ca_where.append("c.state = ? COLLATE NOCASE")
            ca_params.append(state)
        else:
            ca_where.append("c.state = ? COLLATE NOCASE")
            ca_params.append("CA")
        if zip_code:
            zip_digits = "".join(ch for ch in zip_code if ch.isdigit())
            zip5 = zip_digits[:5]
            ca_where.append("(c.zip_code LIKE ? OR substr(c.zip_code,1,5) = ?)")
            ca_params.extend([zip_digits + "%", zip5])
        ca_where_clause = " AND ".join(ca_where)
        ca_cursor.execute(
            f"""SELECT DISTINCT c.first_name, c.last_name, c.city, c.state, c.zip_code,
                       c.contribution_date,
                       COALESCE(fc.name, 'Committee ID: ' || c.recipient_committee_id) as recipient_display,
                       c.amount
                FROM contributions c
                LEFT JOIN committees fc ON c.recipient_committee_id = fc.committee_id
                WHERE {ca_where_clause}
                ORDER BY c.contribution_date DESC LIMIT ?""",
            ca_params + [PERSON_SEARCH_PAGE_SIZE],
        )
        ca_contributions = [
            {"contribution_date": r[5], "recipient_name": r[6], "amount": r[7],
             "city": r[2], "state": r[3], "zip_code": r[4]}
            for r in ca_cursor.fetchall()
        ]
        ca_total = sum(float(c["amount"]) for c in ca_contributions if c["amount"])
        ca_conn.close()
    except Exception as e:
        print(f"API CA search error: {e}")

    resp = {
        "person": {"first_name": first_name, "last_name": last_name,
                    "city": city, "state": state, "zip_code": zip_code},
        "fec": {"contributions": fec_contributions, "total_giving": fec_total},
        "ca": {"contributions": ca_contributions, "total_giving": ca_total},
    }
    if cascade_message:
        resp["cascade_message"] = cascade_message
    if zip_code:
        percentiles = get_donor_percentiles_by_year(first_name, last_name, zip_code)
        resp["percentiles"] = {str(k): v for k, v in percentiles.items()}

    return jsonify(resp)


@app.route("/api/contributor", methods=["GET"])
def api_contributor():
    """JSON API: Contributor detail with pagination."""
    first = request.args.get("first_name", "").strip().upper()
    last = request.args.get("last_name", "").strip().upper()
    city = request.args.get("city", "").strip().upper()
    state = request.args.get("state", "").strip().upper()
    zip_code = request.args.get("zip_code", "").strip().upper()
    sort_by = request.args.get("sort_by", "contribution_date")
    order = request.args.get("order", "desc")
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1

    if not first or not last:
        return jsonify({"error": "first_name and last_name are required"}), 400
    if sort_by not in {"contribution_date", "amount"}: sort_by = "contribution_date"
    if order not in {"asc", "desc"}: order = "desc"

    offset = (page - 1) * PAGE_SIZE
    conn = get_db("fec")
    cursor = conn.cursor()

    wc = ["c.first_name = ?", "c.last_name = ?"]
    qp = [first, last]
    if city: wc.append("c.city = ?"); qp.append(city)
    if state: wc.append("c.state = ?"); qp.append(state)
    if zip_code: wc.append("c.zip_code LIKE ?"); qp.append(zip_code + "%")
    conduit_ph = ",".join(["?"] * len(FEC_CONDUITS))
    wc.append(f"c.recipient_name NOT IN ({conduit_ph})")
    final_qp = qp + list(FEC_CONDUITS.keys())
    where = " AND ".join(wc)
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


@app.route("/api/contributions_by_person", methods=["GET"])
def api_contributions_by_person():
    """JSON API: Quick person lookup by name and ZIP."""
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
    """.format(",".join(["?"] * len(FEC_CONDUITS)))

    params = [first_name, last_name, zip_code + "%"] + list(FEC_CONDUITS.keys())
    conn = get_db("fec")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(query, params)
    contributions = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(contributions)


@app.route("/api/recipient", methods=["GET"])
def api_recipient():
    """JSON API: Recipient detail."""
    committee_id = request.args.get("committee_id", "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1

    if not committee_id:
        return jsonify({"error": "committee_id is required"}), 400

    if committee_id in FEC_CONDUITS:
        return jsonify({"name": FEC_CONDUITS[committee_id], "type": "passthrough",
                        "message": "This is a passthrough platform. No direct contributors shown.",
                        "contributors": [], "total_amount": 0, "page": 1, "total_pages": 0})

    offset = (page - 1) * PAGE_SIZE
    conn = get_db("fec")
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
    """JSON API: Search recipients by name."""
    q = request.args.get("q", "").strip()
    sort_by = request.args.get("sort_by", "recent_activity")
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1

    if not q:
        return jsonify({"error": "q (search query) is required"}), 400

    offset = (page - 1) * PAGE_SIZE
    conn = get_db("fec")
    cursor = conn.cursor()

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


@app.route("/debug/person")
def debug_person_search():
    """Debug endpoint to test person search with API results."""
    first = request.args.get("first", "Joe").strip()
    last = request.args.get("last", "Biden").strip()
    
    # Create search queries like the real person search
    search_queries = {
        "general": f"{first} {last}",
        "linkedin": f"site:linkedin.com {first} {last}",
        "news": f"{first} {last} news"
    }
    
    # Perform API search
    api_search_results = perform_comprehensive_search(search_queries)
    
    return jsonify({
        "search_queries": search_queries,
        "api_search_results": api_search_results,
        "has_results": bool(api_search_results)
    })

@app.route("/recipient")
def recipient_view():
    """Show details for a specific recipient/committee with sortable headers."""
    committee_id = request.args.get("committee_id", "").strip()
    sort_by = request.args.get("sort_by", "total_contributed")
    order = request.args.get("order", "desc")
    page = request.args.get("page", 1, type=int)
    if page < 1: page = 1
    offset = (page - 1) * PAGE_SIZE
    
    if not committee_id:
        return "Missing committee_id parameter", 400
    
    # Validate sort parameters
    valid_sorts = ["first_name", "total_contributed", "latest_date", "contribution_count"]
    if sort_by not in valid_sorts:
        sort_by = "total_contributed"
    if order not in ["asc", "desc"]:
        order = "desc"
    
    conn = get_db()
    cursor = conn.cursor()
    
    contributors = []
    total_pages = 0
    total_results = 0
    total_amount = 0
    recipient_name = committee_id  # fallback
    
    try:
        if get_current_db() == "ca":
            # CA recipient search - Get recipient name with better fallback
            cursor.execute("SELECT display_name, candidate_first_name, candidate_last_name, office_description FROM ca_recipient_lookup WHERE recipient_name = ?", [committee_id])
            name_result = cursor.fetchone()
            
            if name_result:
                display_name, first_name, last_name, office = name_result
                # Better name fallback strategy
                if display_name and display_name != committee_id:
                    recipient_name = display_name
                elif first_name and last_name:
                    recipient_name = f"{first_name} {last_name}"
                    if office:
                        recipient_name += f" ({office})"
                elif office:
                    recipient_name = f"Committee for {office}"
                else:
                    recipient_name = f"Committee {committee_id}"
            else:
                recipient_name = f"Committee {committee_id}"
            
            # Build ORDER BY clause
            if sort_by == "first_name":
                order_clause = f"first_name {order.upper()}, last_name {order.upper()}"
            elif sort_by == "contribution_count":
                order_clause = f"contribution_count {order.upper()}, total_contributed DESC"
            elif sort_by == "latest_date":
                order_clause = f"latest_date {order.upper()}, total_contributed DESC"
            else:  # total_contributed
                order_clause = f"total_contributed {order.upper()}, latest_date DESC"
            
            # Get total stats
            cursor.execute("SELECT COUNT(*) FROM (SELECT 1 FROM contributions WHERE recipient_committee_id = ? GROUP BY UPPER(first_name), UPPER(last_name), UPPER(city), UPPER(state))", [committee_id])
            total_results = cursor.fetchone()[0]
            total_pages = math.ceil(total_results / PAGE_SIZE)
            
            # Get top contributors with sorting
            contributors_query = f"""
                SELECT first_name, last_name, city, state, zip_code, 
                       SUM(amount) as total_contributed,
                       COUNT(*) as contribution_count,
                       MAX(contribution_date) as latest_date
                FROM contributions 
                WHERE recipient_committee_id = ?
                GROUP BY UPPER(first_name), UPPER(last_name), UPPER(city), UPPER(state)
                ORDER BY {order_clause}
                LIMIT ? OFFSET ?
            """
            cursor.execute(contributors_query, [committee_id, PAGE_SIZE, offset])
            contributors = cursor.fetchall()
            
        else:
            # FEC recipient search - Get recipient name
            cursor.execute("SELECT name FROM committees WHERE committee_id = ?", [committee_id])
            name_result = cursor.fetchone()
            recipient_name = name_result[0] if name_result else f"Committee {committee_id}"
            
            # Build ORDER BY clause
            if sort_by == "first_name":
                order_clause = f"first_name {order.upper()}, last_name {order.upper()}"
            elif sort_by == "contribution_count":
                order_clause = f"contribution_count {order.upper()}, total_contributed DESC"
            elif sort_by == "latest_date":
                order_clause = f"latest_date {order.upper()}, total_contributed DESC"
            else:  # total_contributed
                order_clause = f"total_contributed {order.upper()}, latest_date DESC"
            
            # Get total stats
            cursor.execute("SELECT COUNT(*) FROM (SELECT 1 FROM contributions WHERE recipient_name = ? GROUP BY UPPER(first_name), UPPER(last_name))", [committee_id])
            total_results = cursor.fetchone()[0]
            total_pages = math.ceil(total_results / PAGE_SIZE)
            
            # Get top contributors with sorting
            contributors_query = f"""
                SELECT first_name, last_name, city, state, zip_code,
                       SUM(amount) as total_contributed,
                       COUNT(*) as contribution_count,
                       MAX(contribution_date) as latest_date
                FROM contributions 
                WHERE recipient_name = ?
                GROUP BY UPPER(first_name), UPPER(last_name), UPPER(city), UPPER(state)
                ORDER BY {order_clause}
                LIMIT ? OFFSET ?
            """
            cursor.execute(contributors_query, [committee_id, PAGE_SIZE, offset])
            contributors = cursor.fetchall()
                
    except Exception as e:
        print(f"Recipient view error: {e}")
    
    conn.close()
    
    # Build current params for template
    current_params = {
        'committee_id': committee_id,
        'sort_by': sort_by,
        'order': order,
        'page': page
    }
    
    return render_template_string(RECIPIENT_DETAIL_TEMPLATE,
                                committee_id=committee_id,
                                recipient_name=recipient_name,
                                contributors=contributors,
                                total_amount=total_amount,
                                total_results=total_results,
                                page=page,
                                total_pages=total_pages,
                                PAGE_SIZE=PAGE_SIZE,
                                sort_by=sort_by,
                                order=order,
                                current_params=current_params,
                                get_db_info=get_db_info)

# Unified search template
UNIFIED_SEARCH_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ db_info.emoji }} {{ db_info.name }} Campaign Finance Search</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        .db-switcher { 
            background: linear-gradient(135deg, {{ db_info.color }}, {{ db_info.color }}AA); 
            color: white; 
            padding: 10px 20px; 
            border-radius: 8px; 
            margin-bottom: 20px; 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
        }
        .db-switcher h1 { margin: 0; color: white; }
        .db-toggle { 
            background: rgba(255,255,255,0.2); 
            color: white; 
            padding: 8px 15px; 
            border: 2px solid rgba(255,255,255,0.3); 
            border-radius: 6px; 
            text-decoration: none; 
            font-weight: bold;
            transition: all 0.3s ease;
        }
        .db-toggle:hover { 
            background: rgba(255,255,255,0.3); 
            border-color: rgba(255,255,255,0.5);
            color: white;
            text-decoration: none;
        }
        a { color: {{ db_info.color }}; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { margin-bottom: 20px; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; }
        form { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; display: flex; flex-wrap: wrap; gap: 15px; align-items: center; }
        form input[type="text"], form input[type="date"], form select { padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 1em; flex-grow: 1; min-width: 120px; }
        form input[type="submit"], button { background-color: {{ db_info.color }}; color: white; padding: 10px 15px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        form input[type="submit"]:hover, button:hover { filter: brightness(0.9); }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: {{ db_info.color }}22; color: #333; font-weight: 600; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .form-group { display: flex; flex-direction: column; }
        .form-group label { margin-bottom: 5px; font-weight: 500; }
        .pagination { margin: 25px 0; text-align: center; clear: both; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 14px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; color: {{ db_info.color }}; text-decoration: none; font-size: 0.95em; }
        .pagination a:hover { background-color: {{ db_info.color }}22; }
        .pagination .current-page { background-color: {{ db_info.color }}; color: white; border-color: {{ db_info.color }}; font-weight: bold; }
        .results-summary { margin: 20px 0 5px 0; font-size: 0.9em; color: #555; }
        .cascade-info { margin: 0 0 10px 0; font-size: 0.85em; color: {{ db_info.color }}; font-style: italic; }
        .info-link { text-decoration: none; margin-left: 5px; font-size: 0.9em; color: #7f8c8d; }
        .info-link:hover { color: {{ db_info.color }}; }
    </style>
</head>
<body>
    <div class="db-switcher">
        <h1>{{ db_info.emoji }} {{ db_info.name }} Campaign Finance Search</h1>
        <a href="{{ PREFIX }}/toggle_db?{{ urlencode(request.args) }}" class="db-toggle">
            {{ db_info.toggle_emoji }} {{ db_info.toggle_text }}
        </a>
    </div>
    
    <div class="nav-links">
        <a href="{{ PREFIX }}/">🔍 New Search</a>
        <a href="{{ PREFIX }}/search_recipients">👥 Search Recipients</a>
        <a href="{{ PREFIX }}/personsearch">👤 Person Search</a>
    </div>
    
    <form method="get">
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
            {% if current_db() == 'ca' %}<small style="color: #666;">(Defaults to CA)</small>{% endif %}
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
        <input type="submit" value="Search" style="align-self: flex-end;">
    </form>

    {% if results %}
      <h2>Results ({{ db_info.name }} Database)</h2>
      <div class="results-summary">
        Showing {{ (page - 1) * PAGE_SIZE + 1 }} - {{ [page * PAGE_SIZE, total_results]|min }} of {{ total_results }} contributions.
      </div>
      {% if cascade_message %}<div class="cascade-info"><strong>{{ cascade_message }}</strong></div>{% endif %}
      <table>
        <tr>
          <th><a href="?{{ build_sort_url('first_name', current_params) }}" style="color: inherit; text-decoration: none;">First {% if sort_by == 'first_name' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
          <th><a href="?{{ build_sort_url('last_name', current_params) }}" style="color: inherit; text-decoration: none;">Last {% if sort_by == 'last_name' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
          <th><a href="?{{ build_sort_url('contribution_date', current_params) }}" style="color: inherit; text-decoration: none;">Date {% if sort_by == 'contribution_date' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
          <th>Recipient</th>
          <th><a href="?{{ build_sort_url('amount', current_params) }}" style="color: inherit; text-decoration: none;">Amount {% if sort_by == 'amount' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
          <th>Type</th>
          <th>City</th><th>State</th><th>ZIP</th>
        </tr>
        {% for fn, ln, date, recip, amt, typ, cmte_id, city, state, zip in results %}
          <tr>
            <td><a href="{{ PREFIX }}/contributor?first={{ fn }}&last={{ ln }}&city={{ city|urlencode }}&state={{ state|urlencode }}&zip={{ zip|urlencode }}">{{ fn }}</a></td>
            <td><a href="{{ PREFIX }}/contributor?first={{ fn }}&last={{ ln }}&city={{ city|urlencode }}&state={{ state|urlencode }}&zip={{ zip|urlencode }}">{{ ln }}</a></td>
            <td>{{ date }}</td>
            <td>
                {% if recip in ['C00401224', 'C00904466'] %}
                    <a href="{{ PREFIX }}/recipient?committee_id={{ cmte_id }}">ACTBLUE</a>
                    <br><small style="color: #7f8c8d;">→ Final recipient not disclosed</small>
                {% elif recip == 'C00694323' %}
                    <a href="{{ PREFIX }}/recipient?committee_id={{ cmte_id }}">WINRED</a>
                    <br><small style="color: #7f8c8d;">→ Final recipient not disclosed</small>
                {% elif recip and recip.startswith('C00') %}
                    <a href="{{ PREFIX }}/recipient?committee_id={{ cmte_id }}"><span style="font-family: monospace; color: #666;">{{ recip }}</span></a>
                    <br><small style="color: #f39c12;">→ Pass-through service</small>
                {% else %}
                    <a href="{{ PREFIX }}/recipient?committee_id={{ cmte_id }}">{{ recip }}</a>
                {% endif %}
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
          {% set base_url = PREFIX + "/?" + urlencode(pagination_params) %}
          {% if page > 1 %}
              <a href="{{ base_url }}&page={{ page - 1 }}">&laquo; Previous</a>
          {% endif %}
          <span class="current-page">Page {{ page }} of {{ total_pages }}</span>
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

# Person search templates
PERSON_SEARCH_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔍 Person Search - Federal & California</title>
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
        input[type="submit"] { background-color: #9b59b6; color: white; padding: 12px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 1.1em; width: 100%; margin-top: 10px; transition: background-color 0.2s ease; }
        input[type="submit"]:hover { background-color: #8e44ad; }
        .optional-fields { border-top: 1px dashed #ccc; margin-top: 20px; padding-top: 20px; }
        .optional-fields h3 { color: #666; margin-bottom: 15px; font-size: 1.1em; }
        .loading-indicator { display: none; color: #9b59b6; font-weight: bold; text-align: center; margin-top: 15px; }
        .search-description { text-align: center; color: #666; margin-bottom: 20px; }
    </style>
</head>
<body>
    <h1>🔍 Person Search</h1>
    <p class="search-description">Search across both Federal (FEC) and California campaign finance databases</p>
    
    <div class="nav-links">
        <a href="{{ PREFIX }}/">🔍 Contribution Search</a>
        <a href="{{ PREFIX }}/search_recipients">👥 Search Recipients</a>
        <a href="{{ PREFIX }}/personsearch">👤 Person Search</a>
    </div>
    
    <form method="get" action="{{ PREFIX }}/person" onsubmit="document.getElementById('searchButton').disabled = true; document.getElementById('loading').style.display = 'block';">
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

        <div class="form-group" style="margin-top: 10px;">
            <label style="display: inline; font-weight: normal;">
                <input type="checkbox" name="enrich" value="1"> Include web search enrichment (slower)
            </label>
        </div>

        <input type="submit" value="Search Both Databases" id="searchButton">
        <div id="loading" class="loading-indicator">Searching both databases...</div>
    </form>
</body>
</html>
"""

UNIFIED_PERSON_RESULTS_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔍 Profile for {{ original_params.first_name }} {{ original_params.last_name }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2, h3 { color: #2c3e50; margin-bottom: 20px; }
        h1 { text-align: center; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { text-align: center; margin-bottom: 25px; }
        .nav-links a { margin: 0 10px; font-size: 1.1em; }
        .profile-header { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; text-align: center; }
        .results-container { display: grid; grid-template-columns: 1fr 1fr; gap: 30px; margin-bottom: 30px; }
        .database-section { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .federal-section { border-left: 4px solid #3498db; }
        .california-section { border-left: 4px solid #ff6b35; }
        .stats-summary { display: flex; justify-content: space-between; margin-bottom: 20px; padding: 15px; background-color: #f8f9fa; border-radius: 4px; }
        .stat-item { text-align: center; }
        .stat-value { font-size: 1.5em; font-weight: bold; display: block; }
        .federal-section .stat-value { color: #3498db; }
        .california-section .stat-value { color: #ff6b35; }
        .stat-label { color: #666; font-size: 0.9em; }
        .contributions-table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        .contributions-table th, .contributions-table td { padding: 8px; text-align: left; border-bottom: 1px solid #ddd; font-size: 0.9em; }
        .contributions-table th { background-color: #f8f9fa; font-weight: 600; }
        .search-results { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-top: 30px; }
        .search-section { margin-bottom: 30px; padding: 15px; border: 1px solid #e0e0e0; border-radius: 6px; }
        .search-section h3 { margin-top: 0; color: #2c3e50; }
        .search-query { font-style: italic; color: #666; margin-bottom: 15px; }
        .search-result-item { margin-bottom: 15px; padding: 10px; background-color: #f8f9fa; border-radius: 4px; }
        .search-result-title { font-weight: bold; margin-bottom: 5px; }
        .search-result-snippet { color: #555; font-size: 0.9em; line-height: 1.4; }
        .search-result-url { color: #666; font-size: 0.8em; margin-top: 5px; }
        .search-result-url a { color: #666; }
        .no-results { color: #999; font-style: italic; }
        .loading { color: #666; font-style: italic; }
        .search-links { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 15px; }
        .search-links a { background-color: #3498db; color: white; padding: 8px 16px; border-radius: 4px; text-decoration: none; font-size: 0.9em; }
        .search-links a:hover { background-color: #2980b9; }
        .wikipedia-result { border-left: 4px solid #0066cc; }
        .duckduckgo-result { border-left: 4px solid #de5833; }
        .news-result { border-left: 4px solid #28a745; }
        .debug-panel { background-color: #f8f9fb; border: 1px dashed #cbd5e0; border-radius: 6px; padding: 10px 12px; margin-top: 10px; }
        .debug-panel summary { font-weight: 600; color: #2c3e50; cursor: pointer; }
        .debug-pre { background: #fff; border: 1px solid #e1e5ea; border-radius: 4px; padding: 8px; margin: 6px 0 12px 0; white-space: pre-wrap; word-break: break-word; font-size: 0.9em; }
        .debug-pill { display: inline-block; background: #eef2ff; color: #1f2937; padding: 2px 8px; border-radius: 12px; font-weight: 600; margin-right: 8px; font-size: 0.9em; }
        @media (max-width: 768px) { .results-container { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="nav-links">
        <a href="{{ PREFIX }}/">🔍 Contribution Search</a>
        <a href="{{ PREFIX }}/search_recipients">👥 Search Recipients</a>
        <a href="{{ PREFIX }}/personsearch">👤 New Person Search</a>
    </div>

    <div class="profile-header">
        <h1>🔍 Profile: {{ original_params.first_name }} {{ original_params.last_name }}</h1>
        <p>Campaign Finance Data from Federal (FEC) and California Databases</p>
    </div>

    <div class="results-container">
        <!-- Federal Results -->
        <div class="database-section federal-section">
            <h2>🇺🇸 Federal (FEC) Contributions</h2>
            <div class="stats-summary">
                <div class="stat-item">
                    <span class="stat-value">${{ "{:,.2f}".format(fec_total) }}</span>
                    <div class="stat-label">Total Contributed</div>
                </div>
                <div class="stat-item">
                    <span class="stat-value">{{ fec_results|length }}</span>
                    <div class="stat-label">Contributions Found</div>
                </div>
            </div>
            
            {% if fec_results %}
            <table class="contributions-table">
                <thead>
                    <tr><th>Date</th><th>Recipient</th><th>Amount</th><th>City, State</th></tr>
                </thead>
                <tbody>
                    {% for contrib in fec_results[:15] %}
                    <tr>
                        <td>{{ contrib[5] or 'N/A' }}</td>
                        <td>
                            {% if contrib[9] in ['C00401224', 'C00904466'] %}
                                <span style="color: #3498db; font-weight: 500;">ACTBLUE</span>
                                <br><small style="color: #7f8c8d;">→ Pass-through service (final recipient not disclosed)</small>
                            {% elif contrib[9] == 'C00694323' %}
                                <span style="color: #e74c3c; font-weight: 500;">WINRED</span>
                                <br><small style="color: #7f8c8d;">→ Pass-through service (final recipient not disclosed)</small>
                            {% else %}
                                {{ contrib[6] or contrib[9] or 'N/A' }}
                            {% endif %}
                        </td>
                        <td>${{ "{:,.2f}".format(contrib[7] or 0) }}</td>
                        <td>{{ contrib[2] or 'N/A' }}{% if contrib[3] %}, {{ contrib[3] }}{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p>No federal contributions found.</p>
            {% endif %}
        </div>

        <!-- California Results -->
        <div class="database-section california-section">
            <h2>🏛️ California Contributions</h2>
            <div class="stats-summary">
                <div class="stat-item">
                    <span class="stat-value">${{ "{:,.2f}".format(ca_total) }}</span>
                    <div class="stat-label">Total Contributed</div>
                </div>
                <div class="stat-item">
                    <span class="stat-value">{{ ca_results|length }}</span>
                    <div class="stat-label">Contributions Found</div>
                </div>
            </div>
            
            {% if ca_results %}
            <table class="contributions-table">
                <thead>
                    <tr><th>Date</th><th>Recipient</th><th>Amount</th><th>City, State</th></tr>
                </thead>
                <tbody>
                    {% for contrib in ca_results[:15] %}
                    <tr>
                        <td>{{ contrib[5] or 'N/A' }}</td>
                        <td>{{ contrib[6] or 'N/A' }}</td>
                        <td>${{ "{:,.2f}".format(contrib[7] or 0) }}</td>
                        <td>{{ contrib[2] or 'N/A' }}{% if contrib[3] %}, {{ contrib[3] }}{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p>No California contributions found.</p>
            {% endif %}
        </div>
    </div>

    <div class="search-results">
        <h2>🔍 External Research Results</h2>
        
        {% if api_search_results %}
            {% for search_name, search_data in api_search_results.items() %}
            <div class="search-section">
                <h3>{{ search_name|title }} Search</h3>
                <div class="search-query">Searching for: {{ search_data.query }}</div>
                
                {% for source, results in search_data.results.items() %}
                    {% if source == 'wikipedia' and results %}
                    <div class="search-result-item wikipedia-result">
                        <div class="search-result-title">📚 Wikipedia: {{ results.title }}</div>
                        <div class="search-result-snippet">{{ results.extract[:300] }}{% if results.extract|length > 300 %}...{% endif %}</div>
                        {% if results.url %}
                        <div class="search-result-url"><a href="{{ results.url }}" target="_blank">{{ results.url }}</a></div>
                        {% endif %}
                    </div>
                    {% elif source == 'duckduckgo' and results %}
                        {% if results.abstract %}
                        <div class="search-result-item duckduckgo-result">
                            <div class="search-result-title">🦆 DuckDuckGo Instant Answer</div>
                            <div class="search-result-snippet">{{ results.abstract }}</div>
                            {% if results.abstract_url %}
                            <div class="search-result-url"><a href="{{ results.abstract_url }}" target="_blank">{{ results.abstract_url }}</a></div>
                            {% endif %}
                        </div>
                        {% endif %}
                        {% if results.definition %}
                        <div class="search-result-item duckduckgo-result">
                            <div class="search-result-title">📖 Definition</div>
                            <div class="search-result-snippet">{{ results.definition }}</div>
                        </div>
                        {% endif %}
                        {% if results.related_topics %}
                        <div class="search-result-item duckduckgo-result">
                            <div class="search-result-title">🔗 Related Topics</div>
                            {% for topic in results.related_topics %}
                                {% if topic.Text %}
                                <div class="search-result-snippet">{{ topic.Text }}</div>
                                {% endif %}
                            {% endfor %}
                        </div>
                        {% endif %}
                    {% elif source == 'news' and results.articles %}
                        {% for article in results.articles %}
                        <div class="search-result-item news-result">
                            <div class="search-result-title">📰 {{ article.title }}</div>
                            <div class="search-result-snippet">{{ article.description or article.content|truncate(200) }}</div>
                            {% if article.url %}
                            <div class="search-result-url"><a href="{{ article.url }}" target="_blank">{{ article.url }}</a></div>
                            {% endif %}
                            {% if article.publishedAt %}
                            <div class="search-result-url">Published: {{ article.publishedAt[:10] }}</div>
                            {% endif %}
                        </div>
                        {% endfor %}
                    {% endif %}
                {% endfor %}
                
                {% if search_data.debug %}
                <div class="debug-panel">
                    <details open>
                        <summary>Debug info (per source)</summary>
                        {% for source, dbg in search_data.debug.items() %}
                            <div class="debug-pill">{{ source|title }}{% if dbg.status_code %} · status {{ dbg.status_code }}{% endif %}{% if dbg.elapsed_ms %} · {{ dbg.elapsed_ms }} ms{% endif %}</div>
                            <div class="debug-pre">{{ dbg|tojson(indent=2) }}</div>
                        {% endfor %}
                    </details>
                </div>
                {% endif %}
                
                {% if not search_data.results %}
                <div class="no-results">No results found for this search.</div>
                {% endif %}
            </div>
            {% endfor %}
        {% else %}
            <div class="no-results">No external search results available.</div>
        {% endif %}
        
        <!-- Fallback search links for manual searching -->
        <div class="search-section">
            <h3>🔗 Manual Search Links</h3>
            <div class="search-links">
                {% for query_name, query in search_queries.items() %}
                <a href="https://www.google.com/search?q={{ query|quote_plus }}" target="_blank">{{ query_name|title }} Search</a>
                {% endfor %}
            </div>
        </div>
    </div>
</body>
</html>
"""

RECIPIENT_SEARCH_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ get_db_info()['emoji'] }} {{ get_db_info()['name'] }} Recipient Search</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        h1 { text-align: center; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { text-align: center; margin-bottom: 25px; }
        .nav-links a { margin: 0 10px; font-size: 1.1em; }
        form { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; }
        form input[type="text"] { width: 70%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 1em; }
        form input[type="submit"] { background-color: {{ get_db_info()['color'] }}; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; margin-left: 10px; }
        form input[type="submit"]:hover { filter: brightness(0.9); }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #f8f9fa; font-weight: 600; }
        .pagination { text-align: center; margin: 20px 0; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 12px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; text-decoration: none; }
        .pagination .current { background-color: {{ get_db_info()['color'] }}; color: white; border-color: {{ get_db_info()['color'] }}; }
        .pagination a:hover { background-color: #f8f9fa; }
        .results-summary { margin: 20px 0; font-weight: 500; }
    </style>
</head>
<body>
    <h1>{{ get_db_info()['emoji'] }} {{ get_db_info()['name'] }} Recipient Search</h1>
    
    <div class="nav-links">
        <a href="{{ PREFIX }}/">🔍 Contribution Search</a>
        <a href="{{ PREFIX }}/personsearch">👤 Person Search</a>
        <a href="{{ PREFIX }}/search_recipients">👥 Recipient Search</a>
    </div>

    <form method="get" onsubmit="document.getElementById('recipientSearchButton').disabled = true; document.getElementById('recipientLoadingIndicator').style.display = 'inline';">
        <div class="form-group" style="flex-grow: 3;"> 
            <label for="name_query">Name:</label>
            <input id="name_query" name="name_query" value="{{ name_query }}" placeholder="Search recipient names...">
        </div>
        <div class="form-group">
            <label for="sort_by">Sort By:</label>
            <select id="sort_by" name="sort_by">
                <option value="recent_activity" {% if sort_by == 'recent_activity' %}selected{% endif %}>Recent Activity</option>
                <option value="total_activity" {% if sort_by == 'total_activity' %}selected{% endif %}>Total Activity</option>
                <option value="alphabetical" {% if sort_by == 'alphabetical' %}selected{% endif %}>Alphabetical</option>
            </select>
        </div>
        <input type="submit" value="Search Recipients" id="recipientSearchButton" style="align-self: flex-end;">
        <span id="recipientLoadingIndicator" style="display:none; color: #e67e22; font-weight: bold; margin-left: 10px; align-self: flex-end;">Searching...</span>
    </form>

    {% if name_query %}
        {% if results %}
            <div class="results-summary">
                Found {{ total_results }} recipients matching "{{ name_query }}" 
                (showing {{ results|length }} on page {{ page }} of {{ total_pages }})
            </div>
            
            <table>
                <tr>
                  <th>Committee ID</th>
                  <th><a href="?{{ build_sort_url('name', current_params) }}" style="color: inherit; text-decoration: none;">Name {% if sort_by == 'alphabetical' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
                  <th>Type</th>
                  <th><a href="?{{ build_sort_url('recent_activity', current_params) }}" style="color: inherit; text-decoration: none;">Recent Activity<br><small>(365 days)</small> {% if sort_by == 'recent_activity' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
                  <th><a href="?{{ build_sort_url('total_activity', current_params) }}" style="color: inherit; text-decoration: none;">Total Activity<br><small>(all time)</small> {% if sort_by == 'total_activity' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
                  <th>Last Contribution</th>
                </tr>
                {% for committee_id, name, type, total_contrib, total_amt, recent_contrib, recent_amt, last_date in results %}
                  <tr>
                    <td><a href="{{ PREFIX }}/recipient?committee_id={{ committee_id }}">{{ committee_id }}</a></td>
                    <td>
                        <a href="{{ PREFIX }}/recipient?committee_id={{ committee_id }}">{{ name }}</a>
                        <a href="https://www.google.com/search?q={{ name|quote_plus }}" class="info-link" target="_blank" title="Search Google for {{ name }}">&#x24D8;</a>
                    </td>
                    <td>{{ type if type and type != "Unknown" else "PAC" }}</td>
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
                {% if page > 1 %}
                    <a href="?name_query={{ name_query }}&page={{ page - 1 }}">&laquo; Previous</a>
                {% endif %}
                
                {% for p in range(1, total_pages + 1) %}
                    {% if p == page %}
                        <span class="current">{{ p }}</span>
                    {% elif p <= 5 or p > total_pages - 5 or (p >= page - 2 and p <= page + 2) %}
                        <a href="?name_query={{ name_query }}&page={{ p }}">{{ p }}</a>
                    {% elif p == 6 and page > 8 %}
                        <span>...</span>
                    {% elif p == total_pages - 5 and page < total_pages - 7 %}
                        <span>...</span>
                    {% endif %}
                {% endfor %}
                
                {% if page < total_pages %}
                    <a href="?name_query={{ name_query }}&page={{ page + 1 }}">Next &raquo;</a>
                {% endif %}
            </div>
            {% endif %}
        {% else %}
            <p>No recipients found matching "{{ name_query }}".</p>
        {% endif %}
    {% else %}
        <p>Enter a recipient or committee name to search.</p>
    {% endif %}
</body>
</html>
"""

RECIPIENT_DETAIL_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ get_db_info()['emoji'] }} Recipient: {{ recipient_name or committee_id }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        h1 { text-align: center; }
        a { color: #3498db; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { text-align: center; margin-bottom: 25px; }
        .nav-links a { margin: 0 10px; font-size: 1.1em; }
        .recipient-header { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 30px; }
        .stats-summary { display: flex; justify-content: space-around; margin: 20px 0; }
        .stat-item { text-align: center; }
        .stat-value { font-size: 2em; font-weight: bold; color: {{ get_db_info()['color'] }}; display: block; }
        .stat-label { color: #666; margin-top: 5px; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #f8f9fa; font-weight: 600; }
        .pagination { text-align: center; margin: 20px 0; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 12px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; text-decoration: none; }
        .pagination .current { background-color: {{ get_db_info()['color'] }}; color: white; border-color: {{ get_db_info()['color'] }}; }
        .pagination a:hover { background-color: #f8f9fa; }
    </style>
</head>
<body>
    <div class="nav-links">
        <a href="{{ PREFIX }}/">🔍 Contribution Search</a>
        <a href="{{ PREFIX }}/search_recipients">👥 Search Recipients</a>
        <a href="{{ PREFIX }}/personsearch">👤 Person Search</a>
    </div>

    <div class="recipient-header">
        <h1>{{ get_db_info()['emoji'] }} {{ recipient_name or committee_id }}</h1>
        <p><strong>{{ get_db_info()['name'] }} Database</strong> {% if committee_id %}• ID: {{ committee_id }}{% endif %}</p>
        
        <div class="stats-summary">
            <div class="stat-item">
                <span class="stat-value">${{ "{:,.2f}".format(total_amount) }}</span>
                <div class="stat-label">Total Received</div>
            </div>
            <div class="stat-item">
                <span class="stat-value">{{ total_results }}</span>
                <div class="stat-label">Total Contributions</div>
            </div>
            <div class="stat-item">
                <span class="stat-value">{{ contributors|length }}</span>
                <div class="stat-label">Top Contributors (This Page)</div>
            </div>
        </div>
    </div>

    {% if contributors %}
    <h2>Top Contributors</h2>
    <table>
        <thead>
            <tr>
                <th>Contributor</th>
                <th>Location</th>
                <th>Total Contributed</th>
                <th>Number of Contributions</th>
                <th>Latest Contribution</th>
            </tr>
        </thead>
        <tbody>
            {% for contributor in contributors %}
            <tr>
                <td>{{ contributor[0] }} {{ contributor[1] }}</td>
                <td>{{ contributor[2] }}{% if contributor[3] %}, {{ contributor[3] }}{% endif %}{% if contributor[4] %} {{ contributor[4] }}{% endif %}</td>
                <td>${{ "{:,.2f}".format(contributor[5] or 0) }}</td>
                <td>{{ contributor[6] }}</td>
                <td>{{ contributor[7] or 'N/A' }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    {% if total_pages > 1 %}
    <div class="pagination">
        {% if page > 1 %}
            <a href="?committee_id={{ committee_id }}&page={{ page - 1 }}">&laquo; Previous</a>
        {% endif %}
        
        {% for p in range(1, total_pages + 1) %}
            {% if p == page %}
                <span class="current">{{ p }}</span>
            {% elif p <= 5 or p > total_pages - 5 or (p >= page - 2 and p <= page + 2) %}
                <a href="?committee_id={{ committee_id }}&page={{ p }}">{{ p }}</a>
            {% elif p == 6 and page > 8 %}
                <span>...</span>
            {% elif p == total_pages - 5 and page < total_pages - 7 %}
                <span>...</span>
            {% endif %}
        {% endfor %}
        
        {% if page < total_pages %}
            <a href="?committee_id={{ committee_id }}&page={{ page + 1 }}">Next &raquo;</a>
        {% endif %}
    </div>
    {% endif %}
    {% else %}
    <p>No contributors found for this recipient.</p>
    {% endif %}
</body>
</html>
"""

# Recipient detail template
RECIPIENT_DETAIL_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Top Contributors to {{ recipient_name }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 20px; }
        .recipient-header h1 { color: #2c3e50; margin-bottom: 10px; }
        .recipient-header p { color: #666; margin: 5px 0; }
        a { color: {{ get_db_info().color }}; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; margin-bottom:20px;}
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        th a { color: inherit; text-decoration: none; }
        th a:hover { text-decoration: underline; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .pagination { margin: 25px 0; text-align: center; clear: both; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 14px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; color: {{ get_db_info().color }}; text-decoration: none; font-size: 0.95em; }
        .pagination a:hover { background-color: #eaf2f8; border-color: #c5ddec; }
        .pagination .current-page { background-color: {{ get_db_info().color }}; color: white; border-color: {{ get_db_info().color }}; font-weight: bold; }
        .results-summary { margin: 20px 0 10px 0; font-size: 0.9em; color: #555; }
        .info-link { text-decoration: none; margin-left: 5px; font-size: 0.9em; color: #7f8c8d; }
        .info-link:hover { color: {{ get_db_info().color }}; }
    </style>
</head>
<body>
    <div class="recipient-header">
        <h1>{{ get_db_info().emoji }} {{ recipient_name }}</h1>
        <p><strong>{{ get_db_info().name }} Database</strong> • ID: {{ committee_id }}</p>
    </div>
    
    <div class="nav-links">
        <a href="{{ PREFIX }}/">🔍 New Search</a>
        <a href="{{ PREFIX }}/search_recipients">👥 Search Recipients by Name</a>
        <a href="{{ PREFIX }}/personsearch">👤 Person Search</a>
    </div>
    
    <div class="results-summary">
      Showing top {{ (page - 1) * PAGE_SIZE + 1 if total_results > 0 else 0 }} - {{ [page * PAGE_SIZE, total_results]|min }} of {{ total_results }} contributors.
    </div>
    
    <table>
        <thead>
            <tr>
                <th><a href="?committee_id={{ committee_id }}&sort_by=first_name&order={{ 'asc' if sort_by == 'first_name' and order == 'desc' else 'desc' }}">Contributor {% if sort_by == 'first_name' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
                <th>Location</th>
                <th><a href="?committee_id={{ committee_id }}&sort_by=total_contributed&order={{ 'asc' if sort_by == 'total_contributed' and order == 'desc' else 'desc' }}">Total Contributed {% if sort_by == 'total_contributed' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
                <th>Number of Contributions</th>
                <th><a href="?committee_id={{ committee_id }}&sort_by=latest_date&order={{ 'asc' if sort_by == 'latest_date' and order == 'desc' else 'desc' }}">Latest Contribution {% if sort_by == 'latest_date' %}{{ '↓' if order == 'desc' else '↑' }}{% endif %}</a></th>
            </tr>
        </thead>
        <tbody>
            {% for contrib in contributors %}
            <tr>
                <td><a href="{{ PREFIX }}/contributor?first={{ contrib[0] }}&last={{ contrib[1] }}">{{ contrib[0] }} {{ contrib[1] }}</a></td>
                <td>{{ contrib[2] }}{% if contrib[2] and contrib[3] %}, {% endif %}{{ contrib[3] }}{% if contrib[4] %} {{ contrib[4] }}{% endif %}</td>
                <td>{{ contrib[5]|currency if contrib[5] else "$0.00" }}</td>
                <td>{{ contrib[6] if contrib[6] else 0 }}</td>
                <td>{{ contrib[7] if contrib[7] else "Unknown" }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    
    {% if total_pages > 1 %}
    <div class="pagination">
        {% set base_url = PREFIX + "/recipient?committee_id=" + committee_id %}
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

# Contributor detail template
CONTRIBUTOR_DETAIL_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ get_db_info().emoji }} Contributions by {{ filter_desc }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 20px; background-color: #f4f7f6; color: #333; }
        h1, h2 { color: #2c3e50; margin-bottom: 10px; }
        h1 { font-size: 1.8em; }
        h2 { font-size: 1.2em; margin-top: 20px; }
        .filter-info { font-size: 0.95em; color: #555; margin-bottom: 20px; }
        a { color: {{ get_db_info().color }}; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .nav-links { margin-bottom: 20px; }
        .nav-links a { margin-right: 15px; font-size: 1.1em; display: inline-block; }
        table { width: 100%; border-collapse: collapse; background-color: #fff; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; margin-top: 20px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #eaf2f8; color: #333; font-weight: 600; }
        th a { color: inherit; text-decoration: none; }
        th a:hover { text-decoration: underline; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        tr:hover { background-color: #f1f1f1; }
        .pagination { margin: 25px 0; text-align: center; clear: both; }
        .pagination a, .pagination span { display: inline-block; padding: 8px 14px; margin: 0 4px; border: 1px solid #ddd; border-radius: 4px; color: {{ get_db_info().color }}; text-decoration: none; font-size: 0.95em; }
        .pagination a:hover { background-color: #eaf2f8; border-color: #c5ddec; }
        .pagination .current-page { background-color: {{ get_db_info().color }}; color: white; border-color: {{ get_db_info().color }}; font-weight: bold; }
        .results-summary { margin: 20px 0 10px 0; font-size: 0.9em; color: #555; }
        .info-link { text-decoration: none; margin-left: 5px; font-size: 0.9em; color: #7f8c8d; }
        .info-link:hover { color: {{ get_db_info().color }}; }
    </style>
</head>
<body>
    <h1>{{ get_db_info().emoji }} Contributions by {{ first }} {{ last }}</h1>
    <div class="filter-info">{{ get_db_info().name }} Database • Showing contributions matching: {{ filter_desc }}</div>
    
    <div class="nav-links">
        <a href="{{ PREFIX }}/">🔍 New Search</a>
        <a href="{{ PREFIX }}/search_recipients">👥 Search Recipients by Name</a>
        <a href="{{ PREFIX }}/personsearch">👤 Person Search</a>
    </div>
    
    <h2>Total Contributed (matching filter, all pages): {{ total_amount_for_contributor|currency }}</h2>
    
    <div class="results-summary">
      Showing {{ (page - 1) * PAGE_SIZE + 1 if total_results > 0 else 0 }} - {{ [page * PAGE_SIZE, total_results]|min }} of {{ total_results }} contributions.
    </div>
    
    <table>
        <thead>
            <tr>
                <th><a href="{{ base_pagination_url }}&sort_by={{ 'date_asc' if sort_by == 'date_desc' else 'date_desc' }}">Date {% if sort_by.startswith('date') %}{{ '↓' if sort_by == 'date_desc' else '↑' }}{% endif %}</a></th>
                <th>Recipient</th>
                <th><a href="{{ base_pagination_url }}&sort_by={{ 'amount_asc' if sort_by == 'amount_desc' else 'amount_desc' }}">Amount {% if sort_by.startswith('amount') %}{{ '↓' if sort_by == 'amount_desc' else '↑' }}{% endif %}</a></th>
                <th>City</th>
                <th>State</th>
                <th>ZIP</th>
            </tr>
        </thead>
        <tbody>
            {% for r_date, r_name, r_amt, r_cmte_id, r_city, r_state, r_zip in rows %}
            <tr>
                <td>{{ r_date }}</td>
                <td>
                    <a href="{{ PREFIX }}/recipient?committee_id={{ r_cmte_id }}">{{ r_name }}</a>
                    <a href="https://www.google.com/search?q={{ r_name|quote_plus }}" class="info-link" target="_blank" title="Search Google for {{ r_name }}">&#x24D8;</a>
                </td>
                <td>{{ r_amt|currency }}</td>
                <td>{{ r_city }}</td>
                <td>{{ r_state }}</td>
                <td>{{ r_zip }}</td>
            </tr>
            {% endfor %}
        </tbody>
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run Unified Campaign Finance Search App')
    parser.add_argument('--public', action='store_true',
                        help='Run on 0.0.0.0. WARNING: For TESTING ON TRUSTED NETWORKS ONLY.')
    parser.add_argument('--port', type=int, default=5000,
                        help='Port to run on (default: 5000)')
    parser.add_argument('--default-db', choices=['fec', 'ca'], default='fec',
                        help='Default database to use (default: fec)')
    args = parser.parse_args()

    selected_db = args.default_db

    host_ip = '0.0.0.0' if args.public else '127.0.0.1'
    debug_mode = False if args.public else True

    print(f"🚀 Starting Unified Campaign Finance App on http://{host_ip}:{args.port}")
    print(f"📊 Default database: {selected_db.upper()}")
    print(f"🔄 Toggle between databases using the switch button in the app")
    
    if args.public:
        print("⚠️  WARNING: Server is running on 0.0.0.0 for testing only.")
    
    app.run(debug=debug_mode, host=host_ip, port=args.port)
