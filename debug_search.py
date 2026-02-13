#!/usr/bin/env python3
"""
Debug script to test the search APIs directly
"""

import sys
import os

# Add the current directory to Python path so we can import unified_app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_search_apis():
    """Test the search APIs with a simple query."""
    try:
        from unified_app import search_duckduckgo, search_wikipedia, perform_comprehensive_search
        
        print("🧪 Testing Search APIs")
        print("=" * 50)
        
        # Test with a simple query
        test_query = "Joe Biden"
        
        print(f"\n🔍 Testing with query: '{test_query}'")
        print("-" * 30)
        
        # Test DuckDuckGo
        print("\n🦆 Testing DuckDuckGo...")
        ddg_result = search_duckduckgo(test_query)
        print(f"DuckDuckGo result: {ddg_result}")
        
        # Test Wikipedia
        print("\n📚 Testing Wikipedia...")
        wiki_result = search_wikipedia(test_query)
        print(f"Wikipedia result: {wiki_result}")
        
        # Test comprehensive search
        print("\n🔍 Testing comprehensive search...")
        search_queries = {"general": test_query}
        comprehensive_result = perform_comprehensive_search(search_queries)
        print(f"Comprehensive result: {comprehensive_result}")
        
        # Check if we got any results
        if comprehensive_result:
            print("\n✅ SUCCESS: API search is working!")
            for search_name, search_data in comprehensive_result.items():
                print(f"  - {search_name}: {len(search_data.get('results', {}))} sources")
        else:
            print("\n❌ ISSUE: No API results returned")
            
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you're running this from the same directory as unified_app.py")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_search_apis()

