#!/usr/bin/env python3
"""
Test runner for FEC Campaign Finance project.
Discovers and runs all tests in the tests/ directory.

Usage:
    python3 run_tests.py              # Run all tests
    python3 run_tests.py -v           # Verbose output
    python3 run_tests.py -k search    # Run tests matching 'search'
"""

import os
import sys
import unittest

# Ensure project root is on path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "CA"))


def main():
    # Parse simple args
    verbosity = 2 if "-v" in sys.argv or "--verbose" in sys.argv else 1
    pattern_filter = None
    for i, arg in enumerate(sys.argv):
        if arg == "-k" and i + 1 < len(sys.argv):
            pattern_filter = sys.argv[i + 1]

    # Discover tests
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=os.path.join(PROJECT_DIR, "tests"),
        pattern="test_*.py",
        top_level_dir=PROJECT_DIR,
    )

    # Filter by keyword if specified
    if pattern_filter:
        filtered = unittest.TestSuite()
        for test_group in suite:
            for test_case in test_group:
                if hasattr(test_case, "__iter__"):
                    for test in test_case:
                        if pattern_filter.lower() in str(test).lower():
                            filtered.addTest(test)
                elif pattern_filter.lower() in str(test_case).lower():
                    filtered.addTest(test_case)
        suite = filtered

    # Run tests
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
