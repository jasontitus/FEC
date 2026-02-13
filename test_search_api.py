#!/usr/bin/env python3
"""
Test script for the new API-based search functionality
"""

import requests
import json
import time

def test_search_apis():
    """Test the search APIs directly."""
    test_queries = [
        "John Smith",
        "Joe Biden",
        "Donald Trump",
        "Nancy Pelosi"
    ]
    
    print("🔍 Testing Search APIs...")
    print("=" * 50)
    
    for query in test_queries:
        print(f"\n📝 Testing query: '{query}'")
        print("-" * 30)
        
        # Test DuckDuckGo
        try:
            from unified_app import search_duckduckgo
            ddg_results = search_duckduckgo(query)
            if ddg_results:
                print("✅ DuckDuckGo: Found results")
                if ddg_results.get('abstract'):
                    print(f"   Abstract: {ddg_results['abstract'][:100]}...")
                if ddg_results.get('definition'):
                    print(f"   Definition: {ddg_results['definition'][:100]}...")
            else:
                print("❌ DuckDuckGo: No results")
        except Exception as e:
            print(f"❌ DuckDuckGo: Error - {e}")
        
        # Test Wikipedia
        try:
            from unified_app import search_wikipedia
            wiki_results = search_wikipedia(query)
            if wiki_results:
                print("✅ Wikipedia: Found results")
                print(f"   Title: {wiki_results.get('title', 'N/A')}")
                if wiki_results.get('extract'):
                    print(f"   Extract: {wiki_results['extract'][:100]}...")
            else:
                print("❌ Wikipedia: No results")
        except Exception as e:
            print(f"❌ Wikipedia: Error - {e}")
        
        time.sleep(1)  # Be nice to the APIs

def test_flask_api():
    """Test the Flask API endpoint."""
    print("\n🌐 Testing Flask API endpoint...")
    print("=" * 50)
    
    # This would require the Flask app to be running
    # For now, just show how to test it
    print("To test the Flask API endpoint:")
    print("1. Start the Flask app: python unified_app.py")
    print("2. Test with: curl 'http://localhost:5000/api/search?q=John%20Smith'")
    print("3. Or visit: http://localhost:5000/api/search?q=John%20Smith")

if __name__ == "__main__":
    test_search_apis()
    test_flask_api()
