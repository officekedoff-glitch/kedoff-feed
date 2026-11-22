#!/usr/bin/env python3
"""
Kedoff.net -> OpenAI Ads product feed (Ukrainian), CSV.

1. Reads the product list from https://kedoff.net/yml.xml
2. For each offer opens the Ukrainian product page (/ua/product/...) to get
   the Ukrainian title, canonical UA URL and large image (og:image)
3. Writes docs/openai_ua.csv in the OpenAI Ads product feed format

Runs daily via GitHub Actions; the CSV is served by GitHub Pages at a
stable HTTPS URL that is connected in OpenAI Ads Manager as a hosted feed.
"""
import csv
import html
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

YML_URL = "https://kedoff.net/yml.xml"
OUT = os.path.join(os.path.dirname(__file__), "docs", "openai_ua.csv")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
WORKERS = 6
RETURN_POLICY = "https://kedoff.net/ua/warranty.html"

HEADER = [
    "item_id", "title", "description", "url", "brand", "image_url", "price",
    "availability", "seller_name", "seller_url", "return_policy",
    "target_countries", "store_country", "is_eligible_search",
    "is_eligible_checkout", "is_ads_eligible",
]

session = requests.Session()
session.headers["User-Agent"] = UA


def text(el, tag):
    node = el.find(tag)
    return (node.text or "").strip() if node is not None else ""


def load_offers():
    r = session.get(YML_URL, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    offers = []
    for o in root.iter("offer"):
        if o.get("available", "true") != "true":
            continue
        offers.append({
            "id": o.get("id"),
            "url": text(o, "url"),
            "price": text(o, "price"),
            "currency": text(o, "currencyId") or "UAH",
            "picture": text(o, "picture"),
            "brand": text(o, "vendor"),
            "name_ru": text(o, "name"),
        })
    return offers


H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
OG_IMG = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I)
TAGS = re.compile(r"<[^>]+>")


def enrich(o):
    ua_url = o["url"].replace("kedoff.net/product/", "kedoff.net/ua/product/")
    for attempt in range(3):
        try:
            r = session.get(ua_url, timeout=30, allow_redirects=True)
            if r.status_code == 404:
                return None  # product page gone -> skip
            r.raise_for_status()
            page = r.text
            m = H1.search(page)
            title = html.unescape(TAGS.sub("", m.group(1))).strip() if m else ""
            title = re.sub(r"\s+", " ", title)
            img = OG_IMG.search(page)
            final_url = r.url.split("?")[0]
            if "/ua/" not in final_url or not title:
                return None
            return {
                **o,
                "title": title[:150],
                "ua_url": final_url,
                "image": img.group(1) if img else o["picture"],
            }
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


def main():
    offers = load_offers()
    print(f"offers in yml: {len(offers)}")
    with ThreadPoolExecutor(WORKERS) as ex:
        rows = [r for r in ex.map(enrich, offers) if r]
    print(f"ready rows: {len(rows)}")

    # Safety: don't overwrite a good feed with a broken/partial one
    if len(rows) < max(50, int(len(offers) * 0.7)):
        print("too few rows, keeping previous feed", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(HEADER)
        for r in rows:
            desc = (f"{r['title']}. Оригінал від бренду {r['brand']}. "
                    "Доставка Новою поштою по всій Україні, обмін і повернення "
                    "14 днів. Інтернет-магазин Kedoff.net з 2010 року.")
            price = f"{float(r['price']):.2f} {r['currency']}"
            w.writerow([
                r["id"], r["title"], desc, r["ua_url"], r["brand"], r["image"],
                price, "in_stock", "Kedoff.net", "https://kedoff.net/ua/",
                RETURN_POLICY, "UA", "UA", "true", "false", "true",
            ])
    os.replace(tmp, OUT)
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
