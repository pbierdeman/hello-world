"""
NIOSH Pocket Guide to Chemical Hazards — database loader.

Download URL (run db_update.py to fetch automatically):
  https://www.cdc.gov/niosh/npg/npgjson.html
  Direct JSON: https://www.cdc.gov/niosh/npg/all.json

The NIOSH Pocket Guide covers ~700 of the most hazardous industrial
chemicals with:
  - CAS numbers and synonyms
  - OSHA PELs (permissible exposure limits)
  - NIOSH RELs and IDLHs (immediately dangerous to life/health)
  - Physical properties (flash point, boiling point, etc.)
  - Health hazards and symptoms
  - First aid / protective equipment guidance
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone


def load(db_path, json_path):
    """
    Load the NIOSH Pocket Guide JSON into the master database.

    Args:
        db_path:   path to chemicals_master.db
        json_path: path to the downloaded NIOSH JSON file
    """
    if not os.path.exists(json_path):
        print(f"NIOSH JSON not found: {json_path}")
        print("Run: python3 db_update.py --source niosh")
        return 0

    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)

    entries = data if isinstance(data, list) else data.get('chemicals', [])
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    loaded = 0
    skipped = 0

    for entry in entries:
        cas = _clean_cas(entry.get('cas', '') or entry.get('CAS', ''))
        if not cas:
            skipped += 1
            continue

        name = (entry.get('name') or entry.get('chemicalName') or '').strip()
        synonyms = _extract_synonyms(entry)

        # Physical properties
        fp   = _extract_field(entry, 'flashPoint', 'flash_point', 'FlashPoint')
        bp   = _extract_field(entry, 'boilingPoint', 'bp', 'BoilingPoint')
        mp   = _extract_field(entry, 'meltingPoint', 'mp', 'MeltingPoint')
        vp   = _extract_field(entry, 'vaporPressure', 'vapor_pressure', 'VaporPressure')
        den  = _extract_field(entry, 'specificGravity', 'density', 'Density')
        sol  = _extract_field(entry, 'solubility', 'Solubility')
        mf   = _extract_field(entry, 'formula', 'molecularFormula', 'MolecularFormula')
        mw   = _extract_field(entry, 'molecularWeight', 'mw', 'MW')

        # Exposure limits
        osha_pel  = _extract_field(entry, 'OSHA_PEL', 'osha_pel', 'oshaRel', 'pell')
        niosh_rel = _extract_field(entry, 'NIOSH_REL', 'niosh_rel', 'nioshRel', 'rel')
        niosh_idlh = _extract_field(entry, 'IDLH', 'idlh')

        now = datetime.now(timezone.utc).isoformat()

        conn.execute('''
            INSERT OR IGNORE INTO chemicals
                (cas_number, common_name, molecular_formula, molecular_weight,
                 flash_point, boiling_point, melting_point, vapor_pressure,
                 density, solubility,
                 osha_pel, niosh_rel, niosh_idlh,
                 data_sources, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            cas, name, mf, _to_float(mw),
            fp, bp, mp, vp, den, sol,
            osha_pel, niosh_rel, niosh_idlh,
            json.dumps(['NIOSH Pocket Guide']), now,
        ))

        # Update exposure limits on existing rows too
        conn.execute('''
            UPDATE chemicals SET
                osha_pel   = COALESCE(NULLIF(osha_pel, ''),   ?),
                niosh_rel  = COALESCE(NULLIF(niosh_rel, ''),  ?),
                niosh_idlh = COALESCE(NULLIF(niosh_idlh, ''), ?),
                flash_point   = COALESCE(NULLIF(flash_point, ''),   ?),
                boiling_point = COALESCE(NULLIF(boiling_point, ''), ?),
                last_updated  = ?
            WHERE cas_number = ?
        ''', (osha_pel, niosh_rel, niosh_idlh, fp, bp, now, cas))

        # Synonyms
        _insert_synonyms(conn, cas, [name] + synonyms)

        loaded += 1

    conn.execute(
        'INSERT INTO import_log (source, filename, rows_loaded) VALUES (?,?,?)',
        ('NIOSH Pocket Guide', os.path.basename(json_path), loaded)
    )
    conn.commit()
    conn.close()

    print(f"NIOSH: loaded {loaded:,} chemicals, skipped {skipped:,}")
    return loaded


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_cas(raw):
    """Return a validated CAS string or empty string."""
    s = str(raw).strip().rstrip('*').strip()
    if re.match(r'^\d{1,7}-\d{2}-\d$', s):
        return s
    return ''


def _extract_field(entry, *keys):
    for k in keys:
        v = entry.get(k)
        if v and str(v).strip() not in ('', 'N/A', 'NA', 'None', 'none', '-', '--'):
            return str(v).strip()
    return ''


def _to_float(v):
    try:
        return float(str(v).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _extract_synonyms(entry):
    syns = []
    for key in ('synonyms', 'Synonyms', 'aliases', 'otherNames'):
        v = entry.get(key)
        if isinstance(v, list):
            syns += [s.strip() for s in v if isinstance(s, str) and s.strip()]
        elif isinstance(v, str) and v.strip():
            syns += [s.strip() for s in re.split(r'[;,|]', v) if s.strip()]
    return syns


def _insert_synonyms(conn, cas, names):
    for name in names:
        if name:
            conn.execute('''
                INSERT OR IGNORE INTO chemical_synonyms (cas_number, name, source)
                VALUES (?, ?, 'NIOSH')
            ''', (cas, name[:512]))
