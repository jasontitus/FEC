# 🔑 API Keys Setup Guide

This guide explains how to set up API keys for the enhanced person search functionality.

## 🚀 Quick Start (No API Keys Required!)

The person search works great out of the box with **free APIs**:
- ✅ **DuckDuckGo API** - Instant answers, definitions, related topics
- ✅ **Wikipedia API** - Encyclopedia information

**Just run:**
```bash
pip install -r requirements.txt
python unified_app.py
```

## 📰 Optional: News API (Enhanced Results)

For news articles about people, you can add the News API:

### 1. Get a Free API Key
1. Go to [NewsAPI.org](https://newsapi.org/)
2. Sign up for a free account
3. Copy your API key from the dashboard

### 2. Configure the API Key

**Option A: Use the setup script (Recommended)**
```bash
python setup_api_keys.py
```

**Option B: Manual setup**
```bash
# Create .env file
echo "NEWS_API_KEY=your_api_key_here" > .env

# Or set environment variable
export NEWS_API_KEY="your_api_key_here"
```

### 3. News API Limits
- **Free tier**: 1,000 requests per day
- **Rate limit**: 1 request per second
- **Perfect for**: Personal use and testing

## 🧪 Testing Your Setup

Test all APIs:
```bash
python test_search_api.py
```

Test the Flask API endpoint:
```bash
# Start the app
python unified_app.py

# Test in another terminal
curl "http://localhost:5000/api/search?q=John%20Smith"
```

## 🔧 Troubleshooting

### "No results found"
- This is normal for some queries
- The system gracefully handles API failures
- Manual search links are always provided as fallback

### "API key not working"
- Check your API key is correct
- Verify you haven't exceeded rate limits
- News API free tier has daily limits

### "Connection errors"
- Check your internet connection
- Some APIs may be temporarily unavailable
- The system will continue working with available APIs

## 📊 What Each API Provides

| API | Free | Provides | Rate Limits |
|-----|------|----------|-------------|
| DuckDuckGo | ✅ | Instant answers, definitions, related topics | None |
| Wikipedia | ✅ | Encyclopedia articles, summaries | None |
| News API | ✅* | Recent news articles | 1,000/day |

*Requires free account

## 🎯 Best Practices

1. **Start without API keys** - The system works great with just free APIs
2. **Add News API later** - Only if you want news articles
3. **Monitor usage** - News API has daily limits
4. **Use fallback links** - Manual search links always available

## 🆘 Need Help?

- Check the test script: `python test_search_api.py`
- Use the setup script: `python setup_api_keys.py`
- Test individual APIs in the Flask app: `/api/search?q=test`
