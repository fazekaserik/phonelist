#!/usr/bin/env python3
"""
log_contact.py — Kapcsolatfelvétel naplózó
==========================================

Nyilvántartja, hogy melyik leadet mikor kerestük meg,
milyen csatornán és milyen eredménnyel.
Megakadályozza az ismételt megkeresést (duplikáció-védelem).

Használat:
    # Kapcsolatfelvétel naplózása
    python log_contact.py log \
        --name "Kovács János" \
        --phone "+36 1 443 3777 (mellék: 57136)" \
        --channel hivas \
        --result "érdeklődő, visszahív" \
        --package "Full Csomag"

    # Már megkeresett? (ellenőrzés telefonszámra)
    python log_contact.py check --phone "+36 1 443 3777 (mellék: 57136)"

    # Összes napló listázása
    python log_contact.py list

    # Statisztikák
    python log_contact.py stats

Csatornák: hivas, sms, email, facebook, egyeb
Eredmények: erdeklodo, visszahiv, nem_erdekli, nem_vette_fel, hibas_szam, egyeb
"""

import argparse
import csv
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR     = os.path.dirname(__file__)
SENT_LOG_DIR = os.path.join(BASE_DIR, "..", "sent_log")
LOG_FILE     = os.path.join(SENT_LOG_DIR, "contacts.csv")

VALID_CHANNELS = ["hivas", "sms", "email", "facebook", "egyeb"]
VALID_RESULTS  = [
    "erdeklodo", "visszahiv", "nem_erdekli",
    "nem_vette_fel", "hibas_szam", "egyeb",
]

LOG_FIELDS = [
    "timestamp",
    "name",
    "phone",
    "channel",
    "result",
    "package",
    "notes",
    "logged_by",
]

# ---------------------------------------------------------------------------
# Napló kezelése
# ---------------------------------------------------------------------------

def _ensure_log_file():
    os.makedirs(SENT_LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=LOG_FIELDS).writeheader()


def _load_log() -> list[dict]:
    _ensure_log_file()
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _phone_key(phone: str) -> str:
    """Normalizált kulcs összehasonlításhoz (csak számjegyek + mellékszám)."""
    return phone.strip().lower().replace(" ", "")


# ---------------------------------------------------------------------------
# Parancsok
# ---------------------------------------------------------------------------

def cmd_log(args):
    """Új kapcsolatfelvétel rögzítése."""
    _ensure_log_file()

    # Duplikáció ellenőrzés
    existing = _load_log()
    phone_key = _phone_key(args.phone)
    already_contacted = [
        e for e in existing
        if _phone_key(e.get("phone", "")) == phone_key
    ]

    if already_contacted and not args.force:
        last = already_contacted[-1]
        print(f"[FIGYELEM] Ez a szám már meg lett keresve!")
        print(f"  Utoljára: {last['timestamp']} | {last['channel']} | {last['result']}")
        print(f"  Ha mégis naplózni szeretnéd, add hozzá a --force kapcsolót.")
        return

    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name":      args.name,
        "phone":     args.phone,
        "channel":   args.channel,
        "result":    args.result,
        "package":   args.package or "",
        "notes":     args.notes or "",
        "logged_by": args.logged_by or "cli",
    }

    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        writer.writerow(entry)

    print(f"Naplózva: {entry['name']} | {entry['phone']} | {entry['channel']} | {entry['result']}")


def cmd_check(args):
    """Ellenőrzés: volt-e már kapcsolatfelvétel ezzel a számmal."""
    existing = _load_log()
    phone_key = _phone_key(args.phone)
    hits = [e for e in existing if _phone_key(e.get("phone", "")) == phone_key]

    if hits:
        print(f"IGEN — {len(hits)}x megkeresve:")
        for h in hits:
            print(f"  {h['timestamp']} | {h['channel']} | {h['result']} | {h['notes']}")
    else:
        print("NEM — ez a szám még nem volt megkeresve.")


def cmd_list(args):
    """Összes naplóbejegyzés listázása."""
    entries = _load_log()
    if not entries:
        print("A napló üres.")
        return

    # Opcionális szűrés
    if args.channel:
        entries = [e for e in entries if e.get("channel") == args.channel]
    if args.result:
        entries = [e for e in entries if e.get("result") == args.result]

    # Rendezés: legújabb elöl
    entries = sorted(entries, key=lambda x: x.get("timestamp", ""), reverse=True)

    if args.limit:
        entries = entries[:args.limit]

    print(f"{'Időpont':<20} {'Név':<25} {'Telefon':<40} {'Csatorna':<10} {'Eredmény':<15}")
    print("-" * 115)
    for e in entries:
        print(
            f"{e.get('timestamp',''):<20} "
            f"{e.get('name','')[:24]:<25} "
            f"{e.get('phone','')[:39]:<40} "
            f"{e.get('channel',''):<10} "
            f"{e.get('result',''):<15}"
        )
    print(f"\nÖsszes bejegyzés: {len(entries)}")


def cmd_stats(args):
    """Összesített statisztikák."""
    entries = _load_log()
    if not entries:
        print("A napló üres.")
        return

    total = len(entries)
    by_result  = {}
    by_channel = {}
    by_package = {}

    for e in entries:
        r = e.get("result", "ismeretlen")
        c = e.get("channel", "ismeretlen")
        p = e.get("package", "ismeretlen")
        by_result[r]  = by_result.get(r, 0) + 1
        by_channel[c] = by_channel.get(c, 0) + 1
        by_package[p] = by_package.get(p, 0) + 1

    print(f"=== Napló statisztikák ===")
    print(f"Összes kapcsolatfelvétel: {total}\n")

    print("Eredmény szerint:")
    for k, v in sorted(by_result.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>5} ({100*v//total}%)")

    print("\nCsatorna szerint:")
    for k, v in sorted(by_channel.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>5}")

    print("\nCsomag szerint:")
    for k, v in sorted(by_package.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>5}")

    # Érdeklődők és visszahívandók kiemelése
    hot = by_result.get("erdeklodo", 0) + by_result.get("visszahiv", 0)
    print(f"\nMeleg leadek (érdeklődő + visszahív): {hot}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Kapcsolatfelvétel napló")
    sub = parser.add_subparsers(dest="command", required=True)

    # log
    p_log = sub.add_parser("log", help="Kapcsolatfelvétel rögzítése")
    p_log.add_argument("--name",      required=True, help="Lead neve")
    p_log.add_argument("--phone",     required=True, help="Telefonszám")
    p_log.add_argument("--channel",   required=True, choices=VALID_CHANNELS,
                       help=f"Csatorna: {', '.join(VALID_CHANNELS)}")
    p_log.add_argument("--result",    required=True, choices=VALID_RESULTS,
                       help=f"Eredmény: {', '.join(VALID_RESULTS)}")
    p_log.add_argument("--package",   help="Ajánlott csomag (pl. 'Full Csomag')")
    p_log.add_argument("--notes",     help="Megjegyzés")
    p_log.add_argument("--logged-by", help="Naplózó neve")
    p_log.add_argument("--force",     action="store_true",
                       help="Kényszer-naplózás duplikáció esetén is")

    # check
    p_check = sub.add_parser("check", help="Ellenőrzés: volt-e már megkeresve")
    p_check.add_argument("--phone", required=True, help="Telefonszám ellenőrzése")

    # list
    p_list = sub.add_parser("list", help="Naplóbejegyzések listázása")
    p_list.add_argument("--channel", choices=VALID_CHANNELS, help="Szűrés csatornára")
    p_list.add_argument("--result",  choices=VALID_RESULTS,  help="Szűrés eredményre")
    p_list.add_argument("--limit",   type=int, default=50,   help="Max sorok száma")

    # stats
    sub.add_parser("stats", help="Összesített statisztikák")

    args = parser.parse_args()

    if args.command == "log":
        cmd_log(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
