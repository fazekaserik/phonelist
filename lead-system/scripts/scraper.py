#!/usr/bin/env python3
"""
Hungarian Lead Scraper

Sources:
  - joszaki.hu  — Playwright (headless Chromium) for JS-rendered phone numbers
  - jofogas.hu  — requests + BeautifulSoup (phones in static HTML)
  - ceginfo.hu  — requests + BeautifulSoup (phones in static HTML)

Usage:
    python scraper.py "villanyszerelő budapest"
    python scraper.py "villanyszerelő budapest" --source joszaki --pages 5
    python scraper.py "festő budapest" --source jofogas --pages 5
    python scraper.py "klímaszerelő budapest" --source all --pages 5
    python scraper.py "villanyszerelő budapest" --check   # test URLs only
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

REQUEST_DELAY = 2.5   # seconds between page requests
TIMEOUT = 20          # seconds for requests
PW_TIMEOUT = 30_000   # milliseconds for Playwright

RAW_LEADS_DIR = os.path.join(os.path.dirname(__file__), "..", "raw_leads")

# Hungarian phone regex — used as fallback when no tel: link is present
HU_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(\+36|06)"
    r"[\s\-/.]?"
    r"(\d{1,2})"
    r"[\s\-/.]?"
    r"(\d{3,4})"
    r"[\s\-/.]?"
    r"(\d{3,4})"
    r"(?!\d)"
)

# joszaki.hu category URL slugs
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
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "_", text)


def name_from_slug(slug: str) -> str:
    """
    Convert a joszaki URL slug to a display name.
    Strips query params first, then capitalizes each hyphen-separated word.
    'torma-tibor' → 'Torma Tibor'
    '/szakember/kovacs-janos?city=budapest' → 'Kovacs Janos'
    """
    # Strip query params and fragment
    slug = slug.split("?")[0].split("#")[0]
    # Strip leading path components
    slug = slug.replace("/szakember/", "").strip("/")
    return " ".join(word.capitalize() for word in slug.split("-") if word)


def clean_phone(raw: str) -> str:
    """Normalize HU phone to +36XXXXXXXXX. Returns '' on garbage input."""
    digits = re.sub(r"[^\d]", "", raw)
    if raw.strip().startswith("+"):
        digits = "+" + digits
    if digits.startswith("+36") and len(digits) >= 12:
        return digits[:12]
    if digits.startswith("06") and len(digits) >= 10:
        return "+36" + digits[2:11]
    if digits.startswith("36") and len(digits) >= 11:
        return "+" + digits[:12]
    return ""


def extract_phone_from_text(text: str) -> str:
    """Find first Hungarian phone number in arbitrary text."""
    m = HU_PHONE_RE.search(text)
    return clean_phone(m.group(0)) if m else ""


def split_query(query: str) -> tuple[str, str]:
    """
    'villanyszerelő budapest' → ('villanyszerelő', 'budapest')
    City defaults to 'budapest' if not found.
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
    return (" ".join(industry_parts) or query), city


def get_page(url: str, session: requests.Session) -> BeautifulSoup | None:
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"  [WARN] {url}: {e}")
        return None


def check_url(url: str) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        ok = r.status_code == 200
        print(f"  [check] {url} → HTTP {r.status_code} body={len(r.text)} chars")
        return ok
    except Exception as e:
        print(f"  [check] {url} → ERROR: {e}")
        return False


# ---------------------------------------------------------------------------
# Scraper: joszaki.hu  (Playwright — JS-rendered phones)
# ---------------------------------------------------------------------------

def scrape_joszaki(query: str, pages: int = 5) -> list[dict]:
    """
    Scrape joszaki.hu using headless Chromium via Playwright.

    Phone numbers (+36 1 443 3777 / XXXXX) are rendered by React JS.
    We use page.evaluate() to extract data directly from the live browser DOM —
    this is the only reliable way to read React-rendered leaf elements.

    Extraction strategy (runs inside the browser):
      - For each a[href^="/szakember/"] link, walk up to the card container
      - Phone: find the deepest text node that starts with "+36" and contains "/"
      - Website: first external <a href="http..."> that isn't joszaki/facebook
      - City: regex for "XIII. kerület" style text in the card
      - Reviews: regex for "12 értékelés" in the card
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [joszaki] Playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    category_slug = None
    for keyword, slug in JOSZAKI_CATEGORY_MAP.items():
        if keyword in query.lower():
            category_slug = slug
            break

    if not category_slug:
        print(f"  [joszaki] No category mapping for: '{query}'")
        return []

    industry_label, _ = split_query(query)
    leads = []

    # JavaScript that runs inside the browser and returns structured card data.
    # Reads the fully-rendered React DOM — BeautifulSoup cannot do this reliably.
    EXTRACT_JS = """
    () => {
        const results = [];
        const links = Array.from(document.querySelectorAll('a[href^="/szakember/"]'));

        links.forEach(link => {
            // Walk up to find the card container (stops when it finds an ancestor
            // that contains exactly this one specialist link)
            let card = null;
            let el = link.parentElement;
            while (el && el !== document.body) {
                const count = el.querySelectorAll('a[href^="/szakember/"]').length;
                if (count === 1) {
                    card = el;
                }
                el = el.parentElement;
            }

            let phone = '';
            let website = '';
            let cardText = '';
            let reviews = '';

            if (card) {
                cardText = card.innerText || '';

                // Phone: find the shallowest text node that starts with "+36" and
                // contains "/" (joszaki proxy format: "+36 1 443 3777 / 57136")
                const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.textContent.trim();
                    if (t.startsWith('+36') && t.includes('/') && t.length < 35) {
                        phone = t;
                        break;
                    }
                }

                // Website: first external link that isn't joszaki or facebook
                const anchors = card.querySelectorAll('a[href^="http"]');
                for (const a of anchors) {
                    if (!a.href.includes('joszaki.hu') && !a.href.includes('facebook.com')) {
                        website = a.href;
                        break;
                    }
                }

                // Reviews: "12 értékelés" or "12 vélemény"
                const revMatch = cardText.match(/(\\d+)\\s*(értékelés|vélemény)/);
                if (revMatch) reviews = revMatch[1];
            }

            results.push({
                href: link.getAttribute('href') || '',
                phone: phone,
                website: website,
                cardText: cardText.substring(0, 600),
                reviews: reviews
            });
        });

        return results;
    }
    """

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="hu-HU")
        page = ctx.new_page()

        for page_num in range(1, pages + 1):
            url = f"https://joszaki.hu/szakemberek/{category_slug}/budapest?page={page_num}"
            print(f"  [joszaki] Page {page_num}: {url}")

            try:
                page.goto(url, wait_until="networkidle", timeout=PW_TIMEOUT)
                page.wait_for_selector('a[href^="/szakember/"]', timeout=PW_TIMEOUT)
                # Extra pause: React may still be filling in phone button content
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  [joszaki] Page {page_num} load error: {e}")
                break

            # Run extraction entirely inside the browser DOM
            cards_data = page.evaluate(EXTRACT_JS)

            if not cards_data:
                print(f"  [joszaki] No cards on page {page_num}, stopping.")
                break

            page_count = 0
            for item in cards_data:
                raw_href = item.get("href", "")
                name = name_from_slug(raw_href)
                if not name:
                    continue

                profile_url = "https://joszaki.hu" + raw_href.split("?")[0]
                phone = item.get("phone", "").strip()
                website = item.get("website", "").strip()
                google_reviews = item.get("reviews", "")

                # City: look for kerület in card text
                city = "Budapest"
                city_m = re.search(
                    r"([IVXLC]+\.\s*kerület|\d+\.\s*kerület)",
                    item.get("cardText", "")
                )
                if city_m:
                    city = f"Budapest {city_m.group(0).strip()}"

                notes = f"profil: {profile_url}"
                if phone:
                    notes = f"joszaki proxy | {notes}"

                leads.append({
                    "name": name,
                    "phone": phone,
                    "city": city,
                    "website": website,
                    "email": "",
                    "industry": industry_label,
                    "source": "joszaki.hu",
                    "google_reviews": google_reviews,
                    "notes": notes,
                })
                page_count += 1

            print(f"  [joszaki] Page {page_num}: {page_count} specialists "
                  f"(with phone: {sum(1 for c in cards_data if c.get('phone'))}) "
                  f"| total: {len(leads)}")

            if page_count < 5:
                print(f"  [joszaki] Fewer than 5 results — likely last page.")
                break

            time.sleep(REQUEST_DELAY)

        browser.close()

    return leads


# ---------------------------------------------------------------------------
# Scraper: jofogas.hu  (requests — phones in static HTML)
# ---------------------------------------------------------------------------

def scrape_jofogas(query: str, pages: int = 5) -> list[dict]:
    """
    Scrape jofogás.hu — services category.
    URL: https://www.jofogas.hu/magyarorszag/szolgaltatasok?q=QUERY&o=PAGE
    Phone numbers posted by tradespeople appear in the static HTML.
    """
    leads = []
    session = requests.Session()

    industry, city = split_query(query)
    encoded = quote(f"{industry} {city}")
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

        listings = (
            soup.select("article.listing-card")
            or soup.select("div[class*='listing-card']")
            or soup.select("li[class*='listing']")
            or soup.select("div[class*='ad-card']")
            or soup.select("[data-testid*='listing']")
        )

        if not listings:
            preview = soup.get_text()[:300].replace("\n", " ")
            print(f"  [jofogas] No listings. Body preview: {preview!r}")
            break

        page_count = 0
        for item in listings:
            item_text = item.get_text(" ", strip=True)

            name_el = (
                item.select_one("h2")
                or item.select_one("h3")
                or item.select_one("[class*='title']")
                or item.select_one("[data-testid*='title']")
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                continue

            phone = ""
            tel_el = item.select_one("a[href^='tel:']")
            if tel_el:
                phone = clean_phone(tel_el["href"].replace("tel:", "").strip())
            if not phone:
                phone = extract_phone_from_text(item_text)

            city_el = (
                item.select_one("[class*='location']")
                or item.select_one("[class*='city']")
                or item.select_one("[data-testid*='location']")
            )
            city_val = city_el.get_text(strip=True) if city_el else city

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
# Scraper: ceginfo.hu  (requests — phones in static HTML)
# ---------------------------------------------------------------------------

def scrape_ceginfo(query: str, pages: int = 5) -> list[dict]:
    """
    Scrape ceginfo.hu company registry.
    URL: https://www.ceginfo.hu/kereses?q=INDUSTRY&telepules=CITY&oldal=PAGE
    Only keeps entries that have a phone number.
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

        listings = (
            soup.select("div.ceg-lista-elem")
            or soup.select("div[class*='ceg-lista']")
            or soup.select("article[class*='ceg']")
            or soup.select("div.result-item")
            or soup.select("li.result")
            or soup.select("table.results tr:not(:first-child)")
        )

        if not listings:
            preview = soup.get_text()[:300].replace("\n", " ")
            print(f"  [ceginfo] No listings. Body preview: {preview!r}")
            break

        page_count = 0
        for item in listings:
            item_text = item.get_text(" ", strip=True)

            name_el = (
                item.select_one("h2")
                or item.select_one("h3")
                or item.select_one(".ceg-nev")
                or item.select_one("[class*='nev']")
                or item.select_one("[class*='name']")
                or item.select_one("a[href*='/ceg/']")
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                continue

            phone = ""
            tel_el = item.select_one("a[href^='tel:']")
            if tel_el:
                phone = clean_phone(tel_el["href"].replace("tel:", "").strip())
            if not phone:
                ph_el = item.select_one("[class*='telefon'], [class*='phone'], [class*='tel']")
                if ph_el:
                    phone = clean_phone(ph_el.get_text(strip=True))
            if not phone:
                phone = extract_phone_from_text(item_text)
            if not phone:
                continue  # skip entries with no phone

            email = ""
            mail_el = item.select_one("a[href^='mailto:']")
            if mail_el:
                email = mail_el["href"].replace("mailto:", "").strip()

            address = ""
            addr_el = item.select_one("[class*='cim'], [class*='address'], [class*='telepules']")
            if addr_el:
                address = addr_el.get_text(strip=True)

            website = ""
            for a in item.select("a[href]"):
                h = a["href"]
                if h.startswith("http") and "ceginfo.hu" not in h:
                    website = h
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
    filepath = os.path.join(RAW_LEADS_DIR, f"leads_{slugify(query)}_{timestamp}.csv")
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            writer.writerow({field: lead.get(field, "") for field in FIELDNAMES})
    print(f"\nSaved {len(leads)} leads → {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

SOURCES = ["joszaki", "jofogas", "ceginfo", "all"]


def scrape(query: str, source: str = "all", pages: int = 5) -> list[dict]:
    all_leads: list[dict] = []

    if source == "joszaki":
        all_leads = scrape_joszaki(query, pages)
    elif source == "jofogas":
        all_leads = scrape_jofogas(query, pages)
    elif source == "ceginfo":
        all_leads = scrape_ceginfo(query, pages)
    else:
        print(f"\n--- joszaki.hu (Playwright): {query} ---")
        all_leads += scrape_joszaki(query, pages)
        time.sleep(REQUEST_DELAY)

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
    parser = argparse.ArgumentParser(description="Scrape Hungarian business directories")
    parser.add_argument("query", help='e.g. "villanyszerelő budapest"')
    parser.add_argument("--source", choices=SOURCES, default="all")
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--check", action="store_true", help="Test URLs before scraping")
    args = parser.parse_args()

    if args.check:
        industry, city = split_query(args.query)
        category = next(
            (v for k, v in JOSZAKI_CATEGORY_MAP.items() if k in args.query.lower()), "?"
        )
        print("Checking URLs...")
        check_url(f"https://joszaki.hu/szakemberek/{category}/budapest?page=1")
        check_url(
            f"https://www.jofogas.hu/magyarorszag/szolgaltatasok"
            f"?q={quote(industry + ' ' + city)}&o=1"
        )
        check_url(
            f"https://www.ceginfo.hu/kereses"
            f"?q={quote(industry)}&telepules={quote(city)}&oldal=1"
        )
        print()

    print(f"Query: '{args.query}' | source={args.source} | pages={args.pages}")
    leads = scrape(args.query, source=args.source, pages=args.pages)
    print(f"\nTotal unique leads: {len(leads)}")

    if leads:
        save_leads(leads, args.query)
    else:
        print("No leads. Use --check to verify URLs, inspect 'Body preview' to update selectors.")
