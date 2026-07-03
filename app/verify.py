"""
Pre-print SDS verification checker.

`run_checks(sds)` consumes the dict produced by `sds_generator.build_sds`
and returns a structured report of completeness/consistency problems, so the
user can catch missing data before generating a document they might ship.

Severities:
  fail  — the SDS is unsafe or invalid to issue; generation should be blocked
  warn  — the SDS is issuable but has gaps a reviewer should confirm
  pass  — informational note (e.g. "no hazards found — confirm expected")

Return shape:
  {
    'status': 'pass' | 'warn' | 'fail',
    'checks': [
        {'id': str, 'severity': 'fail'|'warn'|'pass',
         'message': str, 'section': int | None},
        ...
    ],
  }
"""

# Section 9 physical-property fields checked for completeness
_PHYSICAL_FIELDS = (
    'flash_point', 'boiling_point', 'melting_point', 'density',
    'solubility', 'auto_ignition', 'molecular_weight',
)

_EMPTY = ('', 'N/A', 'NA', 'None', 'none', '-', '--', None)


def _blank(value):
    return str(value).strip() in [str(e) for e in _EMPTY]


def run_checks(sds):
    """Run all verification rules against a built SDS dict."""
    checks = []
    components = sds.get('components', [])
    hazards = sds.get('hazards', {})
    physical = sds.get('physical', {})
    manufacturer = sds.get('manufacturer', {})
    country_info = sds.get('country_info', {})

    named = [c for c in components if str(c.get('input_name', '')).strip()]

    # ── Fail-level rules ──────────────────────────────────────────────────────
    if not sds.get('product_name', '').strip():
        checks.append(_c('missing_product_name', 'fail',
                         'Product name is required.', 1))

    if not named:
        checks.append(_c('no_components', 'fail',
                         'At least one chemical component is required.', 3))

    for c in named:
        data = c.get('data', {})
        if not data.get('found'):
            checks.append(_c('unresolved_chemical', 'fail',
                             f"No hazard data found for \"{c['input_name']}\" — "
                             "an SDS without hazard data is unsafe to issue.", 3))

    total_pct = sum(_num(c.get('percent')) for c in named)
    if named and abs(total_pct - 100.0) > 0.5:
        checks.append(_c('pct_sum', 'fail',
                         f"Component percentages sum to {total_pct:.1f}%, not 100%.", 3))

    for c in named:
        if _num(c.get('percent')) <= 0:
            checks.append(_c('pct_zero', 'fail',
                             f"\"{c['input_name']}\" has a zero or missing percentage.", 3))

    # ── Warn-level rules ──────────────────────────────────────────────────────
    seen_cas = {}
    for c in named:
        cas = str(c.get('data', {}).get('cas') or c.get('cas_input') or '').strip()
        if cas:
            if cas in seen_cas:
                checks.append(_c('duplicate_cas', 'warn',
                                 f"CAS {cas} appears more than once "
                                 f"(\"{seen_cas[cas]}\" and \"{c['input_name']}\").", 3))
            else:
                seen_cas[cas] = c['input_name']

    if any(_blank(manufacturer.get(f)) for f in ('name', 'address', 'phone')):
        checks.append(_c('missing_company', 'warn',
                         'Company name, address, or phone is missing (Section 1).', 1))

    emerg = str(manufacturer.get('emergency_phone', '')).strip()
    if _blank(emerg) or emerg == str(country_info.get('emergency_number', '')).strip():
        checks.append(_c('missing_emergency', 'warn',
                         'No dedicated emergency phone number — using the jurisdiction '
                         'default. Confirm this is correct.', 1))

    # Exposure limits for hazardous components at reportable concentration
    for c in named:
        data = c.get('data', {})
        if _num(c.get('percent')) < 1.0:
            continue
        h_codes = data.get('ghs', {}).get('hazard_statements', [])
        if h_codes and not data.get('exposure_limits'):
            checks.append(_c('missing_exposure_limits', 'warn',
                             f"\"{c['input_name']}\" is hazardous and ≥1% but has no "
                             "occupational exposure limits (Section 8).", 8))

    # Transport data for a dangerous mixture
    if hazards.get('is_flammable') or hazards.get('is_toxic') or hazards.get('is_corrosive'):
        has_un = any(
            str(c.get('data', {}).get('transport', {}).get('un_number') or '').strip()
            for c in named
        )
        if not has_un:
            checks.append(_c('missing_transport', 'warn',
                             'Mixture appears flammable/toxic/corrosive but no component '
                             'carries a UN number — Section 14 uses heuristic defaults and '
                             'must be verified by a dangerous-goods specialist.', 14))

    if hazards.get('is_flammable') and _blank(physical.get('flash_point')):
        checks.append(_c('missing_flash_point', 'warn',
                         'Mixture is flammable but no flash point is available (Section 9).', 9))

    blank_phys = sum(1 for f in _PHYSICAL_FIELDS if _blank(physical.get(f)))
    if blank_phys >= 5:
        checks.append(_c('missing_physical', 'warn',
                         f'{blank_phys} of {len(_PHYSICAL_FIELDS)} physical properties are '
                         'missing (Section 9).', 9))

    # ── Informational ─────────────────────────────────────────────────────────
    if named and not hazards.get('h_statements'):
        checks.append(_c('no_hazards', 'pass',
                         'No hazards were classified for this mixture. Confirm this is '
                         'expected before issuing.', 2))

    status = 'fail' if any(c['severity'] == 'fail' for c in checks) else \
             'warn' if any(c['severity'] == 'warn' for c in checks) else 'pass'
    return {'status': status, 'checks': checks}


def _c(id_, severity, message, section):
    return {'id': id_, 'severity': severity, 'message': message, 'section': section}


def _num(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
