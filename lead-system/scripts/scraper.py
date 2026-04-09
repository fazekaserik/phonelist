#!/usr/bin/env python3
"""
Hungarian Lead Scraper
Scrapes business leads from Hungarian directories.

Supported sources:
  - firmania.hu
  - arany-oldalak.hu (Arany Oldalak)
  - jofoglas.hu (Jófogás)

Usage:
    python scraper.py "villanyszerelő budapest"
    python scraper.py "vízszerelő budapest" --source firmania
    python scraper.py "festő budapest" --source jofogas --pages 3
"""

import argparse
import csv
import os
import re
import time
import unicodedata
from datetime import datetime

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_DELAY = 2.5  # seconds between requests
TIMEOUT = 15

RAW_LEADS_DIR = os.path.join(os.path.dirname(__file__), "..", "raw_leads")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert text to safe filename slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "_", text)


def clean_phone(phone: str) -> str:
    """Normalize Hungarian phone numbers."""
    phone = re.sub(r"[^\d+]", "", phone)
    if phone.startswith("06"):
        phone = "+36" + phone[2:]
    elif phone.startswith("36") and not phone.startswith("+"):
        phone = "+" + phone
    return phone


def get_page(url: str, session: requests.Session) -> BeautifulSoup | None:
    """Fetch a page and return BeautifulSoup, or None on failure."""
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Scraper: firmania.hu
# ---------------------------------------------------------------------------

def scrape_firmania(query: str, pages: int = 3) -> list[dict]:
    """Scrape firmania.hu for the given query."""
    leads = []
    session = requests.Session()

    # firmania uses URL-encoded query in path
    slug = query.replace(" ", "+")

    for page in range(1, pages + 1):
        url = f"https://firmania.hu/search?q={slug}&page={page}"
        print(f"  [firmania] Fetching page {page}: {url}")
        soup = get_page(url, session)

        if soup is None:
            break

        # Each business listing is in a card/article element
        listings = soup.select("div.company-item, article.listing, div.result-item, li.company")

        if not listings:
            # Try broader selector
            listings = soup.select("[class*='company'], [class*='listing'], [class*='result']")

        if not listings:
            print(f"  [firmania] No listings found on page {page}, stopping.")
            break

        for item in listings:
            name = ""
            phone = ""
            city = ""
            website = ""
            category = query.split()[0] if query else ""

            # Name
            name_el = item.select_one("h2, h3, .company-name, .name, a[href*='/ceg/']")
            if name_el:
                name = name_el.get_text(strip=True)

            # Phone
            phone_el = item.select_one("a[href^='tel:'], .phone, [class*='phone'], [class*='tel']")
            if phone_el:
                raw = phone_el.get("href", "") or phone_el.get_text(strip=True)
                phone = clean_phone(raw.replace("tel:", ""))

            # City
            city_el = item.select_one(".city, .location, .address, [class*='city'], [class*='location']")
            if city_el:
                city = city_el.get_text(strip=True)

            # Website
            website_el = item.select_one("a[href^='http']:not([href*='firmania'])")
            if website_el:
                website = website_el.get("href", "")

            if name:
                leads.append({
                    "name": name,
                    "phone": phone,
                    "city": city,
                    "website": website,
                    "industry": category,
                    "source": "firmania.hu",
                    "notes": "",
                })

        print(f"  [firmania] Page {page}: found {len(listings)} listings (total so far: {len(leads)})")
        time.sleep(REQUEST_DELAY)

    return leads


# ---------------------------------------------------------------------------
# Scraper: arany-oldalak.hu
# ---------------------------------------------------------------------------

def scrape_arany_oldalak(query: str, pages: int = 3) -> list[dict]:
    """Scrape arany-oldalak.hu (Hungarian Yellow Pages)."""
    leads = []
    session = requests.Session()

    parts = query.split()
    category = parts[0] if parts else query
    city = parts[1] if len(parts) > 1 else "budapest"

    slug_cat = category.replace(" ", "-")
    slug_city = city.replace(" ", "-")

    for page in range(1, pages + 1):
        url = f"https://www.arany-oldalak.hu/{slug_cat}/{slug_city}/{page}"
        print(f"  [arany-oldalak] Fetching page {page}: {url}")
        soup = get_page(url, session)

        if soup is None:
            break

        listings = soup.select("div.company, div.ceg, article, li.result, div[class*='company']")

        if not listings:
            print(f"  [arany-oldalak] No listings on page {page}, stopping.")
            break

        for item in listings:
            name = ""
            phone = ""
            city_val = city
            website = ""

            name_el = item.select_one("h2, h3, .ceg-nev, .company-name, a")
            if name_el:
                name = name_el.get_text(strip=True)

            phone_el = item.select_one("a[href^='tel:'], .telefon, .phone")
            if phone_el:
                raw = phone_el.get("href", "") or phone_el.get_text(strip=True)
                phone = clean_phone(raw.replace("tel:", ""))

            city_el = item.select_one(".city, .telepules, .location")
            if city_el:
                city_val = city_el.get_text(strip=True)

            web_el = item.select_one("a[href^='http']:not([href*='arany-oldalak'])")
            if web_el:
                website = web_el.get("href", "")

            if name:
                leads.append({
                    "name": name,
                    "phone": phone,
                    "city": city_val,
                    "website": website,
                    "industry": category,
                    "source": "arany-oldalak.hu",
                    "notes": "",
                })

        print(f"  [arany-oldalak] Page {page}: {len(listings)} listings (total: {len(leads)})")
        time.sleep(REQUEST_DELAY)

    return leads


# ---------------------------------------------------------------------------
# Scraper: jofoglas.hu
# ---------------------------------------------------------------------------

def scrape_jofogas(query: str, pages: int = 3) -> list[dict]:
    """Scrape jofogás.hu service listings."""
    leads = []
    session = requests.Session()

    slug = query.replace(" ", "+")
    category = query.split()[0] if query else query

    for page in range(1, pages + 1):
        url = f"https://www.jofogas.hu/magyarorszag?q={slug}&o={page}"
        print(f"  [jofogas] Fetching page {page}: {url}")
        soup = get_page(url, session)

        if soup is None:
            break

        listings = soup.select("article.listing-card, div.listing-item, li[class*='listing']")

        if not listings:
            listings = soup.select("[class*='listing'], [class*='card']")

        if not listings:
            print(f"  [jofogas] No listings on page {page}, stopping.")
            break

        for item in listings:
            name = ""
            phone = ""
            city = ""
            website = ""

            # On classifieds, name is usually the ad title
            name_el = item.select_one("h2, h3, .title, [class*='title'], a[class*='title']")
            if name_el:
                name = name_el.get_text(strip=True)

            phone_el = item.select_one("a[href^='tel:'], [class*='phone'], [class*='tel']")
            if phone_el:
                raw = phone_el.get("href", "") or phone_el.get_text(strip=True)
                phone = clean_phone(raw.replace("tel:", ""))

            city_el = item.select_one(".city, .location, [class*='location'], [class*='city']")
            if city_el:
                city = city_el.get_text(strip=True)

            if name:
                leads.append({
                    "name": name,
                    "phone": phone,
                    "city": city,
                    "website": website,
                    "industry": category,
                    "source": "jofogas.hu",
                    "notes": "",
                })

        print(f"  [jofogas] Page {page}: {len(listings)} listings (total: {len(leads)})")
        time.sleep(REQUEST_DELAY)

    return leads


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

FIELDNAMES = ["name", "phone", "city", "website", "email", "industry", "source",
              "google_reviews", "notes"]


def save_leads(leads: list[dict], query: str) -> str:
    os.makedirs(RAW_LEADS_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = slugify(query)
    filename = f"leads_{slug}_{timestamp}.csv"
    filepath = os.path.join(RAW_LEADS_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            # Fill missing fields with empty string
            row = {field: lead.get(field, "") for field in FIELDNAMES}
            writer.writerow(row)

    print(f"\nSaved {len(leads)} leads to: {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

SCRAPERS = {
    "firmania": scrape_firmania,
    "arany": scrape_arany_oldalak,
    "jofogas": scrape_jofogas,
    "all": None,  # handled specially
}


def scrape(query: str, source: str = "all", pages: int = 3) -> list[dict]:
    """Run scraper(s) for a query. Returns combined lead list."""
    all_leads = []

    if source == "firmania":
        all_leads = scrape_firmania(query, pages)
    elif source == "arany":
        all_leads = scrape_arany_oldalak(query, pages)
    elif source == "jofogas":
        all_leads = scrape_jofogas(query, pages)
    else:
        # all sources
        print(f"\n--- Scraping firmania.hu for: {query} ---")
        all_leads += scrape_firmania(query, pages)
        time.sleep(REQUEST_DELAY)

        print(f"\n--- Scraping arany-oldalak.hu for: {query} ---")
        all_leads += scrape_arany_oldalak(query, pages)
        time.sleep(REQUEST_DELAY)

        print(f"\n--- Scraping jofogas.hu for: {query} ---")
        all_leads += scrape_jofogas(query, pages)

    # Deduplicate by phone number
    seen_phones = set()
    unique_leads = []
    for lead in all_leads:
        phone = lead.get("phone", "").strip()
        key = phone if phone else lead.get("name", "")
        if key and key not in seen_phones:
            seen_phones.add(key)
            unique_leads.append(lead)

    removed = len(all_leads) - len(unique_leads)
    if removed:
        print(f"\nRemoved {removed} duplicate entries.")

    return unique_leads


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Hungarian business directories for leads")
    parser.add_argument("query", help='Search query, e.g. "villanyszerelő budapest"')
    parser.add_argument(
        "--source",
        choices=list(SCRAPERS.keys()),
        default="all",
        help="Which source to scrape (default: all)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=3,
        help="Number of pages to scrape per source (default: 3)",
    )
    args = parser.parse_args()

    print(f"Scraping: '{args.query}' | Source: {args.source} | Pages: {args.pages}")
    leads = scrape(args.query, source=args.source, pages=args.pages)
    print(f"\nTotal unique leads collected: {len(leads)}")

    if leads:
        save_leads(leads, args.query)
    else:
        print("No leads found. The site's HTML structure may have changed — check selectors.")
