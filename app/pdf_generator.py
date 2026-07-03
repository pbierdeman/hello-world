"""ReportLab-based GHS SDS PDF generator."""

import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Unicode fonts ─────────────────────────────────────────────────────────────
# The built-in Helvetica cannot render the subscripts, degree signs, warning
# glyphs, and accented (EU-language) characters used throughout the SDS text.
# DejaVu Sans covers the full Latin range plus ₂ ° ⚠ – etc. We ship regular +
# bold; there is no oblique face, so italic maps to regular.
_FONTS_DIR = os.path.join(os.path.dirname(__file__), 'fonts')
FONT = 'DejaVuSans'
FONT_BOLD = 'DejaVuSans-Bold'
FONT_ITALIC = 'DejaVuSans'       # no oblique face — fall back to regular
FONT_BOLD_ITALIC = 'DejaVuSans-Bold'

_fonts_registered = False


def _register_fonts():
    """Register the DejaVu Sans family once. Safe to call repeatedly."""
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont(FONT, os.path.join(_FONTS_DIR, 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(_FONTS_DIR, 'DejaVuSans-Bold.ttf')))
    # Map the family so <b>/<i> markup inside Paragraphs resolves correctly.
    pdfmetrics.registerFontFamily(
        FONT, normal=FONT, bold=FONT_BOLD, italic=FONT_ITALIC, boldItalic=FONT_BOLD_ITALIC,
    )
    _fonts_registered = True


_register_fonts()

# ── Colour palette ────────────────────────────────────────────────────────────
BLACK = colors.black
WHITE = colors.white
DARK_BLUE = colors.HexColor('#1a3a5c')
MID_BLUE = colors.HexColor('#2c5f8a')
LIGHT_BLUE = colors.HexColor('#d6e8f7')
YELLOW = colors.HexColor('#ffd700')
RED = colors.HexColor('#c0392b')
GRAY = colors.HexColor('#555555')
LIGHT_GRAY = colors.HexColor('#f0f0f0')
ORANGE = colors.HexColor('#e67e22')


def _styles():
    base = getSampleStyleSheet()
    return {
        'doc_title': ParagraphStyle(
            'doc_title',
            fontSize=18, fontName=FONT_BOLD,
            textColor=WHITE, alignment=TA_CENTER, spaceAfter=2,
        ),
        'doc_subtitle': ParagraphStyle(
            'doc_subtitle',
            fontSize=11, fontName=FONT,
            textColor=WHITE, alignment=TA_CENTER, spaceAfter=0,
        ),
        'product_name': ParagraphStyle(
            'product_name',
            fontSize=14, fontName=FONT_BOLD,
            textColor=DARK_BLUE, spaceBefore=6, spaceAfter=2,
        ),
        'section_heading': ParagraphStyle(
            'section_heading',
            fontSize=11, fontName=FONT_BOLD,
            textColor=WHITE, spaceBefore=0, spaceAfter=0,
            leftIndent=6,
        ),
        'body': ParagraphStyle(
            'body',
            fontSize=9, fontName=FONT,
            textColor=BLACK, spaceBefore=2, spaceAfter=2,
            leading=13,
        ),
        'body_bold': ParagraphStyle(
            'body_bold',
            fontSize=9, fontName=FONT_BOLD,
            textColor=BLACK, spaceBefore=2, spaceAfter=2,
        ),
        'small': ParagraphStyle(
            'small',
            fontSize=8, fontName=FONT,
            textColor=GRAY, spaceBefore=1, spaceAfter=1,
            leading=11,
        ),
        'disclaimer': ParagraphStyle(
            'disclaimer',
            fontSize=7.5, fontName=FONT_BOLD,
            textColor=colors.HexColor('#8b0000'),
            borderColor=RED, borderWidth=1, borderPadding=4,
            spaceBefore=4, spaceAfter=4, leading=11,
        ),
        'signal_danger': ParagraphStyle(
            'signal_danger',
            fontSize=14, fontName=FONT_BOLD,
            textColor=WHITE, alignment=TA_CENTER,
        ),
        'signal_warning': ParagraphStyle(
            'signal_warning',
            fontSize=14, fontName=FONT_BOLD,
            textColor=BLACK, alignment=TA_CENTER,
        ),
        'note': ParagraphStyle(
            'note',
            fontSize=8, fontName=FONT_ITALIC,
            textColor=GRAY, spaceBefore=2, spaceAfter=2,
            leading=11,
        ),
        'footer': ParagraphStyle(
            'footer',
            fontSize=7, fontName=FONT,
            textColor=GRAY, alignment=TA_CENTER,
        ),
    }


def _page_template(canvas, doc, sds):
    canvas.saveState()
    w, h = LETTER

    # Footer line
    canvas.setStrokeColor(MID_BLUE)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.55 * inch, w - 0.75 * inch, 0.55 * inch)

    canvas.setFont(FONT, 7)
    canvas.setFillColor(GRAY)
    product = sds.get('product_name', '')
    rev = sds.get('revision_date', '')
    canvas.drawString(0.75 * inch, 0.38 * inch, f"Product: {product}  |  Revision: {rev}")
    canvas.drawRightString(
        w - 0.75 * inch, 0.38 * inch,
        f"Page {doc.page}"
    )
    canvas.drawCentredString(
        w / 2, 0.38 * inch,
        "SAFETY DATA SHEET — FOR EMERGENCY USE AND TRANSPORTATION"
    )
    canvas.restoreState()


def generate_pdf(sds):
    """Return PDF bytes for the given SDS data dict."""
    buf = io.BytesIO()
    st = _styles()

    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.85 * inch,
        title=f"SDS — {sds.get('product_name', '')}",
        author=sds.get('manufacturer', {}).get('name', ''),
    )

    story = []
    story += _header_block(sds, st)
    story += _s1(sds, st)
    story += _s2(sds, st)
    story += _s3(sds, st)
    story += _s4(sds, st)
    story += _s5(sds, st)
    story += _s6(sds, st)
    story += _s7(sds, st)
    story += _s8(sds, st)
    story += _s9(sds, st)
    story += _s10(sds, st)
    story += _s11(sds, st)
    story += _s12(sds, st)
    story += _s13(sds, st)
    story += _s14(sds, st)
    story += _s15(sds, st)
    story += _s16(sds, st)

    doc.build(
        story,
        onFirstPage=lambda c, d: _page_template(c, d, sds),
        onLaterPages=lambda c, d: _page_template(c, d, sds),
    )
    buf.seek(0)
    return buf.read()


# ── Helper builders ───────────────────────────────────────────────────────────

def _section_header(number, title, st):
    """Blue banner for each section."""
    cell = Paragraph(f"SECTION {number} — {title.upper()}", st['section_heading'])
    tbl = Table([[cell]], colWidths=[6.75 * inch])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK_BLUE),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    return [Spacer(1, 6), tbl, Spacer(1, 4)]


def _label_value(label, value, st, bold_label=True):
    style = st['body_bold'] if bold_label else st['body']
    return Paragraph(f"<b>{label}:</b> {value or 'N/A'}", st['body'])


def _bullet(text, st):
    return Paragraph(f"• {text}", st['body'])


def _hr(st):
    return HRFlowable(width='100%', thickness=0.5, color=LIGHT_BLUE, spaceAfter=3)


def _field_table(rows, st, col_widths=None):
    """2-column label/value table."""
    if col_widths is None:
        col_widths = [1.8 * inch, 4.95 * inch]
    data = [
        [Paragraph(f"<b>{k}</b>", st['body']), Paragraph(str(v or 'N/A'), st['body'])]
        for k, v in rows
    ]
    tbl = Table(data, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [WHITE, LIGHT_GRAY]),
    ]))
    return tbl


# ── Document header ───────────────────────────────────────────────────────────

def _header_block(sds, st):
    story = []
    # Title banner
    title_cell = Table(
        [[Paragraph('SAFETY DATA SHEET', st['doc_title'])],
         [Paragraph(sds['country_info']['standard'], st['doc_subtitle'])]],
        colWidths=[6.75 * inch],
    )
    title_cell.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK_BLUE),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(title_cell)
    story.append(Spacer(1, 6))

    # Product + company info
    mfr = sds.get('manufacturer', {})
    meta_rows = [
        ('Product Name', f"<b>{sds.get('product_name', '')}</b>"),
        ('Product Code', sds.get('product_code', '') or 'N/A'),
        ('Revision Date', sds.get('revision_date', '')),
        ('Version', sds.get('version', '1.0')),
        ('Company', mfr.get('name', '') or 'N/A'),
        ('Emergency Phone', mfr.get('emergency_phone', '') or 'N/A'),
    ]
    story.append(_field_table(meta_rows, st))
    story.append(Spacer(1, 8))

    # Disclaimer
    story.append(Paragraph(sds['s16']['disclaimer'], st['disclaimer']))
    story.append(Spacer(1, 4))
    return story


# ── Section 1 ─────────────────────────────────────────────────────────────────

def _s1(sds, st):
    s = sds['s1']
    story = _section_header(1, s['title'], st)
    mfr = sds.get('manufacturer', {})
    rows = [
        ('Product Name', s['product_name']),
        ('Product Code', s.get('product_code') or 'N/A'),
        ('Intended Use', s['intended_use']),
        ('Restrictions', s['restrictions']),
        ('Supplier / Manufacturer', s['company_name'] or 'N/A'),
        ('Address', s['company_address'] or 'N/A'),
        ('Phone', s['company_phone'] or 'N/A'),
        ('Email', s['company_email'] or 'N/A'),
        ('Emergency Phone', s['emergency_phone'] or 'N/A'),
    ]
    story.append(_field_table(rows, st))
    return story


# ── Section 2 ─────────────────────────────────────────────────────────────────

def _s2(sds, st):
    s = sds['s2']
    h = sds['hazards']
    story = _section_header(2, s['title'], st)

    story.append(Paragraph(s['classification_label'], st['small']))
    story.append(Spacer(1, 4))

    # Signal word box
    sw = h.get('signal_word', 'Warning')
    sw_style = st['signal_danger'] if sw == 'Danger' else st['signal_warning']
    sw_color = RED if sw == 'Danger' else YELLOW
    sw_cell = Table([[Paragraph(f"⚠ {sw.upper()}", sw_style)]], colWidths=[2 * inch])
    sw_cell.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), sw_color),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 1, BLACK),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(sw_cell)
    story.append(Spacer(1, 6))

    # Pictograms
    if h.get('pictograms'):
        story.append(Paragraph('<b>GHS Hazard Pictograms:</b>', st['body_bold']))
        pic_cells = []
        for ghs_id, label, desc in h['pictograms']:
            cell_para = [
                Paragraph(f"<b>{ghs_id}</b>", st['small']),
                Paragraph(label, st['small']),
            ]
            pic_cells.append(cell_para)

        # Arrange in rows of 4
        rows_data = []
        row = []
        for i, cell in enumerate(pic_cells):
            tbl = Table([cell], colWidths=[1.4 * inch])
            tbl.setStyle(TableStyle([
                ('BOX', (0, 0), (-1, -1), 1, MID_BLUE),
                ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BLUE),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            row.append(tbl)
            if len(row) == 4:
                rows_data.append(row)
                row = []
        if row:
            while len(row) < 4:
                row.append('')
            rows_data.append(row)

        if rows_data:
            pic_table = Table(rows_data, colWidths=[1.6 * inch] * 4)
            pic_table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]))
            story.append(pic_table)
            story.append(Spacer(1, 6))

    # Hazard statements
    if h.get('h_statements'):
        story.append(Paragraph('<b>Hazard Statements:</b>', st['body_bold']))
        for stmt in h['h_statements']:
            story.append(_bullet(f"{stmt['code']}: {stmt['text']}", st))

    # Precautionary statements
    if h.get('p_statements'):
        story.append(Spacer(1, 4))
        story.append(Paragraph('<b>Precautionary Statements:</b>', st['body_bold']))
        for stmt in h['p_statements'][:20]:  # Limit to avoid page overflow
            story.append(_bullet(f"{stmt['code']}: {stmt['text']}", st))

    story.append(Spacer(1, 4))
    story.append(Paragraph(s['unlisted_note'], st['small']))
    return story


# ── Section 3 ─────────────────────────────────────────────────────────────────

def _s3(sds, st):
    s = sds['s3']
    story = _section_header(3, s['title'], st)
    story.append(_label_value('Type', s['type'], st))
    story.append(Spacer(1, 4))

    headers = ['Chemical Name', 'CAS Number', '% (w/w)', 'GHS Classification']
    header_row = [Paragraph(f"<b>{h}</b>", st['body']) for h in headers]
    data = [header_row]
    for row in s['rows']:
        data.append([
            Paragraph(row['name'], st['body']),
            Paragraph(row['cas'] or 'N/A', st['body']),
            Paragraph(row['percent'] + '%', st['body']),
            Paragraph(row['classification'], st['small']),
        ])

    col_widths = [2.5 * inch, 1.2 * inch, 0.8 * inch, 2.25 * inch]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), MID_BLUE),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)
    story.append(Paragraph(
        '* Concentration shown as nominal % by weight. Ranges may apply depending on batch.',
        st['note']
    ))
    return story


# ── Sections 4–16 ─────────────────────────────────────────────────────────────

def _two_col_section(heading_num, data_dict, field_map, st):
    story = _section_header(heading_num, data_dict['title'], st)
    rows = [(label, data_dict.get(key, 'N/A')) for label, key in field_map if key in data_dict]
    story.append(_field_table(rows, st))
    return story


def _s4(sds, st):
    s = sds['s4']
    story = _section_header(4, s['title'], st)
    rows = [
        ('Skin Contact', s['skin']),
        ('Eye Contact', s['eye']),
        ('Inhalation', s['inhale']),
        ('Ingestion', s['ingest']),
        ('Note to Physician', s['physician_note']),
    ]
    story.append(_field_table(rows, st, col_widths=[1.4 * inch, 5.35 * inch]))
    story.append(Paragraph('<b>Symptoms:</b>', st['body_bold']))
    for sym in s['symptoms']:
        story.append(_bullet(sym, st))
    return story


def _s5(sds, st):
    s = sds['s5']
    story = _section_header(5, s['title'], st)
    rows = [
        ('Suitable Extinguishing Media', s['suitable_media']),
        ('Unsuitable Media', s['unsuitable_media']),
        ('Flash Point', s['flash_point']),
        ('Auto-Ignition Temp.', s['auto_ignition']),
        ('Special Fire Hazards', s['special_hazards']),
        ('Protective Equipment', s['ppe']),
    ]
    story.append(_field_table(rows, st, col_widths=[1.8 * inch, 4.95 * inch]))
    return story


def _s6(sds, st):
    s = sds['s6']
    story = _section_header(6, s['title'], st)
    rows = [
        ('Personal Precautions', s['personal_precautions']),
        ('Environmental Precautions', s['environmental_precautions']),
        ('Containment/Cleanup', s['cleanup_methods']),
        ('References', s['reference']),
    ]
    story.append(_field_table(rows, st, col_widths=[1.8 * inch, 4.95 * inch]))
    return story


def _s7(sds, st):
    s = sds['s7']
    story = _section_header(7, s['title'], st)
    rows = [
        ('Safe Handling', s['handling']),
        ('Storage Conditions', s['storage']),
        ('Incompatible Materials', s['incompatibilities']),
        ('Temperature Limit', s['safe_temperature']),
    ]
    story.append(_field_table(rows, st, col_widths=[1.4 * inch, 5.35 * inch]))
    return story


def _s8(sds, st):
    s = sds['s8']
    story = _section_header(8, s['title'], st)
    story.append(Paragraph('<b>Occupational Exposure Limits:</b>', st['body_bold']))

    if s.get('exposure_limits'):
        headers = ['Chemical', 'CAS', 'OSHA PEL', 'NIOSH REL', 'ACGIH TLV']
        hrow = [Paragraph(f"<b>{h}</b>", st['small']) for h in headers]
        data = [hrow]
        for lim in s['exposure_limits']:
            data.append([
                Paragraph(lim['name'], st['small']),
                Paragraph(lim.get('cas', ''), st['small']),
                Paragraph(lim.get('osha_pel', 'N/A'), st['small']),
                Paragraph(lim.get('niosh_rel', 'N/A'), st['small']),
                Paragraph(lim.get('acgih_tlv', 'N/A'), st['small']),
            ])
        tbl = Table(data, colWidths=[1.5*inch, 0.9*inch, 1.4*inch, 1.4*inch, 1.5*inch])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), MID_BLUE),
            ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph(
            s.get('no_limits_note', 'No occupational exposure limits found. Consult regulatory sources.'),
            st['body']
        ))

    story.append(Spacer(1, 4))
    story.append(_label_value('Engineering Controls', s['engineering_controls'], st))
    story.append(Spacer(1, 4))
    story.append(Paragraph('<b>Personal Protective Equipment:</b>', st['body_bold']))
    ppe = s['ppe']
    for key, label in [('respiratory', 'Respiratory'), ('skin', 'Skin'), ('eye', 'Eye/Face'), ('body', 'Body')]:
        story.append(_bullet(f"<b>{label}:</b> {ppe.get(key, 'N/A')}", st))
    story.append(_label_value('Hygiene Measures', s['hygiene'], st))
    return story


def _s9(sds, st):
    s = sds['s9']
    story = _section_header(9, s['title'], st)
    rows = [
        ('Appearance', s['appearance']),
        ('Odour', s['odor']),
        ('pH', s['ph']),
        ('Melting / Freezing Point', s['melting_point']),
        ('Boiling Point', s['boiling_point']),
        ('Flash Point', s['flash_point']),
        ('Auto-Ignition Temp.', s['auto_ignition']),
        ('Relative Density', s['relative_density']),
        ('Solubility in Water', s['solubility']),
        ('Viscosity', s['viscosity']),
        ('Vapour Pressure', s['vapor_pressure']),
        ('Vapour Density', s['vapor_density']),
    ]
    story.append(_field_table(rows, st))
    if s.get('note'):
        story.append(Paragraph(f"<i>Note: {s['note']}</i>", st['note']))
    return story


def _s10(sds, st):
    s = sds['s10']
    story = _section_header(10, s['title'], st)
    rows = [
        ('Reactivity', s['reactivity']),
        ('Chemical Stability', s['stability']),
        ('Hazardous Reactions', s['hazardous_reactions']),
        ('Conditions to Avoid', s['conditions_to_avoid']),
        ('Incompatible Materials', s['incompatible_materials']),
        ('Hazardous Decomposition Products', s['hazardous_decomposition']),
    ]
    story.append(_field_table(rows, st, col_widths=[2.0 * inch, 4.75 * inch]))
    return story


def _s11(sds, st):
    s = sds['s11']
    story = _section_header(11, s['title'], st)
    rows = [
        ('Routes of Exposure', ', '.join(s['routes'])),
        ('Acute Effects', s['acute_effects']),
        ('Skin Corrosion/Irritation', s['skin_corrosion']),
        ('Serious Eye Damage/Irritation', s['eye_damage']),
        ('Sensitization', s['sensitization']),
        ('Germ Cell Mutagenicity', s['germ_cell']),
        ('Carcinogenicity', s['carcinogenicity']),
        ('Reproductive Toxicity', s['reproductive_toxicity']),
        ('STOT — Single Exposure', s['stot_single']),
        ('STOT — Repeated Exposure', s['stot_repeated']),
        ('Aspiration Hazard', s['aspiration_hazard']),
    ]
    story.append(_field_table(rows, st, col_widths=[2.0 * inch, 4.75 * inch]))

    if s.get('component_data'):
        story.append(Spacer(1, 4))
        story.append(Paragraph('<b>Hazardous Component Data:</b>', st['body_bold']))
        headers = ['Component', 'CAS', 'GHS H-Codes']
        hrow = [Paragraph(f"<b>{h}</b>", st['small']) for h in headers]
        data = [hrow]
        for cd in s['component_data']:
            data.append([
                Paragraph(cd['name'], st['small']),
                Paragraph(cd.get('cas', ''), st['small']),
                Paragraph(cd['h_codes'], st['small']),
            ])
        tbl = Table(data, colWidths=[2.5 * inch, 1.2 * inch, 3.05 * inch])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), MID_BLUE),
            ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT_GRAY]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)
    return story


def _s12(sds, st):
    s = sds['s12']
    story = _section_header(12, s['title'], st)
    rows = [
        ('Ecotoxicity', s['ecotoxicity']),
        ('Persistence / Degradability', s['persistence']),
        ('Bioaccumulative Potential', s['bioaccumulation']),
        ('Mobility in Soil', s['mobility']),
        ('PBT / vPvB Assessment', s['pbt_vpvb']),
    ]
    story.append(_field_table(rows, st, col_widths=[2.0 * inch, 4.75 * inch]))
    story.append(Paragraph(s['note'], st['note']))
    return story


def _s13(sds, st):
    s = sds['s13']
    story = _section_header(13, s['title'], st)
    rows = [
        ('Waste Treatment Methods', s['waste_treatment']),
        ('Applicable Regulations', s['regulations']),
        ('Contaminated Packaging', s['contaminated_packaging']),
    ]
    story.append(_field_table(rows, st, col_widths=[2.0 * inch, 4.75 * inch]))
    return story


def _s14(sds, st):
    s = sds['s14']
    story = _section_header(14, s['title'], st)
    rows = [
        ('UN Number', s['un_number']),
        ('Proper Shipping Name', s['proper_shipping_name']),
        ('Hazard Class', s['hazard_class']),
        ('Packing Group', s['packing_group']),
        ('Marine Pollutant', s['marine_pollutant']),
        ('Regulation', s['regulation']),
        ('Special Precautions', s['special_precautions']),
    ]
    story.append(_field_table(rows, st, col_widths=[1.8 * inch, 4.95 * inch]))
    # Transport verification note
    note_cell = Table(
        [[Paragraph(f"⚠ VERIFY: {s['note']}", st['disclaimer'])]],
        colWidths=[6.75 * inch]
    )
    note_cell.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, RED),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fff0f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(Spacer(1, 4))
    story.append(note_cell)
    return story


def _s15(sds, st):
    s = sds['s15']
    story = _section_header(15, s['title'], st)
    rows = [
        ('Compliance Standard', s['standard']),
        ('Applicable Regulations', s['regulatory_refs']),
    ]
    story.append(_field_table(rows, st, col_widths=[1.8 * inch, 4.95 * inch]))
    story.append(Paragraph(s['safety_note'], st['small']))
    return story


def _s16(sds, st):
    s = sds['s16']
    story = _section_header(16, s['title'], st)
    rows = [
        ('Prepared By', s['prepared_by']),
        ('Revision Date', s['revision_date']),
        ('Version', s['version']),
    ]
    story.append(_field_table(rows, st))
    story.append(Spacer(1, 4))
    story.append(Paragraph('<b>Data Sources:</b>', st['body_bold']))
    for src in s['sources']:
        story.append(_bullet(src, st))
    story.append(Spacer(1, 6))
    # Final disclaimer box
    disc_cell = Table(
        [[Paragraph(s['disclaimer'], st['disclaimer'])]],
        colWidths=[6.75 * inch]
    )
    disc_cell.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1.5, RED),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fff8f8')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(disc_cell)
    return story
