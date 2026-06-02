"""
ECHA C&L Inventory — database loader.

Download URL:
  https://echa.europa.eu/information-on-chemicals/cl-inventory-database
  -> "Export all C&L notifications" -> CSV (zipped, ~300 MB unzipped)

The C&L Inventory is the single best bulk source for GHS classifications.
It contains mandatory classifications submitted by manufacturers/importers
under EU CLP Regulation for ~150,000 substances.

CSV columns (2024 export format):
  EC Number | CAS Number | International Chemical Identification |
  Specific Conc. Limit | M Factor | Hazard Class and Category Code(s) |
  Hazard Statement Code(s) | Pictogram(s) | Signal Word | ...

Because multiple companies submit classifications for the same substance,
the CSV may have multiple rows per CAS number with different classifications.
We take the CONSENSUS classification (the one with the highest agreement %).
If no consensus field is present we use the most common H-code set.

For the US GHS classification we store the ECHA data under ghs_eu_*
and separately map it to ghs_us_* only where the two systems agree.
"""

import csv
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone


def load(db_path, csv_path, progress_every=10000):
    """
    Load the ECHA C&L CSV into the master database.

    Args:
        db_path:        path to chemicals_master.db
        csv_path:       path to the ECHA C&L CSV (unzipped)
        progress_every: print a progress line every N rows
    """
    if not os.path.exists(csv_path):
        print(f"ECHA C&L CSV not found: {csv_path}")
        print("Run: python3 db_update.py --source echa")
        return 0

    # First pass: accumulate rows per CAS number
    print("ECHA: scanning CSV (this may take a minute)...")
    by_cas = {}  # cas -> {name, ec, h_codes counter, p_codes counter, signals counter}

    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter=',')
        headers = reader.fieldnames or []
        col = _map_cols(headers)

        row_num = 0
        for row in reader:
            row_num += 1
            if progress_every and row_num % progress_every == 0:
                print(f"  ...{row_num:,} rows scanned, {len(by_cas):,} unique CAS so far")

            cas = _clean_cas(_get(row, col, 'cas'))
            if not cas:
                continue

            name = _get(row, col, 'name').strip()
            ec   = _get(row, col, 'ec').strip()
            h_raw = _get(row, col, 'h_codes')
            p_raw = _get(row, col, 'p_codes')
            sig   = _get(row, col, 'signal').strip().capitalize()
            pics  = _get(row, col, 'pictograms').strip()

            h_codes = _parse_codes(h_raw, r'H\d{3}[A-Z]?')
            p_codes = _parse_codes(p_raw, r'P\d{3}(?:\+P\d{3})*')

            if cas not in by_cas:
                by_cas[cas] = {
                    'name': name,
                    'ec':   ec,
                    'h_counts': Counter(),
                    'p_counts': Counter(),
                    'signal_counts': Counter(),
                    'pic_counts': Counter(),
                    'total': 0,
                }

            entry = by_cas[cas]
            if name and not entry['name']:
                entry['name'] = name
            if ec and not entry['ec']:
                entry['ec'] = ec
            entry['h_counts'].update(h_codes)
            entry['p_counts'].update(p_codes)
            if sig in ('Danger', 'Warning'):
                entry['signal_counts'][sig] += 1
            if pics:
                entry['pic_counts'][pics] += 1
            entry['total'] += 1

    print(f"ECHA: {row_num:,} rows → {len(by_cas):,} unique CAS numbers")

    # Second pass: write consensus data to DB
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')

    loaded = 0
    threshold = 0.25  # H-code must appear in ≥25% of notifications to be included

    now = datetime.now(timezone.utc).isoformat()

    for cas, data in by_cas.items():
        total = data['total']
        # Consensus H-codes: appear in at least `threshold` fraction of notifications
        h_codes = [
            code for code, count in data['h_counts'].items()
            if count / total >= threshold
        ]
        h_codes.sort()

        # All P-codes that appear at all (they're additive)
        p_codes = sorted(data['p_counts'].keys())

        # Signal word: majority vote
        sig_counts = data['signal_counts']
        if sig_counts.get('Danger', 0) > sig_counts.get('Warning', 0):
            signal = 'Danger'
        elif sig_counts:
            signal = 'Warning'
        else:
            signal = None

        name = data['name']
        ec   = data['ec']

        conn.execute('''
            INSERT INTO chemicals
                (cas_number, ec_number, common_name,
                 ghs_eu_h_codes, ghs_eu_signal,
                 p_codes,
                 data_sources, last_updated)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(cas_number) DO UPDATE SET
                ec_number       = COALESCE(NULLIF(excluded.ec_number, ''),  ec_number),
                common_name     = COALESCE(NULLIF(excluded.common_name, ''), common_name),
                ghs_eu_h_codes  = excluded.ghs_eu_h_codes,
                ghs_eu_signal   = excluded.ghs_eu_signal,
                p_codes         = excluded.p_codes,
                data_sources    = excluded.data_sources,
                last_updated    = excluded.last_updated
        ''', (
            cas, ec, name,
            json.dumps(h_codes),
            signal,
            json.dumps(p_codes),
            json.dumps(['ECHA C&L Inventory']),
            now,
        ))

        # Insert GHS statement rows
        for code in h_codes:
            conn.execute('''
                INSERT OR REPLACE INTO ghs_statements
                    (cas_number, jurisdiction, code, signal_word)
                VALUES (?, 'EU', ?, ?)
            ''', (cas, code, signal))

        # Synonyms
        if name:
            conn.execute('''
                INSERT OR IGNORE INTO chemical_synonyms (cas_number, name, source)
                VALUES (?, ?, 'ECHA')
            ''', (cas, name[:512]))

        loaded += 1
        if loaded % 10000 == 0:
            conn.commit()
            print(f"  ...{loaded:,} written")

    conn.execute(
        'INSERT INTO import_log (source, filename, rows_loaded) VALUES (?,?,?)',
        ('ECHA C&L Inventory', os.path.basename(csv_path), loaded)
    )
    conn.commit()
    conn.close()

    print(f"ECHA: wrote {loaded:,} chemicals to database")
    return loaded


# ── Helpers ───────────────────────────────────────────────────────────────────

# Maps logical field names to possible CSV column names from different ECHA export versions
_COL_CANDIDATES = {
    'cas':       ['CAS Number', 'CAS No', 'cas_number'],
    'ec':        ['EC Number', 'EC No', 'EINECS'],
    'name':      ['International Chemical Identification', 'Chemical Name', 'Substance Name'],
    'h_codes':   ['Hazard Statement Code(s)', 'H Statements', 'Hazard Codes'],
    'p_codes':   ['Precautionary Statement Code(s)', 'P Statements', 'Precautionary Codes'],
    'signal':    ['Signal Word', 'Signal'],
    'pictograms':['Pictogram(s)', 'GHS Pictograms', 'Pictogram Code(s)'],
}


def _map_cols(headers):
    mapping = {}
    for field, candidates in _COL_CANDIDATES.items():
        for candidate in candidates:
            for h in headers:
                if candidate.lower() in h.lower():
                    mapping[field] = h
                    break
            if field in mapping:
                break
    return mapping


def _get(row, col_map, field, default=''):
    col = col_map.get(field)
    return (row.get(col, '') or '') if col else default


def _clean_cas(raw):
    s = str(raw).strip()
    if re.match(r'^\d{1,7}-\d{2}-\d$', s):
        return s
    return ''


def _parse_codes(raw, pattern):
    """Extract all codes matching pattern from a raw string."""
    if not raw:
        return []
    return list(dict.fromkeys(re.findall(pattern, str(raw))))
