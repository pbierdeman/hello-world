"""
Chemical Master Database — download and load script.

Usage:
    python3 db_update.py                   # load bundled data + download all sources
    python3 db_update.py --source bundled  # load only bundled paint chemicals (no internet)
    python3 db_update.py --source pubchem  # bulk-load THOUSANDS from PubChem GHS index
    python3 db_update.py --source pubchem --max-pages 5   # quick partial PubChem load
    python3 db_update.py --source niosh    # load only NIOSH
    python3 db_update.py --source dot      # load only DOT HMT
    python3 db_update.py --source echa     # load only ECHA C&L
    python3 db_update.py --no-download     # skip downloading, use existing files
    python3 db_update.py --stats           # show database stats only

The 'bundled' source is always loaded first — it requires no internet and provides
~35 common paint/coating chemicals immediately (solvents, pigments, extenders).

To grow the database to thousands of chemicals, run:
    python3 db_update.py --source pubchem
This pages through PubChem's public GHS Classification index (tens of thousands
of chemicals with H-codes, signal words, and P-codes) and resolves CAS numbers.
It is NOT run by default because it takes several minutes; run it once after setup.

Run quarterly to stay current with source updates.

Network requirements:
    NIOSH JSON   ~1 MB   (cdc.gov)
    DOT HMT CSV  ~3 MB   (phmsa.dot.gov / govinfo.gov)
    ECHA C&L ZIP ~80 MB  (echa.europa.eu)  — unzips to ~300 MB CSV

Total first-run time: ~5–10 minutes depending on network speed.
Database size after loading all three: ~150–200 MB.
"""

import argparse
import os
import sys
import zipfile

# Allow running from project root
sys.path.insert(0, os.path.dirname(__file__))

from db.schema import DB_PATH, init_schema, stats

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), 'db', 'downloads')

# ── Source definitions ────────────────────────────────────────────────────────
SOURCES = {
    'bundled': {
        'description': 'Bundled paint/coating chemicals — ~35 common chemicals, no download',
        'inline': True,                   # no file download needed
        'loader': 'db.parsers.bundled_parser',
        'loader_fn': 'load',
    },
    'pubchem': {
        'description': 'PubChem GHS Classification index — thousands of chemicals (API)',
        'api': True,                      # loader fetches its own data over the API
        'loader': 'db.parsers.pubchem_bulk',
        'loader_fn': 'load',
    },
    'niosh': {
        'description': 'NIOSH Pocket Guide to Chemical Hazards (~700 chemicals)',
        'url': 'https://www.cdc.gov/niosh/npg/all.json',
        'filename': 'niosh_npg.json',
        'loader': 'db.parsers.niosh_parser',
        'loader_fn': 'load',
        'file_arg': 'json_path',
    },
    'dot': {
        'description': 'US DOT Hazardous Materials Table 49 CFR 172.101',
        'url': 'https://www.phmsa.dot.gov/sites/phmsa.dot.gov/files/2023-11/HMT-2024.csv',
        'filename': 'dot_hmt.csv',
        'alt_urls': [
            # Fallback: govinfo.gov publishes the CFR as XML/CSV
            'https://www.govinfo.gov/content/pkg/CFR-2024-title49-vol2/xml/CFR-2024-title49-vol2-part172.xml',
        ],
        'loader': 'db.parsers.dot_parser',
        'loader_fn': 'load',
        'file_arg': 'csv_path',
        'note': (
            'If automatic download fails, download the HMT manually from:\n'
            '  https://www.phmsa.dot.gov/training/hazmat/erg/emergency-response-guidebook-erge\n'
            'Save as: db/downloads/dot_hmt.csv'
        ),
    },
    'echa': {
        'description': 'ECHA C&L Inventory (~150,000 classified substances)',
        'url': 'https://www.echa.europa.eu/documents/10162/17227/cl_inventory_en.csv.zip',
        'filename': 'echa_cl.zip',
        'unzip_to': 'echa_cl.csv',
        'loader': 'db.parsers.echa_parser',
        'loader_fn': 'load',
        'file_arg': 'csv_path',
        'note': (
            'If automatic download fails, download the C&L Inventory manually from:\n'
            '  https://echa.europa.eu/information-on-chemicals/cl-inventory-database\n'
            '  Click "Export all C&L notifications" and save to: db/downloads/echa_cl.zip'
        ),
    },
}


def download_file(url, dest_path, chunk_size=1024 * 256):
    """Download url to dest_path with a progress bar."""
    try:
        import requests
    except ImportError:
        print("  requests not installed — run: pip install requests")
        return False

    print(f"  Downloading: {url}")
    try:
        resp = requests.get(url, stream=True, timeout=120,
                            headers={'User-Agent': 'SDS-Generator/1.0'})
        resp.raise_for_status()

        total = int(resp.headers.get('content-length', 0))
        downloaded = 0

        with open(dest_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    mb = downloaded / 1_048_576
                    print(f"\r  {mb:.1f} MB / {total/1_048_576:.1f} MB ({pct:.0f}%)",
                          end='', flush=True)
        print()
        return True
    except Exception as e:
        print(f"\n  Download failed: {e}")
        return False


def unzip_first(zip_path, out_dir, out_name):
    """Unzip the first file from a zip archive."""
    print(f"  Unzipping {os.path.basename(zip_path)}...")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not names:
            print("  Empty zip file")
            return None
        # Pick the largest file (usually the CSV)
        target = max(names, key=lambda n: zf.getinfo(n).file_size)
        out_path = os.path.join(out_dir, out_name)
        with zf.open(target) as src, open(out_path, 'wb') as dst:
            total = zf.getinfo(target).file_size
            written = 0
            while True:
                chunk = src.read(1024 * 256)
                if not chunk:
                    break
                dst.write(chunk)
                written += len(chunk)
                print(f"\r  {written/1_048_576:.1f} / {total/1_048_576:.1f} MB",
                      end='', flush=True)
        print()
    return out_path


def run_source(key, skip_download=False, **extra):
    src = SOURCES[key]
    print(f"\n{'='*60}")
    print(f"Source: {src['description']}")
    print(f"{'='*60}")

    # API sources (pubchem) fetch their own data; pass through extra options
    if src.get('api'):
        import importlib
        mod = importlib.import_module(src['loader'])
        fn  = getattr(mod, src['loader_fn'])
        kwargs = {k: v for k, v in extra.items() if v is not None}
        return fn(db_path=DB_PATH, **kwargs)

    # Inline sources (bundled) have no file to download
    if src.get('inline'):
        import importlib
        mod = importlib.import_module(src['loader'])
        fn  = getattr(mod, src['loader_fn'])
        return fn(db_path=DB_PATH)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    dl_path = os.path.join(DOWNLOAD_DIR, src['filename'])
    data_path = dl_path

    # Download
    if not skip_download:
        if not os.path.exists(dl_path):
            ok = download_file(src['url'], dl_path)
            if not ok:
                # Try alternative URLs
                for alt in src.get('alt_urls', []):
                    ok = download_file(alt, dl_path)
                    if ok:
                        break
            if not ok:
                print(src.get('note', ''))
                return 0
        else:
            print(f"  Using cached: {dl_path}")
    elif not os.path.exists(dl_path):
        print(f"  File not found (--no-download): {dl_path}")
        if 'note' in src:
            print(src['note'])
        return 0

    # Unzip if needed
    if 'unzip_to' in src:
        unzipped = os.path.join(DOWNLOAD_DIR, src['unzip_to'])
        if not os.path.exists(unzipped):
            data_path = unzip_first(dl_path, DOWNLOAD_DIR, src['unzip_to'])
            if not data_path:
                return 0
        else:
            print(f"  Using cached unzipped: {unzipped}")
            data_path = unzipped

    # Load into DB
    import importlib
    mod = importlib.import_module(src['loader'])
    fn  = getattr(mod, src['loader_fn'])

    kwargs = {'db_path': DB_PATH, src['file_arg']: data_path}
    return fn(**kwargs)


def main():
    parser = argparse.ArgumentParser(
        description='Download and load chemical databases into chemicals_master.db'
    )
    parser.add_argument('--source', choices=list(SOURCES.keys()),
                        help='Load only one source (default: bundled + all download sources)')
    parser.add_argument('--no-download', action='store_true',
                        help='Skip downloading; use files already in db/downloads/')
    parser.add_argument('--max-pages', type=int, default=None,
                        help='For --source pubchem: stop after N pages (quick partial load)')
    parser.add_argument('--stats', action='store_true',
                        help='Print database statistics and exit')
    args = parser.parse_args()

    # Always ensure schema exists
    init_schema()

    if args.stats:
        s = stats()
        print(f"\nDatabase: {DB_PATH}")
        print(f"  Chemicals:      {s['chemicals']:>10,}")
        print(f"  Synonyms:       {s['synonyms']:>10,}")
        print(f"  GHS statements: {s['ghs_statements']:>10,}")
        if s['recent_imports']:
            print("\nRecent imports:")
            for imp in s['recent_imports']:
                print(f"  {imp['source']:<35} {imp['rows_loaded']:>8,} rows  {imp['imported_at'][:10]}")
        return

    if args.source:
        targets = [args.source]
    else:
        # Default run: bundled first, then the file-download sources.
        # 'pubchem' is opt-in (slow, makes many API calls) — request it explicitly.
        targets = ['bundled'] + [k for k in SOURCES
                                 if k not in ('bundled', 'pubchem')]

    total_loaded = 0
    for key in targets:
        # --no-download only skips file-based sources, not inline/api ones
        src = SOURCES[key]
        skip = args.no_download and not (src.get('inline') or src.get('api'))
        extra = {'max_pages': args.max_pages} if src.get('api') else {}
        n = run_source(key, skip_download=skip, **extra)
        total_loaded += (n or 0)

    print(f"\nDone. Total records processed: {total_loaded:,}")
    s = stats()
    print(f"Database now contains {s['chemicals']:,} chemicals, "
          f"{s['synonyms']:,} synonyms, "
          f"{s['ghs_statements']:,} GHS statement rows.")


if __name__ == '__main__':
    main()
