"""Tests for the SDS verification checker (app/verify.py)."""

from app.verify import run_checks


def _sds(**over):
    """Minimal valid SDS dict; override individual keys per test."""
    base = {
        'product_name': 'Test Product',
        'country_info': {'emergency_number': '1-800-DEFAULT'},
        'manufacturer': {
            'name': 'Acme', 'address': '1 St', 'phone': '555-1',
            'emergency_phone': '555-CUSTOM',
        },
        'components': [
            {'input_name': 'Toluene', 'cas_input': '108-88-3', 'percent': 40.0,
             'data': {'found': True, 'cas': '108-88-3',
                      'ghs': {'hazard_statements': [{'code': 'H225'}]},
                      'exposure_limits': {'OSHA_PEL': '200 ppm'},
                      'transport': {'un_number': 'UN1294'}}},
            {'input_name': 'Acetone', 'cas_input': '67-64-1', 'percent': 60.0,
             'data': {'found': True, 'cas': '67-64-1',
                      'ghs': {'hazard_statements': [{'code': 'H225'}]},
                      'exposure_limits': {'OSHA_PEL': '1000 ppm'},
                      'transport': {'un_number': 'UN1090'}}},
        ],
        'hazards': {'h_statements': [{'code': 'H225'}], 'is_flammable': True},
        'physical': {'flash_point': '4 C', 'boiling_point': '111 C',
                     'density': '0.87', 'solubility': 'low',
                     'auto_ignition': '480 C', 'melting_point': '-95 C',
                     'molecular_weight': '92'},
    }
    base.update(over)
    return base


def _ids(report):
    return {c['id'] for c in report['checks']}


def test_clean_formula_passes():
    report = run_checks(_sds())
    assert report['status'] == 'pass', _ids(report)


def test_missing_product_name_fails():
    report = run_checks(_sds(product_name=''))
    assert report['status'] == 'fail'
    assert 'missing_product_name' in _ids(report)


def test_no_components_fails():
    report = run_checks(_sds(components=[]))
    assert 'no_components' in _ids(report)
    assert report['status'] == 'fail'


def test_percent_sum_off_fails():
    sds = _sds()
    sds['components'][0]['percent'] = 10.0   # 10 + 60 = 70
    report = run_checks(sds)
    assert 'pct_sum' in _ids(report)
    assert report['status'] == 'fail'


def test_zero_percent_fails():
    sds = _sds()
    sds['components'][0]['percent'] = 0.0
    report = run_checks(sds)
    assert 'pct_zero' in _ids(report)


def test_unresolved_chemical_fails():
    sds = _sds()
    sds['components'][0]['data'] = {'found': False}
    report = run_checks(sds)
    assert 'unresolved_chemical' in _ids(report)
    assert report['status'] == 'fail'


def test_duplicate_cas_warns():
    sds = _sds()
    sds['components'][1]['data']['cas'] = '108-88-3'   # same as component 0
    report = run_checks(sds)
    assert 'duplicate_cas' in _ids(report)


def test_missing_company_warns():
    sds = _sds()
    sds['manufacturer']['address'] = ''
    report = run_checks(sds)
    assert 'missing_company' in _ids(report)


def test_default_emergency_warns():
    sds = _sds()
    sds['manufacturer']['emergency_phone'] = '1-800-DEFAULT'   # equals jurisdiction default
    report = run_checks(sds)
    assert 'missing_emergency' in _ids(report)


def test_missing_exposure_limits_warns():
    sds = _sds()
    sds['components'][0]['data']['exposure_limits'] = {}
    report = run_checks(sds)
    assert 'missing_exposure_limits' in _ids(report)


def test_missing_flash_point_warns():
    sds = _sds()
    sds['physical']['flash_point'] = ''
    report = run_checks(sds)
    assert 'missing_flash_point' in _ids(report)


def test_no_hazards_note():
    sds = _sds(hazards={'h_statements': []})
    report = run_checks(sds)
    assert 'no_hazards' in _ids(report)
