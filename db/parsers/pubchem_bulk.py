"""
PubChem GHS Classification — bulk database loader.

This is the high-volume source. It pages through PubChem's public
"GHS Classification" annotations endpoint, which lists *every* compound
PubChem has a GHS classification for (tens of thousands of chemicals),
and loads each one into the master database with:

  - GHS hazard statement codes (H-codes)
  - Signal word (Danger / Warning)
  - Precautionary statement codes (P-codes)
  - PubChem CID and chemical name(s)

CAS numbers are resolved in a second pass using PubChem's synonyms
endpoint (batched, with CAS check-digit validation), so most chemicals
end up keyed by their real CAS number.

API endpoints used (all public, no key required):
  Annotations:
    https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/JSON
        ?heading=GHS+Classification&page=N
  Synonyms (CAS resolution):
    https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/<cids>/synonyms/JSON

Usage:
    python3 db_update.py --source pubchem                 # full load (slow)
    python3 db_update.py --source pubchem --max-pages 5   # quick partial load

PubChem rate limit is 5 requests/sec; this loader sleeps between calls to
stay well under that. A full load makes a few hundred requests and takes
several minutes.
"""

import json
import re
import sqlite3
import time
from datetime import datetime, timezone

ANNOTATION_URL = (
    'https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/JSON'
    '?heading=GHS+Classification&page={page}'
)
SYNONYM_URL = (
    'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cids}/synonyms/JSON'
)

# Codes that imply a "Danger" signal word when PubChem doesn't state one explicitly
_DANGER_HCODES = {
    'H224', 'H225', 'H250', 'H260', 'H271', 'H280',
    'H300', 'H301', 'H304', 'H310', 'H311', 'H314', 'H318',
    'H330', 'H331', 'H340', 'H350', 'H360', 'H370', 'H372',
    'H400', 'H410',
}

_HCODE_RE  = re.compile(r'\bH\d{3}[A-Za-z]{0,3}\b')
_PCODE_RE  = re.compile(r'\bP\d{3}(?:\s*\+\s*P\d{3})*\b')
_SIGNAL_RE = re.compile(r'Signal[^A-Za-z]*?(Danger|Warning)', re.IGNORECASE)
_CAS_RE    = re.compile(r'^\d{2,7}-\d{2}-\d$')


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get_json(url, session, delay):
    """GET a URL and return parsed JSON, or None on failure. Sleeps `delay` after."""
    try:
        resp = session.get(url, timeout=60,
                           headers={'User-Agent': 'SDS-Generator/1.0'})
        time.sleep(delay)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  request failed: {e}")
        time.sleep(delay)
        return None


# ── GHS annotation parsing ────────────────────────────────────────────────────

def _flatten_strings(annotation):
    """Collect every StringWithMarkup 'String' value in an annotation's Data."""
    out = []
    for data in annotation.get('Data', []):
        value = data.get('Value', {})
        for swm in value.get('StringWithMarkup', []):
            s = swm.get('String')
            if s:
                out.append(s)
    return out


def _parse_ghs(annotation):
    """Return (h_codes, p_codes, signal_word) parsed from one annotation."""
    strings = _flatten_strings(annotation)
    blob = '\n'.join(strings)

    h_codes = sorted(set(_HCODE_RE.findall(blob)))

    p_codes = []
    for m in _PCODE_RE.findall(blob):
        norm = m.replace(' ', '')
        if norm not in p_codes:
            p_codes.append(norm)

    # Signal word: prefer an explicit "Signal: Danger/Warning" statement
    signal = None
    sm = _SIGNAL_RE.search(blob)
    if sm:
        signal = sm.group(1).capitalize()
    elif h_codes:
        signal = 'Danger' if any(h in _DANGER_HCODES for h in h_codes) else 'Warning'

    return h_codes, p_codes, signal


def _fetch_annotations_page(page, session, delay):
    """Fetch one page of GHS annotations. Returns (records, total_pages)."""
    url = ANNOTATION_URL.format(page=page)
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
        cid = cids[0]
        name = (ann.get('Name') or '').strip()
        h_codes, p_codes, signal = _parse_ghs(ann)
        if not h_codes:
            continue  # nothing useful to store
        records.append({
            'cid': cid,
            'name': name,
            'h_codes': h_codes,
            'p_codes': p_codes,
            'signal': signal,
        })
    return records, total_pages


# ── CAS resolution ────────────────────────────────────────────────────────────

def _valid_cas(s):
    """Validate a CAS number string including its check digit."""
    if not _CAS_RE.match(s):
        return False
    digits = s.replace('-', '')
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * i for i, d in enumerate(reversed(body), start=1))
    return total % 10 == check


def _resolve_cas(cids, session, delay, batch=100):
    """
    Map a list of CIDs to (cas, [synonyms]) using PubChem's synonyms endpoint.
    Returns {cid: {'cas': str|None, 'synonyms': [..]}}.
    """
    result = {}
    for i in range(0, len(cids), batch):
        chunk = cids[i:i + batch]
        url = SYNONYM_URL.format(cids=','.join(str(c) for c in chunk))
        data = _get_json(url, session, delay)
        info = (data or {}).get('InformationList', {}).get('Information', [])
        for entry in info:
            cid = entry.get('CID')
            syns = entry.get('Synonym', []) or []
            cas = next((s for s in syns if _valid_cas(s)), None)
            # Keep a few readable synonyms (skip CAS-like and overly long tokens)
            readable = [s for s in syns
                        if not _CAS_RE.match(s) and 1 < len(s) <= 80][:8]
            result[cid] = {'cas': cas, 'synonyms': readable}
    return result


# ── Database write ────────────────────────────────────────────────────────────

def _upsert(conn, rec, cas, synonyms, now):
    """Insert/merge one chemical record. Returns True if a row was written."""
    key = cas if cas else f"PCID{rec['cid']}"
    h_json = json.dumps(rec['h_codes'])
    p_json = json.dumps(rec['p_codes'])

    conn.execute('''
        INSERT INTO chemicals
            (cas_number, pubchem_cid, common_name,
             ghs_us_h_codes, ghs_us_signal,
             ghs_eu_h_codes, ghs_eu_signal,
             p_codes, data_sources, last_updated)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(cas_number) DO UPDATE SET
            pubchem_cid    = COALESCE(chemicals.pubchem_cid, excluded.pubchem_cid),
            common_name    = COALESCE(NULLIF(chemicals.common_name, ''), excluded.common_name),
            -- only fill GHS fields if the existing row has none
            ghs_us_h_codes = CASE WHEN chemicals.ghs_us_h_codes IS NULL
                                   OR chemicals.ghs_us_h_codes IN ('', '[]')
                                  THEN excluded.ghs_us_h_codes ELSE chemicals.ghs_us_h_codes END,
            ghs_us_signal  = COALESCE(NULLIF(chemicals.ghs_us_signal, ''), excluded.ghs_us_signal),
            ghs_eu_h_codes = CASE WHEN chemicals.ghs_eu_h_codes IS NULL
                                   OR chemicals.ghs_eu_h_codes IN ('', '[]')
                                  THEN excluded.ghs_eu_h_codes ELSE chemicals.ghs_eu_h_codes END,
            ghs_eu_signal  = COALESCE(NULLIF(chemicals.ghs_eu_signal, ''), excluded.ghs_eu_signal),
            p_codes        = CASE WHEN chemicals.p_codes IS NULL
                                   OR chemicals.p_codes IN ('', '[]')
                                  THEN excluded.p_codes ELSE chemicals.p_codes END,
            last_updated   = excluded.last_updated
    ''', (
        key, rec['cid'], rec['name'] or None,
        h_json, rec['signal'],
        h_json, rec['signal'],
        p_json,
        json.dumps(['PubChem GHS Classification']),
        now,
    ))

    # Synonyms (name + any readable synonyms, plus the real CAS as searchable)
    names = []
    if rec['name']:
        names.append(rec['name'])
    names.extend(synonyms)
    if cas:
        names.append(cas)
    for name in names:
        if name:
            conn.execute('''
                INSERT OR IGNORE INTO chemical_synonyms (cas_number, name, source)
                VALUES (?, ?, 'PubChem')
            ''', (key, name[:512]))
    return True


# ── Entry point ───────────────────────────────────────────────────────────────

def load(db_path, max_pages=None, delay=0.25, resolve_cas=True):
    """
    Bulk-load GHS classifications from PubChem into the master database.

    Args:
        db_path:     path to chemicals_master.db
        max_pages:   stop after this many annotation pages (None = all)
        delay:       seconds to sleep between API requests (rate-limit politeness)
        resolve_cas: if True, resolve CAS numbers via the synonyms endpoint
    """
    try:
        import requests
    except ImportError:
        print("  requests not installed — run: pip install requests")
        return 0

    session = requests.Session()
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    print("  Querying PubChem GHS Classification index...")
    first_records, total_pages = _fetch_annotations_page(1, session, delay)
    if total_pages == 0:
        print("  Could not reach PubChem (no pages returned). Check your connection.")
        conn.close()
        return 0

    last_page = total_pages if max_pages is None else min(total_pages, max_pages)
    print(f"  PubChem reports {total_pages} pages; loading {last_page}.")

    total_loaded = 0
    for page in range(1, last_page + 1):
        records = first_records if page == 1 else \
            _fetch_annotations_page(page, session, delay)[0]
        if not records:
            continue

        cas_map = {}
        if resolve_cas:
            cas_map = _resolve_cas([r['cid'] for r in records], session, delay)

        now = datetime.now(timezone.utc).isoformat()
        for rec in records:
            info = cas_map.get(rec['cid'], {})
            cas = info.get('cas')
            syns = info.get('synonyms', [])
            _upsert(conn, rec, cas, syns, now)
            total_loaded += 1

        conn.commit()
        print(f"\r  page {page}/{last_page} — {total_loaded:,} chemicals loaded",
              end='', flush=True)
    print()

    conn.execute(
        'INSERT INTO import_log (source, filename, rows_loaded) VALUES (?,?,?)',
        ('PubChem GHS Classification', f'pages 1-{last_page}', total_loaded)
    )
    conn.commit()
    conn.close()
    print(f"PubChem: loaded/updated {total_loaded:,} chemicals from {last_page} pages")
    return total_loaded
