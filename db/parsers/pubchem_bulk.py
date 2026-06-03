"""
PubChem GHS Classification — two-phase bulk loader.

Phase 1  DOWNLOAD  pages through PubChem's GHS annotation index, batch-resolves
          CAS numbers, and saves everything to a local compressed cache file:
              db/downloads/pubchem_ghs.json.gz

Phase 2  LOAD      reads the cache file (no internet needed) and merges records
          into chemicals_master.db.

This means:
  - You only need internet for the first run.
  - Re-loading after a schema change or DB reset is instant.
  - The cache file can be copied to another machine.

Usage (via db_update.py):
    python3 db_update.py --source pubchem                 # download + load
    python3 db_update.py --source pubchem --no-download   # load from cache only
    python3 db_update.py --source pubchem --max-pages 5   # quick test (5 pages)

API endpoints (no key required):
    GHS annotations  https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/JSON
    CAS synonyms     https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/<cids>/synonyms/JSON
"""

import gzip
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

CACHE_FILE = os.path.join(os.path.dirname(__file__), '..', 'downloads', 'pubchem_ghs.json.gz')

ANNOTATION_URL = (
    'https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/JSON'
    '?heading=GHS+Classification&page={page}'
)
SYNONYM_URL = (
    'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cids}/synonyms/JSON'
)

# PubChem allows up to 5 requests/second without an API key.
# We stay comfortably under with a shared rate limiter across all threads.
_MAX_RPS     = 4       # requests per second ceiling
_CAS_BATCH   = 200     # CIDs per synonym lookup request
_CAS_WORKERS = 3       # concurrent synonym requests

_DANGER_HCODES = {
    'H224','H225','H250','H260','H271','H280',
    'H300','H301','H304','H310','H311','H314','H318',
    'H330','H331','H340','H350','H360','H370','H372',
    'H400','H410',
}
_HCODE_RE = re.compile(r'\bH\d{3}[A-Za-z]{0,3}\b')
_PCODE_RE = re.compile(r'\bP\d{3}(?:\s*\+\s*P\d{3})*\b')
_CAS_RE   = re.compile(r'^\d{2,7}-\d{2}-\d$')


# ── Rate-limited HTTP ─────────────────────────────────────────────────────────

class _RateLimiter:
    """Thread-safe token-bucket rate limiter."""
    def __init__(self, rps):
        self._min_interval = 1.0 / rps
        self._last = 0.0
        self._lock = threading.Lock()

    def get(self, session, url):
        with self._lock:
            wait = self._min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
        try:
            resp = session.get(url, timeout=60,
                               headers={'User-Agent': 'SDS-Generator/1.0'})
            if resp.status_code == 404:
                return None
            if not resp.ok:
                print(f"\n  HTTP {resp.status_code} from PubChem: {resp.text[:200]}")
                return None
            return resp.json()
        except Exception as exc:
            print(f"\n  Connection error: {type(exc).__name__}: {exc}")
            return None


# ── GHS parsing ───────────────────────────────────────────────────────────────

def _parse_ghs(annotation):
    strings = []
    for data in annotation.get('Data', []):
        for swm in data.get('Value', {}).get('StringWithMarkup', []):
            s = swm.get('String')
            if s:
                strings.append(s)
    blob = '\n'.join(strings)

    h_codes = sorted(set(_HCODE_RE.findall(blob)))
    p_codes = []
    for m in _PCODE_RE.findall(blob):
        norm = m.replace(' ', '')
        if norm not in p_codes:
            p_codes.append(norm)

    signal = None
    m = re.search(r'Signal[^A-Za-z]*?(Danger|Warning)', blob, re.I)
    if m:
        signal = m.group(1).capitalize()
    elif h_codes:
        signal = 'Danger' if any(h in _DANGER_HCODES for h in h_codes) else 'Warning'

    return h_codes, p_codes, signal


def _valid_cas(s):
    if not _CAS_RE.match(s):
        return False
    digits = s.replace('-', '')
    body = digits[:-1]
    check = int(digits[-1])
    return sum(int(d) * i for i, d in enumerate(reversed(body), start=1)) % 10 == check


# ── Download phase ────────────────────────────────────────────────────────────

def _fetch_page(page, rl, session):
    """Fetch one annotation page. Returns (records_dict_by_cid, total_pages)."""
    data = rl.get(session, ANNOTATION_URL.format(page=page))
    if not data:
        return {}, 0
    if 'Annotations' not in data:
        preview = json.dumps(data)[:300]
        print(f"\n  Unexpected response structure from PubChem: {preview}")
        return {}, 0
    block = data.get('Annotations', {})
    total = block.get('TotalPages', 0)
    by_cid = {}
    for ann in block.get('Annotation', []):
        cids = (ann.get('LinkedRecords', {}) or {}).get('CID', [])
        if not cids:
            continue
        h_codes, p_codes, signal = _parse_ghs(ann)
        if not h_codes:
            continue
        cid = cids[0]
        by_cid[cid] = {
            'cid':    cid,
            'name':   (ann.get('Name') or '').strip(),
            'h_codes': h_codes,
            'p_codes': p_codes,
            'signal':  signal,
        }
    return by_cid, total


def _resolve_cas_batch(chunk, rl, session):
    """Resolve one batch of CIDs to CAS numbers via the synonyms endpoint."""
    url = SYNONYM_URL.format(cids=','.join(str(c) for c in chunk))
    data = rl.get(session, url)
    result = {}
    for entry in (data or {}).get('InformationList', {}).get('Information', []):
        cid  = entry.get('CID')
        syns = entry.get('Synonym', []) or []
        cas  = next((s for s in syns if _valid_cas(s)), None)
        readable = [s for s in syns if not _CAS_RE.match(s) and 1 < len(s) <= 80][:6]
        result[cid] = {'cas': cas, 'synonyms': readable}
    return result


def download(cache_file=CACHE_FILE, max_pages=None, session=None):
    """
    Download all GHS-annotated chemicals from PubChem and save to cache_file.
    Returns the number of records downloaded, or 0 on failure.
    """
    import requests as req_lib
    if session is None:
        session = req_lib.Session()

    rl = _RateLimiter(_MAX_RPS)

    print("  Connecting to PubChem...")
    first_page, total_pages = _fetch_page(1, rl, session)
    if not total_pages:
        print("  PubChem unreachable — check your internet connection.")
        print("  If you have a cache from a previous run, use --no-download.")
        return 0

    last_page = total_pages if max_pages is None else min(total_pages, max_pages)
    print(f"  PubChem GHS index: {total_pages} pages total, downloading {last_page}...")
    t0 = time.time()

    # Fetch remaining pages concurrently
    all_records = dict(first_page)
    remaining = list(range(2, last_page + 1))
    done_pages = 1

    with ThreadPoolExecutor(max_workers=_CAS_WORKERS) as pool:
        futures = {pool.submit(_fetch_page, p, rl, session): p for p in remaining}
        for fut in as_completed(futures):
            page_records, _ = fut.result()
            all_records.update(page_records)
            done_pages += 1
            _progress(f"  pages {done_pages}/{last_page} — {len(all_records):,} chemicals with GHS data")

    print()
    print(f"  Resolving CAS numbers for {len(all_records):,} chemicals...")

    # Batch-resolve CAS numbers concurrently
    cids   = list(all_records.keys())
    chunks = [cids[i:i + _CAS_BATCH] for i in range(0, len(cids), _CAS_BATCH)]
    done_batches = 0

    with ThreadPoolExecutor(max_workers=_CAS_WORKERS) as pool:
        futures = {pool.submit(_resolve_cas_batch, chunk, rl, session): chunk
                   for chunk in chunks}
        for fut in as_completed(futures):
            cas_result = fut.result()
            for cid, info in cas_result.items():
                if cid in all_records:
                    all_records[cid]['cas']      = info.get('cas')
                    all_records[cid]['synonyms'] = info.get('synonyms', [])
            done_batches += 1
            _progress(f"  CAS resolution: {done_batches}/{len(chunks)} batches")

    print()
    elapsed = time.time() - t0
    with_cas = sum(1 for r in all_records.values() if r.get('cas'))
    print(f"  Download complete: {len(all_records):,} chemicals, "
          f"{with_cas:,} with CAS numbers — {elapsed/60:.1f} min")

    # Save cache
    os.makedirs(os.path.dirname(os.path.abspath(cache_file)), exist_ok=True)
    payload = {
        'downloaded_at': datetime.now(timezone.utc).isoformat(),
        'pages':         last_page,
        'total_pages':   total_pages,
        'records':       list(all_records.values()),
    }
    with gzip.open(cache_file, 'wt', encoding='utf-8') as f:
        json.dump(payload, f)
    mb = os.path.getsize(cache_file) / 1_048_576
    print(f"  Saved: {os.path.basename(cache_file)} ({mb:.1f} MB)")
    return len(all_records)


# ── Load phase ────────────────────────────────────────────────────────────────

def load_cache(db_path, cache_file=CACHE_FILE):
    """
    Read the local cache file and merge records into the master database.
    Returns the number of records processed.
    """
    if not os.path.exists(cache_file):
        print(f"  Cache file not found: {cache_file}")
        print("  Run without --no-download to fetch from PubChem first.")
        return 0

    print(f"  Reading cache: {os.path.basename(cache_file)} ...", end='', flush=True)
    with gzip.open(cache_file, 'rt', encoding='utf-8') as f:
        payload = json.load(f)
    records = payload.get('records', [])
    print(f" {len(records):,} records")

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    now = datetime.now(timezone.utc).isoformat()
    src_json = json.dumps(['PubChem GHS Classification'])

    loaded = 0
    for rec in records:
        h_json = json.dumps(rec.get('h_codes', []))
        p_json = json.dumps(rec.get('p_codes', []))
        signal = rec.get('signal')
        cas    = rec.get('cas')
        key    = cas if cas else f"PCID{rec['cid']}"

        conn.execute('''
            INSERT INTO chemicals
                (cas_number, pubchem_cid, common_name,
                 ghs_us_h_codes, ghs_us_signal,
                 ghs_eu_h_codes, ghs_eu_signal,
                 p_codes, data_sources, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(cas_number) DO UPDATE SET
                pubchem_cid   = COALESCE(chemicals.pubchem_cid, excluded.pubchem_cid),
                common_name   = COALESCE(NULLIF(chemicals.common_name,''), excluded.common_name),
                ghs_us_h_codes = CASE WHEN COALESCE(chemicals.ghs_us_h_codes,'') IN ('','[]')
                                      THEN excluded.ghs_us_h_codes
                                      ELSE chemicals.ghs_us_h_codes END,
                ghs_us_signal  = COALESCE(NULLIF(chemicals.ghs_us_signal,''), excluded.ghs_us_signal),
                ghs_eu_h_codes = CASE WHEN COALESCE(chemicals.ghs_eu_h_codes,'') IN ('','[]')
                                      THEN excluded.ghs_eu_h_codes
                                      ELSE chemicals.ghs_eu_h_codes END,
                ghs_eu_signal  = COALESCE(NULLIF(chemicals.ghs_eu_signal,''), excluded.ghs_eu_signal),
                p_codes        = CASE WHEN COALESCE(chemicals.p_codes,'') IN ('','[]')
                                      THEN excluded.p_codes
                                      ELSE chemicals.p_codes END,
                last_updated   = excluded.last_updated
        ''', (key, rec['cid'], rec.get('name') or None,
              h_json, signal, h_json, signal, p_json, src_json, now))

        # Synonyms
        names = []
        if rec.get('name'):
            names.append(rec['name'])
        names.extend(rec.get('synonyms', []))
        if cas:
            names.append(cas)
        for name in names:
            if name:
                conn.execute(
                    'INSERT OR IGNORE INTO chemical_synonyms (cas_number, name, source)'
                    ' VALUES (?,?,?)', (key, name[:512], 'PubChem')
                )

        loaded += 1
        if loaded % 5000 == 0:
            conn.commit()
            _progress(f"  loading: {loaded:,}/{len(records):,}")

    conn.commit()
    conn.execute(
        'INSERT INTO import_log (source, filename, rows_loaded) VALUES (?,?,?)',
        ('PubChem GHS Classification', os.path.basename(cache_file), loaded)
    )
    conn.commit()
    conn.close()
    print(f"\r  Loaded {loaded:,} chemicals into database.                    ")
    return loaded


# ── Entry point (called by db_update.py) ─────────────────────────────────────

def load(db_path, max_pages=None, no_download=False, cache_file=CACHE_FILE):
    """
    Download (unless no_download) then load PubChem GHS data into db_path.

    Args:
        db_path:     path to chemicals_master.db
        max_pages:   limit annotation pages downloaded (None = all ~90 pages)
        no_download: if True, skip download and load from existing cache_file
        cache_file:  path to the intermediate cache (default: db/downloads/pubchem_ghs.json.gz)
    """
    if not no_download:
        n = download(cache_file=cache_file, max_pages=max_pages)
        if n == 0 and not os.path.exists(cache_file):
            return 0
    return load_cache(db_path, cache_file=cache_file)


# ── Progress helper ───────────────────────────────────────────────────────────

def _progress(msg):
    print(f"\r{msg:<72}", end='', flush=True)


# ── Backward-compat shims (used by niosh_pubchem.py) ─────────────────────────

def _get_json(url, session, delay):
    """Sequential HTTP GET with fixed delay. Kept for niosh_pubchem.py."""
    try:
        resp = session.get(url, timeout=60, headers={'User-Agent': 'SDS-Generator/1.0'})
        time.sleep(delay)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception:
        time.sleep(delay)
        return None


def _resolve_cas(cids, session, delay, batch=100):
    """Sequential CAS resolution. Kept for niosh_pubchem.py."""
    result = {}
    for i in range(0, len(cids), batch):
        chunk = cids[i:i + batch]
        url  = SYNONYM_URL.format(cids=','.join(str(c) for c in chunk))
        data = _get_json(url, session, delay)
        for entry in (data or {}).get('InformationList', {}).get('Information', []):
            cid  = entry.get('CID')
            syns = entry.get('Synonym', []) or []
            cas  = next((s for s in syns if _valid_cas(s)), None)
            readable = [s for s in syns if not _CAS_RE.match(s) and 1 < len(s) <= 80][:8]
            result[cid] = {'cas': cas, 'synonyms': readable}
    return result
