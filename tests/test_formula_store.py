"""Tests for the formula library store and its routes."""

import os

import pytest
from werkzeug.datastructures import MultiDict

from app import formula_store


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'formulas.db')
    formula_store.init_schema(path)
    return path


def _form(name='Enamel White', **over):
    md = MultiDict()
    md['product_name'] = name
    md['product_code'] = over.get('product_code', 'EW-1')
    md['country'] = over.get('country', 'US')
    md['company_name'] = over.get('company_name', 'Acme')
    for cname, cas, pct in over.get('components',
                                    [('Toluene', '108-88-3', '40'),
                                     ('Acetone', '67-64-1', '60')]):
        md.add('chem_name', cname)
        md.add('chem_cas', cas)
        md.add('chem_pct', pct)
    return md


def test_save_and_get(db):
    fid, rev = formula_store.save_formula(_form(), db)
    assert rev == 1
    data = formula_store.get_revision(fid, db_path=db)
    assert data['name'] == 'Enamel White'
    assert data['revision'] == 1
    assert len(data['components']) == 2
    assert data['components'][0] == {'name': 'Toluene', 'cas': '108-88-3', 'pct': 40.0}


def test_resave_creates_new_revision(db):
    fid, r1 = formula_store.save_formula(_form(), db)
    fid2, r2 = formula_store.save_formula(
        _form(components=[('Water', '7732-18-5', '100')]), db)
    assert fid2 == fid          # same formula (same name)
    assert r2 == 2              # new revision

    # Revision 1 is immutable / still retrievable
    old = formula_store.get_revision(fid, 1, db)
    assert len(old['components']) == 2
    new = formula_store.get_revision(fid, 2, db)
    assert new['components'][0]['name'] == 'Water'


def test_list_formulas(db):
    formula_store.save_formula(_form('Alpha'), db)
    formula_store.save_formula(_form('Beta'), db)
    formula_store.save_formula(_form('Alpha'), db)   # Alpha rev 2

    listing = formula_store.list_formulas(db)
    names = {f['name']: f for f in listing}
    assert set(names) == {'Alpha', 'Beta'}
    assert names['Alpha']['latest_revision'] == 2
    assert names['Alpha']['component_count'] == 2


def test_revision_to_multidict_roundtrips_build_sds(db):
    fid, _ = formula_store.save_formula(_form(), db)
    data = formula_store.get_revision(fid, db_path=db)
    md = formula_store.revision_to_multidict(data)
    assert md.getlist('chem_name') == ['Toluene', 'Acetone']
    assert md.getlist('chem_pct') == ['40.0', '60.0']
    assert md['product_name'] == 'Enamel White'
    assert md['revision'] == '1'


def test_delete(db):
    fid, _ = formula_store.save_formula(_form(), db)
    formula_store.delete_formula(fid, db)
    assert formula_store.get_revision(fid, db_path=db) is None
    assert formula_store.list_formulas(db) == []


def test_save_requires_name(db):
    with pytest.raises(ValueError):
        formula_store.save_formula(MultiDict({'product_name': ''}), db)
