"""
Temp script to get top 100 articles from newsapi.org or google news rss and save them to a CSV file.
"""

import argparse
import asyncio
from selectolax.parser import HTMLParser
import json
import os
import sys

import requests

class newsAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://newsapi.org/v2/top-headlines"

        # https://newsapi.org/v2/top-headlines?country=us&apiKey=1f74d91f84f04dd196eed3fa4a1075f9

    def fetch_top_articles(self, country="us", page_size=10):
        import requests

        params = {
            "apiKey": self.api_key,
            "country": country,
            "pageSize": page_size
        }

        response = requests.get(self.base_url, params=params)
        print(response.url, response.status_code, response.text)
        if response.status_code == 200:
            data = response.json()
            return data.get("articles", [])
        else:
            print(f"Error fetching articles: {response.status_code}")
            return []

    def save_to_csv(self, articles, filename="top_articles.csv"):
        import csv

        with open(filename, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(["Title", "Description", "URL", "Published At"])
            for article in articles:
                writer.writerow([
                    article.get("title"),
                    article.get("description"),
                    article.get("url"),
                    article.get("publishedAt")
                ])
        print(f"Saved {len(articles)} articles to {filename}")

class googleNewsRSS:
    def __init__(self, country="us", language="en"):
        self.base_url = "https://news.google.com/rss"
        self.country = country
        self.language_base = language
        self.timeout = 10
        
    def fetch_top_articles(self, country="us", source:str = None):
        import feedparser

        if source:
            site_chunk = 'site:' + source
            encode_site_chunk = requests.utils.quote(site_chunk)
            url = f"{self.base_url}?q={encode_site_chunk}&hl={country}&gl={country}&ceid={country}:{self.language_base}"
        else:
            url = f"{self.base_url}?hl={country}&gl={country}&ceid={country}:{self.language_base}"
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries:
            articles.append({
                "title": entry.title,
                "description": entry.summary,
                "url": entry.link,
                "publishedAt": entry.published
            })
        return articles
    
    def save_to_csv(self, articles, filename="top_articles.csv"):
        import csv

        with open(filename, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(["Title", "Description", "URL", "Resolved URL", "Published At"])
            for article in articles:
                writer.writerow([
                    article.get("title"),
                    article.get("description"),
                    article.get("url"),
                    article.get("resolved_url"),
                    article.get("publishedAt")
                ])
        print(f"Saved {len(articles)} articles to {filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch top articles from NewsAPI and save to CSV.")
    parser.add_argument("--country", type=str, default="gb", help="Country code for top headlines (default: us)")
    parser.add_argument("--page_size", type=int, default=10, help="Number of articles to fetch (default: 10)")
    parser.add_argument("--output", type=str, default="top_articles.csv", help="Output CSV file name (default: top_articles.csv)")
    args = parser.parse_args()

    from app.config import get_settings
    from app.logging_config import configure_logging
    from app.render.browser import RenderPool
    from app.render.collect import render_page_sampled

    api_key = os.getenv("AI_NEWSAPI_KEY")
    api_key = "1f74d91f84f04dd196eed3fa4a1075f9"
    if not api_key:
        print("Error: AI_NEWSAPI_KEY environment variable is not set.")
        sys.exit(1)

    news_sources = [
                    # {"rank": 1, "name": "BBC", "domain": "bbc.co.uk", "audience_millions": 42.3},
                    {"rank": 2, "name": "The Sun", "domain": "thesun.co.uk", "audience_millions": 22.73},
                    {"rank": 3, "name": "The Guardian", "domain": "theguardian.com", "audience_millions": 22.68},
                    {"rank": 4, "name": "Mail Online / Daily Mail", "domain": "dailymail.co.uk", "audience_millions": 21.77},
                    {"rank": 5, "name": "The Independent", "domain": "independent.co.uk", "audience_millions": 21.23},
                    {"rank": 6, "name": "Mirror", "domain": "mirror.co.uk", "audience_millions": 19.9},
                    {"rank": 7, "name": "Sky News", "domain": "news.sky.com", "audience_millions": 19.5},
                    {"rank": 8, "name": "Daily Express", "domain": "express.co.uk", "audience_millions": 18.11},
                    {"rank": 9, "name": "The Telegraph", "domain": "telegraph.co.uk", "audience_millions": 18.18},
                    {"rank": 10, "name": "Yahoo News UK", "domain": "uk.news.yahoo.com", "audience_millions": 17.3},

                    {"rank": 11, "name": "Metro", "domain": "metro.co.uk", "audience_millions": 15.0},
                    {"rank": 12, "name": "MoneySavingExpert", "domain": "moneysavingexpert.com", "audience_millions": 15.0},
                    {"rank": 13, "name": "ITV News", "domain": "itv.com/news", "audience_millions": 14.0},
                    {"rank": 14, "name": "Evening Standard", "domain": "standard.co.uk", "audience_millions": 14.0},
                    {"rank": 15, "name": "Daily Record", "domain": "dailyrecord.co.uk", "audience_millions": 7.5},
                    {"rank": 16, "name": "Daily Star", "domain": "dailystar.co.uk", "audience_millions": 8.0},
                    {"rank": 17, "name": "Liverpool Echo", "domain": "liverpoolecho.co.uk", "audience_millions": 7.8},
                    {"rank": 18, "name": "Wales Online", "domain": "walesonline.co.uk", "audience_millions": 7.9},
                    {"rank": 19, "name": "Manchester Evening News", "domain": "manchestereveningnews.co.uk", "audience_millions": 10.0},
                    {"rank": 20, "name": "GB News", "domain": "gbnews.com", "audience_millions": 6.3},

                    {"rank": 21, "name": "Examiner Live", "domain": "examinerlive.co.uk", "audience_millions": 3.8},
                    {"rank": 22, "name": "MyLondon", "domain": "mylondon.news", "audience_millions": 3.3},
                    {"rank": 23, "name": "Nottinghamshire Live", "domain": "nottinghamshirelive.co.uk", "audience_millions": 4.6},
                    {"rank": 24, "name": "Surrey Live", "domain": "surreylive.co.uk", "audience_millions": 5.0},
                    {"rank": 25, "name": "Devon Live", "domain": "devonlive.com", "audience_millions": 5.3}
    ]


    news_api = googleNewsRSS(country=args.country, language="en")
    total_articles = []
    for source in news_sources:
        print(f"Fetching top articles from {source['name']} ({source['domain']})...")
        articles = news_api.fetch_top_articles(country=args.country, source=source["domain"])
        total_articles.extend(articles)
    
    cleaned_articles = []

    # Use RenderPool to render each article URL and extract the cleaned URL
    async def render_and_clean_urls():
        pool = RenderPool(concurrency=12, headless=True)
        await pool.start()
        try:
            async def render_one(article):
                url = article.get("url")
                print(f"Rendering URL: {url}")
                if url:
                    result = await render_page_sampled(pool, url, accept_consent=True, dwell_ms=1000)
                    article["resolved_url"] = result.get("final_url", url)
                else:
                    article["resolved_url"] = None
                cleaned_articles.append(article)

            await asyncio.gather(*[render_one(article) for article in total_articles]) # Limit to first 10 articles for testing
        finally:
            await pool.stop()

    asyncio.run(render_and_clean_urls())
    
    news_api.save_to_csv(cleaned_articles, filename=args.output)