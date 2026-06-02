import sqlite3
import json
import re
import time
import os
import sys

import requests

CACHE_DB = os.path.join(os.path.dirname(__file__), 'cache', 'chemicals.db')
PUBCHEM_BASE = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug'
PUBCHEM_VIEW = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug_view'

# Path to the master chemical database built by db_update.py
_MASTER_DB = os.path.join(
    os.path.dirname(__file__), '..', 'db', 'chemicals_master.db'
)


def init_db():
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS chemical_cache (
            identifier TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


def lookup_chemical(identifier):
    """
    Return a dict of chemical data for the given name or CAS number.

    Lookup order:
      1. In-memory cache (fastest)
      2. Local master database (ECHA + NIOSH + DOT — offline, instant)
      3. PubChem API (requires internet, results cached after first hit)
    """
    key = identifier.strip().lower()

    # 1. Cache
    cached = _from_cache(key)
    if cached:
        return cached

    # 2. Master DB (ECHA / NIOSH / DOT)
    master = _from_master_db(identifier.strip())
    if master and master.get('found'):
        _to_cache(key, master)
        return master

    # 3. PubChem API
    data = _fetch(identifier.strip())
    _to_cache(key, data)
    return data


# ── Master DB lookup ──────────────────────────────────────────────────────────

def _from_master_db(identifier):
    """Look up identifier in the locally-built master database."""
    master_path = os.path.normpath(_MASTER_DB)
    if not os.path.exists(master_path):
        return None

    try:
        conn = sqlite3.connect(master_path)
        conn.row_factory = sqlite3.Row

        # Try CAS pattern first, then name/synonym search
        row = None
        if re.match(r'^\d{1,7}-\d{2}-\d$', identifier):
            row = conn.execute(
                'SELECT * FROM chemicals WHERE cas_number = ?', (identifier,)
            ).fetchone()
        if not row:
            row = conn.execute(
                'SELECT * FROM chemicals WHERE common_name = ? COLLATE NOCASE LIMIT 1',
                (identifier,)
            ).fetchone()
        if not row:
            row = conn.execute('''
                SELECT c.* FROM chemicals c
                JOIN chemical_synonyms s ON s.cas_number = c.cas_number
                WHERE s.name = ? COLLATE NOCASE LIMIT 1
            ''', (identifier,)).fetchone()

        if not row:
            conn.close()
            return None

        d = dict(row)
        conn.close()
        return _master_row_to_result(d, identifier)

    except Exception:
        return None


def _master_row_to_result(d, identifier):
    """Convert a master DB row into the standard result dict."""
    def jload(v):
        try:
            return json.loads(v) if v else []
        except Exception:
            return []

    h_codes_eu = jload(d.get('ghs_eu_h_codes'))
    h_codes_us = jload(d.get('ghs_us_h_codes')) or h_codes_eu  # fall back to EU codes
    p_codes    = jload(d.get('p_codes'))
    signal     = d.get('ghs_us_signal') or d.get('ghs_eu_signal')

    # Build hazard statements from H-codes using local dictionary
    from .hazard_data import lookup_h_statement, lookup_p_statement
    h_statements = []
    for code in h_codes_us:
        text = lookup_h_statement(code)
        h_statements.append({'code': code, 'text': text, 'signal_word': '', 'hazard_class': ''})

    sources = jload(d.get('data_sources')) or ['Local DB']

    return {
        'identifier': identifier,
        'found': True,
        'source': 'local_db',
        'cid': d.get('pubchem_cid'),
        'name': d.get('common_name') or d.get('iupac_name') or identifier,
        'cas': d.get('cas_number'),
        'molecular_formula': d.get('molecular_formula', ''),
        'molecular_weight': str(d.get('molecular_weight', '')) if d.get('molecular_weight') else '',
        'boiling_point': d.get('boiling_point', ''),
        'melting_point': d.get('melting_point', ''),
        'flash_point': d.get('flash_point', ''),
        'auto_ignition': d.get('auto_ignition', ''),
        'vapor_pressure': d.get('vapor_pressure', ''),
        'density': d.get('density', ''),
        'solubility': d.get('solubility', ''),
        'ghs': {
            'signal_word': signal,
            'hazard_statements': h_statements,
            'precautionary_codes': p_codes,
            'pictograms': [],
        },
        'exposure_limits': {
            k: v for k, v in {
                'OSHA_PEL':  d.get('osha_pel', ''),
                'NIOSH_REL': d.get('niosh_rel', ''),
                'NIOSH_IDLH': d.get('niosh_idlh', ''),
                'ACGIH_TLV': d.get('acgih_tlv', ''),
            }.items() if v
        },
        'transport': {
            'un_number':    d.get('un_number', ''),
            'hazard_class': d.get('dot_hazard_class', ''),
            'packing_group': d.get('dot_packing_group', ''),
            'labels':       d.get('dot_labels', ''),
            'marine_pollutant': bool(d.get('marine_pollutant', 0)),
        },
        'regulatory': {
            'iarc_group':    d.get('iarc_group', ''),
            'ntp_carcinogen': d.get('ntp_carcinogen', ''),
            'prop65':        bool(d.get('prop65', 0)),
            'reach_svhc':    bool(d.get('reach_svhc', 0)),
            'reach_svhc_reason': d.get('reach_svhc_reason', ''),
        },
        'data_sources': sources,
    }


def _from_cache(key):
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            'SELECT data FROM chemical_cache WHERE identifier = ?', (key,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def _to_cache(key, data):
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            'INSERT OR REPLACE INTO chemical_cache (identifier, data) VALUES (?, ?)',
            (key, json.dumps(data))
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _get(url, timeout=15):
    try:
        resp = requests.get(url, timeout=timeout, headers={'User-Agent': 'SDS-Generator/1.0'})
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _get_cid(identifier):
    url = f"{PUBCHEM_BASE}/compound/name/{requests.utils.quote(identifier)}/cids/JSON"
    data = _get(url)
    if data:
        ids = data.get('IdentifierList', {}).get('CID', [])
        return ids[0] if ids else None
    return None


def _get_properties(cid):
    props = (
        'MolecularFormula,MolecularWeight,IUPACName,'
        'BoilingPoint,MeltingPoint,FlashPoint,'
        'AutoIgnitionTemperature,Solubility,Density'
    )
    url = f"{PUBCHEM_BASE}/compound/cid/{cid}/property/{props}/JSON"
    data = _get(url)
    if data:
        rows = data.get('PropertyTable', {}).get('Properties', [])
        return rows[0] if rows else {}
    return {}


def _get_synonyms(cid):
    """Try to find the CAS number from PubChem synonyms."""
    url = f"{PUBCHEM_BASE}/compound/cid/{cid}/synonyms/JSON"
    data = _get(url)
    if not data:
        return None, []
    synonyms = data.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
    cas = None
    for s in synonyms:
        if re.match(r'^\d{1,7}-\d{2}-\d$', s):
            cas = s
            break
    return cas, synonyms[:10]


def _parse_ghs(raw):
    result = {
        'signal_word': None,
        'hazard_statements': [],
        'precautionary_codes': [],
        'pictograms': [],
    }
    if not raw:
        return result

    try:
        sections = raw.get('Record', {}).get('Section', [])
        for top in sections:
            for sub in top.get('Section', []):
                heading = sub.get('TOCHeading', '')
                if 'GHS' not in heading and 'Safety' not in heading:
                    continue
                for info in sub.get('Information', []):
                    name = info.get('Name', '')
                    strings = [
                        s.get('String', '')
                        for s in info.get('Value', {}).get('StringWithMarkup', [])
                    ]
                    if 'Hazard Statement' in name:
                        result['hazard_statements'] = _parse_h_statements(strings)
                    elif 'Precautionary' in name:
                        codes = []
                        for s in strings:
                            codes += re.findall(r'P\d{3}(?:\+P\d{3})*', s)
                        result['precautionary_codes'] = list(dict.fromkeys(codes))
                    elif 'Signal' in name:
                        result['signal_word'] = strings[0].strip() if strings else None
                    elif 'Pictogram' in name:
                        result['pictograms'] = [s for s in strings if s]
    except Exception:
        pass

    # Derive signal word from hazard statement brackets if not found directly
    if not result['signal_word'] and result['hazard_statements']:
        words = {h.get('signal_word', '') for h in result['hazard_statements']}
        if 'Danger' in words:
            result['signal_word'] = 'Danger'
        elif 'Warning' in words:
            result['signal_word'] = 'Warning'

    return result


def _parse_h_statements(strings):
    stmts = []
    seen = set()
    for s in strings:
        # Format: "H225 (99%): Highly flammable liquid and vapour [Danger Flammable liquids]"
        m = re.match(r'(H\d{3}[A-Z]?)\s*(?:\([^)]+\))?\s*:\s*(.+?)(?:\s*\[(\w+)\s+(.+?)\])?\s*$', s)
        if m:
            code = m.group(1)
            if code in seen:
                continue
            seen.add(code)
            stmts.append({
                'code': code,
                'text': m.group(2).strip(),
                'signal_word': m.group(3) or '',
                'hazard_class': m.group(4) or '',
            })
    return stmts


def _fetch(identifier):
    cid = _get_cid(identifier)
    if not cid:
        return {
            'identifier': identifier,
            'found': False,
            'cid': None,
            'name': identifier,
        }

    time.sleep(0.3)
    props = _get_properties(cid)
    time.sleep(0.3)
    cas, synonyms = _get_synonyms(cid)
    time.sleep(0.3)

    url = f"{PUBCHEM_VIEW}/data/compound/{cid}/JSON?heading=GHS+Classification"
    raw_ghs = _get(url)
    ghs = _parse_ghs(raw_ghs)

    # Try exposure limits (OSHA/NIOSH)
    time.sleep(0.2)
    exposure = _get_exposure_limits(cid)

    return {
        'identifier': identifier,
        'found': True,
        'cid': cid,
        'name': props.get('IUPACName', identifier),
        'cas': cas,
        'synonyms': synonyms,
        'molecular_formula': props.get('MolecularFormula', ''),
        'molecular_weight': props.get('MolecularWeight', ''),
        'boiling_point': props.get('BoilingPoint', ''),
        'melting_point': props.get('MeltingPoint', ''),
        'flash_point': props.get('FlashPoint', ''),
        'auto_ignition': props.get('AutoIgnitionTemperature', ''),
        'solubility': props.get('Solubility', ''),
        'density': props.get('Density', ''),
        'ghs': ghs,
        'exposure_limits': exposure,
    }


def _get_exposure_limits(cid):
    """Fetch occupational exposure limits if available."""
    url = f"{PUBCHEM_VIEW}/data/compound/{cid}/JSON?heading=NIOSH+Pocket+Guide"
    data = _get(url)
    limits = {}
    if not data:
        return limits
    try:
        for top in data.get('Record', {}).get('Section', []):
            for sub in top.get('Section', []):
                for info in sub.get('Information', []):
                    name = info.get('Name', '')
                    strings = [
                        s.get('String', '')
                        for s in info.get('Value', {}).get('StringWithMarkup', [])
                    ]
                    val = '; '.join(strings)
                    if 'REL' in name:
                        limits['NIOSH_REL'] = val
                    elif 'PEL' in name:
                        limits['OSHA_PEL'] = val
                    elif 'TLV' in name:
                        limits['ACGIH_TLV'] = val
    except Exception:
        pass
    return limits
