"""
NIOSH / OSHA occupational exposure limits — database loader (via PubChem).

The CDC's bulk NIOSH Pocket Guide download (the old `npg/all.json`) was removed
in the 2024 cdc.gov reorganization, so it can no longer be fetched directly.
PubChem, however, mirrors the same authoritative data under public annotation
headings sourced from NIOSH and OSHA:

  - "Immediately Dangerous to Life or Health (IDLH)"   → NIOSH IDLH
  - "Occupational Exposure Limits"                     → OSHA PEL / NIOSH REL / ACGIH TLV

This loader pages through those headings, resolves CAS numbers, and *merges*
the exposure limits into existing chemical rows (Section 8 of the SDS). It only
fills fields that are currently empty, so curated bundled data is never
overwritten.

Usage:
    python3 db_update.py --source niosh                 # full load (slow)
    python3 db_update.py --source niosh --max-pages 3   # quick partial load

This is opt-in (many rate-limited API calls). Run it after the GHS bulk load
(`--source pubchem`) so there are already rows to enrich.

For users who have the official NIOSH Pocket Guide JSON file on disk, the
file-based loader in `niosh_parser.py` is still available.
"""

import re
import sqlite3
import time
from urllib.parse import quote

# Reuse the verified PubChem helpers from the GHS bulk loader
from db.parsers.pubchem_bulk import _get_json, _resolve_cas

ANNOTATION_URL = (
    'https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/annotations/heading/'
    '{heading}/JSON?page={page}'
)

# PubChem annotation headings → which DB columns each one feeds.
# 'extract' maps a db column to a function(list_of_strings) -> value|None.
HEADINGS = [
    {
        'heading': 'Immediately Dangerous to Life or Health (IDLH)',
        'columns': {'niosh_idlh': lambda strs: _first_clean(strs)},
    },
    {
        'heading': 'Occupational Exposure Limits',
        'columns': {
            'osha_pel':  lambda strs: _labeled(strs, r'\bOSHA\b'),
            'niosh_rel': lambda strs: _labeled(strs, r'\bNIOSH\b'),
            'acgih_tlv': lambda strs: _labeled(strs, r'ACGIH|\bTLV\b'),
        },
    },
]


# ── Text extraction helpers ───────────────────────────────────────────────────

def _clean(s):
    """Collapse whitespace and trim a value to a reasonable length."""
    s = re.sub(r'\s+', ' ', s or '').strip()
    return s[:250] if s else None


def _first_clean(strings):
    """Return the first non-empty, cleaned string."""
    for s in strings:
        c = _clean(s)
        if c:
            return c
    return None


def _labeled(strings, pattern):
    """Return the first string matching `pattern` (e.g. mentions OSHA/NIOSH/ACGIH)."""
    rx = re.compile(pattern, re.IGNORECASE)
    for s in strings:
        if rx.search(s or ''):
            return _clean(s)
    return None


def _flatten_strings(annotation):
    out = []
    for data in annotation.get('Data', []):
        value = data.get('Value', {})
        for swm in value.get('StringWithMarkup', []):
            s = swm.get('String')
            if s:
                out.append(s)
    return out


def _fetch_heading_page(heading, page, session, delay):
    """Fetch one page of a heading. Returns (records, total_pages)."""
    url = ANNOTATION_URL.format(heading=quote(heading), page=page)
    data = _get_json(url, session, delay)
    if not data:
        return [], 0

    block = data.get('Annotations', {})
    total_pages = block.get('TotalPages', 0)
    records = []
    for ann in block.get('Annotation', []):
        cids = (ann.get('LinkedRecords', {}) or {}).get('CID', [])
        if not cids:
            continue
        strings = _flatten_strings(ann)
        if strings:
            records.append({'cid': cids[0], 'strings': strings})
    return records, total_pages


# ── Database merge ────────────────────────────────────────────────────────────

def _apply(conn, cid, cas, updates):
    """
    Fill empty exposure-limit columns for the matching chemical row.
    Matches by CAS first, then by PubChem CID. Returns True if a row changed.
    """
    if not updates:
        return False

    set_clause = ', '.join(
        f"{col} = COALESCE(NULLIF({col}, ''), :{col})" for col in updates
    )
    params = dict(updates)

    # Try matching by CAS number
    if cas:
        params['key'] = cas
        cur = conn.execute(
            f"UPDATE chemicals SET {set_clause} WHERE cas_number = :key", params
        )
        if cur.rowcount:
            return True

    # Fall back to matching by PubChem CID (rows created by the GHS bulk loader)
    params['key'] = cid
    cur = conn.execute(
        f"UPDATE chemicals SET {set_clause} WHERE pubchem_cid = :key", params
    )
    return bool(cur.rowcount)


# ── Entry point ───────────────────────────────────────────────────────────────

def load(db_path, max_pages=None, delay=0.25):
    """
    Load occupational exposure limits from PubChem into the master database.

    Args:
        db_path:   path to chemicals_master.db
        max_pages: per-heading page cap (None = all pages)
        delay:     seconds between API requests (rate-limit politeness)
    """
    try:
        import requests
    except ImportError:
        print("  requests not installed — run: pip install requests")
        return 0

    session = requests.Session()
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    total_updated = 0

    for spec in HEADINGS:
        heading = spec['heading']
        columns = spec['columns']
        print(f"\n  Heading: {heading}")

        first_records, total_pages = _fetch_heading_page(heading, 1, session, delay)
        if total_pages == 0:
            print("    (no data returned — heading unavailable or unreachable; skipping)")
            continue

        last_page = total_pages if max_pages is None else min(total_pages, max_pages)
        print(f"    {total_pages} pages reported; loading {last_page}.")

        heading_updated = 0
        for page in range(1, last_page + 1):
            records = first_records if page == 1 else \
                _fetch_heading_page(heading, page, session, delay)[0]
            if not records:
                continue

            cas_map = _resolve_cas([r['cid'] for r in records], session, delay)

            for rec in records:
                updates = {}
                for col, extractor in columns.items():
                    val = extractor(rec['strings'])
                    if val:
                        updates[col] = val
                cas = cas_map.get(rec['cid'], {}).get('cas')
                if _apply(conn, rec['cid'], cas, updates):
                    heading_updated += 1

            conn.commit()
            print(f"\r    page {page}/{last_page} — {heading_updated:,} rows enriched",
                  end='', flush=True)
        print()
        total_updated += heading_updated

    conn.execute(
        'INSERT INTO import_log (source, filename, rows_loaded) VALUES (?,?,?)',
        ('NIOSH/OSHA exposure limits (PubChem)', 'annotation headings', total_updated)
    )
    conn.commit()
    conn.close()
    print(f"\nNIOSH: enriched {total_updated:,} chemical rows with exposure limits")
    return total_updated
