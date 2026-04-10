#!/usr/bin/env python3
"""
Lead Scoring Engine
Scores Hungarian local business leads 0-100 and assigns package recommendations.

Usage:
    python scorer.py <input_csv>
    python scorer.py ../raw_leads/leads_2024-01-01.csv
"""

import csv
import json
import sys
import os
from datetime import date


# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

BUDAPEST_AGGLOMERATION = {
    "budaörs", "érd", "dunakeszi", "szigetszentmiklós", "göd", "fót",
    "vecsés", "gyál", "törökbálint", "szentendre", "visegrád", "vác",
    "gödöllő", "monor", "dabas", "ráckeve", "százhalombatta",
    "biatorbágy", "solymár", "pilisvörösvár", "pomáz", "csobánka",
}

PEST_MEGYE_CITIES = {
    "cegléd", "nagykőrös", "kecskemét", "szolnok", "hatvan", "aszód",
    "albertirsa", "abony", "cegled", "nagykoros",
}

TIER1_INDUSTRIES = {"villanyszerelő", "vízszerelő", "klímaszerelő", "villanyszereló", "vizszereló", "klimaszereló"}
TIER2_INDUSTRIES = {"festő", "kőműves", "burkoló", "tapétázó", "festó", "kómuves", "burkolo", "tapetazo"}
TIER3_INDUSTRIES = {"autószerelő", "takarítás", "kertész", "autoszereló", "takaritas", "kertesz"}

COMPANY_KEYWORDS = {"kft", "bt.", "zrt", "és társa", "group", "team", "csoport"}


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def score_website(lead: dict) -> tuple[int, str]:
    """
    Returns (points, website_status).

    joszaki.hu leads: website field is empty because we only scrape the listing
    page. We genuinely don't know if they have a website — score as "unknown"
    (15 pts) rather than assuming "none" (35 pts / Full Csomag).

    Other sources: empty website field means they likely don't have one.
    """
    website = (lead.get("website") or "").strip().lower()
    source = (lead.get("source") or "").strip().lower()

    if not website or website in ("nincs", "n/a", "-", "none"):
        if "joszaki" in source:
            return 15, "unknown"   # don't know — visit profile to check
        return 35, "none"          # other sources: no website = Full Csomag signal

    has_ssl = website.startswith("https://")
    is_very_short = len(website) < 15

    if not has_ssl or is_very_short:
        return 20, "basic"

    return 10, "decent"


def score_industry(lead: dict) -> int:
    industry_raw = (lead.get("industry") or lead.get("category") or "").strip().lower()

    for keyword in TIER1_INDUSTRIES:
        if keyword in industry_raw:
            return 20

    for keyword in TIER2_INDUSTRIES:
        if keyword in industry_raw:
            return 18

    for keyword in TIER3_INDUSTRIES:
        if keyword in industry_raw:
            return 14

    return 8


def score_geography(lead: dict) -> int:
    city_raw = (lead.get("city") or lead.get("varos") or "").strip().lower()

    if "budapest" in city_raw or "bp." in city_raw or city_raw.startswith("bp"):
        return 15

    if city_raw in BUDAPEST_AGGLOMERATION:
        return 13

    if city_raw in PEST_MEGYE_CITIES:
        return 10

    if city_raw:
        return 6

    return 0


def score_business_size(lead: dict) -> int:
    points = 0
    phone = (lead.get("phone") or lead.get("telefon") or "").strip()
    reviews = lead.get("google_reviews") or lead.get("reviews") or ""
    name = (lead.get("name") or lead.get("nev") or "").lower()

    if phone:
        points += 10

    try:
        review_count = int(str(reviews).strip() or "0")
    except ValueError:
        review_count = 0

    if review_count > 0:
        points += 5
    if review_count >= 10:
        points += 3

    for keyword in COMPANY_KEYWORDS:
        if keyword in name:
            points += 2
            break

    return min(points, 20)


def score_contact_quality(lead: dict) -> tuple[int, bool]:
    """
    Returns (points, disqualified).

    Disqualification rules:
    - No phone AND no email AND no profile URL → disqualify
    - Email-only (no phone) → disqualify (can't cold call)
    - No phone but has joszaki profile URL → keep with 0 pts (C grade, manual follow-up)
    - Phone only → 7 pts
    - Phone + email → 10 pts
    """
    phone = (lead.get("phone") or lead.get("telefon") or "").strip()
    email = (lead.get("email") or "").strip()
    notes = (lead.get("notes") or "").strip()

    has_phone = bool(phone)
    has_email = bool(email)
    has_profile = "profil:" in notes  # joszaki profile URL in notes

    if not has_phone and not has_email and not has_profile:
        return 0, True  # nothing at all → disqualify

    if not has_phone and has_email and not has_profile:
        return 0, True  # email-only, no way to call → disqualify

    if has_phone and has_email:
        return 10, False

    if has_phone:
        return 7, False

    # No phone but has a joszaki profile URL — keep as C grade for manual lookup
    return 0, False


def determine_package(website_status: str, score: int) -> str:
    if website_status == "none":
        return "Full Csomag"

    if website_status == "unknown":
        return "Both - árajánlat"  # check their profile, then decide

    if website_status == "basic":
        if score >= 55:
            return "Full Csomag"
        return "Both - árajánlat"

    # decent website
    return "AI SEO"


def get_grade(score: int) -> str:
    if score >= 60:
        return "A"
    if score >= 40:
        return "B"
    return "C"


# ---------------------------------------------------------------------------
# Main scoring pipeline
# ---------------------------------------------------------------------------

def score_lead(lead: dict) -> dict | None:
    """Returns scored lead dict, or None if disqualified."""
    contact_pts, disqualified = score_contact_quality(lead)
    if disqualified:
        return None

    website_pts, website_status = score_website(lead)
    industry_pts = score_industry(lead)
    geo_pts = score_geography(lead)
    size_pts = score_business_size(lead)

    total = website_pts + industry_pts + geo_pts + size_pts + contact_pts
    total = min(total, 100)

    grade = get_grade(total)
    package = determine_package(website_status, total)

    phone = (lead.get("phone") or lead.get("telefon") or "").strip()
    priority_call = grade == "A" and bool(phone)

    return {
        **lead,
        "website_status": website_status,
        "score": total,
        "grade": grade,
        "recommended_package": package,
        "priority_call": priority_call,
        "score_breakdown": {
            "website": website_pts,
            "industry": industry_pts,
            "geography": geo_pts,
            "business_size": size_pts,
            "contact_quality": contact_pts,
        },
    }


def score_file(input_path: str) -> str:
    if not os.path.exists(input_path):
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        raw_leads = list(reader)

    print(f"Loaded {len(raw_leads)} raw leads from {input_path}")

    scored = []
    disqualified_count = 0

    for lead in raw_leads:
        result = score_lead(lead)
        if result is None:
            disqualified_count += 1
        else:
            scored.append(result)

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Stats
    grade_a = [l for l in scored if l["grade"] == "A"]
    grade_b = [l for l in scored if l["grade"] == "B"]
    grade_c = [l for l in scored if l["grade"] == "C"]
    full_csomag = [l for l in scored if l["recommended_package"] == "Full Csomag"]
    ai_seo = [l for l in scored if l["recommended_package"] == "AI SEO"]
    both = [l for l in scored if l["recommended_package"] == "Both - árajánlat"]
    priority_calls = [l for l in scored if l["priority_call"]]

    print("\n=== SCORING SUMMARY ===")
    print(f"Total raw leads:      {len(raw_leads)}")
    print(f"Disqualified:         {disqualified_count}")
    print(f"Scored leads:         {len(scored)}")
    print(f"  Grade A (azonnal):  {len(grade_a)}")
    print(f"  Grade B (2. kör):   {len(grade_b)}")
    print(f"  Grade C (alacsony): {len(grade_c)}")
    print(f"\nPackage breakdown:")
    print(f"  Full Csomag:        {len(full_csomag)}")
    print(f"  AI SEO:             {len(ai_seo)}")
    print(f"  Both - árajánlat:   {len(both)}")
    print(f"\nPriority calls (A + phone): {len(priority_calls)}")
    print("=======================\n")

    # Save output
    today = date.today().isoformat()
    output_dir = os.path.join(os.path.dirname(__file__), "..", "scored_leads")
    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"scored_{base_name}_{today}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scored, f, ensure_ascii=False, indent=2)

    print(f"Saved scored leads to: {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scorer.py <input_csv>")
        print("Example: python scorer.py ../raw_leads/leads_2024-01-01.csv")
        sys.exit(1)

    score_file(sys.argv[1])
