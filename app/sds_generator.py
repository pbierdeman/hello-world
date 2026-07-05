"""Aggregate component data into a structured SDS dictionary."""

from datetime import date

from .hazard_data import (
    lookup_p_statement,
    get_pictograms_for_h_codes,
    CMR_CODES,
)

COUNTRIES = {
    'US': {
        'standard': 'OSHA HazCom 2012 (GHS)',
        'transport_authority': 'DOT (49 CFR)',
        'emergency_number': '1-800-424-9300 (CHEMTREC)',
        'regulatory_refs': (
            'OSHA 29 CFR 1910.1200 (Hazard Communication); '
            'TSCA; EPA RCRA (40 CFR 261); CERCLA; SARA Title III'
        ),
        'transport_regulation': 'US DOT (49 CFR)',
    },
    'CA': {
        'standard': 'WHMIS 2015 (GHS)',
        'transport_authority': 'TDG (Transportation of Dangerous Goods)',
        'emergency_number': '1-800-424-9300 (CANUTEC) or 613-996-6666',
        'regulatory_refs': (
            'WHMIS 2015 (Canada Occupational Health and Safety Regulations); '
            'CEPA; TDG Regulations SOR/2001-286'
        ),
        'transport_regulation': 'TDG (SOR/2001-286)',
    },
}


def build_sds(form_data, component_lookups):
    """
    Build a complete SDS data dict from form input and PubChem lookup results.

    form_data: dict from the web form
    component_lookups: list of dicts from pubchem.lookup_chemical
    """
    country = form_data.get('country', 'US')
    country_info = COUNTRIES.get(country, COUNTRIES['US'])

    components = _build_component_list(form_data, component_lookups)
    hazards = _aggregate_hazards(components)
    physical = _summarize_physical_props(components)

    return {
        'product_name': form_data.get('product_name', 'Unknown Product'),
        'product_code': form_data.get('product_code', ''),
        'revision_date': date.today().isoformat(),
        'version': form_data.get('revision') or '1.0',
        'country': country,
        'country_info': country_info,
        'manufacturer': {
            'name': form_data.get('company_name', ''),
            'address': form_data.get('company_address', ''),
            'phone': form_data.get('company_phone', ''),
            'emergency_phone': form_data.get('emergency_phone', country_info['emergency_number']),
            'email': form_data.get('company_email', ''),
        },
        'intended_use': form_data.get('intended_use', 'Paint / coating product'),
        'components': components,
        'hazards': hazards,
        'physical': physical,
        's1': _section1(form_data, country_info),
        's2': _section2(hazards, country),
        's3': _section3(components),
        's4': _section4(hazards),
        's5': _section5(hazards, physical),
        's6': _section6(hazards),
        's7': _section7(hazards, physical),
        's8': _section8(components, hazards),
        's9': _section9(physical, components),
        's10': _section10(hazards),
        's11': _section11(hazards, components),
        's12': _section12(hazards),
        's13': _section13(country),
        's14': _section14(hazards, country_info, country),
        's15': _section15(hazards, country_info),
        's16': _section16(),
    }


def _build_component_list(form_data, lookups):
    names = form_data.getlist('chem_name')
    cas_inputs = form_data.getlist('chem_cas')
    percents = form_data.getlist('chem_pct')

    components = []
    for i, lookup in enumerate(lookups):
        try:
            pct = float(percents[i]) if i < len(percents) else 0.0
        except (ValueError, TypeError):
            pct = 0.0

        components.append({
            'input_name': names[i] if i < len(names) else '',
            'cas_input': cas_inputs[i] if i < len(cas_inputs) else '',
            'percent': pct,
            'data': lookup,
        })
    return components


def _aggregate_hazards(components):
    """Apply GHS mixture classification rules (simplified)."""
    all_h = {}     # code -> statement dict
    all_p_codes = set()

    for comp in components:
        pct = comp['percent']
        data = comp['data']
        if not data.get('found'):
            continue

        ghs = data.get('ghs', {})
        for h in ghs.get('hazard_statements', []):
            code = h['code']
            threshold = 0.1 if code in CMR_CODES else 1.0
            if pct >= threshold:
                all_h[code] = h

        for p in ghs.get('precautionary_codes', []):
            all_p_codes.add(p)

    h_list = sorted(all_h.values(), key=lambda x: x['code'])
    h_codes = [h['code'] for h in h_list]

    signal_word = 'Warning'
    for h in h_list:
        if h.get('signal_word', '').lower() == 'danger':
            signal_word = 'Danger'
            break

    pictograms = get_pictograms_for_h_codes(h_codes)

    p_list = []
    for code in sorted(all_p_codes):
        text = lookup_p_statement(code)
        p_list.append({'code': code, 'text': text})

    flammable_codes = {
        'H224', 'H225', 'H226', 'H227', 'H228',
        'H220', 'H221', 'H222', 'H223',
    }
    toxic_codes = {'H300', 'H301', 'H310', 'H311', 'H330', 'H331'}
    corrosive_codes = {'H290', 'H314', 'H318'}
    irritant_codes = {'H315', 'H319', 'H335', 'H336'}
    enviro_codes = {'H400', 'H401', 'H410', 'H411', 'H412', 'H413'}
    aspiration_codes = {'H304', 'H305'}

    h_set = set(h_codes)
    return {
        'signal_word': signal_word,
        'h_statements': h_list,
        'p_statements': p_list,
        'pictograms': pictograms,
        'is_flammable': bool(h_set & flammable_codes),
        'is_toxic': bool(h_set & toxic_codes),
        'is_corrosive': bool(h_set & corrosive_codes),
        'is_irritant': bool(h_set & irritant_codes),
        'is_environmental': bool(h_set & enviro_codes),
        'is_aspiration_hazard': bool(h_set & aspiration_codes),
    }


def _summarize_physical_props(components):
    """Pick physical properties from the dominant component."""
    dominant = max(components, key=lambda c: c['percent'], default=None)
    if not dominant or not dominant['data'].get('found'):
        return {}
    d = dominant['data']
    return {
        'flash_point': d.get('flash_point', ''),
        'boiling_point': d.get('boiling_point', ''),
        'melting_point': d.get('melting_point', ''),
        'density': d.get('density', ''),
        'solubility': d.get('solubility', ''),
        'auto_ignition': d.get('auto_ignition', ''),
        'molecular_formula': d.get('molecular_formula', ''),
        'molecular_weight': d.get('molecular_weight', ''),
        'note': (
            f"Physical properties shown are for the dominant component "
            f"({dominant['input_name']}, {dominant['percent']:.1f}%). "
            "Actual mixture properties should be measured."
        ),
    }


# ── Section builders ──────────────────────────────────────────────────────────

def _section1(form_data, country_info):
    return {
        'title': 'Identification',
        'product_name': form_data.get('product_name', ''),
        'product_code': form_data.get('product_code', ''),
        'intended_use': form_data.get('intended_use', 'Paint / coating product'),
        'restrictions': 'For industrial/professional use only.',
        'company_name': form_data.get('company_name', ''),
        'company_address': form_data.get('company_address', ''),
        'company_phone': form_data.get('company_phone', ''),
        'company_email': form_data.get('company_email', ''),
        'emergency_phone': form_data.get('emergency_phone', country_info['emergency_number']),
    }


def _section2(hazards, country):
    label = (
        'This product has been classified in accordance with the hazard criteria of '
        'OSHA HazCom 2012 (29 CFR 1910.1200).'
        if country == 'US' else
        'This product has been classified in accordance with the hazard criteria of WHMIS 2015.'
    )
    unlisted = (
        'No additional hazards are known for the remaining components not listed in Section 3.'
    )
    return {
        'title': 'Hazard(s) Identification',
        'classification_label': label,
        'signal_word': hazards['signal_word'],
        'h_statements': hazards['h_statements'],
        'p_statements': hazards['p_statements'],
        'pictograms': hazards['pictograms'],
        'unlisted_note': unlisted,
    }


def _section3(components):
    rows = []
    for c in components:
        d = c['data']
        cas = d.get('cas') or c.get('cas_input') or 'N/A'
        rows.append({
            'name': c['input_name'],
            'cas': cas,
            'percent': f"{c['percent']:.1f}",
            'classification': _short_classification(d),
        })
    return {
        'title': 'Composition / Information on Ingredients',
        'type': 'Mixture',
        'rows': rows,
    }


def _short_classification(data):
    if not data.get('found'):
        return 'Data not available'
    codes = [h['code'] for h in data.get('ghs', {}).get('hazard_statements', [])]
    return ', '.join(codes[:4]) + (' ...' if len(codes) > 4 else '') if codes else 'Not classified'


def _section4(hazards):
    skin = eye = inhale = ingest = 'Seek medical attention if symptoms occur.'
    symptoms = []

    if hazards['is_corrosive']:
        skin = ('Remove contaminated clothing. Immediately flush skin with large amounts of water '
                'for at least 20 minutes. Seek immediate medical attention.')
        eye = ('Immediately flush eyes with water for at least 15–20 minutes while lifting upper '
               'and lower eyelids. Remove contact lenses if easy to do. Seek immediate medical attention.')
    elif hazards['is_irritant']:
        skin = ('Wash skin with soap and water for at least 15 minutes. '
                'Remove contaminated clothing. Seek medical attention if irritation persists.')
        eye = ('Flush eyes with water for at least 15 minutes. '
               'Remove contact lenses if easy to do. Seek medical attention if irritation persists.')

    if hazards['is_toxic']:
        inhale = ('Remove to fresh air immediately. If breathing has stopped, '
                  'give artificial respiration. Seek immediate medical attention.')
        ingest = ('Do NOT induce vomiting. Rinse mouth with water. '
                  'Seek immediate medical attention or call a Poison Control Center.')
    elif hazards['is_aspiration_hazard']:
        ingest = ('Do NOT induce vomiting — aspiration hazard. '
                  'Rinse mouth with water. Seek immediate medical attention.')
    elif hazards['is_irritant'] or hazards['is_flammable']:
        inhale = ('Remove to fresh air. If breathing is difficult, give oxygen. '
                  'Seek medical attention if symptoms persist.')

    if hazards['is_flammable']:
        symptoms.append('Drowsiness, dizziness, headache from vapour inhalation')
    if hazards['is_irritant']:
        symptoms.append('Skin redness, eye irritation, respiratory irritation')
    if hazards['is_toxic']:
        symptoms.append('Nausea, vomiting, central nervous system depression')

    return {
        'title': 'First-Aid Measures',
        'skin': skin,
        'eye': eye,
        'inhale': inhale,
        'ingest': ingest,
        'symptoms': symptoms or ['No specific symptoms known; seek medical attention if concerned.'],
        'physician_note': (
            'Treat symptomatically. No specific antidote known. '
            'For acute toxic exposures contact Poison Control (US: 1-800-222-1222).'
        ),
    }


def _section5(hazards, physical):
    if hazards['is_flammable']:
        media = 'Dry chemical, CO₂, foam, or water fog. Do not use a direct water stream.'
        special = ('Vapours may form explosive mixtures with air. '
                   'Containers may explode when heated. '
                   'Keep containers cool with water spray. '
                   'Evacuate area and fight fire from a safe distance.')
    else:
        media = 'Use extinguishing media appropriate for surrounding fire conditions.'
        special = 'Product itself is not flammable. Standard firefighting procedures apply.'

    flash = physical.get('flash_point', 'N/A')
    auto = physical.get('auto_ignition', 'N/A')

    return {
        'title': 'Fire-Fighting Measures',
        'suitable_media': media,
        'unsuitable_media': 'Do not use water jet on burning liquid.' if hazards['is_flammable'] else 'N/A',
        'special_hazards': special,
        'flash_point': flash if flash else 'N/A',
        'auto_ignition': auto if auto else 'N/A',
        'ppe': ('Full protective clothing, self-contained breathing apparatus (SCBA). '
                'Cool fire-exposed containers with water.'),
    }


def _section6(hazards):
    if hazards['is_flammable']:
        personal = ('Eliminate all ignition sources. '
                    'Use explosion-proof equipment. '
                    'Keep people away from and upwind of spill/leak. '
                    'Wear appropriate PPE (see Section 8).')
        cleanup = ('Contain spill using sand, earth, or other non-combustible absorbent material. '
                   'Collect in appropriate waste containers. '
                   'Do not use metal tools or equipment.')
    else:
        personal = 'Avoid contact with skin and eyes. Use appropriate PPE (see Section 8).'
        cleanup = ('Absorb with inert material. '
                   'Collect in appropriate waste containers for disposal per Section 13.')

    env = ('Prevent material from entering storm drains, waterways, or soil. '
           'Report spills to appropriate authorities per local regulations.')

    return {
        'title': 'Accidental Release Measures',
        'personal_precautions': personal,
        'environmental_precautions': env,
        'cleanup_methods': cleanup,
        'reference': 'See Sections 8 and 13 for personal protective equipment and disposal.',
    }


def _section7(hazards, physical):
    if hazards['is_flammable']:
        handling = ('Keep away from heat, sparks, and open flames. '
                    'Use only in well-ventilated areas. '
                    'Ground and bond containers when transferring. '
                    'Use explosion-proof equipment. '
                    'Avoid breathing vapours.')
        storage = ('Store in a cool, dry, well-ventilated area away from heat and ignition sources. '
                   'Keep container tightly closed. '
                   'Store away from oxidizing agents.')
    else:
        handling = ('Avoid contact with eyes, skin, and clothing. '
                    'Use in well-ventilated areas. '
                    'Follow good industrial hygiene practices.')
        storage = ('Store in a cool, dry, well-ventilated area. '
                   'Keep container tightly closed. '
                   'Keep away from incompatible materials.')

    return {
        'title': 'Handling and Storage',
        'handling': handling,
        'storage': storage,
        'incompatibilities': (
            'Strong oxidizers, strong acids, strong bases, '
            'heat sources, and ignition sources.'
            if hazards['is_flammable'] else
            'Keep away from incompatible materials; consult component SDS for specific data.'
        ),
        'safe_temperature': 'Store at temperatures below 50°C (122°F).',
    }


def _section8(components, hazards):
    # Collect exposure limits from component data
    limits = []
    for comp in components:
        d = comp['data']
        if not d.get('found'):
            continue
        el = d.get('exposure_limits', {})
        if el:
            limits.append({
                'name': comp['input_name'],
                'cas': d.get('cas') or comp.get('cas_input', ''),
                'osha_pel': el.get('OSHA_PEL', 'Not established'),
                'niosh_rel': el.get('NIOSH_REL', 'Not established'),
                'acgih_tlv': el.get('ACGIH_TLV', 'Not established'),
            })

    if hazards['is_flammable']:
        ppe = {
            'respiratory': 'Organic vapour respirator (if ventilation is inadequate)',
            'skin': 'Chemical-resistant gloves (nitrile or neoprene)',
            'eye': 'Safety glasses with side shields; chemical splash goggles if splashing possible',
            'body': 'Chemical-resistant clothing; antistatic footwear',
        }
        controls = ('Use in well-ventilated areas. Local exhaust ventilation preferred. '
                    'Keep airborne concentrations below exposure limits.')
    else:
        ppe = {
            'respiratory': 'Not normally required with adequate ventilation',
            'skin': 'Protective gloves appropriate for the product',
            'eye': 'Safety glasses or chemical splash goggles',
            'body': 'Lab coat or protective clothing',
        }
        controls = 'Provide adequate ventilation to maintain airborne concentrations below exposure limits.'

    return {
        'title': 'Exposure Controls / Personal Protection',
        'exposure_limits': limits,
        'no_limits_note': 'Consult regulatory sources for current OELs.' if not limits else '',
        'engineering_controls': controls,
        'ppe': ppe,
        'hygiene': (
            'Wash hands thoroughly before eating, drinking, or using restroom. '
            'Do not eat, drink, or smoke in work areas. '
            'Remove contaminated clothing before leaving work area.'
        ),
    }


def _section9(physical, components):
    def fmt(v):
        return str(v).strip() if v else 'N/A'

    return {
        'title': 'Physical and Chemical Properties',
        'appearance': 'Liquid (paint/coating mixture)',
        'odor': 'Characteristic',
        'odor_threshold': 'N/A',
        'ph': 'N/A — depends on formulation',
        'melting_point': fmt(physical.get('melting_point')),
        'boiling_point': fmt(physical.get('boiling_point')),
        'flash_point': fmt(physical.get('flash_point')),
        'evaporation_rate': 'N/A',
        'flammability': 'See Section 5',
        'vapor_pressure': 'N/A',
        'vapor_density': 'N/A',
        'relative_density': fmt(physical.get('density')),
        'solubility': fmt(physical.get('solubility')),
        'partition_coefficient': 'N/A',
        'auto_ignition': fmt(physical.get('auto_ignition')),
        'decomposition_temp': 'N/A',
        'viscosity': 'N/A',
        'note': physical.get('note', ''),
    }


def _section10(hazards):
    return {
        'title': 'Stability and Reactivity',
        'reactivity': 'No known reactivity hazards under normal conditions of use.',
        'stability': 'Stable under recommended storage and handling conditions.',
        'hazardous_reactions': (
            'Vapours may form explosive air-vapour mixtures at elevated temperatures.'
            if hazards['is_flammable'] else
            'No hazardous reactions known under normal conditions.'
        ),
        'conditions_to_avoid': (
            'Heat, sparks, open flames, ignition sources.'
            if hazards['is_flammable'] else
            'Extremes of temperature; incompatible materials.'
        ),
        'incompatible_materials': (
            'Strong oxidizers, strong acids, strong alkalis.'
        ),
        'hazardous_decomposition': (
            'Carbon monoxide (CO), carbon dioxide (CO₂), and other combustion products.'
        ),
    }


def _section11(hazards, components):
    toxicology_items = []
    for comp in components:
        d = comp['data']
        if not d.get('found'):
            continue
        codes = [h['code'] for h in d.get('ghs', {}).get('hazard_statements', [])]
        if codes:
            toxicology_items.append({
                'name': comp['input_name'],
                'cas': d.get('cas') or comp.get('cas_input', ''),
                'h_codes': ', '.join(codes),
            })

    return {
        'title': 'Toxicological Information',
        'routes': ['Inhalation', 'Skin contact', 'Eye contact', 'Ingestion'],
        'acute_effects': (
            'May cause drowsiness, dizziness, headache, or narcotic effects at high vapour concentrations.'
            if hazards['is_flammable'] else
            'Refer to component data in Section 3 for specific toxicological data.'
        ),
        'skin_corrosion': 'Yes — see Section 2' if hazards['is_corrosive'] else 'No',
        'eye_damage': 'Yes — see Section 2' if hazards['is_corrosive'] else 'Possible irritation',
        'sensitization': 'See component data.',
        'germ_cell': 'See component data and applicable regulatory lists.',
        'carcinogenicity': (
            'Some components may be classified as known or suspected carcinogens. '
            'See Section 3 for component listing. Consult IARC, NTP, and OSHA carcinogen lists.'
            if any(h in {h['code'] for c in components
                         for h in c['data'].get('ghs', {}).get('hazard_statements', [])}
                   for h in ['H350', 'H351'])
            else 'Not classified as carcinogenic based on available data.'
        ),
        'reproductive_toxicity': 'See component data.',
        'stot_single': 'See Section 2 H-statements.',
        'stot_repeated': 'See Section 2 H-statements.',
        'aspiration_hazard': 'Yes — do not induce vomiting.' if hazards['is_aspiration_hazard'] else 'Not classified.',
        'component_data': toxicology_items,
    }


def _section12(hazards):
    return {
        'title': 'Ecological Information',
        'ecotoxicity': (
            'This product contains components classified as hazardous to the aquatic environment. '
            'Prevent product from entering waterways, drains, or the environment.'
            if hazards['is_environmental'] else
            'No specific ecotoxicity data available for the mixture. '
            'Prevent release to the environment as a good practice.'
        ),
        'persistence': 'No data available for the mixture.',
        'bioaccumulation': 'No data available for the mixture.',
        'mobility': 'Liquid; may spread in soil and surface water if spilled.',
        'pbt_vpvb': 'Not assessed.',
        'other_effects': 'N/A',
        'note': (
            'REGULATORY NOTE: Ecological data for the complete mixture are not available. '
            'Handle and dispose of in accordance with Section 13.'
        ),
    }


def _section13(country):
    if country == 'CA':
        regs = (
            'Dispose of in accordance with Canadian Environmental Protection Act (CEPA) and '
            'provincial/territorial hazardous waste regulations.'
        )
    else:
        regs = (
            'Dispose of in accordance with federal, state, and local regulations. '
            'May be a RCRA hazardous waste (40 CFR 261). Contact your local environmental agency.'
        )

    return {
        'title': 'Disposal Considerations',
        'waste_treatment': (
            'Do not pour down drains or into waterways. '
            'Consult a licensed waste disposal company. '
            'Empty containers may retain residue — treat as hazardous waste.'
        ),
        'regulations': regs,
        'contaminated_packaging': (
            'Triple-rinse containers and recycle or dispose per local regulations. '
            'Do not reuse empty containers.'
        ),
    }


def _section14(hazards, country_info, country):
    # Determine UN number and hazard class based on predominant hazard
    if hazards['is_flammable']:
        un_number = 'UN1263'
        proper_name = ('PAINT (including paint, lacquer, enamel, stain, shellac, '
                       'varnish, polish, liquid filler, and liquid lacquer base)')
        hazard_class = '3'
        packing_group = 'II or III (depending on flash point)'
        marine_pollutant = 'No (unless aquatic hazard — verify per component data)'
    elif hazards['is_toxic']:
        un_number = 'UN2810'
        proper_name = 'TOXIC LIQUID, ORGANIC, N.O.S.'
        hazard_class = '6.1'
        packing_group = 'II or III'
        marine_pollutant = 'No'
    elif hazards['is_corrosive']:
        un_number = 'UN1760'
        proper_name = 'CORROSIVE LIQUID, N.O.S.'
        hazard_class = '8'
        packing_group = 'II or III'
        marine_pollutant = 'No'
    else:
        un_number = 'Not regulated'
        proper_name = 'Not regulated'
        hazard_class = 'Not regulated'
        packing_group = 'N/A'
        marine_pollutant = 'No'

    regulation = country_info['transport_regulation']

    return {
        'title': 'Transport Information',
        'un_number': un_number,
        'proper_shipping_name': proper_name,
        'hazard_class': hazard_class,
        'packing_group': packing_group,
        'marine_pollutant': marine_pollutant,
        'regulation': regulation,
        'special_precautions': (
            'Transport in accordance with applicable national and international regulations. '
            'Secure containers to prevent shifting during transport. '
            'Keep away from heat and ignition sources.'
            if un_number != 'Not regulated' else
            'Not classified as a dangerous good for transport under listed regulations.'
        ),
        'note': (
            'VERIFY: UN number, hazard class, and packing group must be confirmed '
            'based on measured flash point and formulation. '
            'Consult a qualified dangerous goods specialist before shipping.'
        ),
    }


def _section15(hazards, country_info):
    return {
        'title': 'Regulatory Information',
        'standard': country_info['standard'],
        'regulatory_refs': country_info['regulatory_refs'],
        'safety_note': (
            'This SDS was prepared in accordance with the regulatory standard listed above. '
            'Users are responsible for ensuring compliance with all applicable local, '
            'state/provincial, and federal regulations.'
        ),
    }


def _section16():
    return {
        'title': 'Other Information',
        'prepared_by': 'Generated by SDS Generator',
        'revision_date': date.today().isoformat(),
        'version': '1.0',
        'sources': [
            'PubChem (National Library of Medicine)',
            'OSHA HazCom 2012 (29 CFR 1910.1200)',
            'GHS Revision 9 (UN)',
            'NIOSH Pocket Guide to Chemical Hazards',
        ],
        'disclaimer': (
            'THIS DOCUMENT WAS COMPUTER-GENERATED FROM PUBLICLY AVAILABLE DATA. '
            'IT MUST BE REVIEWED AND APPROVED BY A QUALIFIED SAFETY PROFESSIONAL '
            'BEFORE USE. THE GENERATOR AND ITS OPERATORS ACCEPT NO LIABILITY '
            'FOR INCOMPLETE OR INACCURATE INFORMATION. ALWAYS VERIFY REGULATORY '
            'COMPLIANCE FOR YOUR SPECIFIC JURISDICTION AND INTENDED USE.'
        ),
    }
