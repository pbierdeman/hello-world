"""Route-level tests for the library + batch generation flow.

Requires the bundled master chemical DB for component lookups; skips if it
has not been built.
"""

import io
import os
import zipfile

import pytest

from db.schema import DB_PATH
from app import create_app, formula_store

_DB_READY = os.path.exists(DB_PATH)
skip_no_db = pytest.mark.skipif(not _DB_READY,
                                reason="master DB not built (run: python db_update.py)")


@pytest.fixture
def client(tmp_path):
    app = create_app()
    # Isolate the formula library to a temp DB
    formula_store.DEFAULT_DB_PATH = str(tmp_path / 'formulas.db')
    formula_store.init_schema(formula_store.DEFAULT_DB_PATH)
    app.config['TESTING'] = True
    return app.test_client()


def _save(client, name):
    return client.post('/api/formulas', data={
        'product_name': name, 'country': 'US', 'company_name': 'Acme',
        'chem_name': ['Toluene', 'Acetone'],
        'chem_cas': ['108-88-3', '67-64-1'],
        'chem_pct': ['40', '60'],
    })


def test_save_lists_and_revisions(client):
    r = _save(client, 'Batch Enamel')
    assert r.status_code == 200
    assert r.get_json()['revision'] == 1

    r2 = _save(client, 'Batch Enamel')          # same name → rev 2
    assert r2.get_json()['revision'] == 2

    listing = client.get('/api/formulas').get_json()
    assert len(listing) == 1
    assert listing[0]['latest_revision'] == 2


def test_get_revision_route(client):
    fid = _save(client, 'Getter').get_json()['formula_id']
    data = client.get(f'/api/formulas/{fid}?rev=1').get_json()
    assert data['name'] == 'Getter'
    assert len(data['components']) == 2


def test_delete_route(client):
    fid = _save(client, 'Deleter').get_json()['formula_id']
    assert client.delete(f'/api/formulas/{fid}').status_code == 200
    assert client.get('/api/formulas').get_json() == []


@skip_no_db
def test_generate_batch_zip(client):
    f1 = _save(client, 'Alpha Coat').get_json()
    f2 = _save(client, 'Beta Coat').get_json()

    resp = client.post('/generate-batch', json={'items': [
        {'formula_id': f1['formula_id'], 'revision': 1},
        {'formula_id': f2['formula_id'], 'revision': 1},
    ]})
    assert resp.status_code == 200
    assert resp.mimetype == 'application/zip'

    zf = zipfile.ZipFile(io.BytesIO(resp.get_data()))
    pdfs = [n for n in zf.namelist() if n.endswith('.pdf')]
    assert len(pdfs) == 2
    # Each member is a real PDF
    for n in pdfs:
        assert zf.read(n)[:4] == b'%PDF'


def test_generate_batch_empty(client):
    assert client.post('/generate-batch', json={'items': []}).status_code == 400


@skip_no_db
def test_batch_missing_formula_reported(client):
    good = _save(client, 'Good Coat').get_json()
    resp = client.post('/generate-batch', json={'items': [
        {'formula_id': good['formula_id'], 'revision': 1},
        {'formula_id': 99999, 'revision': 1},        # does not exist
    ]})
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.get_data()))
    assert '_errors.txt' in zf.namelist()
    assert len([n for n in zf.namelist() if n.endswith('.pdf')]) == 1
