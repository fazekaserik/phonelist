# Hungarian Local Business Lead Generation System

## Business Overview

This is a cold outreach lead generation system for a Hungarian marketing agency targeting local tradespeople and service businesses.

## Products We Sell

### 1. "Full Csomag" (Full Package)
- Target: Businesses with **NO website**
- Includes: Website creation + Google Maps optimization + branding + AI SEO
- Pitch: Build their entire online presence from zero

### 2. "AI SEO"
- Target: Businesses WITH a website but **not visible in ChatGPT/Gemini** results
- Includes: AI-optimized SEO so the business appears when people search via AI assistants
- Example trigger: "budapest villanyszerelés" in ChatGPT → they should appear

## Package Assignment Logic

| Situation | Recommended Package |
|---|---|
| No website at all | Full Csomag |
| Has basic/outdated website | Full Csomag OR AI SEO |
| Has decent website, not AI-visible | AI SEO |
| Unclear | Both - árajánlat (send quote for both) |

## Target Market

- **Primary location:** Budapest (all kerületek) and agglomeration
- **Valid area:** All of Hungary (but Budapest is priority)

## Target Industries (priority order)

1. Villanyszerelők (electricians)
2. Vízvezetékszerelők (plumbers)
3. Festők / tapétázók (painters)
4. Kőművesek / építők (builders/masons)
5. Klímaszerelők (HVAC)
6. Bútorozók / lakberendezők (furniture/interior)
7. Autószerelők (mechanics)
8. Takarítók / tisztítók (cleaning services)

## Data Sources

- **jofogás.hu** — classifieds, tradespeople post services
- **joszaki.hu/szakemberek** — Hungarian service provider directory
- **firmania.hu** — Hungarian business directory
- **arany-oldalak.hu** — Hungarian Yellow Pages equivalent
- **céginfo.hu** — Hungarian company registry info
- **Google Maps** — local business search by category

## Folder Structure

```
/lead-system
  /raw_leads        ← CSV files with scraped raw data (timestamped)
  /scored_leads     ← processed JSON with scores + package recommendations
  /outreach         ← call scripts and message templates
  /sent_log         ← log of who was contacted and when
  /scripts          ← all Python scripts
```

## Scripts

| Script | Purpose |
|---|---|
| `scripts/scraper.py` | Scrapes a single query from Hungarian directories |
| `scripts/run_scraper.py` | Loops through all industries automatically |
| `scripts/scorer.py` | Scores raw leads 0-100 and assigns packages |
| `scripts/log_contact.py` | Logs outreach attempts, prevents duplicates |

## Scoring Summary

- **35 pts** — Website status (no website = max points)
- **20 pts** — Industry fit
- **15 pts** — Geography (Budapest = max)
- **20 pts** — Business size signals
- **10 pts** — Contact info quality

Grade A (60-100): Call immediately | Grade B (40-59): Second round | Grade C (0-39): Low priority
