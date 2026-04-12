#!/usr/bin/env python3
"""
run_scraper.py — Összes iparág automatikus scrapeolása
======================================================

Végigmegy a joszaki.hu összes beállított iparágán,
minden iparághoz meghívja a joszaki_scraper.py-t, és
elmenti az eredményeket a /raw_leads/ mappába.

Deduplikáció: a joszaki_scraper.py induláskor beolvassa az összes meglévő
raw_leads/*.csv fájlból a profile_url mezőket, és kihagyja az ismert
profilokat. Ha nincs egyetlen új lead sem, nem jön létre üres CSV fájl.

Majd automatikusan lefuttatja a score_leads.py-t is.

Használat:
    python run_scraper.py                     # összes iparág, 5 oldal
    python run_scraper.py --pages 3           # összes iparág, 3 oldal
    python run_scraper.py --only villanyszerelo komuves  # csak ezek
    python run_scraper.py --skip-scoring      # ne pontozzon
"""

import argparse
import csv
import glob
import subprocess
import sys
import time
from pathlib import Path

# Iparági kulcsok (joszaki_scraper.py CATEGORIES kulcsaival egyeznek)
ALL_INDUSTRIES = [
    "villanyszerelo",
    "vizvezetekszerelo",
    "festo",
    "komuves",
    "klimaszerelo",
    "autoszerelo",
    "takaritas",
]

BETWEEN_INDUSTRY_DELAY = 3.0  # másodperc iparágak között

SCRIPTS_DIR  = Path(__file__).parent
RAW_LEADS_DIR = SCRIPTS_DIR.parent / "raw_leads"


def count_known_urls() -> int:
    """Megszámolja az összes ismert profil URL-t a raw_leads/ mappában."""
    known: set[str] = set()
    for csv_file in glob.glob(str(RAW_LEADS_DIR / "*.csv")):
        try:
            with open(csv_file, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    url = (row.get("profile_url") or "").strip()
                    if url:
                        known.add(url)
        except Exception:
            pass
    return len(known)


def run_scraper_for(category: str, pages: int) -> bool:
    """Egy iparág scrapeolása subprocess-en keresztül."""
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "joszaki_scraper.py"),
        category,
        "--pages", str(pages),
    ]
    print(f"\n{'='*60}")
    print(f"  Iparág: {category} | Oldalak: {pages}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(SCRIPTS_DIR))
    return result.returncode == 0


def run_scoring() -> bool:
    """Lead pontozás futtatása."""
    print(f"\n{'='*60}")
    print("  Lead pontozás...")
    print(f"{'='*60}")
    cmd = [sys.executable, str(SCRIPTS_DIR / "score_leads.py")]
    result = subprocess.run(cmd, cwd=str(SCRIPTS_DIR))
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(
        description="Joszaki.hu — összes iparág automatikus scrapeolása"
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=5,
        help="Listázó oldalak száma iparágonként (alapértelmezett: 5)",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=ALL_INDUSTRIES,
        metavar="IPARAG",
        help="Csak ezeket az iparágakat scrapeolja",
    )
    parser.add_argument(
        "--skip-scoring",
        action="store_true",
        help="Ne futtassa a pontozást a scraping után",
    )
    args = parser.parse_args()

    industries = args.only if args.only else ALL_INDUSTRIES

    known_count = count_known_urls()

    print(f"Erik Marketing — Lead Generáló Rendszer")
    print(f"Iparágak ({len(industries)}): {', '.join(industries)}")
    print(f"Oldalak iparágonként: {args.pages}")
    print(f"Deduplikáció: {known_count} ismert profil URL kizárva")
    print()

    success_count = 0
    fail_count    = 0

    for i, category in enumerate(industries):
        ok = run_scraper_for(category, args.pages)
        if ok:
            success_count += 1
        else:
            fail_count += 1
            print(f"  [FIGYELEM] {category} scraping sikertelen!")

        # Delay az utolsó kivételével
        if i < len(industries) - 1:
            print(f"\n  Következő iparág {BETWEEN_INDUSTRY_DELAY:.0f}mp múlva...")
            time.sleep(BETWEEN_INDUSTRY_DELAY)

    print(f"\n{'='*60}")
    print(f"  Scraping kész: {success_count} sikeres, {fail_count} sikertelen")
    print(f"{'='*60}")

    if not args.skip_scoring:
        run_scoring()
    else:
        print("\nPontozás kihagyva (--skip-scoring).")

    print("\nKész.")


if __name__ == "__main__":
    main()
