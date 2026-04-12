#!/usr/bin/env python3
"""
joszaki_scraper.py — Jószaki.hu Lead Scraper
============================================

A joszaki.hu egy közvetítő telefonszámot (proxy) ad meg minden profil oldalon.
Központi szám: +36 1 443 3777
Mellékszám: 4-6 jegyű szám, ami a proxy hívás URL-ben és/vagy a megjelenített
szövegben is szerepel (pl. "+36 1 443 3777 / 57136").

Kimenet formátum: "+36 1 443 3777 (mellék: XXXXX)"

MINDKETTŐ kötelező: ha nincs mellékszám, a lead phone mezője üres marad.

Használat:
    python joszaki_scraper.py villanyszerelo --pages 5
    python joszaki_scraper.py festo --pages 3
    python joszaki_scraper.py all --pages 5

Kategória slugok:
    villanyszerelo, vizvezetekszerelo, festo, komuves,
    klimaszerelo, autoszerelo, takaritas
"""

import argparse
import csv
import os
import re
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CENTRAL_NUMBER = "+36 1 443 3777"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 2-3 másodperc delay oldalak között
PAGE_DELAY    = 2.5   # listázó oldalak között (másodperc)
PROFILE_DELAY = 2.0   # profiloldalak között (másodperc)
PW_TIMEOUT    = 35_000  # Playwright timeout milliszekundumban

RAW_LEADS_DIR = os.path.join(os.path.dirname(__file__), "..", "raw_leads")

# Iparág → joszaki URL slug + emberi cím
CATEGORIES = {
    "villanyszerelo":    ("villanyszerelo",       "Villanyszerelő"),
    "vizvezetekszerelo": ("vizvezetekszerelo",     "Vízvezetékszerelő"),
    "festo":             ("szobafesto-tapetazo",   "Festő / Tapétázó"),
    "komuves":           ("komuves",               "Kőműves"),
    "klimaszerelo":      ("klimaszerelo",           "Klímaszerelő"),
    "autoszerelo":       ("autoszerelo",            "Autószerelő"),
    "takaritas":         ("takaritas",              "Takarítás"),
}

# CSV mezők sorrendje
FIELDNAMES = [
    "name",
    "phone",          # "+36 1 443 3777 (mellék: XXXXX)" vagy ""
    "extension",      # csak a mellékszám (pl. "57136")
    "website",
    "google_reviews",
    "industry",
    "profile_url",
    "scraped_at",
]

# ---------------------------------------------------------------------------
# JavaScript — profiloldalon fut Playwright-en belül
# ---------------------------------------------------------------------------

_PROFILE_JS = r"""
() => {
    const result = {
        extension: '',
        website:   '',
        reviews:   '',
        proxyUrl:  '',
    };

    // ---------------------------------------------------------------
    // 1) MELLÉKSZÁM kinyerése
    //    A) Proxy hívás URL-ből: keressük a /hivas/ vagy /call/ stílusú
    //       linkeket, amik tartalmaznak egy 4-6 jegyű mellékszámot.
    //    B) Szöveges megjelenítésből: "+36 1 443 3777 / 57136"
    // ---------------------------------------------------------------

    // A) Proxy URL-ek az oldalon (href attribútumokban)
    const allLinks = Array.from(document.querySelectorAll('a[href]'));
    for (const a of allLinks) {
        const href = a.href || '';
        // joszaki proxy URL formátumok: /hivas/XXXXX, ?mellekszam=XXXXX, stb.
        const urlExt = href.match(/[\/=](\d{4,6})(?:[\/&?#]|$)/);
        if (urlExt && href.includes('joszaki')) {
            result.extension = urlExt[1];
            result.proxyUrl  = href;
            break;
        }
    }

    // B) Szövegcsomópontok végigolvasása — "+36 1 443 3777 / 57136"
    if (!result.extension) {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const t = node.textContent.trim();
            // Vonal: "+36 1 443 3777 / 57136" vagy "+36 1 4433777/57136"
            if (t.startsWith('+36') && /\/\s*\d{4,6}/.test(t)) {
                const m = t.match(/\/\s*(\d{4,6})/);
                if (m) {
                    result.extension = m[1];
                    break;
                }
            }
        }
    }

    // ---------------------------------------------------------------
    // 2) WEBOLDAL — első külső link, nem joszaki/fb/instagram/google
    // ---------------------------------------------------------------
    const SKIP_DOMAINS = [
        'joszaki.hu', 'facebook.com', 'instagram.com',
        'google.com', 'youtube.com', 'twitter.com', 't.me',
    ];
    for (const a of allLinks) {
        const href = a.href || '';
        if (href.startsWith('http') && !SKIP_DOMAINS.some(d => href.includes(d))) {
            result.website = href;
            break;
        }
    }

    // ---------------------------------------------------------------
    // 3) ÉRTÉKELÉSEK SZÁMA — "12 értékelés" vagy "12 vélemény"
    // ---------------------------------------------------------------
    const bodyText = document.body.innerText || '';
    const revM = bodyText.match(/(\d+)\s*(értékelés|vélemény|csillag)/);
    if (revM) result.reviews = revM[1];

    return result;
}
"""

# JavaScript — listázó oldalon fut, összegyűjti a profil href-eket
_LIST_JS = """
() => Array.from(document.querySelectorAll('a[href^="/szakember/"]'))
         .map(a => a.getAttribute('href') || '')
         .filter(h => h.startsWith('/szakember/'))
"""

# ---------------------------------------------------------------------------
# Segédfüggvények
# ---------------------------------------------------------------------------

def slug_to_name(slug: str) -> str:
    """'/szakember/kovacs-janos?city=budapest' → 'Kovacs Janos'"""
    slug = slug.split("?")[0].split("#")[0]
    slug = slug.replace("/szakember/", "").strip("/")
    return " ".join(word.capitalize() for word in slug.split("-") if word)


def format_phone(extension: str) -> str:
    """Formázott telefonszám: '+36 1 443 3777 (mellék: XXXXX)'"""
    if not extension:
        return ""
    return f"{CENTRAL_NUMBER} (mellék: {extension})"


# ---------------------------------------------------------------------------
# Fő scraper függvény
# ---------------------------------------------------------------------------

def scrape_joszaki(category_key: str, pages: int = 5) -> list[dict]:
    """
    Joszaki.hu scraper két fázisban:

    FÁZIS 1 — Listázó oldalak (https://joszaki.hu/szakemberek/SLUG/budapest?page=N):
      Összegyűjti az összes /szakember/NÉV-SLUG profil URL-t.

    FÁZIS 2 — Profil oldalak (https://joszaki.hu/szakember/NÉV-SLUG):
      Kinyeri a mellékszámot, weboldalt, értékelések számát.
      FONTOS: a mellékszám a proxy hívás URL-ből vagy a "+36 / XXXXX"
      szövegből kerül kinyerésre.

    Telefonszám formátum: "+36 1 443 3777 (mellék: XXXXX)"
    Ha nincs mellékszám → phone mező üres.
    """
    if category_key not in CATEGORIES:
        print(f"[HIBA] Ismeretlen kategória: '{category_key}'")
        print(f"  Érvényes kulcsok: {', '.join(CATEGORIES)}")
        return []

    category_slug, industry_label = CATEGORIES[category_key]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[HIBA] Playwright nincs telepítve.")
        print("  Futtasd: pip install playwright && playwright install chromium")
        return []

    leads: list[dict] = []
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=USER_AGENT,
            locale="hu-HU",
        )
        page = ctx.new_page()

        # Elfogott proxy URL-ek tárolása (request interception)
        proxy_extensions: dict[str, str] = {}  # profile_slug → extension

        def _on_request(req):
            """Hálózati kérések figyelése: proxy URL-ből mellékszám kinyerése."""
            url = req.url
            if "joszaki" in url and re.search(r"[\/=](\d{4,6})(?:[\/&?#]|$)", url):
                m = re.search(r"[\/=](\d{4,6})(?:[\/&?#]|$)", url)
                if m:
                    ext = m.group(1)
                    # Az aktuális URL-ből megállapítjuk melyik profilhoz tartozik
                    # (context-ből: az utolsóként navigált profil URL slug-ja)
                    proxy_extensions["_last"] = ext

        page.on("request", _on_request)

        # ==================================================================
        # FÁZIS 1: Profil URL-ek összegyűjtése a listázó oldalakról
        # ==================================================================
        profile_slugs: list[str] = []
        seen_slugs: set[str] = set()

        for page_num in range(1, pages + 1):
            list_url = (
                f"https://joszaki.hu/szakemberek/{category_slug}/budapest"
                f"?page={page_num}"
            )
            print(f"  [lista] {page_num}. oldal: {list_url}")

            try:
                page.goto(list_url, wait_until="networkidle", timeout=PW_TIMEOUT)
                page.wait_for_selector('a[href^="/szakember/"]', timeout=PW_TIMEOUT)
            except Exception as e:
                print(f"  [lista] {page_num}. oldal hiba: {e}")
                break

            hrefs: list[str] = page.evaluate(_LIST_JS)
            new_count = 0
            for href in hrefs:
                slug = href.split("?")[0].replace("/szakember/", "").strip("/")
                if slug and slug not in seen_slugs:
                    seen_slugs.add(slug)
                    profile_slugs.append(slug)
                    new_count += 1

            print(f"  [lista] {page_num}. oldal: {new_count} új profil "
                  f"(összesen: {len(profile_slugs)})")

            if new_count == 0:
                print(f"  [lista] Nincs új profil a(z) {page_num}. oldalon — megállás.")
                break

            time.sleep(PAGE_DELAY)

        print(f"\n  1. fázis kész. {len(profile_slugs)} profil felkeresése következik...\n")

        # ==================================================================
        # FÁZIS 2: Profil oldalak felkeresése, adatok kinyerése
        # ==================================================================
        for i, slug in enumerate(profile_slugs, 1):
            name = slug_to_name(slug)
            profile_url = f"https://joszaki.hu/szakember/{slug}"

            print(f"  [profil] {i}/{len(profile_slugs)}: {profile_url}")

            # Reset a proxy intercept gyűjtőben
            proxy_extensions.pop("_last", None)

            try:
                page.goto(profile_url, wait_until="networkidle", timeout=PW_TIMEOUT)
                # Várunk a telefonszám JS-rendereléséig
                page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  [profil] Hiba ({slug}): {e}")
                leads.append({
                    "name":          name,
                    "phone":         "",
                    "extension":     "",
                    "website":       "",
                    "google_reviews": "",
                    "industry":      industry_label,
                    "profile_url":   profile_url,
                    "scraped_at":    scraped_at,
                })
                continue

            data = page.evaluate(_PROFILE_JS)

            # Mellékszám: JS-ből, vagy proxy intercept-ből
            extension = data.get("extension", "").strip()
            if not extension:
                extension = proxy_extensions.get("_last", "").strip()

            phone = format_phone(extension)

            website = data.get("website", "").strip()
            reviews = data.get("reviews", "").strip()

            if phone:
                print(f"    → Telefon: {phone} | Web: {website or '–'} | Értékelés: {reviews or '–'}")
            else:
                print(f"    → Nincs mellékszám | Web: {website or '–'}")

            leads.append({
                "name":          name,
                "phone":         phone,
                "extension":     extension,
                "website":       website,
                "google_reviews": reviews,
                "industry":      industry_label,
                "profile_url":   profile_url,
                "scraped_at":    scraped_at,
            })

            time.sleep(PROFILE_DELAY)

        browser.close()

    with_phone   = sum(1 for l in leads if l["phone"])
    with_website = sum(1 for l in leads if l["website"])
    print(
        f"\n  Kész. {len(leads)} lead | "
        f"telefonnal: {with_phone} | weboldallal: {with_website}"
    )
    return leads


# ---------------------------------------------------------------------------
# CSV mentés
# ---------------------------------------------------------------------------

def save_leads(leads: list[dict], category_key: str) -> str:
    """CSV mentés /raw_leads/ mappába, timestamp névvel."""
    os.makedirs(RAW_LEADS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"leads_{category_key}_budapest_{timestamp}.csv"
    filepath  = os.path.join(RAW_LEADS_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(leads)

    print(f"\nMentve: {len(leads)} lead → {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# CLI belépési pont
# ---------------------------------------------------------------------------

def main():
    all_keys = list(CATEGORIES.keys()) + ["all"]
    parser = argparse.ArgumentParser(
        description="Joszaki.hu lead scraper — Budapest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(f"  {k}" for k in CATEGORIES),
    )
    parser.add_argument(
        "category",
        choices=all_keys,
        help="Kategória kulcs vagy 'all' az összes iparághoz",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=5,
        help="Listázó oldalak száma iparágonként (alapértelmezett: 5)",
    )
    args = parser.parse_args()

    if args.category == "all":
        for key in CATEGORIES:
            print(f"\n{'='*60}")
            print(f"  Iparág: {CATEGORIES[key][1]}")
            print(f"{'='*60}")
            leads = scrape_joszaki(key, pages=args.pages)
            if leads:
                save_leads(leads, key)
            time.sleep(PAGE_DELAY)
    else:
        leads = scrape_joszaki(args.category, pages=args.pages)
        if leads:
            save_leads(leads, args.category)
        elif leads is not None:
            print("Nincs lead. Ellenőrizd a hálózatot és a kategória slugot.")


if __name__ == "__main__":
    main()
