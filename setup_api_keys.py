#!/usr/bin/env python3
"""
Setup script for API keys configuration
"""

import os
import sys

def setup_news_api():
    """Guide user through News API setup."""
    print("📰 News API Setup")
    print("=" * 40)
    print("1. Go to: https://newsapi.org/")
    print("2. Sign up for a free account")
    print("3. Go to your dashboard and copy your API key")
    print("4. Set it as an environment variable:")
    print()
    
    api_key = input("Enter your News API key (or press Enter to skip): ").strip()
    
    if api_key:
        # Create .env file
        with open('.env', 'w') as f:
            f.write(f"NEWS_API_KEY={api_key}\n")
        print("✅ API key saved to .env file")
        
        # Also set for current session
        os.environ['NEWS_API_KEY'] = api_key
        print("✅ API key set for current session")
    else:
        print("⏭️  Skipping News API setup")
    
    return bool(api_key)

def test_apis():
    """Test the configured APIs."""
    print("\n🧪 Testing APIs...")
    print("=" * 40)
    
    # Test DuckDuckGo (no key needed)
    try:
        from unified_app import search_duckduckgo
        result = search_duckduckgo("test")
        print("✅ DuckDuckGo API: Working")
    except Exception as e:
        print(f"❌ DuckDuckGo API: Error - {e}")
    
    # Test Wikipedia (no key needed)
    try:
        from unified_app import search_wikipedia
        result = search_wikipedia("test")
        print("✅ Wikipedia API: Working")
    except Exception as e:
        print(f"❌ Wikipedia API: Error - {e}")
    
    # Test News API (if configured)
    if os.getenv('NEWS_API_KEY'):
        try:
            from unified_app import search_news
            result = search_news("test")
            print("✅ News API: Working")
        except Exception as e:
            print(f"❌ News API: Error - {e}")
    else:
        print("⏭️  News API: Not configured")

def main():
    print("🔑 API Keys Setup for Person Search")
    print("=" * 50)
    print()
    print("This script will help you configure API keys for enhanced search results.")
    print("Note: DuckDuckGo and Wikipedia APIs work without keys!")
    print()
    
    # Setup News API
    news_configured = setup_news_api()
    
    # Test all APIs
    test_apis()
    
    print("\n🎉 Setup Complete!")
    print("=" * 40)
    print("You can now run: python unified_app.py")
    print("Then visit: http://localhost:5000/personsearch")
    
    if news_configured:
        print("\n💡 Tip: To make the News API key permanent, add this to your shell profile:")
        print("   echo 'export NEWS_API_KEY=\"your_key_here\"' >> ~/.bashrc")
        print("   source ~/.bashrc")

if __name__ == "__main__":
    main()
