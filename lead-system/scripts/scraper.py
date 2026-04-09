#!/usr/bin/env python3
"""
Hungarian Lead Scraper
Scrapes business leads from two confirmed-working sources:

  - jofogas.hu   — classifieds, real phone numbers in static HTML
  - ceginfo.hu   — Hungarian company registry with phone + address

Usage:
    python scraper.py "villanyszerelő budapest"
    python scraper.py "vízszerelő budapest" --source ceginfo
    python scraper.py "festő budapest" --source jofogas --pages 5
    python scraper.py "klímaszerelő budapest" --source all --pages 5
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

# Hungarian phone pattern: matches 06XX XXX XXXX, +36XX XXX XXXX, (06XX) XXX-XXXX etc.
HU_PHONE_RE = re.compile(
    r"(?<!\d)"                       # not preceded by digit
    r"(\+36|06)"                     # country/trunk prefix
    r"[\s\-/.]?"
    r"(\d{1,2})"                     # area code
    r"[\s\-/.]?"
    r"(\d{3,4})"                     # first group
    r"[\s\-/.]?"
    r"(\d{3,4})"                     # second group
    r"(?!\d)"                        # not followed by digit
)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert text to safe filename slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "_", text)


def clean_phone(raw: str) -> str:
    """
    Normalize a Hungarian phone number to +36XXXXXXXXX format.
    Returns empty string if input doesn't look like a valid HU phone.
    """
    # Strip everything except digits and leading +
    digits = re.sub(r"[^\d]", "", raw)
    if raw.strip().startswith("+"):
        digits = "+" + digits

    if digits.startswith("+36") and len(digits) >= 11:
        return digits[:12]           # +36 + 9 digits
    if digits.startswith("06") and len(digits) >= 10:
        return "+36" + digits[2:11]
    if digits.startswith("36") and len(digits) >= 11:
        return "+" + digits[:12]

    return ""


def extract_phone_from_text(text: str) -> str:
    """Find first Hungarian phone number in arbitrary text."""
    m = HU_PHONE_RE.search(text)
    if not m:
        return ""
    return clean_phone(m.group(0))


def split_query(query: str) -> tuple[str, str]:
    """
    Split 'villanyszerelő budapest' → ('villanyszerelő', 'budapest').
    If no city word present, city defaults to 'budapest'.
    """
    CITIES = {
        "budapest", "debrecen", "miskolc", "pécs", "győr", "nyíregyháza",
        "kecskemét", "székesfehérvár", "szombathely", "érd",
    }
    parts = query.strip().split()
    city = "budapest"
    industry_parts = []
    for part in parts:
        if part.lower() in CITIES:
            city = part.lower()
        else:
            industry_parts.append(part)
    industry = " ".join(industry_parts) if industry_parts else query
    return industry, city


def get_page(url: str, session: requests.Session) -> BeautifulSoup | None:
    """Fetch a page and return BeautifulSoup, or None on failure."""
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return soup
    except requests.RequestException as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return None


def check_url(url: str) -> bool:
    """Quick connectivity check — prints result, returns True if 200."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        ok = r.status_code == 200
        print(f"  [check] {url} → HTTP {r.status_code} ({'OK' if ok else 'FAIL'})"
              f"  body={len(r.text)} chars")
        return ok
    except Exception as e:
        print(f"  [check] {url} → ERROR: {e}")
        return False


# ---------------------------------------------------------------------------
# Scraper: jofogas.hu
# ---------------------------------------------------------------------------

def scrape_jofogas(query: str, pages: int = 5) -> list[dict]:
    """
    Scrape jofogás.hu classifieds — services category.
    URL: https://www.jofogas.hu/magyarorszag/szolgaltatasok?q=QUERY&o=PAGE

    Phone numbers posted by tradespeople appear directly in the static HTML
    either as tel: links or as plain text in the ad body.
    """
    leads = []
    session = requests.Session()

    industry, city = split_query(query)
    # jofogas: combine both into q, e.g. "villanyszerelő budapest"
    full_query = f"{industry} {city}"
    encoded = quote(full_query)
    industry_label = industry

    for page in range(1, pages + 1):
        url = (
            f"https://www.jofogas.hu/magyarorszag/szolgaltatasok"
            f"?q={encoded}&o={page}"
        )
        print(f"  [jofogas] Page {page}: {url}")
        soup = get_page(url, session)
        if soup is None:
            break

        # jofogas listing cards — try multiple selector patterns
        listings = (
            soup.select("article.listing-card")
            or soup.select("div[class*='listing-card']")
            or soup.select("li[class*='listing']")
            or soup.select("div[class*='ad-card']")
            or soup.select("[data-testid*='listing']")
        )

        if not listings:
            # Debug: show a snippet so selectors can be diagnosed
            body_preview = soup.get_text()[:300].replace("\n", " ")
            print(f"  [jofogas] No listings found. Body preview: {body_preview!r}")
            break

        page_count = 0
        for item in listings:
            item_text = item.get_text(" ", strip=True)

            # --- Name: ad title ---
            name_el = (
                item.select_one("h2")
                or item.select_one("h3")
                or item.select_one("[class*='title']")
                or item.select_one("a[class*='title']")
                or item.select_one("[data-testid*='title']")
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                continue

            # --- Phone: tel: link first, then regex in card text ---
            phone = ""
            tel_el = item.select_one("a[href^='tel:']")
            if tel_el:
                phone = clean_phone(tel_el["href"].replace("tel:", "").strip())
            if not phone:
                phone = extract_phone_from_text(item_text)

            # --- City ---
            city_el = (
                item.select_one("[class*='location']")
                or item.select_one("[class*='city']")
                or item.select_one("[data-testid*='location']")
            )
            city_val = city_el.get_text(strip=True) if city_el else city

            # --- Description snippet ---
            desc_el = item.select_one("[class*='desc'], [class*='body'], p")
            description = desc_el.get_text(strip=True)[:200] if desc_el else ""

            leads.append({
                "name": name,
                "phone": phone,
                "city": city_val,
                "website": "",
                "email": "",
                "industry": industry_label,
                "source": "jofogas.hu",
                "google_reviews": "",
                "notes": description,
            })
            page_count += 1

        print(f"  [jofogas] Page {page}: {page_count} ads (total: {len(leads)})")

        if page_count == 0:
            break

        time.sleep(REQUEST_DELAY)

    return leads


# ---------------------------------------------------------------------------
# Scraper: ceginfo.hu
# ---------------------------------------------------------------------------

def scrape_ceginfo(query: str, pages: int = 5) -> list[dict]:
    """
    Scrape ceginfo.hu Hungarian company registry.
    URL: https://www.ceginfo.hu/kereses?q=INDUSTRY&telepules=CITY&oldal=PAGE

    Returns: company name, phone, address, website.
    Only keeps entries that have at least a phone number.
    """
    leads = []
    session = requests.Session()

    industry, city = split_query(query)
    industry_label = industry

    for page in range(1, pages + 1):
        url = (
            f"https://www.ceginfo.hu/kereses"
            f"?q={quote(industry)}"
            f"&telepules={quote(city)}"
            f"&oldal={page}"
        )
        print(f"  [ceginfo] Page {page}: {url}")
        soup = get_page(url, session)
        if soup is None:
            break

        # ceginfo result rows — try known patterns
        listings = (
            soup.select("div.ceg-lista-elem")
            or soup.select("div[class*='ceg-lista']")
            or soup.select("article[class*='ceg']")
            or soup.select("div.result-item")
            or soup.select("li.result")
            or soup.select("table.results tr:not(:first-child)")
        )

        if not listings:
            body_preview = soup.get_text()[:300].replace("\n", " ")
            print(f"  [ceginfo] No listings found. Body preview: {body_preview!r}")
            break

        page_count = 0
        for item in listings:
            item_text = item.get_text(" ", strip=True)

            # --- Company name ---
            name_el = (
                item.select_one("h2")
                or item.select_one("h3")
                or item.select_one(".ceg-nev")
                or item.select_one("[class*='nev']")
                or item.select_one("[class*='name']")
                or item.select_one("a[href*='/ceg/']")
                or item.select_one("a[href*='/company/']")
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                continue

            # --- Phone ---
            phone = ""
            tel_el = item.select_one("a[href^='tel:']")
            if tel_el:
                phone = clean_phone(tel_el["href"].replace("tel:", "").strip())
            if not phone:
                phone_el = item.select_one(
                    "[class*='telefon'], [class*='phone'], [class*='tel']"
                )
                if phone_el:
                    phone = clean_phone(phone_el.get_text(strip=True))
            if not phone:
                phone = extract_phone_from_text(item_text)

            # Only keep entries with a phone number
            if not phone:
                continue

            # --- Email ---
            email = ""
            mail_el = item.select_one("a[href^='mailto:']")
            if mail_el:
                email = mail_el["href"].replace("mailto:", "").strip()

            # --- Address ---
            address = ""
            addr_el = item.select_one(
                "[class*='cim'], [class*='address'], [class*='telepules']"
            )
            if addr_el:
                address = addr_el.get_text(strip=True)

            # --- Website ---
            website = ""
            for a in item.select("a[href]"):
                href = a["href"]
                if href.startswith("http") and "ceginfo.hu" not in href:
                    website = href
                    break

            leads.append({
                "name": name,
                "phone": phone,
                "city": address or city,
                "website": website,
                "email": email,
                "industry": industry_label,
                "source": "ceginfo.hu",
                "google_reviews": "",
                "notes": address,
            })
            page_count += 1

        print(f"  [ceginfo] Page {page}: {page_count} companies with phone (total: {len(leads)})")

        if page_count == 0:
            break

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
    filename = f"leads_{slugify(query)}_{timestamp}.csv"
    filepath = os.path.join(RAW_LEADS_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            writer.writerow({field: lead.get(field, "") for field in FIELDNAMES})

    print(f"\nSaved {len(leads)} leads → {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Main scrape orchestrator
# ---------------------------------------------------------------------------

SOURCES = ["jofogas", "ceginfo", "all"]


def scrape(query: str, source: str = "all", pages: int = 5) -> list[dict]:
    """Run scraper(s) for a query. Returns deduplicated lead list."""
    all_leads: list[dict] = []

    if source == "jofogas":
        all_leads = scrape_jofogas(query, pages)
    elif source == "ceginfo":
        all_leads = scrape_ceginfo(query, pages)
    else:
        print(f"\n--- jofogas.hu: {query} ---")
        all_leads += scrape_jofogas(query, pages)
        time.sleep(REQUEST_DELAY)

        print(f"\n--- ceginfo.hu: {query} ---")
        all_leads += scrape_ceginfo(query, pages)

    # Deduplicate by phone, then by name
    seen: set[str] = set()
    unique: list[dict] = []
    for lead in all_leads:
        phone = lead.get("phone", "").strip()
        key = phone if phone else lead.get("name", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(lead)

    removed = len(all_leads) - len(unique)
    if removed:
        print(f"  Removed {removed} duplicates.")

    return unique


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape Hungarian business directories for leads"
    )
    parser.add_argument("query", help='Search query e.g. "villanyszerelő budapest"')
    parser.add_argument(
        "--source",
        choices=SOURCES,
        default="all",
        help="Which source to scrape (default: all)",
    )
    parser.add_argument(
        "--pages", type=int, default=5,
        help="Pages per source (default: 5)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Test URLs for HTTP 200 before scraping",
    )
    args = parser.parse_args()

    if args.check:
        industry, city = split_query(args.query)
        print("Checking source URLs...")
        check_url(
            f"https://www.jofogas.hu/magyarorszag/szolgaltatasok"
            f"?q={quote(industry + ' ' + city)}&o=1"
        )
        check_url(
            f"https://www.ceginfo.hu/kereses"
            f"?q={quote(industry)}&telepules={quote(city)}&oldal=1"
        )
        print()

    print(f"Scraping: '{args.query}' | source={args.source} | pages={args.pages}")
    leads = scrape(args.query, source=args.source, pages=args.pages)
    print(f"\nTotal unique leads: {len(leads)}")

    if leads:
        save_leads(leads, args.query)
    else:
        print(
            "No leads found. Try --check to test if the URLs are reachable,\n"
            "then inspect the 'Body preview' output to update selectors."
        )
