#!/usr/bin/env python3
"""
Batch Lead Scraper
Loops through all target industries and runs the scraper for each.

Usage:
    python run_scraper.py
    python run_scraper.py --source joszaki --pages 5
    python run_scraper.py --source all --score   # auto-score each file after scraping
"""

import argparse
import os
import time

from scraper import scrape, save_leads


INDUSTRIES = [
    "villanyszerelő budapest",
    "vízszerelő budapest",
    "festő budapest",
    "kőműves budapest",
    "klímaszerelő budapest",
    "autószerelő budapest",
    "takarítás budapest",
    "vízvezetékszerelő budapest",
    "burkoló budapest",
    "kertész budapest",
]

DELAY_BETWEEN_QUERIES = 5  # seconds between different industry queries


def main():
    parser = argparse.ArgumentParser(description="Run scraper for all target industries")
    parser.add_argument(
        "--source",
        default="all",
        choices=["all", "joszaki", "jofogas", "ceginfo"],
        help="Which source to scrape (default: all)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=3,
        help="Pages per source per industry (default: 3)",
    )
    parser.add_argument(
        "--score",
        action="store_true",
        help="Automatically run scorer.py on each output CSV",
    )
    args = parser.parse_args()

    saved_files = []
    total_leads = 0

    print("=" * 60)
    print("HUNGARIAN LEAD SCRAPER — BATCH RUN")
    print(f"Industries: {len(INDUSTRIES)}")
    print(f"Source: {args.source} | Pages: {args.pages}")
    print("=" * 60)

    for i, query in enumerate(INDUSTRIES, 1):
        print(f"\n[{i}/{len(INDUSTRIES)}] Query: {query}")
        print("-" * 40)

        leads = scrape(query, source=args.source, pages=args.pages)
        total_leads += len(leads)

        if leads:
            filepath = save_leads(leads, query)
            saved_files.append(filepath)
        else:
            print(f"  No leads found for: {query}")

        if i < len(INDUSTRIES):
            print(f"\nWaiting {DELAY_BETWEEN_QUERIES}s before next query...")
            time.sleep(DELAY_BETWEEN_QUERIES)

    print("\n" + "=" * 60)
    print("BATCH SCRAPING COMPLETE")
    print(f"Total leads collected: {total_leads}")
    print(f"Files saved: {len(saved_files)}")
    for f in saved_files:
        print(f"  - {f}")
    print("=" * 60)

    if args.score and saved_files:
        print("\nAuto-scoring all collected files...")
        import subprocess
        scorer_path = os.path.join(os.path.dirname(__file__), "scorer.py")
        for filepath in saved_files:
            print(f"\nScoring: {filepath}")
            subprocess.run(["python3", scorer_path, filepath], check=False)


if __name__ == "__main__":
    main()
