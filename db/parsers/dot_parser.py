"""
US DOT Hazardous Materials Table (49 CFR 172.101) — database loader.

Download URL:
  The DOT HMT is published as part of the CFR and also as a CSV/Excel
  download from the Pipeline and Hazardous Materials Safety Administration:
  https://www.phmsa.dot.gov/training/hazmat/erg/emergency-response-guidebook-erge

We parse the HMT CSV which PHMSA publishes. The columns are:
  Symbol | Hazardous Materials Description | Hazard Class/Division |
  ID Number | PG | Label(s) | Special Provisions | ...

The UN/ID number is the key that ties transport data to chemicals.
We also load the ERG (Emergency Response Guidebook) mapping.

For chemicals we can match by CAS number from the supplemental CAS
cross-reference list that PHMSA publishes alongside the HMT.
"""

import csv
import json
import os
import re
import sqlite3
from datetime import datetime, timezone


# Column positions in the standard PHMSA HMT CSV export
# (varies slightly between annual editions — we handle by column name)
COL_ALIASES = {
    'description':   ['Hazardous Materials Descriptions and Proper Shipping Names',
                      'Proper Shipping Name', 'Description'],
    'hazard_class':  ['Hazard Class or Division', 'Class/Division', 'Class'],
    'un_id':         ['Identification Numbers', 'ID Number', 'UN Number', 'UN/NA'],
    'packing_group': ['Packing Group', 'PG'],
    'labels':        ['Label Codes', 'Labels Required', 'Labels'],
    'special_prov':  ['Special Provisions', 'SP'],
    'marine_poll':   ['Marine Pollutants', 'Marine Pollutant'],
}


def load(db_path, csv_path, cas_xref_path=None):
    """
    Load the DOT HMT CSV into the master database.

    Args:
        db_path:       path to chemicals_master.db
        csv_path:      path to the HMT CSV file
        cas_xref_path: optional path to CAS cross-reference CSV
    """
    if not os.path.exists(csv_path):
        print(f"DOT HMT CSV not found: {csv_path}")
        print("Run: python3 db_update.py --source dot")
        return 0

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    # Load CAS cross-reference first if available
    cas_to_un = {}
    un_to_cas = {}
    if cas_xref_path and os.path.exists(cas_xref_path):
        cas_to_un, un_to_cas = _load_cas_xref(cas_xref_path)
        print(f"  CAS xref: {len(cas_to_un):,} entries")

    # Parse HMT
    loaded = 0
    skipped = 0

    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        col_map = _map_columns(headers)

        for row in reader:
            un_id = _get(row, col_map, 'un_id', '').strip().upper()
            if not un_id or not re.match(r'^(UN|NA)\d{4}$', un_id):
                skipped += 1
                continue

            description  = _get(row, col_map, 'description', '').strip()
            hazard_class = _get(row, col_map, 'hazard_class', '').strip()
            pg           = _get(row, col_map, 'packing_group', '').strip()
            labels       = _get(row, col_map, 'labels', '').strip()
            special_prov = _get(row, col_map, 'special_prov', '').strip()
            marine_raw   = _get(row, col_map, 'marine_poll', '').strip()
            marine       = 1 if marine_raw.upper() in ('YES', 'Y', 'P', 'PP', 'TRUE', '1') else 0

            # Try to find matching CAS numbers from the xref
            cas_list = un_to_cas.get(un_id.replace('UN', '').replace('NA', ''), [])

            now = datetime.now(timezone.utc).isoformat()

            if cas_list:
                for cas in cas_list:
                    _upsert_transport(conn, cas, un_id, hazard_class, pg, labels,
                                      special_prov, marine, now)
            else:
                # Store as a standalone transport entry keyed by UN number
                # (will be linked to CAS when chemicals are loaded later)
                _store_un_entry(conn, un_id, description, hazard_class, pg,
                                labels, special_prov, marine, now)

            loaded += 1

    conn.execute(
        'INSERT INTO import_log (source, filename, rows_loaded) VALUES (?,?,?)',
        ('DOT HMT 49 CFR 172.101', os.path.basename(csv_path), loaded)
    )
    conn.commit()
    conn.close()

    print(f"DOT HMT: loaded {loaded:,} entries, skipped {skipped:,}")
    return loaded


def _upsert_transport(conn, cas, un_number, hazard_class, pg, labels,
                      special_prov, marine, now):
    conn.execute('''
        UPDATE chemicals SET
            un_number        = COALESCE(NULLIF(un_number, ''), ?),
            dot_hazard_class = COALESCE(NULLIF(dot_hazard_class, ''), ?),
            dot_packing_group = COALESCE(NULLIF(dot_packing_group, ''), ?),
            dot_labels       = COALESCE(NULLIF(dot_labels, ''), ?),
            dot_special_prov = COALESCE(NULLIF(dot_special_prov, ''), ?),
            marine_pollutant = MAX(marine_pollutant, ?),
            last_updated     = ?
        WHERE cas_number = ?
    ''', (un_number, hazard_class, pg, labels, special_prov, marine, now, cas))


def _store_un_entry(conn, un_id, description, hazard_class, pg,
                    labels, special_prov, marine, now):
    """Store a UN-keyed transport entry for later CAS matching."""
    # We store these in a separate table so they can be joined later
    conn.execute('''
        CREATE TABLE IF NOT EXISTS dot_un_entries (
            un_number        TEXT,
            description      TEXT,
            dot_hazard_class TEXT,
            dot_packing_group TEXT,
            dot_labels       TEXT,
            dot_special_prov TEXT,
            marine_pollutant INTEGER DEFAULT 0,
            loaded_at        TEXT,
            PRIMARY KEY (un_number, description)
        )
    ''')
    conn.execute('''
        INSERT OR REPLACE INTO dot_un_entries
            (un_number, description, dot_hazard_class, dot_packing_group,
             dot_labels, dot_special_prov, marine_pollutant, loaded_at)
        VALUES (?,?,?,?,?,?,?,?)
    ''', (un_id, description[:512], hazard_class, pg, labels, special_prov, marine, now))


def lookup_by_un(db_path, un_number):
    """Return all HMT entries for a given UN number."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute('''
            SELECT * FROM dot_un_entries WHERE un_number = ?
        ''', (un_number.upper(),)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _map_columns(headers):
    """Return a dict mapping logical field names to actual CSV column names."""
    mapping = {}
    for field, aliases in COL_ALIASES.items():
        for alias in aliases:
            for h in headers:
                if alias.lower() in h.lower():
                    mapping[field] = h
                    break
            if field in mapping:
                break
    return mapping


def _get(row, col_map, field, default=''):
    col = col_map.get(field)
    if col:
        return row.get(col, default) or default
    return default


def _load_cas_xref(path):
    """Load CAS ↔ UN number cross-reference CSV.
    Expected columns: CAS, UN (or UN_Number).
    """
    cas_to_un = {}
    un_to_cas = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            cas = row.get('CAS', '') or row.get('cas_number', '')
            un  = row.get('UN', '') or row.get('UN_Number', '') or row.get('un_number', '')
            cas = cas.strip()
            un  = un.strip().lstrip('0')
            if cas and un:
                cas_to_un.setdefault(cas, []).append(un)
                un_to_cas.setdefault(un, []).append(cas)
    return cas_to_un, un_to_cas
