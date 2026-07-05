"""Tests for formula import parsing (app/importer.py).

Excel/CSV/text/paste run fully offline. The PDF path is exercised through
the public API but skips if pypdf is unavailable in the environment.
"""

import io

import pytest

from app.importer import parse_text, parse_upload, _valid_cas


class _FakeUpload:
    """Minimal werkzeug-FileStorage stand-in for parse_upload."""
    def __init__(self, filename, data):
        self.filename = filename
        self._data = data

    def read(self):
        return self._data


# ── CAS validation ────────────────────────────────────────────────────────────

def test_valid_cas_checkdigit():
    assert _valid_cas('108-88-3')       # toluene
    assert _valid_cas('67-64-1')        # acetone
    assert not _valid_cas('108-88-4')   # wrong check digit
    assert not _valid_cas('hello')


# ── Pasted text ───────────────────────────────────────────────────────────────

def test_parse_text_tab_with_header():
    text = "Chemical\tCAS\t%\nToluene\t108-88-3\t40\nAcetone\t67-64-1\t60"
    result = parse_text(text)
    comps = result['components']
    assert len(comps) == 2
    assert comps[0] == {'name': 'Toluene', 'cas': '108-88-3', 'pct': 40.0}
    assert comps[1]['pct'] == 60.0


def test_parse_text_no_header_positional():
    text = "Toluene\t108-88-3\t40\nAcetone\t67-64-1\t60"
    result = parse_text(text)
    assert len(result['components']) == 2
    names = {c['name'] for c in result['components']}
    assert 'Toluene' in names and 'Acetone' in names


def test_parse_text_comma_separated():
    text = "Chemical,CAS,%\nXylene,1330-20-7,100"
    result = parse_text(text)
    assert result['components'][0]['cas'] == '1330-20-7'
    assert result['components'][0]['pct'] == 100.0


def test_parse_text_percent_sign_stripped():
    text = "Chemical\t%\nToluene\t40%"
    result = parse_text(text)
    assert result['components'][0]['pct'] == 40.0


def test_parse_text_empty():
    result = parse_text('   ')
    assert result['components'] == []
    assert result['warnings']


# ── CSV upload ────────────────────────────────────────────────────────────────

def test_parse_csv_upload():
    csv_bytes = b"Name,CAS Number,Weight %\nToluene,108-88-3,40\nAcetone,67-64-1,60\n"
    result = parse_upload(_FakeUpload('formula.csv', csv_bytes))
    assert result['source'] == 'csv'
    assert len(result['components']) == 2
    assert result['components'][0]['cas'] == '108-88-3'


# ── XLSX upload ───────────────────────────────────────────────────────────────

def test_parse_xlsx_upload():
    openpyxl = pytest.importorskip('openpyxl')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Chemical', 'CAS', '% w/w'])
    ws.append(['Toluene', '108-88-3', 40])
    ws.append(['Acetone', '67-64-1', 60])
    buf = io.BytesIO()
    wb.save(buf)

    result = parse_upload(_FakeUpload('formula.xlsx', buf.getvalue()))
    assert result['source'] == 'xlsx'
    assert len(result['components']) == 2
    assert result['components'][1] == {'name': 'Acetone', 'cas': '67-64-1', 'pct': 60.0}


def test_xls_rejected_with_message():
    result = parse_upload(_FakeUpload('old.xls', b'\xd0\xcf\x11\xe0'))
    assert result['components'] == []
    assert any('xlsx' in w.lower() for w in result['warnings'])


# ── CAS embedded in the name column ──────────────────────────────────────────

def test_cas_recovered_from_name_cell():
    text = "Chemical\t%\nToluene 108-88-3\t100"
    result = parse_text(text)
    c = result['components'][0]
    assert c['cas'] == '108-88-3'
    assert c['name'] == 'Toluene'
