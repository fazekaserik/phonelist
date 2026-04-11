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
# Scraper: joszaki.hu  (Playwright — two-phase: list → profile pages)
# ---------------------------------------------------------------------------

# JavaScript run on each INDIVIDUAL PROFILE PAGE to extract contact details.
# Phone and website are only rendered on the profile, not on the listing page.
_JOSZAKI_PROFILE_JS = """
() => {
    let phone = '';
    let extension = '';
    let website = '';
    let reviews = '';
    let city = '';

    // PHONE: walk every text node in the document looking for the joszaki proxy
    // format "+36 1 443 3777 / 57136" — the number appears only after JS renders.
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const t = node.textContent.trim();
        if (t.startsWith('+36') && t.includes('/')) {
            phone = t;
            const afterSlash = t.split('/').pop().trim();
            if (/^\\d{4,6}$/.test(afterSlash)) extension = afterSlash;
            break;
        }
    }

    // WEBSITE: first external link that is not joszaki, facebook, instagram, google
    const SKIP = ['joszaki.hu', 'facebook.com', 'instagram.com', 'google.com', 'youtube.com'];
    const anchors = document.querySelectorAll('a[href^="http"]');
    for (const a of anchors) {
        if (!SKIP.some(domain => a.href.includes(domain))) {
            website = a.href;
            break;
        }
    }

    // REVIEWS: "12 értékelés" or "12 vélemény"
    const bodyText = document.body.innerText || '';
    const revM = bodyText.match(/(\\d+)\\s*(értékelés|vélemény)/);
    if (revM) reviews = revM[1];

    // CITY: Budapest kerület from profile text
    const cityM = bodyText.match(/([IVXLC]+\\.\\s*kerület|\\d+\\.\\s*kerület)/);
    if (cityM) city = 'Budapest ' + cityM[0].trim();

    return { phone, extension, website, reviews, city };
}
"""

# JavaScript run on each LISTING PAGE to collect profile hrefs.
_JOSZAKI_LIST_JS = """
() => Array.from(document.querySelectorAll('a[href^="/szakember/"]'))
          .map(a => a.getAttribute('href') || '')
          .filter(h => h.startsWith('/szakember/'))
"""


def scrape_joszaki(query: str, pages: int = 5) -> list[dict]:
    """
    Scrape joszaki.hu in two phases using headless Chromium (Playwright):

    PHASE 1 — Listing pages:
      https://joszaki.hu/szakemberek/CATEGORY/budapest?page=N
      Collects all /szakember/NAME-SLUG profile URLs.
      Name is derived from the slug ("torma-tibor" → "Torma Tibor").

    PHASE 2 — Individual profile pages:
      https://joszaki.hu/szakember/NAME-SLUG
      Extracts: phone, extension, website, reviews, city.
      Phone and website are ONLY available on the profile page, not the listing.

    A single browser/context is kept open across both phases to minimize overhead.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [joszaki] Playwright not installed.")
        print("  Run: pip install playwright && playwright install chromium")
        return []

    category_slug = None
    for keyword, slug in JOSZAKI_CATEGORY_MAP.items():
        if keyword in query.lower():
            category_slug = slug
            break

    if not category_slug:
        print(f"  [joszaki] No category mapping for: '{query}'")
        print(f"  Known keywords: {', '.join(JOSZAKI_CATEGORY_MAP)}")
        return []

    industry_label, _ = split_query(query)
    leads = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="hu-HU")
        page = ctx.new_page()

        # ------------------------------------------------------------------
        # PHASE 1: collect profile slugs from listing pages
        # ------------------------------------------------------------------
        profile_slugs: list[str] = []   # e.g. ["torma-tibor", "kovacs-janos"]
        seen_slugs: set[str] = set()

        for page_num in range(1, pages + 1):
            list_url = (
                f"https://joszaki.hu/szakemberek/{category_slug}/budapest"
                f"?page={page_num}"
            )
            print(f"  [joszaki] Listing page {page_num}: {list_url}")

            try:
                page.goto(list_url, wait_until="networkidle", timeout=PW_TIMEOUT)
                page.wait_for_selector('a[href^="/szakember/"]', timeout=PW_TIMEOUT)
            except Exception as e:
                print(f"  [joszaki] Listing page {page_num} error: {e}")
                break

            hrefs: list[str] = page.evaluate(_JOSZAKI_LIST_JS)
            new_this_page = 0
            for href in hrefs:
                # Clean slug: strip query params, strip path prefix
                slug = href.split("?")[0].replace("/szakember/", "").strip("/")
                if slug and slug not in seen_slugs:
                    seen_slugs.add(slug)
                    profile_slugs.append(slug)
                    new_this_page += 1

            print(f"  [joszaki] Listing page {page_num}: {new_this_page} new profiles "
                  f"(total collected: {len(profile_slugs)})")

            if new_this_page == 0:
                print(f"  [joszaki] No new profiles on page {page_num} — stopping listing.")
                break

            time.sleep(REQUEST_DELAY)

        print(f"\n  [joszaki] Phase 1 done. Visiting {len(profile_slugs)} profiles...\n")

        # ------------------------------------------------------------------
        # PHASE 2: visit each profile page and extract contact details
        # ------------------------------------------------------------------
        for i, slug in enumerate(profile_slugs, 1):
            name = name_from_slug(slug)
            profile_url = f"https://joszaki.hu/szakember/{slug}"

            print(f"  [joszaki] Profile {i}/{len(profile_slugs)}: {profile_url}")

            try:
                page.goto(profile_url, wait_until="networkidle", timeout=PW_TIMEOUT)
                # Wait for the phone element to render
                page.wait_for_timeout(1500)
            except Exception as e:
                print(f"  [joszaki] Profile error ({slug}): {e}")
                leads.append(_joszaki_empty_lead(name, profile_url, industry_label))
                continue

            data = page.evaluate(_JOSZAKI_PROFILE_JS)

            phone     = data.get("phone", "").strip()
            extension = data.get("extension", "").strip()
            website   = data.get("website", "").strip()
            reviews   = data.get("reviews", "")
            city      = data.get("city", "Budapest").strip() or "Budapest"

            if phone and extension:
                notes = f"Joszaki proxy - mellék: {extension} | profil: {profile_url}"
            elif phone:
                notes = f"Joszaki proxy | profil: {profile_url}"
            else:
                notes = f"Nincs telefon | profil: {profile_url}"

            leads.append({
                "name":          name,
                "phone":         phone,
                "extension":     extension,
                "city":          city,
                "website":       website,
                "email":         "",
                "industry":      industry_label,
                "source":        "joszaki.hu",
                "google_reviews": reviews,
                "notes":         notes,
            })

            time.sleep(REQUEST_DELAY)

        browser.close()

    with_phone   = sum(1 for l in leads if l["phone"])
    with_website = sum(1 for l in leads if l["website"])
    print(f"\n  [joszaki] Done. {len(leads)} leads | "
          f"with phone: {with_phone} | with website: {with_website}")

    return leads


def _joszaki_empty_lead(name: str, profile_url: str, industry: str) -> dict:
    """Placeholder lead when a profile page fails to load."""
    return {
        "name": name, "phone": "", "extension": "", "city": "Budapest",
        "website": "", "email": "", "industry": industry,
        "source": "joszaki.hu", "google_reviews": "",
        "notes": f"profil: {profile_url} [betöltési hiba]",
    }


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
    "name", "phone", "extension", "city", "website",
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
