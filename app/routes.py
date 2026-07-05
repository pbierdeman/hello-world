import io
import zipfile
from flask import Blueprint, render_template, request, send_file, jsonify

from .pubchem import lookup_chemical
from .sds_generator import build_sds, COUNTRIES
from .pdf_generator import generate_pdf
from .verify import run_checks
from . import formula_store

main = Blueprint('main', __name__)

MAX_BATCH = 200


def _safe_name(product_name):
    return ''.join(
        c if c.isalnum() or c in (' ', '-', '_') else '_' for c in product_name
    ).strip() or 'SDS'


def _generate_pdf_for(form):
    """Run the full lookup → build → render pipeline for one formula form."""
    lookups = _lookup_components(form)
    sds = build_sds(form, lookups)
    return sds, generate_pdf(sds)


def _lookup_components(form):
    """Look up every component in a submitted formula form.

    Uses the CAS number if provided, otherwise the chemical name.
    Returns a list of lookup-result dicts aligned with the form's chem_name rows.
    """
    names = form.getlist('chem_name')
    cas_list = form.getlist('chem_cas')
    lookups = []
    for i, name in enumerate(names):
        cas = cas_list[i].strip() if i < len(cas_list) else ''
        identifier = cas if cas else name.strip()
        if identifier:
            lookups.append(lookup_chemical(identifier))
        else:
            lookups.append({'identifier': '', 'found': False, 'name': name})
    return lookups


@main.route('/')
def index():
    return render_template('index.html')


@main.route('/formula')
def formula():
    form_data = None
    prefill_components = None
    load_id = request.args.get('load', type=int)
    if load_id:
        rev = request.args.get('rev', type=int)
        data = formula_store.get_revision(load_id, rev)
        if data:
            form_data = formula_store.revision_to_multidict(data)
            prefill_components = data['components']
    return render_template('formula.html', countries=COUNTRIES,
                           form_data=form_data,
                           prefill_components=prefill_components)


@main.route('/api/lookup')
def api_lookup():
    """AJAX endpoint: look up a single chemical and return name + CAS."""
    identifier = request.args.get('q', '').strip()
    if not identifier:
        return jsonify({'found': False, 'error': 'No identifier provided'}), 400
    data = lookup_chemical(identifier)
    return jsonify({
        'found': data.get('found', False),
        'name': data.get('name', identifier),
        'cas': data.get('cas', ''),
        'molecular_formula': data.get('molecular_formula', ''),
        'flash_point': data.get('flash_point', ''),
        'signal_word': data.get('ghs', {}).get('signal_word', ''),
        'h_codes': [h['code'] for h in data.get('ghs', {}).get('hazard_statements', [])],
    })


@main.route('/generate', methods=['POST'])
def generate():
    form = request.form

    names = form.getlist('chem_name')
    if not names or not any(n.strip() for n in names):
        return render_template(
            'formula.html',
            countries=COUNTRIES,
            error='Please add at least one chemical component.',
            form_data=form,
        )

    # Validate percentages sum roughly to 100
    try:
        total = sum(float(p or 0) for p in form.getlist('chem_pct'))
        if total <= 0:
            raise ValueError
    except ValueError:
        return render_template(
            'formula.html',
            countries=COUNTRIES,
            error='Please enter valid percentages for each component.',
            form_data=form,
        )

    # Look up each component (use CAS if provided, else name)
    lookups = _lookup_components(form)

    sds = build_sds(form, lookups)
    pdf_bytes = generate_pdf(sds)

    product_name = form.get('product_name', 'SDS').strip()
    safe_name = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in product_name)
    filename = f"SDS_{safe_name}_{sds['revision_date']}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename,
    )


@main.route('/api/import', methods=['POST'])
def api_import():
    """Parse an uploaded formula file or pasted text into component rows.

    Accepts either a multipart 'file' upload or JSON {"text": "..."}.
    Returns {components, warnings, source}. Never writes to any database.
    """
    from .importer import parse_upload, parse_text

    if 'file' in request.files and request.files['file'].filename:
        result = parse_upload(request.files['file'])
    else:
        payload = request.get_json(silent=True) or {}
        text = payload.get('text') or request.form.get('text', '')
        if not text.strip():
            return jsonify({'components': [], 'warnings': ['Nothing to import.'],
                            'source': 'text'}), 400
        result = parse_text(text)
    return jsonify(result)


@main.route('/api/suggest')
def api_suggest():
    """Autocomplete: return chemical name/CAS suggestions for a prefix."""
    q = request.args.get('q', '').strip()
    try:
        limit = min(int(request.args.get('limit', 10)), 25)
    except (ValueError, TypeError):
        limit = 10
    if len(q) < 2:
        return jsonify([])
    try:
        from db.schema import search_names
        return jsonify(search_names(q, limit))
    except Exception:
        return jsonify([])


@main.route('/api/verify', methods=['POST'])
def api_verify():
    """Run the completeness checker on a submitted formula and return a report.

    Accepts the same form encoding as /generate so the client can send
    new FormData(form) before generating the PDF.
    """
    form = request.form
    lookups = _lookup_components(form)
    sds = build_sds(form, lookups)
    report = run_checks(sds)
    return jsonify(report)


# ── Formula library ───────────────────────────────────────────────────────────

@main.route('/library')
def library():
    return render_template('library.html', formulas=formula_store.list_formulas())


@main.route('/api/formulas', methods=['GET'])
def api_formulas_list():
    return jsonify(formula_store.list_formulas())


@main.route('/api/formulas', methods=['POST'])
def api_formulas_save():
    """Save the current formula form as a new revision."""
    try:
        formula_id, revision = formula_store.save_formula(request.form)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'formula_id': formula_id, 'revision': revision})


@main.route('/api/formulas/<int:formula_id>', methods=['GET'])
def api_formula_get(formula_id):
    rev = request.args.get('rev', type=int)
    data = formula_store.get_revision(formula_id, rev)
    if not data:
        return jsonify({'error': 'Formula not found'}), 404
    return jsonify(data)


@main.route('/api/formulas/<int:formula_id>', methods=['DELETE'])
def api_formula_delete(formula_id):
    formula_store.delete_formula(formula_id)
    return jsonify({'deleted': formula_id})


@main.route('/generate-batch', methods=['POST'])
def generate_batch():
    """Generate SDS PDFs for several saved formulas and return them as a ZIP."""
    payload = request.get_json(silent=True) or {}
    items = payload.get('items', [])
    if not items:
        return jsonify({'error': 'No formulas selected.'}), 400
    if len(items) > MAX_BATCH:
        return jsonify({'error': f'Batch limited to {MAX_BATCH} formulas.'}), 400

    override_country = payload.get('country')
    buf = io.BytesIO()
    errors = []
    made = 0

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        used_names = set()
        for item in items:
            fid = item.get('formula_id')
            rev = item.get('revision')
            data = formula_store.get_revision(fid, rev)
            if not data:
                errors.append(f'Formula {fid} revision {rev}: not found.')
                continue
            try:
                form = formula_store.revision_to_multidict(data)
                if override_country:
                    form['country'] = override_country
                sds, pdf_bytes = _generate_pdf_for(form)
                fname = f"SDS_{_safe_name(data['name'])}_rev{data['revision']}.pdf"
                # Avoid collisions if two revisions share a sanitized name
                base, ext = fname[:-4], '.pdf'
                n = 2
                while fname in used_names:
                    fname = f"{base}_{n}{ext}"
                    n += 1
                used_names.add(fname)
                zf.writestr(fname, pdf_bytes)
                made += 1
            except Exception as exc:  # keep the batch going
                errors.append(f"{data.get('name', fid)}: {exc}")

        if errors:
            zf.writestr('_errors.txt',
                        'The following formulas could not be generated:\n\n'
                        + '\n'.join(errors))

    if made == 0:
        return jsonify({'error': 'No PDFs could be generated.', 'details': errors}), 400

    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name='SDS_batch.zip')
