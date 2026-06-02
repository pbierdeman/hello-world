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

### 2. Build the chemical database (one-time, requires internet)
```bash
python3 db_update.py
```
Downloads and loads ECHA C&L (~150k chemicals), NIOSH Pocket Guide (~700),
and DOT Hazardous Materials Table (~3,200) into a local SQLite database.
After this, lookups for any of those chemicals are **instant and offline**.

For quick startup with just common paint chemicals:
```bash
python3 seed_chemicals.py   # ~25 chemicals, much faster
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
| [ECHA C&L Inventory](https://echa.europa.eu/information-on-chemicals/cl-inventory-database) | ~150,000 | GHS classifications, H/P codes, signal words |
| [NIOSH Pocket Guide](https://www.cdc.gov/niosh/npg/) | ~700 | OSHA PEL, NIOSH REL, IDLH, physical properties |
| [DOT HMT 49 CFR 172.101](https://www.phmsa.dot.gov) | ~3,200 | UN numbers, hazard class, packing group |

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
│       ├── echa_parser.py  # ECHA C&L CSV parser
│       ├── niosh_parser.py # NIOSH Pocket Guide JSON parser
│       └── dot_parser.py   # DOT HMT CSV parser
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
