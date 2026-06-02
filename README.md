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

### 2. Seed the chemical cache (requires internet access)
```bash
python3 seed_chemicals.py
```
This pre-loads ~25 common paint chemicals so lookups are instant during lab use.

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

The `--timeout 120` is important — PubChem lookups for new chemicals can take
10–30 seconds the first time (results are cached after that).

## Data Sources

| Data | Source |
|------|--------|
| Chemical identification (CAS, IUPAC name) | PubChem PUG REST API |
| Physical/chemical properties | PubChem PUG REST API |
| GHS hazard/precautionary statements | PubChem PUG View API |
| Occupational exposure limits | PubChem / NIOSH Pocket Guide |
| GHS classification rules | UN GHS Rev 9 / OSHA HazCom 2012 |
| Transport information | DOT 49 CFR / TDG (template — must verify) |

## Important Disclaimer

This tool generates **draft** SDS documents from publicly available data.
All output **must be reviewed and approved by a qualified safety professional** before
use for shipping, labeling, or regulatory compliance. Transport classification (Section 14)
in particular requires verification by a qualified dangerous goods specialist.

## Project Structure

```
├── run.py                  # Flask entry point
├── requirements.txt
├── seed_chemicals.py       # Pre-populate cache for common chemicals
└── app/
    ├── __init__.py
    ├── routes.py           # URL handlers + AJAX lookup endpoint
    ├── pubchem.py          # PubChem API client + SQLite cache
    ├── hazard_data.py      # H/P statement dictionaries, pictogram mapping
    ├── sds_generator.py    # 16-section GHS SDS builder
    ├── pdf_generator.py    # ReportLab PDF generation
    ├── cache/              # SQLite cache (auto-created)
    ├── templates/
    └── static/
```
