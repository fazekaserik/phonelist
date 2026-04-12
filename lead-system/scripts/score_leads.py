#!/usr/bin/env python3
"""
score_leads.py — Lead pontozó és csomag-ajánló
===============================================

Beolvassa a /raw_leads/ mappa CSV fájljait, minden lead-et
0–100 pontig értékel, majd elmenti a /scored_leads/ mappába.

Pontozási rendszer (összesen 100 pont):
  35 pt — Weboldal státusz     (nincs weboldal = max pont → Full Csomag)
  20 pt — Iparági illeszkedés  (prioritás lista alapján)
  15 pt — Földrajz             (Budapest = max pont)
  20 pt — Üzleti méret jelek   (Google értékelések száma)
  10 pt — Kontakt adat minőség (van telefon + weboldal)

Csomag javaslat:
  Nincs weboldal        → Full Csomag
  Van weboldal          → AI SEO
  Mindkét szint teljesül → Mindkettő

Használat:
    python score_leads.py
    python score_leads.py --input ../raw_leads/leads_villanyszerelo_budapest_20260412.csv
    python score_leads.py --min-score 50
"""

import argparse
import csv
import glob
import os
from datetime import datetime

# ---------------------------------------------------------------------------
# Könyvtárak
# ---------------------------------------------------------------------------

BASE_DIR         = os.path.dirname(__file__)
RAW_LEADS_DIR    = os.path.join(BASE_DIR, "..", "raw_leads")
SCORED_LEADS_DIR = os.path.join(BASE_DIR, "..", "scored_leads")

# ---------------------------------------------------------------------------
# Iparági prioritás (1 = legjobb)
# ---------------------------------------------------------------------------

INDUSTRY_PRIORITY: dict[str, int] = {
    "Villanyszerelő":      1,
    "Vízvezetékszerelő":   2,
    "Festő / Tapétázó":    3,
    "Kőműves":             4,
    "Klímaszerelő":        5,
    "Autószerelő":         6,
    "Takarítás":           7,
}

# Iparági pont: 1. prioritás → 20pt, 7. → 5pt (lineáris skála)
def _industry_score(industry: str) -> int:
    for label, rank in INDUSTRY_PRIORITY.items():
        if label.lower() in industry.lower() or industry.lower() in label.lower():
            # rank 1 → 20 pt, rank 7 → 5 pt
            return max(5, 20 - (rank - 1) * 2)
    return 10  # ismeretlen iparág: közepes pont

# ---------------------------------------------------------------------------
# Pontozó függvény
# ---------------------------------------------------------------------------

def score_lead(lead: dict) -> dict:
    """
    Egy lead pontozása. Visszaad egy új dict-et az összes eredeti
    mezővel + score, grade, package mezőkkel.
    """
    score = 0

    website = (lead.get("website") or "").strip()
    phone   = (lead.get("phone") or "").strip()
    reviews_raw = (lead.get("google_reviews") or "").strip()
    industry    = (lead.get("industry") or "").strip()
    profile_url = (lead.get("profile_url") or "").strip()

    # ------------------------------------------------------------------
    # 1) Weboldal státusz — 35 pt
    # ------------------------------------------------------------------
    if not website:
        website_score = 35      # Nincs weboldal → Full Csomag jelölt
    else:
        # Van weboldal, de lehet elavult vagy AI-ban nem látható
        website_score = 10

    score += website_score

    # ------------------------------------------------------------------
    # 2) Iparági illeszkedés — max 20 pt
    # ------------------------------------------------------------------
    score += _industry_score(industry)

    # ------------------------------------------------------------------
    # 3) Földrajz — 15 pt
    # ------------------------------------------------------------------
    # Minden lead Budapest (joszaki Budapest szűrőn fut), teljes pont
    score += 15

    # ------------------------------------------------------------------
    # 4) Üzleti méret jelek — max 20 pt (Google értékelések)
    # ------------------------------------------------------------------
    try:
        reviews = int(reviews_raw)
    except (ValueError, TypeError):
        reviews = 0

    if reviews == 0:
        review_score = 5     # Nincs értékelés: kisebb, kevésbé etablírozott
    elif reviews < 5:
        review_score = 10
    elif reviews < 20:
        review_score = 15
    elif reviews < 50:
        review_score = 18
    else:
        review_score = 20

    score += review_score

    # ------------------------------------------------------------------
    # 5) Kontakt adat minőség — max 10 pt
    # ------------------------------------------------------------------
    contact_score = 0
    if phone:
        contact_score += 7   # Van telefonszám (mellékkel együtt)
    if profile_url:
        contact_score += 3   # Van profil URL

    score += contact_score

    # ------------------------------------------------------------------
    # Osztályzat és csomag javaslat
    # ------------------------------------------------------------------
    if score >= 60:
        grade = "A"
    elif score >= 40:
        grade = "B"
    else:
        grade = "C"

    if not website:
        package = "Full Csomag"
    else:
        package = "AI SEO"

    scored = dict(lead)
    scored["score"]   = score
    scored["grade"]   = grade
    scored["package"] = package
    return scored


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

INPUT_FIELDS = [
    "name", "phone", "extension", "website", "google_reviews",
    "industry", "profile_url", "scraped_at",
]

OUTPUT_FIELDS = INPUT_FIELDS + ["score", "grade", "package"]


def load_csv(filepath: str) -> list[dict]:
    leads = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append(dict(row))
    return leads


def save_scored(leads: list[dict], source_path: str) -> str:
    os.makedirs(SCORED_LEADS_DIR, exist_ok=True)
    basename  = os.path.basename(source_path).replace("leads_", "scored_")
    # Timestamp frissítése
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    basename  = re.sub(r"\d{8}_\d{6}", timestamp, basename) if re.search(
        r"\d{8}_\d{6}", basename
    ) else basename
    filepath  = os.path.join(SCORED_LEADS_DIR, basename)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(leads, key=lambda x: -x.get("score", 0)))

    return filepath


# ---------------------------------------------------------------------------
# Belépési pont
# ---------------------------------------------------------------------------

import re  # noqa: E402  (re már fent importálva, de biztonság kedvéért)


def main():
    parser = argparse.ArgumentParser(description="Joszaki lead pontozó")
    parser.add_argument(
        "--input",
        default=None,
        help="Konkrét CSV fájl (alapért.: az összes /raw_leads/*.csv)",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=0,
        help="Csak ennyi pont feletti leadek mentése (alapért.: 0)",
    )
    args = parser.parse_args()

    if args.input:
        csv_files = [args.input]
    else:
        pattern   = os.path.join(RAW_LEADS_DIR, "*.csv")
        csv_files = sorted(glob.glob(pattern))

    if not csv_files:
        print("Nincs CSV fájl a feldolgozáshoz.")
        return

    total_processed = 0
    total_saved     = 0

    for csv_path in csv_files:
        print(f"\nFeldolgozás: {os.path.basename(csv_path)}")
        try:
            leads = load_csv(csv_path)
        except Exception as e:
            print(f"  [HIBA] {e}")
            continue

        scored = [score_lead(l) for l in leads]
        filtered = [l for l in scored if l["score"] >= args.min_score]

        a_count = sum(1 for l in filtered if l["grade"] == "A")
        b_count = sum(1 for l in filtered if l["grade"] == "B")
        c_count = sum(1 for l in filtered if l["grade"] == "C")

        print(f"  {len(leads)} lead → A: {a_count} | B: {b_count} | C: {c_count}")

        if filtered:
            out_path = save_scored(filtered, csv_path)
            print(f"  Mentve: {out_path}")
            total_saved += len(filtered)

        total_processed += len(leads)

    print(f"\nÖsszesítés: {total_processed} lead feldolgozva, {total_saved} mentve.")


if __name__ == "__main__":
    main()
