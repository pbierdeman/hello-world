"""Tests for autocomplete search and the /api/suggest route.

These run offline against the bundled 28-chemical master DB. If the DB
has not been built, the tests are skipped rather than failing.
"""

import os

import pytest

from db.schema import DB_PATH, search_names
from app import create_app

_DB_READY = os.path.exists(DB_PATH)
skip_no_db = pytest.mark.skipif(not _DB_READY,
                                reason="master DB not built (run: python db_update.py)")


@pytest.fixture
def client():
    return create_app().test_client()


@skip_no_db
def test_search_names_prefix():
    results = search_names('tol')
    names = [r['name'].lower() for r in results]
    assert any('toluene' in n for n in names)


@skip_no_db
def test_search_names_returns_cas():
    results = search_names('acetone')
    assert results
    assert results[0]['cas'] == '67-64-1'


def test_search_names_min_length():
    # Fewer than 2 chars must return nothing regardless of DB state
    assert search_names('t') == []
    assert search_names('') == []


@skip_no_db
def test_suggest_route(client):
    r = client.get('/api/suggest?q=tol')
    assert r.status_code == 200
    data = r.get_json()
    assert any('toluene' in item['name'].lower() for item in data)


def test_suggest_route_short_query(client):
    r = client.get('/api/suggest?q=t')
    assert r.status_code == 200
    assert r.get_json() == []
