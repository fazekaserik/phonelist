#!/usr/bin/env python3
"""
Hungarian Lead Scraper
Scrapes business leads from Hungarian directories.

Supported sources:
  - joszaki.hu  (service provider directory, SSR React, works)
  - jofogas.hu  (classifieds, services category)
  - ceginfo.hu  (Hungarian company registry / directory)

Usage:
    python scraper.py "villanyszerelő budapest"
    python scraper.py "vízszerelő budapest" --source joszaki
    python scraper.py "festő budapest" --source jofogas --pages 3
    python scraper.py "klímaszerelő budapest" --source all
"""

import argparse
import csv
import os
import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import quote

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
TIMEOUT = 20

RAW_LEADS_DIR = os.path.join(os.path.dirname(__file__), "..", "raw_leads")

# Map from search query keyword → joszaki.hu URL category slug
JOSZAKI_CATEGORY_MAP = {
    "villanyszerelő": "villanyszerelo",
    "villanyszerelo": "villanyszerelo",
    "vízszerelő": "vizszerelo",
    "vizszerelo": "vizszerelo",
    "vízvezetékszerelő": "vizszerelo",
    "festő": "szobafesto-tapetazo",
    "tapétázó": "szobafesto-tapetazo",
    "szobafestő": "szobafesto-tapetazo",
    "kőműves": "komuves",
    "komuves": "komuves",
    "burkoló": "komuves",
    "klímaszerelő": "klimaszerelo",
    "klimaszerelo": "klimaszerelo",
    "autószerelő": "autoszerelo",
    "autoszerelo": "autoszerelo",
    "takarítás": "takaritas",
    "takaritas": "takaritas",
    "takarító": "takaritas",
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert text to safe filename slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "_", text)


def name_from_slug(slug: str) -> str:
    """Convert URL slug to display name. 'torma-tibor' → 'Torma Tibor'."""
    return " ".join(part.capitalize() for part in slug.split("-"))


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


def resolve_joszaki_category(query: str) -> str | None:
    """
    Given a query like 'villanyszerelő budapest', return the joszaki.hu
    URL category slug, or None if not mappable.
    """
    query_lower = query.lower()
    for keyword, slug in JOSZAKI_CATEGORY_MAP.items():
        if keyword in query_lower:
            return slug
    return None


# ---------------------------------------------------------------------------
# Scraper: joszaki.hu
# ---------------------------------------------------------------------------

def scrape_joszaki(query: str, pages: int = 5) -> list[dict]:
    """
    Scrape joszaki.hu/szakemberek/CATEGORY/budapest.

    The site is React-rendered but the full HTML is server-side rendered
    (body ~880KB), so plain requests + BeautifulSoup works.

    Each professional card contains a link:
        <a href="/szakember/NAME-SLUG">Bővebben</a>

    Phone shown is a joszaki proxy (+36 1 443 3777/XXXXX) — stored in notes.
    Real website links (external) are extracted from the card.
    """
    leads = []
    session = requests.Session()

    category_slug = resolve_joszaki_category(query)
    if not category_slug:
        print(f"  [joszaki] No category mapping for query: '{query}'. Skipping.")
        print(f"  [joszaki] Available keywords: {', '.join(JOSZAKI_CATEGORY_MAP.keys())}")
        return []

    industry_label = query.split()[0]  # first word as industry label

    for page in range(1, pages + 1):
        url = f"https://joszaki.hu/szakemberek/{category_slug}/budapest?page={page}"
        print(f"  [joszaki] Fetching page {page}: {url}")
        soup = get_page(url, session)

        if soup is None:
            break

        # Find all "Bővebben" links pointing to /szakember/SLUG
        bovebben_links = soup.find_all(
            "a",
            href=re.compile(r"^/szakember/[^/]+$"),
        )

        if not bovebben_links:
            print(f"  [joszaki] No professional cards found on page {page}. Stopping.")
            break

        page_count = 0
        for link in bovebben_links:
            href = link.get("href", "")
            slug = href.replace("/szakember/", "").strip()
            if not slug:
                continue

            name = name_from_slug(slug)
            profile_url = f"https://joszaki.hu{href}"

            # Walk up to the card container to extract sibling data
            card = link.find_parent(
                lambda tag: tag.name in ("div", "article", "li", "section")
                and len(tag.find_all("a")) >= 1
            )

            phone = ""
            website = ""
            city = "Budapest"  # default — we're always scraping /budapest
            google_reviews = ""
            card_text = ""

            if card:
                card_text = card.get_text(" ", strip=True)

                # Phone: joszaki shows proxy number in tel: links
                tel_link = card.find("a", href=re.compile(r"^tel:"))
                if tel_link:
                    raw = tel_link.get("href", "").replace("tel:", "")
                    phone = clean_phone(raw)

                # Website: any external link that isn't joszaki itself
                for a in card.find_all("a", href=True):
                    href_val = a["href"]
                    if (
                        href_val.startswith("http")
                        and "joszaki.hu" not in href_val
                        and "facebook.com" not in href_val
                    ):
                        website = href_val
                        break

                # City: look for Budapest kerület patterns (e.g. "XIII. kerület")
                city_match = re.search(
                    r"(\bBudapest\b.*?kerület|[IVXLC]+\.\s*kerület|\d+\.\s*kerület)",
                    card_text,
                )
                if city_match:
                    city = city_match.group(0).strip()

                # Reviews count: look for patterns like "12 értékelés" or "12 vélemény"
                review_match = re.search(r"(\d+)\s*(értékelés|vélemény|csillag)", card_text)
                if review_match:
                    google_reviews = review_match.group(1)

            notes = ""
            if phone:
                notes = "joszaki proxy szám"

            leads.append({
                "name": name,
                "phone": phone,
                "city": city,
                "website": website,
                "email": "",
                "industry": industry_label,
                "source": "joszaki.hu",
                "google_reviews": google_reviews,
                "notes": f"{notes} | profil: {profile_url}".strip(" |"),
            })
            page_count += 1

        print(f"  [joszaki] Page {page}: {page_count} professionals found (total: {len(leads)})")

        # If fewer results than expected, probably last page
        if page_count < 10:
            print(f"  [joszaki] Fewer than 10 results on page {page} — likely last page.")
            break

        time.sleep(REQUEST_DELAY)

    return leads


# ---------------------------------------------------------------------------
# Scraper: jofogas.hu (services category)
# ---------------------------------------------------------------------------

def scrape_jofogas(query: str, pages: int = 3) -> list[dict]:
    """
    Scrape jofogás.hu classifieds in the services (szolgaltatasok) category.
    URL: https://www.jofogas.hu/magyarorszag/szolgaltatasok?q=QUERY&o=PAGE
    """
    leads = []
    session = requests.Session()

    industry_label = query.split()[0]
    encoded_query = quote(query)

    for page in range(1, pages + 1):
        url = f"https://www.jofogas.hu/magyarorszag/szolgaltatasok?q={encoded_query}&o={page}"
        print(f"  [jofogas] Fetching page {page}: {url}")
        soup = get_page(url, session)

        if soup is None:
            break

        # Jófogás listing cards
        listings = soup.select(
            "article.listing-card, "
            "div[class*='listing-card'], "
            "li[class*='listing'], "
            "div[class*='ad-card']"
        )

        if not listings:
            # Broader fallback
            listings = soup.select("[data-testid*='listing'], [class*='result-item']")

        if not listings:
            print(f"  [jofogas] No listings on page {page}, stopping.")
            break

        page_count = 0
        for item in listings:
            name = ""
            phone = ""
            city = ""
            website = ""

            # Ad title as name
            name_el = item.select_one(
                "h2, h3, [class*='title'], a[class*='title'], [data-testid*='title']"
            )
            if name_el:
                name = name_el.get_text(strip=True)

            phone_el = item.select_one("a[href^='tel:']")
            if phone_el:
                raw = phone_el.get("href", "").replace("tel:", "")
                phone = clean_phone(raw)

            city_el = item.select_one(
                "[class*='location'], [class*='city'], [data-testid*='location']"
            )
            if city_el:
                city = city_el.get_text(strip=True)

            if name:
                leads.append({
                    "name": name,
                    "phone": phone,
                    "city": city,
                    "website": website,
                    "email": "",
                    "industry": industry_label,
                    "source": "jofogas.hu",
                    "google_reviews": "",
                    "notes": "",
                })
                page_count += 1

        print(f"  [jofogas] Page {page}: {page_count} listings (total: {len(leads)})")
        time.sleep(REQUEST_DELAY)

    return leads


# ---------------------------------------------------------------------------
# Scraper: ceginfo.hu
# ---------------------------------------------------------------------------

def scrape_ceginfo(query: str, pages: int = 3) -> list[dict]:
    """
    Scrape ceginfo.hu Hungarian company directory.
    URL: https://www.ceginfo.hu/search?q=QUERY&page=N
    Focuses on entries that have a phone number.
    """
    leads = []
    session = requests.Session()

    industry_label = query.split()[0]
    encoded_query = quote(query)

    for page in range(1, pages + 1):
        url = f"https://www.ceginfo.hu/search?q={encoded_query}&page={page}"
        print(f"  [ceginfo] Fetching page {page}: {url}")
        soup = get_page(url, session)

        if soup is None:
            break

        # ceginfo company listing rows/cards
        listings = soup.select(
            "div.company-result, "
            "div[class*='company'], "
            "article[class*='company'], "
            "tr[class*='company'], "
            "li[class*='company'], "
            "div.result-item"
        )

        if not listings:
            listings = soup.select("table tr:not(:first-child), .search-result")

        if not listings:
            print(f"  [ceginfo] No listings on page {page}, stopping.")
            break

        page_count = 0
        for item in listings:
            name = ""
            phone = ""
            city = ""
            website = ""
            email = ""

            name_el = item.select_one(
                "h2, h3, .company-name, .ceg-nev, [class*='name'], a[href*='/ceg/']"
            )
            if name_el:
                name = name_el.get_text(strip=True)

            phone_el = item.select_one("a[href^='tel:'], [class*='phone'], [class*='tel']")
            if phone_el:
                raw = phone_el.get("href", "") or phone_el.get_text(strip=True)
                phone = clean_phone(raw.replace("tel:", ""))

            email_el = item.select_one("a[href^='mailto:'], [class*='email']")
            if email_el:
                email = email_el.get("href", "").replace("mailto:", "") or email_el.get_text(strip=True)

            city_el = item.select_one("[class*='city'], [class*='location'], [class*='telepules']")
            if city_el:
                city = city_el.get_text(strip=True)

            web_el = item.select_one("a[href^='http']:not([href*='ceginfo'])")
            if web_el:
                website = web_el.get("href", "")

            # Only save if we have a name and at least a phone or email
            if name and (phone or email):
                leads.append({
                    "name": name,
                    "phone": phone,
                    "city": city,
                    "website": website,
                    "email": email,
                    "industry": industry_label,
                    "source": "ceginfo.hu",
                    "google_reviews": "",
                    "notes": "",
                })
                page_count += 1

        print(f"  [ceginfo] Page {page}: {page_count} companies with contact (total: {len(leads)})")
        time.sleep(REQUEST_DELAY)

    return leads


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "name", "phone", "city", "website", "email",
    "industry", "source", "google_reviews", "notes",
]


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
            row = {field: lead.get(field, "") for field in FIELDNAMES}
            writer.writerow(row)

    print(f"\nSaved {len(leads)} leads to: {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Main scrape orchestrator
# ---------------------------------------------------------------------------

SOURCES = ["joszaki", "jofogas", "ceginfo", "all"]


def scrape(query: str, source: str = "all", pages: int = 3) -> list[dict]:
    """Run scraper(s) for a query. Returns deduplicated lead list."""
    all_leads: list[dict] = []

    if source == "joszaki":
        all_leads = scrape_joszaki(query, pages)
    elif source == "jofogas":
        all_leads = scrape_jofogas(query, pages)
    elif source == "ceginfo":
        all_leads = scrape_ceginfo(query, pages)
    else:
        # all sources
        print(f"\n--- joszaki.hu: {query} ---")
        all_leads += scrape_joszaki(query, pages)
        time.sleep(REQUEST_DELAY)

        print(f"\n--- jofogas.hu: {query} ---")
        all_leads += scrape_jofogas(query, pages)
        time.sleep(REQUEST_DELAY)

        print(f"\n--- ceginfo.hu: {query} ---")
        all_leads += scrape_ceginfo(query, pages)

    # Deduplicate: by phone first, then by name
    seen = set()
    unique_leads = []
    for lead in all_leads:
        phone = lead.get("phone", "").strip()
        name = lead.get("name", "").strip().lower()
        key = phone if phone else name
        if key and key not in seen:
            seen.add(key)
            unique_leads.append(lead)

    removed = len(all_leads) - len(unique_leads)
    if removed:
        print(f"\nRemoved {removed} duplicate entries.")

    return unique_leads


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape Hungarian business directories for leads"
    )
    parser.add_argument("query", help='Search query, e.g. "villanyszerelő budapest"')
    parser.add_argument(
        "--source",
        choices=SOURCES,
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
        print(
            "No leads found.\n"
            "Possible reasons:\n"
            "  - Site HTML structure changed (update selectors)\n"
            "  - No joszaki category mapping for this query (check JOSZAKI_CATEGORY_MAP)\n"
            "  - Network/rate-limit issue"
        )
