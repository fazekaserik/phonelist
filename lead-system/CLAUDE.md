# Erik Marketing — Lead Generáló Rendszer

## Üzleti háttér

**Cég:** Erik Marketing  
**Helyszín:** Budapest / Magyarország  
**Célpiac:** Helyi kézművesek és szolgáltató vállalkozások

### Eladott termékek

#### 1. "Full Csomag"
- **Célcsoport:** Vállalkozások, amelyeknek **NINCS weboldaluk**
- **Tartalom:** Weboldal + Google Térképes megjelenés + branding + AI SEO
- **Pitch:** Az egész online jelenlétet nulláról felépítjük

#### 2. "AI SEO"
- **Célcsoport:** Vállalkozások, amelyeknek **VAN weboldaluk**, de **nem jelennek meg ChatGPT/Gemini** válaszaiban
- **Tartalom:** AI-optimalizált SEO, hogy a vállalkozás megjelenjen, ha valaki pl. "budapest villanyszerelés" iránt érdeklődik AI asszisztensnél

### Csomag-döntési logika

| Helyzet | Javasolt csomag |
|---|---|
| Nincs weboldal | Full Csomag |
| Van elavult/egyszerű weboldal | Full Csomag VAGY AI SEO |
| Van rendes weboldal, de AI-ban nem látható | AI SEO |
| Bizonytalan | Mindkettő — küldj árajánlatot |

---

## Adatforrás: joszaki.hu

### Telefonszám formátum — KRITIKUS

A joszaki.hu minden szakember profilhoz **közvetítő (proxy) telefonszámot** ad meg:

- **Központi szám:** `+36 1 443 3777`
- **Mellékszám:** 4–6 jegyű egyedi szám (a proxy hívás URL-ből vagy a megjelenített szövegből kerül kinyerésre)

**Kötelező formátum a CSV-ben:**
```
+36 1 443 3777 (mellék: XXXXX)
```

**Ha nincs mellékszám → a `phone` mező ÜRES marad** (nem írunk be hiányos adatot).

A mellékszám kinyerési módszerek (sorrendben):
1. Hálózati kérés interceptálása: Playwright `page.on("request")` — proxy URL-ben lévő szám
2. JavaScript szöveges keresés: `"+36 1 443 3777 / XXXXX"` formátumú szövegcsomópont

---

## Mappastruktúra

```
/lead-system
  /raw_leads       ← Scraped CSV fájlok (timestamp névvel)
  /scored_leads    ← Pontozás utáni CSV-k (score + grade + package)
  /outreach        ← Hívási scriptek, üzenet sablonok
  /sent_log        ← Kapcsolatfelvételi napló (contacts.csv)
  /scripts         ← Python scriptek
```

---

## Scriptek

### `joszaki_scraper.py` — Fő scraper
```bash
# Egy iparág
python scripts/joszaki_scraper.py villanyszerelo --pages 5

# Összes iparág
python scripts/joszaki_scraper.py all --pages 5
```

**Működés:**
1. FÁZIS 1: Listázó oldalak végigolvasása → profil URL-ek gyűjtése
2. FÁZIS 2: Profiloldalak felkeresése → mellékszám, weboldal, értékelések kinyerése
3. 2–3 másodperces delay minden lépés között
4. CSV mentés `/raw_leads/leads_{kategória}_budapest_{timestamp}.csv`

**Kimenet mezők:**
| Mező | Leírás |
|---|---|
| `name` | Szakember neve (URL slug-ból) |
| `phone` | `+36 1 443 3777 (mellék: XXXXX)` vagy üres |
| `extension` | Csak a mellékszám (pl. `57136`) |
| `website` | Weboldal URL (ha van) |
| `google_reviews` | Értékelések száma |
| `industry` | Iparág neve (pl. `Villanyszerelő`) |
| `profile_url` | Joszaki.hu profil URL |
| `scraped_at` | Scraping időpontja |

### `score_leads.py` — Lead pontozó
```bash
# Összes raw CSV pontozása
python scripts/score_leads.py

# Minimum pont szűrővel
python scripts/score_leads.py --min-score 50

# Konkrét fájl
python scripts/score_leads.py --input ../raw_leads/leads_villanyszerelo_budapest_20260412.csv
```

**Pontozási rendszer (összesen 100 pt):**
| Szempont | Max pont | Leírás |
|---|---|---|
| Weboldal státusz | 35 | Nincs weboldal = 35 pt |
| Iparági illeszkedés | 20 | Villanyszerelő = 20 pt (legjobb) |
| Földrajz | 15 | Budapest = 15 pt |
| Google értékelések | 20 | 50+ értékelés = 20 pt |
| Kontakt minőség | 10 | Van telefon + profil URL |

**Osztályzatok:**
- **A (60–100):** Azonnal hívható
- **B (40–59):** Második kör
- **C (0–39):** Alacsony prioritás

### `run_scraper.py` — Automatikus futtatás
```bash
# Összes iparág, 5 oldal
python scripts/run_scraper.py

# Csak megadott iparágak
python scripts/run_scraper.py --only villanyszerelo komuves --pages 3

# Pontozás nélkül
python scripts/run_scraper.py --skip-scoring
```

### `log_contact.py` — Kapcsolatfelvétel napló
```bash
# Naplózás
python scripts/log_contact.py log \
  --name "Kovács János" \
  --phone "+36 1 443 3777 (mellék: 57136)" \
  --channel hivas \
  --result erdeklodo \
  --package "Full Csomag"

# Ellenőrzés (volt-e már megkeresve)
python scripts/log_contact.py check --phone "+36 1 443 3777 (mellék: 57136)"

# Lista
python scripts/log_contact.py list

# Statisztikák
python scripts/log_contact.py stats
```

---

## GitHub Actions — Napi Automatizálás

**Fájl:** `.github/workflows/scrape.yml`  
**Futás:** Naponta **08:00 UTC** (= 10:00 Budapest nyári időszámítás / 09:00 téli)

**Folyamat:**
1. Python 3.11 + Playwright Chromium telepítése
2. `run_scraper.py --pages 3` futtatása (összes 7 iparág)
3. Új CSV fájlok commit + push → `raw_leads/` és `scored_leads/`

---

## Célzott iparágak (prioritás sorrendben)

1. Villanyszerelők
2. Vízvezetékszerelők
3. Festők / tapétázók
4. Kőművesek
5. Klímaszerelők
6. Autószerelők
7. Takarítók

---

## Telepítés

```bash
pip install -r lead-system/requirements.txt
playwright install chromium
```

## Gyors start

```bash
cd lead-system/scripts
python run_scraper.py --pages 2
```
