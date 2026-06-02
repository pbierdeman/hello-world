import io
from flask import Blueprint, render_template, request, send_file, jsonify

from .pubchem import lookup_chemical
from .sds_generator import build_sds, COUNTRIES
from .pdf_generator import generate_pdf

main = Blueprint('main', __name__)


@main.route('/')
def index():
    return render_template('index.html')


@main.route('/formula')
def formula():
    return render_template('formula.html', countries=COUNTRIES)


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
    lookups = []
    cas_list = form.getlist('chem_cas')
    for i, name in enumerate(names):
        cas = cas_list[i].strip() if i < len(cas_list) else ''
        identifier = cas if cas else name.strip()
        if identifier:
            lookups.append(lookup_chemical(identifier))
        else:
            lookups.append({'identifier': '', 'found': False, 'name': name})

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
