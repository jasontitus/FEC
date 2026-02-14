"""
Tests for the Flask app JSON API endpoints.
Uses a small in-memory SQLite database to avoid depending on the production DB.
"""

import json
import os
import sys
import sqlite3
import unittest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as fec_app


def create_test_db(path):
    """Create a small test database with sample data."""
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contributions (
            first_name TEXT, last_name TEXT, city TEXT, state TEXT,
            zip_code TEXT, contribution_date TEXT, recipient_name TEXT,
            amount REAL, recipient_type TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS committees (
            committee_id TEXT PRIMARY KEY, name TEXT, type TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipient_lookup (
            recipient_name TEXT PRIMARY KEY, display_name TEXT, committee_type TEXT,
            total_contributions INTEGER DEFAULT 0, total_amount REAL DEFAULT 0,
            recent_contributions INTEGER DEFAULT 0, recent_amount REAL DEFAULT 0,
            first_contribution_date TEXT, last_contribution_date TEXT,
            contributor_count INTEGER DEFAULT 0, updated_at TEXT
        )
    """)
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS recipient_lookup_fts USING fts5(
            recipient_name, display_name, content='recipient_lookup', content_rowid='rowid'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS donor_totals_by_year (
            donor_key TEXT NOT NULL, year INTEGER NOT NULL, total_amount REAL NOT NULL,
            contribution_count INTEGER NOT NULL, first_name TEXT, last_name TEXT,
            zip5 TEXT, PRIMARY KEY (donor_key, year)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS percentile_thresholds_by_year (
            year INTEGER NOT NULL, percentile INTEGER NOT NULL,
            amount_threshold REAL NOT NULL, donor_count_at_threshold INTEGER NOT NULL,
            PRIMARY KEY (year, percentile)
        )
    """)

    # Insert test committees
    committees = [
        ("C00000001", "TEST COMMITTEE ALPHA", "P"),
        ("C00000002", "TEST COMMITTEE BETA", "X"),
        ("C00000003", "DEMOCRATIC TEST FUND", "Y"),
    ]
    cursor.executemany("INSERT INTO committees VALUES (?, ?, ?)", committees)

    # Insert test contributions
    contributions = [
        ("JOHN", "SMITH", "LOS ANGELES", "CA", "90210", "2024-06-15", "C00000001", 250.0, "15"),
        ("JOHN", "SMITH", "LOS ANGELES", "CA", "90210", "2024-03-10", "C00000002", 500.0, "15"),
        ("JOHN", "SMITH", "LOS ANGELES", "CA", "90210", "2023-11-20", "C00000001", 100.0, "15"),
        ("JANE", "DOE", "SAN FRANCISCO", "CA", "94102", "2024-07-01", "C00000001", 1000.0, "15"),
        ("JANE", "DOE", "SAN FRANCISCO", "CA", "94102", "2024-01-15", "C00000003", 2500.0, "15"),
        ("BOB", "JONES", "NEW YORK", "NY", "10001", "2024-05-20", "C00000002", 750.0, "15"),
        ("BOB", "JONES", "NEW YORK", "NY", "10001", "2024-02-28", "C00000001", 300.0, "15"),
        ("ALICE", "WILLIAMS", "CHICAGO", "IL", "60601", "2024-08-10", "C00000003", 150.0, "15"),
    ]
    cursor.executemany("INSERT INTO contributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", contributions)

    # Insert test recipient_lookup
    lookups = [
        ("C00000001", "TEST COMMITTEE ALPHA", "P", 4, 1650.0, 3, 1550.0, "2023-11-20", "2024-07-01", 3, "2024-01-01"),
        ("C00000002", "TEST COMMITTEE BETA", "X", 2, 1250.0, 2, 1250.0, "2024-02-28", "2024-06-15", 2, "2024-01-01"),
        ("C00000003", "DEMOCRATIC TEST FUND", "Y", 2, 2650.0, 2, 2650.0, "2024-01-15", "2024-08-10", 2, "2024-01-01"),
    ]
    cursor.executemany("INSERT INTO recipient_lookup VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", lookups)

    # Insert FTS data
    for name, display, *_ in lookups:
        cursor.execute("INSERT INTO recipient_lookup_fts (recipient_name, display_name) VALUES (?, ?)", (name, display))

    # Insert donor totals for percentile testing
    donor_totals = [
        ("JOHN|SMITH|90210", 2024, 750.0, 2, "JOHN", "SMITH", "90210"),
        ("JANE|DOE|94102", 2024, 3500.0, 2, "JANE", "DOE", "94102"),
        ("BOB|JONES|10001", 2024, 1050.0, 2, "BOB", "JONES", "10001"),
    ]
    cursor.executemany("INSERT INTO donor_totals_by_year VALUES (?, ?, ?, ?, ?, ?, ?)", donor_totals)

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_name ON contributions (first_name, last_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_location ON contributions (city, state, zip_code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_contrib_date ON contributions (contribution_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_contrib_recipient ON contributions (recipient_name)")

    conn.commit()
    conn.close()


class TestAPIBase(unittest.TestCase):
    """Base class for API tests with test database setup."""

    @classmethod
    def setUpClass(cls):
        cls.test_db = os.path.join(os.path.dirname(__file__), "test_fec.db")
        create_test_db(cls.test_db)
        fec_app.DB_PATH = cls.test_db
        fec_app.app.config["TESTING"] = True
        cls.client = fec_app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.test_db):
            os.unlink(cls.test_db)


class TestSearchAPI(TestAPIBase):
    """Tests for /api/search endpoint."""

    def test_search_by_last_name(self):
        resp = self.client.get("/api/search?last_name=SMITH")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("results", data)
        self.assertGreater(data["total_results"], 0)
        for r in data["results"]:
            self.assertEqual(r["last_name"], "SMITH")

    def test_search_by_state(self):
        resp = self.client.get("/api/search?state=CA")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertGreater(data["total_results"], 0)
        for r in data["results"]:
            self.assertEqual(r["state"], "CA")

    def test_search_by_year(self):
        resp = self.client.get("/api/search?last_name=SMITH&year=2024")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertGreater(data["total_results"], 0)
        for r in data["results"]:
            self.assertTrue(r["contribution_date"].startswith("2024"))

    def test_search_no_params_returns_error(self):
        resp = self.client.get("/api/search")
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_search_pagination(self):
        resp = self.client.get("/api/search?state=CA&page=1")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["page"], 1)
        self.assertIn("total_pages", data)

    def test_search_sort_by_amount(self):
        resp = self.client.get("/api/search?state=CA&sort_by=amount&order=desc")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        amounts = [r["amount"] for r in data["results"]]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_search_result_fields(self):
        resp = self.client.get("/api/search?last_name=SMITH")
        data = json.loads(resp.data)
        expected_fields = {"first_name", "last_name", "contribution_date",
                           "recipient_name", "amount", "recipient_type",
                           "committee_id", "city", "state", "zip_code"}
        for r in data["results"]:
            self.assertEqual(set(r.keys()), expected_fields)


class TestContributorAPI(TestAPIBase):
    """Tests for /api/contributor endpoint."""

    def test_contributor_basic(self):
        resp = self.client.get("/api/contributor?first_name=JOHN&last_name=SMITH")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("contributions", data)
        self.assertIn("total_amount", data)
        self.assertGreater(data["total_amount"], 0)

    def test_contributor_with_zip_has_percentiles(self):
        resp = self.client.get("/api/contributor?first_name=JOHN&last_name=SMITH&zip_code=90210")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("percentiles", data)

    def test_contributor_missing_name_returns_error(self):
        resp = self.client.get("/api/contributor?first_name=JOHN")
        self.assertEqual(resp.status_code, 400)

    def test_contributor_filter_by_state(self):
        resp = self.client.get("/api/contributor?first_name=JOHN&last_name=SMITH&state=CA")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertGreater(data["total_results"], 0)

    def test_contributor_no_results(self):
        resp = self.client.get("/api/contributor?first_name=NONEXISTENT&last_name=PERSON")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["total_results"], 0)
        self.assertEqual(data["contributions"], [])


class TestRecipientAPI(TestAPIBase):
    """Tests for /api/recipient endpoint."""

    def test_recipient_basic(self):
        resp = self.client.get("/api/recipient?committee_id=C00000001")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["name"], "TEST COMMITTEE ALPHA")
        self.assertIn("contributors", data)
        self.assertGreater(len(data["contributors"]), 0)

    def test_recipient_missing_id_returns_error(self):
        resp = self.client.get("/api/recipient")
        self.assertEqual(resp.status_code, 400)

    def test_recipient_conduit_returns_passthrough(self):
        resp = self.client.get("/api/recipient?committee_id=C00401224")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["type"], "passthrough")

    def test_recipient_contributors_sorted_by_total(self):
        resp = self.client.get("/api/recipient?committee_id=C00000001")
        data = json.loads(resp.data)
        amounts = [c["total_amount"] for c in data["contributors"]]
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_recipient_total_amount(self):
        resp = self.client.get("/api/recipient?committee_id=C00000001")
        data = json.loads(resp.data)
        self.assertGreater(data["total_amount"], 0)


class TestSearchRecipientsAPI(TestAPIBase):
    """Tests for /api/search_recipients endpoint."""

    def test_search_recipients_basic(self):
        resp = self.client.get("/api/search_recipients?q=TEST")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("results", data)
        self.assertGreater(data["total_results"], 0)

    def test_search_recipients_no_query_returns_error(self):
        resp = self.client.get("/api/search_recipients")
        self.assertEqual(resp.status_code, 400)

    def test_search_recipients_result_fields(self):
        resp = self.client.get("/api/search_recipients?q=TEST")
        data = json.loads(resp.data)
        expected = {"committee_id", "name", "type", "total_contributions",
                    "total_amount", "recent_contributions", "recent_amount",
                    "last_contribution_date"}
        for r in data["results"]:
            self.assertEqual(set(r.keys()), expected)

    def test_search_recipients_democratic(self):
        resp = self.client.get("/api/search_recipients?q=DEMOCRATIC")
        data = json.loads(resp.data)
        self.assertGreater(data["total_results"], 0)
        for r in data["results"]:
            self.assertIn("DEMOCRATIC", r["name"].upper())


class TestPersonAPI(TestAPIBase):
    """Tests for /api/person endpoint."""

    def test_person_basic(self):
        resp = self.client.get("/api/person?first_name=JOHN&last_name=SMITH")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("contributions", data)
        self.assertIn("total_giving", data)

    def test_person_missing_name_returns_error(self):
        resp = self.client.get("/api/person?first_name=JOHN")
        self.assertEqual(resp.status_code, 400)

    def test_person_with_zip_has_percentiles(self):
        resp = self.client.get("/api/person?first_name=JOHN&last_name=SMITH&zip_code=90210")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("percentiles", data)

    def test_person_searches_all_states_when_none_specified(self):
        """Person search searches all states when none specified."""
        resp = self.client.get("/api/person?first_name=BOB&last_name=JONES")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        # Should find NY results since no state filter is applied
        self.assertGreater(data["total_giving"], 0)


class TestLegacyAPI(TestAPIBase):
    """Tests for /api/contributions_by_person (legacy endpoint)."""

    def test_legacy_api_basic(self):
        resp = self.client.get("/api/contributions_by_person?first_name=JOHN&last_name=SMITH&zip_code=90210")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_legacy_api_missing_params_returns_error(self):
        resp = self.client.get("/api/contributions_by_person?first_name=JOHN&last_name=SMITH")
        self.assertEqual(resp.status_code, 400)


class TestHelperFunctions(unittest.TestCase):
    """Tests for helper/utility functions."""

    def test_format_currency(self):
        self.assertEqual(fec_app.format_currency(1234.56), "$1,234.56")
        self.assertEqual(fec_app.format_currency(0), "$0.00")
        self.assertEqual(fec_app.format_currency(None), "$0.00")

    def test_format_comma(self):
        self.assertEqual(fec_app.format_comma(1234567), "1,234,567")
        self.assertEqual(fec_app.format_comma(0), "0")
        self.assertEqual(fec_app.format_comma(None), "0")

    def test_normalize_phone_valid(self):
        self.assertEqual(fec_app.normalize_and_format_phone("(310) 555-1234"), "310-555-1234")
        self.assertEqual(fec_app.normalize_and_format_phone("310-555-1234"), "310-555-1234")
        self.assertEqual(fec_app.normalize_and_format_phone("3105551234"), "310-555-1234")
        self.assertEqual(fec_app.normalize_and_format_phone("13105551234"), "310-555-1234")

    def test_normalize_phone_invalid(self):
        self.assertIsNone(fec_app.normalize_and_format_phone(""))
        self.assertIsNone(fec_app.normalize_and_format_phone(None))
        self.assertIsNone(fec_app.normalize_and_format_phone("12345"))

    def test_map_cmte_type(self):
        self.assertEqual(fec_app.map_cmte_type("H"), "Candidate")
        self.assertEqual(fec_app.map_cmte_type("S"), "Candidate")
        self.assertEqual(fec_app.map_cmte_type("P"), "Candidate")
        self.assertEqual(fec_app.map_cmte_type("X"), "Party Committee")
        self.assertEqual(fec_app.map_cmte_type("Z"), "PAC")  # Unknown code

    def test_known_conduits(self):
        self.assertIn("C00401224", fec_app.KNOWN_CONDUITS)
        self.assertEqual(fec_app.KNOWN_CONDUITS["C00401224"], "ACTBLUE")


class TestUpdateScripts(unittest.TestCase):
    """Tests for update script modules (import and basic function checks)."""

    def test_update_fec_imports(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import update_fec
        self.assertTrue(hasattr(update_fec, "main"))
        self.assertTrue(hasattr(update_fec, "check_for_updates"))
        self.assertTrue(hasattr(update_fec, "load_metadata"))
        self.assertTrue(hasattr(update_fec, "save_metadata"))

    def test_update_all_imports(self):
        import update_all
        self.assertTrue(hasattr(update_all, "main"))
        self.assertTrue(hasattr(update_all, "run_update"))

    def test_update_fec_metadata_roundtrip(self):
        import update_fec
        import tempfile
        import json

        original = update_fec.METADATA_FILE
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            update_fec.METADATA_FILE = tmp
            test_data = {"test_url": {"content_length": "12345", "last_modified": "Mon, 01 Jan 2024"}}
            update_fec.save_metadata(test_data)
            loaded = update_fec.load_metadata()
            self.assertEqual(loaded, test_data)
        finally:
            update_fec.METADATA_FILE = original
            os.unlink(tmp)

    def test_process_incremental_imports(self):
        try:
            import process_incremental
            self.assertTrue(hasattr(process_incremental, "process_file_incrementally"))
            self.assertTrue(hasattr(process_incremental, "record_exists"))
        except ImportError as e:
            if "tqdm" in str(e):
                self.skipTest("tqdm not installed (install with: pip install tqdm)")
            raise


class TestCAScripts(unittest.TestCase):
    """Tests for CA script modules."""

    def test_process_ca_imports(self):
        ca_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CA")
        sys.path.insert(0, ca_dir)
        import process_ca
        self.assertTrue(hasattr(process_ca, "main"))
        self.assertTrue(hasattr(process_ca, "create_database"))
        self.assertTrue(hasattr(process_ca, "parse_ca_date"))

    def test_parse_ca_date(self):
        ca_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CA")
        sys.path.insert(0, ca_dir)
        import process_ca
        self.assertEqual(process_ca.parse_ca_date("6/15/2024 12:00:00 AM"), "2024-06-15")
        self.assertEqual(process_ca.parse_ca_date("12/1/2023"), "2023-12-01")
        self.assertIsNone(process_ca.parse_ca_date(""))
        self.assertIsNone(process_ca.parse_ca_date(None))

    def test_ca_percentile_imports(self):
        ca_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CA")
        sys.path.insert(0, ca_dir)
        import build_ca_percentile_tables
        self.assertTrue(hasattr(build_ca_percentile_tables, "build_ca_donor_totals_by_year"))
        self.assertTrue(hasattr(build_ca_percentile_tables, "build_ca_percentile_thresholds"))

    def test_ca_recipient_lookup_imports(self):
        ca_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CA")
        sys.path.insert(0, ca_dir)
        import build_ca_recipient_lookup
        self.assertTrue(hasattr(build_ca_recipient_lookup, "build_ca_recipient_lookup"))


if __name__ == "__main__":
    unittest.main()
