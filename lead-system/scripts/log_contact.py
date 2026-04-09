#!/usr/bin/env python3
"""
Contact Logger
Log outreach attempts and prevent duplicate calls.

Usage (interactive):
    python log_contact.py

Usage (command-line args):
    python log_contact.py --name "Kovács Villanyszerelő" --phone "+36301234567" \
        --method call --outcome interested --notes "Visszahív szerdán"
"""

import argparse
import csv
import os
import sys
from datetime import date, datetime

SENT_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "sent_log")
CONTACTS_FILE = os.path.join(SENT_LOG_DIR, "contacts.csv")

FIELDNAMES = [
    "date",
    "time",
    "business_name",
    "phone",
    "method",
    "outcome",
    "notes",
]

VALID_METHODS = ["call", "sms", "viber", "whatsapp", "email"]
VALID_OUTCOMES = [
    "no_answer",
    "interested",
    "not_interested",
    "callback_scheduled",
    "voicemail",
    "wrong_number",
    "already_has_service",
]


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def ensure_log_file():
    """Create log file with headers if it doesn't exist."""
    os.makedirs(SENT_LOG_DIR, exist_ok=True)
    if not os.path.exists(CONTACTS_FILE):
        with open(CONTACTS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
        print(f"Created new contacts log: {CONTACTS_FILE}")


def load_contacted_phones() -> set[str]:
    """Return set of all phone numbers already in the log."""
    if not os.path.exists(CONTACTS_FILE):
        return set()

    phones = set()
    with open(CONTACTS_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phone = row.get("phone", "").strip()
            if phone:
                phones.add(phone)
    return phones


def normalize_phone(phone: str) -> str:
    """Normalize phone for dedup comparison."""
    import re
    digits = re.sub(r"[^\d]", "", phone)
    # strip leading country code for comparison
    if digits.startswith("36") and len(digits) > 10:
        digits = digits[2:]
    if digits.startswith("06"):
        digits = digits[2:]
    return digits


def is_duplicate(phone: str, contacted_phones: set[str]) -> bool:
    """Check if phone (or its normalized form) was already contacted."""
    norm = normalize_phone(phone)
    for existing in contacted_phones:
        if normalize_phone(existing) == norm:
            return True
    return False


def append_contact(entry: dict):
    """Append a single contact entry to the CSV log."""
    ensure_log_file()
    with open(CONTACTS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writerow(entry)


def log_contact(
    business_name: str,
    phone: str,
    method: str,
    outcome: str,
    notes: str = "",
    contact_date: str = "",
    force: bool = False,
) -> bool:
    """
    Log a contact attempt.
    Returns True if logged successfully, False if duplicate (and not forced).
    """
    ensure_log_file()

    # Validate
    phone = phone.strip()
    if not phone:
        print("ERROR: Phone number is required.")
        return False

    if method not in VALID_METHODS:
        print(f"ERROR: Invalid method '{method}'. Valid: {', '.join(VALID_METHODS)}")
        return False

    if outcome not in VALID_OUTCOMES:
        print(f"ERROR: Invalid outcome '{outcome}'. Valid: {', '.join(VALID_OUTCOMES)}")
        return False

    # Duplicate check
    contacted_phones = load_contacted_phones()
    if not force and is_duplicate(phone, contacted_phones):
        print(f"\n[DUPLICATE] {phone} was already contacted before.")
        print("Use --force to log anyway.")
        return False

    now = datetime.now()
    entry = {
        "date": contact_date or date.today().isoformat(),
        "time": now.strftime("%H:%M"),
        "business_name": business_name.strip(),
        "phone": phone,
        "method": method,
        "outcome": outcome,
        "notes": notes.strip(),
    }

    append_contact(entry)

    print(f"\n[LOGGED] {entry['date']} {entry['time']} | {business_name} | {phone}")
    print(f"         Method: {method} | Outcome: {outcome}")
    if notes:
        print(f"         Notes: {notes}")

    return True


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def choose_from_list(prompt: str, options: list[str]) -> str:
    """Show numbered list and let user pick."""
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        choice = input("Enter number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print("Invalid choice. Try again.")


def interactive_mode():
    print("\n=== CONTACT LOGGER ===")
    print("Log an outreach attempt\n")

    business_name = input("Business name (cég neve): ").strip()
    if not business_name:
        print("Business name is required.")
        sys.exit(1)

    phone = input("Phone number (telefonszám): ").strip()
    if not phone:
        print("Phone number is required.")
        sys.exit(1)

    # Duplicate check early
    contacted_phones = load_contacted_phones()
    if is_duplicate(phone, contacted_phones):
        print(f"\n[WARNING] This phone ({phone}) was already contacted!")
        proceed = input("Log anyway? (y/N): ").strip().lower()
        if proceed != "y":
            print("Aborted.")
            sys.exit(0)
        force = True
    else:
        force = False

    contact_date = input(f"Date (today = {date.today().isoformat()}, press Enter to use today): ").strip()
    if contact_date and not _valid_date(contact_date):
        print("Invalid date format. Using today.")
        contact_date = ""

    method = choose_from_list("Contact method:", VALID_METHODS)
    outcome = choose_from_list("Outcome:", VALID_OUTCOMES)
    notes = input("Notes (megjegyzés, optional): ").strip()

    log_contact(
        business_name=business_name,
        phone=phone,
        method=method,
        outcome=outcome,
        notes=notes,
        contact_date=contact_date,
        force=force,
    )


def _valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Stats command
# ---------------------------------------------------------------------------

def print_stats():
    """Print summary of contacts log."""
    if not os.path.exists(CONTACTS_FILE):
        print("No contacts log found.")
        return

    with open(CONTACTS_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("Contacts log is empty.")
        return

    from collections import Counter
    outcomes = Counter(r["outcome"] for r in rows)
    methods = Counter(r["method"] for r in rows)

    print("\n=== CONTACT LOG STATS ===")
    print(f"Total contacts logged: {len(rows)}")
    print(f"Unique phones: {len(load_contacted_phones())}")
    print("\nBy outcome:")
    for outcome, count in sorted(outcomes.items(), key=lambda x: -x[1]):
        print(f"  {outcome}: {count}")
    print("\nBy method:")
    for method, count in sorted(methods.items(), key=lambda x: -x[1]):
        print(f"  {method}: {count}")
    print(f"\nLog file: {CONTACTS_FILE}")
    print("=========================\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log outreach contact attempts")
    parser.add_argument("--name", help="Business name")
    parser.add_argument("--phone", help="Phone number")
    parser.add_argument("--date", help="Contact date (YYYY-MM-DD, default: today)")
    parser.add_argument("--method", choices=VALID_METHODS, help="Contact method")
    parser.add_argument("--outcome", choices=VALID_OUTCOMES, help="Call outcome")
    parser.add_argument("--notes", default="", help="Optional notes")
    parser.add_argument("--force", action="store_true", help="Log even if duplicate")
    parser.add_argument("--stats", action="store_true", help="Show contact log statistics")

    args = parser.parse_args()

    if args.stats:
        print_stats()
        sys.exit(0)

    # If all required args provided, run non-interactively
    if args.name and args.phone and args.method and args.outcome:
        success = log_contact(
            business_name=args.name,
            phone=args.phone,
            method=args.method,
            outcome=args.outcome,
            notes=args.notes,
            contact_date=args.date or "",
            force=args.force,
        )
        sys.exit(0 if success else 1)
    else:
        # Interactive mode
        interactive_mode()
