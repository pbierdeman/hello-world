# Chemical SDS / MSDS Generator

A Flask web application that generates GHS-compliant **Safety Data Sheets (SDS)** for
paint formulas and chemical mixtures. Chemical hazard data is automatically fetched from
[PubChem (NCBI/NIH)](https://pubchem.ncbi.nlm.nih.gov/) and cached locally.

## Features

- **16-section GHS SDS** per OSHA HazCom 2012 (US) or WHMIS 2015 (Canada)
- **PubChem integration** — auto-fills GHS hazard statements, pictograms, flash point,
  physical properties, and occupational exposure limits
- **Mixture classification** — aggregates hazards across all components using GHS mixing rules
- **Professional PDF output** — print-ready, colour-coded, includes disclaimer
- **Local SQLite cache** — repeated lookups are instant; no repeated API calls
- **Multi-user** — runs as a shared lab server; handles concurrent requests
- **Live formula builder** — add/remove components, CAS lookup before generating

## Regulatory Compliance

| Country | Standard | Transport |
|---------|----------|-----------|
| United States | OSHA HazCom 2012 (GHS Rev 9) | DOT 49 CFR |
| Canada | WHMIS 2015 (GHS aligned) | TDG SOR/2001-286 |

## Quick Start

### 1. Install dependencies
```bash
pip3 install -r requirements.txt
```

### 2. Build the chemical database

**a) Load the bundled chemicals (instant, no internet):**
```bash
python3 db_update.py
```
Loads ~35 common paint/coating chemicals (solvents, pigments, extenders) that are
bundled with the app. It also *attempts* the ECHA / NIOSH / DOT downloads, but those
government sources frequently block automated access — the bundled set guarantees a
working app regardless.

**b) Grow to thousands of chemicals from PubChem (recommended, requires internet):**
```bash
python3 db_update.py --source pubchem
```
Pages through PubChem's public **GHS Classification** index — tens of thousands of
chemicals with H-codes, signal words, and P-codes — and resolves CAS numbers. This
takes several minutes (it makes many rate-limited API calls), so it is **opt-in**.

For a quick partial load to try it out:
```bash
python3 db_update.py --source pubchem --max-pages 5
```

**c) Add occupational exposure limits — Section 8 data (optional):**
```bash
python3 db_update.py --source niosh
```
Pulls OSHA PEL, NIOSH REL, IDLH, and ACGIH TLV from PubChem's NIOSH/OSHA-sourced
annotations and merges them into the chemicals you already loaded (it only fills
empty fields, never overwriting curated data). Run it *after* `--source pubchem`.

> The CDC's bulk NIOSH Pocket Guide file was removed in their 2024 site
> reorganization, so this data now comes from PubChem's mirror of the same
> authoritative NIOSH/OSHA values. If you have an official NPG JSON export on
> disk, you can still load it with `--source niosh-file --no-download`.

After loading, lookups for any stored chemical are **instant and offline**.
Check what you have at any time:
```bash
python3 db_update.py --stats
```

### 3. Run the server
```bash
python3 run.py          # development mode
# or
gunicorn run:app -w 4   # production (4 workers for concurrent users)
```

Open `http://localhost:5000` in your browser.

## Production Deployment

For a shared lab server, use gunicorn behind nginx:

```bash
gunicorn run:app \
  --workers 4 \
  --bind 0.0.0.0:5000 \
  --timeout 120 \
  --access-logfile logs/access.log
```

Run `python3 db_update.py` quarterly to refresh the chemical database from source.

## Data Sources

### Local Database (offline, instant)
| Source | # Chemicals | Data Provided |
|--------|-------------|---------------|
| Bundled dataset | ~35 | Curated paint/coating chemicals — full GHS + OEL + transport, no download |
| [PubChem GHS Classification](https://pubchem.ncbi.nlm.nih.gov) | tens of thousands | GHS H/P codes, signal words, CAS numbers (`--source pubchem`) |
| NIOSH/OSHA exposure limits (via PubChem) | ~hundreds | OSHA PEL, NIOSH REL, IDLH, ACGIH TLV (`--source niosh`) |
| [DOT HMT 49 CFR 172.101](https://www.phmsa.dot.gov) | ~3,200 | UN numbers, hazard class, packing group |
| [ECHA C&L Inventory](https://echa.europa.eu/information-on-chemicals/cl-inventory-database) | ~150,000 | GHS classifications, H/P codes, signal words |

> **Note:** ECHA, NIOSH, and DOT publish their bulk files behind portals that block
> automated downloads. The **bundled** dataset works with zero setup, and
> **`--source pubchem`** is the recommended way to reach thousands of chemicals.

### Online Fallback (requires internet)
| Source | Data Provided |
|--------|---------------|
| [PubChem](https://pubchem.ncbi.nlm.nih.gov) | Physical properties, additional GHS data for unlisted chemicals |

## Lookup Priority
```
1. In-memory cache (previous lookups this session)
2. Local master database  ← ECHA + NIOSH + DOT, works offline
3. PubChem API            ← fallback for chemicals not in local DB
```

## Important Disclaimer

This tool generates **draft** SDS documents from publicly available data.
All output **must be reviewed and approved by a qualified safety professional** before
use for shipping, labeling, or regulatory compliance. Transport classification (Section 14)
in particular requires verification by a qualified dangerous goods specialist.

## Project Structure

```
├── run.py                  # Flask entry point
├── requirements.txt
├── db_update.py            # Download + load ECHA / NIOSH / DOT databases
├── seed_chemicals.py       # Quick seed for common paint chemicals
├── db/
│   ├── schema.py           # Master DB schema + lookup helpers
│   ├── chemicals_master.db # Built by db_update.py (not committed to git)
│   ├── downloads/          # Raw downloaded source files (cached)
│   └── parsers/
│       ├── bundled_parser.py # ~35 curated paint chemicals (no download)
│       ├── pubchem_bulk.py   # Bulk loader: PubChem GHS index (thousands)
│       ├── niosh_pubchem.py  # Exposure limits (PEL/REL/IDLH/TLV) via PubChem
│       ├── echa_parser.py    # ECHA C&L CSV parser
│       ├── niosh_parser.py   # NIOSH Pocket Guide JSON (manual official file)
│       └── dot_parser.py     # DOT HMT CSV parser
└── app/
    ├── __init__.py
    ├── routes.py           # URL handlers + AJAX lookup endpoint
    ├── pubchem.py          # Lookup: cache → master DB → PubChem API
    ├── hazard_data.py      # H/P statement text, pictogram mapping
    ├── sds_generator.py    # 16-section GHS SDS builder
    ├── pdf_generator.py    # ReportLab PDF generation
    ├── cache/              # Session lookup cache (auto-created)
    ├── templates/
    └── static/
```
