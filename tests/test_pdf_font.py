"""Tests that the SDS PDF embeds the Unicode font and renders EU/GHS glyphs.

reportlab is required; the test skips cleanly if it is not installed.
"""

import os

import pytest

pytest.importorskip("reportlab")

from db.schema import DB_PATH
from werkzeug.datastructures import MultiDict

_DB_READY = os.path.exists(DB_PATH)
skip_no_db = pytest.mark.skipif(not _DB_READY,
                                reason="master DB not built (run: python db_update.py)")


@skip_no_db
def test_pdf_embeds_dejavu_font():
    from app.pubchem import lookup_chemical
    from app.sds_generator import build_sds
    from app.pdf_generator import generate_pdf

    form = MultiDict([
        ('product_name', 'Font Test'), ('country', 'US'),
        ('chem_name', 'Toluene'), ('chem_cas', '108-88-3'), ('chem_pct', '100'),
    ])
    lookups = [lookup_chemical('108-88-3')]
    sds = build_sds(form, lookups)
    pdf = generate_pdf(sds)

    assert pdf[:4] == b'%PDF'
    # The Unicode font must be embedded (built-in Helvetica cannot render ₂/°/⚠).
    assert b'DejaVuSans' in pdf
    assert b'DejaVuSans-Bold' in pdf


def test_font_registration_is_idempotent():
    from app.pdf_generator import _register_fonts
    _register_fonts()
    _register_fonts()   # must not raise on a second call
