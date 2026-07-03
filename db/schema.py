"""
Master chemical database — schema and connection management.

This module owns the SQLite database that stores pre-loaded data from:
  - ECHA C&L Inventory  (~150k classified chemicals)
  - NIOSH Pocket Guide  (~700 chemicals, complete OEL/health data)
  - DOT HMT             (~3,200 entries, UN numbers + transport class)

The app's pubchem.py lookup falls back to this database before hitting
the PubChem API, so lookups for any pre-loaded chemical are instant
and work fully offline.
"""

import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), 'chemicals_master.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_schema():
    """Create all tables if they do not exist."""
    conn = get_conn()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS chemicals (
            cas_number          TEXT PRIMARY KEY,
            ec_number           TEXT,
            pubchem_cid         INTEGER,
            iupac_name          TEXT,
            common_name         TEXT,
            molecular_formula   TEXT,
            molecular_weight    REAL,

            -- Physical properties (raw strings as reported by source)
            boiling_point       TEXT,
            melting_point       TEXT,
            flash_point         TEXT,
            auto_ignition       TEXT,
            vapor_pressure      TEXT,
            density             TEXT,
            solubility          TEXT,

            -- GHS classifications per jurisdiction (stored as JSON arrays of H-codes)
            ghs_us_h_codes      TEXT,   -- JSON list of H-code strings
            ghs_us_signal       TEXT,
            ghs_eu_h_codes      TEXT,
            ghs_eu_signal       TEXT,

            -- Precautionary codes (JSON list)
            p_codes             TEXT,

            -- Occupational exposure limits
            osha_pel            TEXT,
            niosh_rel           TEXT,
            niosh_idlh          TEXT,
            acgih_tlv           TEXT,
            eu_oel              TEXT,

            -- Regulatory flags
            iarc_group          TEXT,
            ntp_carcinogen      TEXT,
            prop65              INTEGER DEFAULT 0,
            tsca_active         INTEGER DEFAULT 1,
            reach_svhc          INTEGER DEFAULT 0,
            reach_svhc_reason   TEXT,

            -- Transport (UN system used by US/CA/MX/EU)
            un_number           TEXT,
            dot_hazard_class    TEXT,
            dot_packing_group   TEXT,
            dot_labels          TEXT,
            dot_special_prov    TEXT,
            marine_pollutant    INTEGER DEFAULT 0,

            -- Metadata
            data_sources        TEXT,   -- JSON list of source names
            last_updated        TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_chemicals_cas
            ON chemicals(cas_number);
        CREATE INDEX IF NOT EXISTS idx_chemicals_name
            ON chemicals(common_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_chemicals_pubchem
            ON chemicals(pubchem_cid);

        -- Alternative names / synonyms
        CREATE TABLE IF NOT EXISTS chemical_synonyms (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cas_number  TEXT NOT NULL REFERENCES chemicals(cas_number) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            lang        TEXT DEFAULT 'en',
            source      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_synonyms_name
            ON chemical_synonyms(name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_synonyms_cas
            ON chemical_synonyms(cas_number);

        -- Full GHS hazard statement details (code + text, per jurisdiction)
        CREATE TABLE IF NOT EXISTS ghs_statements (
            cas_number      TEXT NOT NULL REFERENCES chemicals(cas_number) ON DELETE CASCADE,
            jurisdiction    TEXT NOT NULL,   -- 'US', 'EU', 'CA', 'MX'
            code            TEXT NOT NULL,
            statement_text  TEXT,
            signal_word     TEXT,
            hazard_class    TEXT,
            PRIMARY KEY (cas_number, jurisdiction, code)
        );
        CREATE INDEX IF NOT EXISTS idx_ghs_cas
            ON ghs_statements(cas_number);

        -- Import log to track which files have been loaded
        CREATE TABLE IF NOT EXISTS import_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            filename    TEXT,
            rows_loaded INTEGER,
            imported_at TEXT DEFAULT (datetime('now'))
        );
    ''')
    conn.commit()
    conn.close()
    print(f"Schema ready: {DB_PATH}")


def lookup_by_cas(cas):
    """Return a Row (or None) for the given CAS number."""
    conn = get_conn()
    row = conn.execute(
        'SELECT * FROM chemicals WHERE cas_number = ?', (cas.strip(),)
    ).fetchone()
    conn.close()
    return row


def lookup_by_name(name):
    """Search synonyms table for a case-insensitive name match."""
    conn = get_conn()
    row = conn.execute('''
        SELECT c.* FROM chemicals c
        JOIN chemical_synonyms s ON s.cas_number = c.cas_number
        WHERE s.name = ? COLLATE NOCASE
        LIMIT 1
    ''', (name.strip(),)).fetchone()
    if not row:
        # also try common_name column directly
        row = conn.execute(
            'SELECT * FROM chemicals WHERE common_name = ? COLLATE NOCASE LIMIT 1',
            (name.strip(),)
        ).fetchone()
    conn.close()
    return row


def search_names(prefix, limit=10):
    """Prefix search across synonyms and common names for autocomplete.

    Returns a list of {name, cas, common_name} dicts, best (shortest) matches
    first. Uses the case-insensitive index on chemical_synonyms.name and
    chemicals.common_name, so a prefix LIKE is index-backed and fast.
    """
    prefix = (prefix or '').strip()
    if len(prefix) < 2:
        return []

    conn = get_conn()
    like = prefix + '%'
    rows = conn.execute('''
        SELECT name, cas_number, common_name FROM (
            SELECT s.name AS name, s.cas_number AS cas_number, c.common_name AS common_name
              FROM chemical_synonyms s
              JOIN chemicals c ON c.cas_number = s.cas_number
             WHERE s.name LIKE ? COLLATE NOCASE
            UNION
            SELECT common_name AS name, cas_number, common_name
              FROM chemicals
             WHERE common_name LIKE ? COLLATE NOCASE
        )
        GROUP BY cas_number
        ORDER BY length(name), name COLLATE NOCASE
        LIMIT ?
    ''', (like, like, limit)).fetchall()
    conn.close()
    return [
        {'name': r['name'], 'cas': r['cas_number'], 'common_name': r['common_name']}
        for r in rows
    ]


def row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict, decoding JSON fields."""
    if row is None:
        return None
    d = dict(row)
    for field in ('ghs_us_h_codes', 'ghs_eu_h_codes', 'p_codes', 'data_sources'):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = []
    return d


def stats():
    conn = get_conn()
    chem_count = conn.execute('SELECT COUNT(*) FROM chemicals').fetchone()[0]
    syn_count  = conn.execute('SELECT COUNT(*) FROM chemical_synonyms').fetchone()[0]
    ghs_count  = conn.execute('SELECT COUNT(*) FROM ghs_statements').fetchone()[0]
    imports    = conn.execute(
        'SELECT source, rows_loaded, imported_at FROM import_log ORDER BY imported_at DESC LIMIT 10'
    ).fetchall()
    conn.close()
    return {
        'chemicals': chem_count,
        'synonyms': syn_count,
        'ghs_statements': ghs_count,
        'recent_imports': [dict(r) for r in imports],
    }


if __name__ == '__main__':
    init_schema()
    s = stats()
    print(f"Chemicals: {s['chemicals']:,}")
    print(f"Synonyms:  {s['synonyms']:,}")
    print(f"GHS rows:  {s['ghs_statements']:,}")
