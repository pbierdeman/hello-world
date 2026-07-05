"""
Formula library — persistent storage for saved formulas and their revisions.

This uses a separate, app-writable SQLite database (instance/formulas.db) so
the read-only master chemical DB is never touched. Revisions are immutable:
saving under an existing product name creates revision N+1 rather than editing
the previous one, giving a simple audit trail.

Rehydration: get_revision() / revision_to_multidict() return a
werkzeug MultiDict shaped exactly like the submitted /generate form, so the
existing build_sds pipeline is reused unchanged for load-from-library and
batch generation.
"""

import os
import sqlite3

from werkzeug.datastructures import MultiDict

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'instance', 'formulas.db'
)

# Scalar form fields carried on each revision (name → form field)
_REVISION_FIELDS = (
    'country', 'language', 'intended_use',
    'company_name', 'company_address', 'company_phone',
    'company_email', 'emergency_phone', 'notes',
)


def _connect(db_path):
    # Resolve at call time so a rebind of DEFAULT_DB_PATH (see app factory) applies.
    db_path = db_path or DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_schema(db_path=None):
    conn = _connect(db_path)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS formulas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            product_code TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS formula_revisions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            formula_id   INTEGER NOT NULL REFERENCES formulas(id) ON DELETE CASCADE,
            revision     INTEGER NOT NULL,
            country      TEXT DEFAULT 'US',
            language     TEXT DEFAULT 'en',
            intended_use TEXT DEFAULT '',
            company_name TEXT DEFAULT '',
            company_address TEXT DEFAULT '',
            company_phone   TEXT DEFAULT '',
            company_email   TEXT DEFAULT '',
            emergency_phone TEXT DEFAULT '',
            notes        TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now')),
            UNIQUE(formula_id, revision)
        );
        CREATE TABLE IF NOT EXISTS formula_components (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            revision_id  INTEGER NOT NULL REFERENCES formula_revisions(id) ON DELETE CASCADE,
            position     INTEGER NOT NULL,
            chem_name    TEXT NOT NULL,
            cas          TEXT DEFAULT '',
            percent      REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_rev_formula ON formula_revisions(formula_id);
        CREATE INDEX IF NOT EXISTS idx_comp_rev ON formula_components(revision_id);
    ''')
    conn.commit()
    conn.close()


def save_formula(form_data, db_path=None):
    """Save the submitted form as a new revision. Returns (formula_id, revision)."""
    name = (form_data.get('product_name') or '').strip()
    if not name:
        raise ValueError('Product name is required to save a formula.')
    product_code = (form_data.get('product_code') or '').strip()

    conn = _connect(db_path)
    try:
        row = conn.execute('SELECT id FROM formulas WHERE name = ?', (name,)).fetchone()
        if row:
            formula_id = row['id']
            conn.execute('UPDATE formulas SET product_code = ? WHERE id = ?',
                         (product_code, formula_id))
        else:
            cur = conn.execute(
                'INSERT INTO formulas (name, product_code) VALUES (?, ?)',
                (name, product_code))
            formula_id = cur.lastrowid

        nxt = conn.execute(
            'SELECT COALESCE(MAX(revision), 0) + 1 AS n FROM formula_revisions '
            'WHERE formula_id = ?', (formula_id,)).fetchone()['n']

        cols = ', '.join(_REVISION_FIELDS)
        placeholders = ', '.join('?' for _ in _REVISION_FIELDS)
        values = [form_data.get(f, '') or '' for f in _REVISION_FIELDS]
        cur = conn.execute(
            f'INSERT INTO formula_revisions (formula_id, revision, {cols}) '
            f'VALUES (?, ?, {placeholders})',
            [formula_id, nxt] + values)
        revision_id = cur.lastrowid

        names = form_data.getlist('chem_name')
        cas_list = form_data.getlist('chem_cas')
        pcts = form_data.getlist('chem_pct')
        pos = 0
        for i, cname in enumerate(names):
            if not str(cname).strip():
                continue
            cas = cas_list[i] if i < len(cas_list) else ''
            try:
                pct = float(pcts[i]) if i < len(pcts) and pcts[i] != '' else 0.0
            except (ValueError, TypeError):
                pct = 0.0
            conn.execute(
                'INSERT INTO formula_components (revision_id, position, chem_name, cas, percent) '
                'VALUES (?, ?, ?, ?, ?)',
                (revision_id, pos, str(cname).strip(), str(cas).strip(), pct))
            pos += 1

        conn.commit()
        return formula_id, nxt
    finally:
        conn.close()


def list_formulas(db_path=None):
    """List saved formulas with their latest revision and component count."""
    conn = _connect(db_path)
    rows = conn.execute('''
        SELECT f.id, f.name, f.product_code,
               MAX(r.revision) AS latest_revision,
               MAX(r.created_at) AS updated_at
          FROM formulas f
          JOIN formula_revisions r ON r.formula_id = f.id
      GROUP BY f.id
      ORDER BY updated_at DESC
    ''').fetchall()

    result = []
    for row in rows:
        rev = conn.execute(
            'SELECT id FROM formula_revisions WHERE formula_id = ? AND revision = ?',
            (row['id'], row['latest_revision'])).fetchone()
        count = conn.execute(
            'SELECT COUNT(*) AS n FROM formula_components WHERE revision_id = ?',
            (rev['id'],)).fetchone()['n'] if rev else 0
        result.append({
            'id': row['id'],
            'name': row['name'],
            'product_code': row['product_code'],
            'latest_revision': row['latest_revision'],
            'revisions': list(range(1, row['latest_revision'] + 1)),
            'component_count': count,
            'updated_at': row['updated_at'],
        })
    conn.close()
    return result


def get_revision(formula_id, revision=None, db_path=None):
    """Return a revision as a plain dict (or None). Latest if revision is None."""
    conn = _connect(db_path)
    frow = conn.execute('SELECT * FROM formulas WHERE id = ?', (formula_id,)).fetchone()
    if not frow:
        conn.close()
        return None

    if revision is None:
        rrow = conn.execute(
            'SELECT * FROM formula_revisions WHERE formula_id = ? '
            'ORDER BY revision DESC LIMIT 1', (formula_id,)).fetchone()
    else:
        rrow = conn.execute(
            'SELECT * FROM formula_revisions WHERE formula_id = ? AND revision = ?',
            (formula_id, revision)).fetchone()
    if not rrow:
        conn.close()
        return None

    comps = conn.execute(
        'SELECT chem_name, cas, percent FROM formula_components '
        'WHERE revision_id = ? ORDER BY position', (rrow['id'],)).fetchall()
    conn.close()

    data = {
        'formula_id': formula_id,
        'name': frow['name'],
        'product_name': frow['name'],
        'product_code': frow['product_code'],
        'revision': rrow['revision'],
        'components': [
            {'name': c['chem_name'], 'cas': c['cas'], 'pct': c['percent']} for c in comps
        ],
    }
    for f in _REVISION_FIELDS:
        data[f] = rrow[f]
    return data


def revision_to_multidict(rev):
    """Convert a get_revision() dict into a MultiDict shaped like the form."""
    md = MultiDict()
    md['product_name'] = rev.get('product_name', rev.get('name', ''))
    md['product_code'] = rev.get('product_code', '')
    md['revision'] = str(rev.get('revision', 1))
    for f in _REVISION_FIELDS:
        md[f] = rev.get(f, '') or ''
    for c in rev.get('components', []):
        md.add('chem_name', c['name'])
        md.add('chem_cas', c.get('cas', '') or '')
        md.add('chem_pct', str(c.get('pct', '') if c.get('pct') is not None else ''))
    return md


def delete_formula(formula_id, db_path=None):
    conn = _connect(db_path)
    conn.execute('DELETE FROM formulas WHERE id = ?', (formula_id,))
    conn.commit()
    conn.close()
