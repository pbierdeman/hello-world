# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A multi-user Flask web application for a paint lab that generates GHS-compliant 16-section Safety Data Sheets (SDS/MSDS) from chemical formulas. Users enter a product name and a list of chemical components (name or CAS number + weight %), the app looks up each chemical's hazard data, aggregates GHS classifications using mixture rules, and returns a print-ready PDF.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start dev server
python run.py

# Production (4 workers)
gunicorn run:app -w 4

# Load bundled chemicals (28 common paint chemicals, instant, no internet)
python db_update.py

# Bulk-load thousands of chemicals from PubChem GHS index (requires internet, takes minutes)
python db_update.py --source pubchem
python db_update.py --source pubchem --max-pages 5   # quick partial load

# Add OSHA PEL / NIOSH REL / IDLH / ACGIH TLV (run after pubchem bulk)
python db_update.py --source niosh

# Check database statistics
python db_update.py --stats

# Load a single source
python db_update.py --source bundled|pubchem|niosh|dot|echa|niosh-file
```

There are no tests and no linter configured.

## Architecture

### Request flow

```
Browser form  →  POST /generate
                    routes.py: validate form, call lookup_chemical() for each component
                    sds_generator.py: build_sds() → structured SDS dict (s1–s16)
                    pdf_generator.py: generate_pdf() → PDF bytes
                 ← PDF download
```

### Chemical lookup chain (pubchem.py)

`lookup_chemical(identifier)` checks three layers in order, returning on first hit:

1. **SQLite session cache** (`app/cache/chemicals.db`) — plain key/value, persists across requests
2. **Master database** (`db/chemicals_master.db`) — built offline by `db_update.py`, covers all pre-loaded chemicals
3. **PubChem live API** — fallback for unknown chemicals; result is written back to cache

The master database is built separately from the app. The app never writes to it.

### SDS generation (sds_generator.py)

`build_sds(form_data, component_lookups)` does two critical things before building sections:

- **`_build_component_list`** — pairs form inputs (name, CAS, %) with lookup results
- **`_aggregate_hazards`** — applies GHS mixture classification rules: H-codes from a component are inherited by the mixture if the component is ≥1% (≥0.1% for CMR codes in `hazard_data.CMR_CODES`). This drives all hazard-related sections (2, 4–8, 10–12, 14–15).

The return value is a flat dict with keys `s1`–`s16` (each a dict of labelled strings/lists) plus top-level `components`, `hazards`, and `physical`. `pdf_generator.py` and `sds_generator.py` must agree on this schema.

### PDF generation (pdf_generator.py)

Uses ReportLab Platypus. Each GHS section has a dedicated `_s1()` … `_s16()` function that returns a list of Flowables. Section headers are dark navy (#1a3a5c). The disclaimer box at the end is red.

### Data pipeline (db/ + db_update.py)

`db_update.py` orchestrates multiple source loaders:

| Source key | Loader | Type |
|---|---|---|
| `bundled` | `bundled_parser.py` | Inline — 28 curated chemicals, no download |
| `pubchem` | `pubchem_bulk.py` | API — pages PubChem GHS annotation index |
| `niosh` | `niosh_pubchem.py` | API — pages PubChem IDLH + OEL headings |
| `niosh-file` | `niosh_parser.py` | File — CDC JSON (if manually obtained) |
| `dot` | `dot_parser.py` | File — DOT HMT CSV |
| `echa` | `echa_parser.py` | File — ECHA C&L CSV |

`pubchem` and `niosh` are **opt-in** (not in the default run) because they make many rate-limited API calls. DOT and ECHA direct downloads are frequently 403'd.

`db/schema.py` owns the SQLite schema and helper functions (`lookup_by_cas`, `lookup_by_name`, `row_to_dict`, `stats`). The `chemicals` table uses `cas_number` as the primary key; rows without a resolved CAS get a synthetic `PCID{cid}` key.

All bulk loaders **merge, never clobber** — `ON CONFLICT DO UPDATE` only fills fields that are currently `NULL` or empty, so curated bundled data (which has full OEL/transport) is preserved when the PubChem bulk loader runs over the same CAS.

### Regulatory / GHS data (hazard_data.py)

Static dictionaries: `H_STATEMENTS`, `P_STATEMENTS`, `PICTOGRAM_TRIGGERS` (maps GHS01–GHS09 pictogram IDs to the H-codes that trigger them), `CMR_CODES` (H-codes that use the 0.1% mixture threshold instead of 1%).

### Country / jurisdiction

`COUNTRIES` dict in `sds_generator.py` controls per-jurisdiction strings (standard name, transport authority, emergency number, regulatory refs). Currently US (OSHA HazCom 2012) and CA (WHMIS 2015).

## Key file relationships

- `app/pubchem.py` → `db/chemicals_master.db` (read-only at runtime)
- `app/pubchem.py` → `app/hazard_data.py` (H-code text lookup in `_master_row_to_result`)
- `app/sds_generator.py` → `app/hazard_data.py` (CMR_CODES, P-statement text, pictogram triggers)
- `app/routes.py` → `app/sds_generator.py` → `app/pdf_generator.py` (pipeline)
- `db_update.py` → `db/parsers/*.py` → `db/schema.py` (build-time pipeline)
- `db/parsers/niosh_pubchem.py` → `db/parsers/pubchem_bulk.py` (reuses `_get_json`, `_resolve_cas`)
