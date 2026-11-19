"""Fourteen-section, source-traceable PDF reports for saved analyses."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import json
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

NAVY = colors.HexColor("#17324F")
BLUE = colors.HexColor("#246B8F")
PALE = colors.HexColor("#EAF1F5")
MUTED = colors.HexColor("#607181")
LINE = colors.HexColor("#D6E0E7")
GREEN = colors.HexColor("#21835B")
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_REGULAR = FONT_DIR / "DejaVuSans.ttf"
FONT_BOLD = FONT_DIR / "DejaVuSans-Bold.ttf"

SECTIONS = [
    "Cover",
    "Executive summary",
    "Ingredient profile",
    "Search and matching",
    "Evidence overview",
    "Carrier comparison",
    "Formulation batches",
    "Experimental results",
    "Biological evaluation",
    "Release kinetics",
    "References",
    "AI interpretation",
    "Limitations",
    "Appendix and glossary",
]


def _register_fonts():
    if not FONT_REGULAR.exists() or not FONT_BOLD.exists():
        raise FileNotFoundError("Bundled DejaVu Sans font files are required for Unicode PDF output.")
    if "SmartNano" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("SmartNano", str(FONT_REGULAR)))
        pdfmetrics.registerFont(TTFont("SmartNano-Bold", str(FONT_BOLD)))
        pdfmetrics.registerFontFamily("SmartNano", normal="SmartNano", bold="SmartNano-Bold",
                                      italic="SmartNano", boldItalic="SmartNano-Bold")


def _plain(value, missing="Not reported"):
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "null"}):
        return missing
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _excerpt(value, limit=220, missing="Not reported"):
    text = _plain(value, missing)
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip() if " " in text[:limit] else text[:limit]
    return clipped + "… [excerpt; see source record ID]"


def _p(value, style="Cell", styles=None, missing="Not reported"):
    text = escape(_plain(value, missing)).replace("\n", "<br/>")
    return Paragraph(text, styles[style])


def _table(headers, rows, widths, styles, font_size=7.1):
    data = [[_p(x, "TableHead", styles) for x in headers]]
    data.extend([[_p(x, "Cell", styles) for x in row] for row in rows])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE),
        ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), .35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("FONTNAME", (0, 0), (-1, -1), "SmartNano"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
    ]))
    return table


def _study_type(value):
    """Map only explicit source labels; uncertain/unknown labels remain pending review."""
    raw = _plain(value, "").strip().lower()
    if not raw:
        return "Pending review"
    checks = (
        (r"human clinical|clinical trial|clinical study", "Human clinical"),
        (r"ex vivo human|human tissue ex vivo", "Ex vivo human tissue"),
        (r"animal.*in vivo|in vivo.*animal", "Animal in vivo"),
        (r"animal.*ex vivo|ex vivo.*animal", "Animal ex vivo"),
        (r"cell[- ]based.*in vitro|in vitro.*cell", "Cell-based in vitro"),
        (r"physicochemical only|physicochemical characterization only", "Physicochemical only"),
        (r"other in vitro|in vitro other", "Other in vitro"),
    )
    for pattern, label in checks:
        if re.search(pattern, raw):
            return label
    return "Pending review"


class _PageCountCanvas(canvas.Canvas):
    """Delay painting page numbers until the final total is known."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._page_states = []

    def showPage(self):
        self._page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._page_states)
        for state in self._page_states:
            self.__dict__.update(state)
            self.saveState()
            self.setFont("SmartNano", 6.2)
            self.setFillColor(MUTED)
            self.drawString(16 * mm, 17 * mm,
                "Research prototype only; not clinically validated; no dosing, safety, efficacy or delivery assurance.")
            self.setStrokeColor(LINE)
            self.line(16 * mm, 14 * mm, 194 * mm, 14 * mm)
            self.drawString(16 * mm, 9 * mm, "SmartNanoFormulator · verify every source and extraction")
            self.drawRightString(194 * mm, 9 * mm, f"Page {self._pageNumber} of {total}")
            self.restoreState()
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


def pdf(res, analysis_id=None, created=None):
    """Build a Unicode PDF with explicit source lineage and all required report sections."""
    _register_fonts()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CoverTitle", fontName="SmartNano-Bold", fontSize=23, leading=29,
                              textColor=NAVY, alignment=TA_LEFT, spaceAfter=10))
    styles.add(ParagraphStyle(name="CoverSub", fontName="SmartNano", fontSize=13, leading=18,
                              textColor=BLUE, spaceAfter=9))
    styles.add(ParagraphStyle(name="Section", fontName="SmartNano-Bold", fontSize=14, leading=18,
                              textColor=NAVY, spaceBefore=2, spaceAfter=8, keepWithNext=True))
    styles.add(ParagraphStyle(name="Sub", fontName="SmartNano-Bold", fontSize=9.5, leading=12,
                              textColor=BLUE, spaceBefore=7, spaceAfter=4, keepWithNext=True))
    styles.add(ParagraphStyle(name="Body", fontName="SmartNano", fontSize=8.6, leading=12,
                              textColor=colors.HexColor("#263B4A"), spaceAfter=5))
    styles.add(ParagraphStyle(name="Cell", fontName="SmartNano", fontSize=7.1, leading=9,
                              textColor=colors.HexColor("#263B4A"), splitLongWords=1))
    styles.add(ParagraphStyle(name="TableHead", fontName="SmartNano-Bold", fontSize=6.9, leading=8.5,
                              textColor=NAVY, splitLongWords=1))
    styles.add(ParagraphStyle(name="Small", fontName="SmartNano", fontSize=7.1, leading=9,
                              textColor=MUTED, spaceAfter=4))
    styles.add(ParagraphStyle(name="Contents", fontName="SmartNano", fontSize=9, leading=14,
                              textColor=colors.HexColor("#263B4A"), leftIndent=8))
    styles.add(ParagraphStyle(name="Callout", fontName="SmartNano-Bold", fontSize=9, leading=12,
                              textColor=NAVY, backColor=PALE, borderColor=LINE, borderWidth=.5,
                              borderPadding=7, spaceBefore=4, spaceAfter=7))

    inp = res.get("input") or {}
    values = res.get("values_used") or {}
    provenance = res.get("provenance") or {}
    property_sources = res.get("property_sources") or {}
    carriers = res.get("carrier_results") or []
    organ = res.get("organ_context") or {}
    candidate_records = [x for c in carriers for x in (c.get("candidates") or [])]
    refs = res.get("references") or []
    stamp = created or res.get("created") or "Not recorded"
    analysis_ref = analysis_id if analysis_id is not None else res.get("analysis_id")

    story = []
    # 1 — Cover
    story.extend([
        Spacer(1, 26 * mm),
        _p("01 · Cover", "Section", styles),
        _p("SMARTNANOFORMULATOR", "CoverTitle", styles),
        _p("Source-traceable formulation evidence report", "CoverSub", styles),
        Spacer(1, 7 * mm),
        _p(f"Analysis {analysis_ref if analysis_ref is not None else 'not recorded'}", "Section", styles),
        _p(f"Ingredient: {_plain(inp.get('drug_name'))}", "Body", styles),
        _p(f"Target: {_plain(inp.get('target_organ'))} · {_plain(inp.get('target_tissue'))} · {_plain(inp.get('route'))}", "Body", styles),
        _p(f"Created: {stamp}", "Body", styles),
        Spacer(1, 6 * mm),
        _p("Evidence retrieval only. A catalogue match is not a carrier recommendation, efficacy conclusion, or prediction for a new batch.", "Callout", styles),
        _p("Research prototype; primary sources require independent review and any proposed formulation requires experimental validation.", "Small", styles),
        PageBreak(),
    ])

    # Contents page lists the complete numbered document structure.
    story.append(_p("Contents", "Section", styles))
    for index, title in enumerate(SECTIONS, 1):
        story.append(_p(f"{index:02d}  {title}", "Contents", styles))
    story.extend([Spacer(1, 5 * mm), _p("Page numbers are printed as Page X of Y. Detailed tables repeat their header row when they continue across pages.", "Small", styles), PageBreak()])

    # 2 — Executive summary
    story.append(_p("02 · Executive summary", "Section", styles))
    story.append(_p(res.get("decision") or "No decision summary available.", "Callout", styles))
    story.append(_p("Direct, related same-class, and other-ingredient context remain separate. Counts describe catalogue representation and do not rank carrier performance.", "Body", styles))
    direct_total = sum(int(c.get("direct_batches") or 0) for c in carriers)
    related_total = sum(int(c.get("analogue_batches") or 0) for c in carriers)
    context_total = sum(int(c.get("other_context_batches") or 0) for c in carriers)
    organ_direct = sum(int(x.get("direct_examples") or 0) for x in organ.get("targeting_examples_by_carrier", []))
    organ_related = sum(int(x.get("same_class_examples") or 0) for x in organ.get("targeting_examples_by_carrier", []))
    organ_context = sum(int(x.get("other_drug_context_examples") or 0) for x in organ.get("targeting_examples_by_carrier", []))
    story.append(_table(["Evidence branch", "Batch / example count", "Interpretation"], [
        ["Direct ingredient/form batch matches", direct_total, "Exact selected identity/form linkage"],
        ["Related same-class batch matches", related_total, "Indirect; not direct ingredient evidence"],
        ["Other-ingredient batch context", context_total, "Context only; not drug-specific"],
        ["Separate organ examples: direct / related / context", f"{organ_direct} / {organ_related} / {organ_context}", "Not merged with batch evidence"],
    ], [54 * mm, 34 * mm, 90 * mm], styles))
    story.append(Spacer(1, 4))
    story.append(_p(f"Catalogue update date: {_plain(max((c.get('catalogue_updated_at') for c in carriers if c.get('catalogue_updated_at')), default=None), 'Not recorded')}. The supplied catalogue contains only the bundled records; this report does not imply comprehensive literature coverage.", "Small", styles))
    story.append(PageBreak())

    # 3 — Ingredient profile
    story.append(_p("03 · Ingredient profile", "Section", styles))
    story.append(_p(f"Selected name: {_plain(inp.get('drug_name'))} · Ingredient ID: {_plain(inp.get('ingredient_id'))} · Form ID: {_plain(inp.get('form_id'))}", "Body", styles))
    story.append(_p("Identity-linked properties only; a parent ingredient property is not inherited by an explicitly selected form. Manual values and retrieved observations are distinct.", "Small", styles))
    profile_rows = []
    prop_labels = [("mw", "Molecular weight", "g/mol"), ("logp", "LogP", ""), ("pka", "pKa", ""), ("solubility_mg_ml", "Aqueous solubility", "mg/mL")]
    for key, label, default_unit in prop_labels:
        src = property_sources.get(key) or {}
        value = values.get(key)
        unit = src.get("unit") or default_unit
        shown = _excerpt(value)
        if value is not None and unit:
            shown = f"{shown} {unit}"
        origin = provenance.get(key) or ("Not reported" if value is None else "Provenance not recorded")
        manual = str(origin).lower().startswith("user-provided")
        kind = "manual input" if manual else (src.get("observation_kind") or ("retrieved" if value is not None else "missing"))
        source_value = src.get("value")
        source_observation = _excerpt(source_value, 220, "None retrieved")
        if source_value is not None and (src.get("unit") or default_unit):
            source_observation += " " + str(src.get("unit") or default_unit)
        profile_rows.append([label, shown, source_observation, kind,
                             "User-provided" if manual else (src.get("source_system") or "No exact-form source observation"),
                             "Not applicable" if manual else src.get("source_id"),
                             "Not applicable" if manual else src.get("source_record_id"),
                             "User input" if manual else (src.get("retrieved_at") or "Date not recorded"),
                             src.get("status") or origin,
                             "Manual value; any retrieved observation is shown separately." if manual else _excerpt(src.get("source_text"), 220, "No source extraction note stored")])
    story.append(_table(["Property", "Value used / unit", "Source observation", "Type", "Source / entry", "Source ID", "Source-record ID", "Retrieved", "Review / provenance", "Extraction note"],
                        profile_rows, [17 * mm, 19 * mm, 19 * mm, 14 * mm, 18 * mm, 13 * mm, 16 * mm, 16 * mm, 26 * mm, 20 * mm], styles, 6.0))
    story.append(Spacer(1, 5))
    story.append(_p("Long property strings and extraction notes are excerpted in this table; use the listed source-record ID to locate the complete stored observation.", "Small", styles))
    story.append(_p(f"Chemical class: {_plain(res.get('drug_class'))}. Formula, identifiers and other identity-specific source observations are available in the ingredient catalogue; this report does not substitute missing property values.", "Small", styles))
    story.append(PageBreak())

    # 4 — Search and matching
    story.append(_p("04 · Search and matching", "Section", styles))
    identity_note = f"Selected ingredient ID {_plain(inp.get('ingredient_id'))}" + (f" / form ID {_plain(inp.get('form_id'))}" if inp.get("form_id") else "")
    story.append(_table(["Search input", "Resolved identity", "Matching rule", "Value type"], [[
        inp.get("drug_name"), identity_note if inp.get("ingredient_id") else "No catalogue identity selected",
        "Ingredient/formulation identity IDs; no free-text promotion to direct evidence",
        "User input / explicit selection",
    ], [inp.get("target_organ"), "Organ-level catalogue filter", "Organ aliases retained; tissue is contextual unless directly structured", "User input"],
       [inp.get("target_tissue"), "Requested tissue retained", "Tissue match not asserted unless a structured source field supports it", "User input"],
       [inp.get("route"), "Route relation applied", "Direct, same-class, other-context, and route mismatch stay distinct", "User input"]],
        [28 * mm, 40 * mm, 76 * mm, 34 * mm], styles))
    story.append(Spacer(1, 5))
    story.append(_p("Link review status and match explanations are carried into batch rows below. Ambiguous identities require explicit selection. Imported source records may remain pending human review.", "Body", styles))
    story.append(PageBreak())

    # 5 — Evidence overview
    story.append(_p("05 · Evidence overview", "Section", styles))
    evidence_rows = []
    for c in carriers:
        evidence_rows.append([c.get("carrier"), c.get("direct_batches", 0), c.get("direct_papers", 0),
                             c.get("analogue_batches", 0), c.get("analogue_papers", 0),
                             c.get("other_context_batches", 0), c.get("other_context_papers", 0), c.get("status")])
    story.append(_table(["Carrier", "Direct batches", "Direct papers", "Related batches", "Related papers", "Context batches", "Context papers", "Evidence status"],
                        evidence_rows or [["Not reported", 0, 0, 0, 0, 0, 0, "No carrier rows returned"]],
                        [21 * mm, 20 * mm, 16 * mm, 22 * mm, 17 * mm, 21 * mm, 16 * mm, 45 * mm], styles, 6.5))
    story.append(Spacer(1, 5))
    story.append(_p("Counts are distinct within their stated evidence branch. Organ-level examples are reported separately in Section 9. Evidence volume is not a ranking, efficacy measure, or recommendation.", "Body", styles))
    story.append(PageBreak())

    # 6 — Carrier comparison
    story.append(_p("06 · Carrier comparison", "Section", styles))
    carrier_rows = [[c.get("carrier"), c.get("status"), c.get("evidence_basis"), c.get("direct_batches", 0),
                     c.get("analogue_batches", 0), c.get("other_context_batches", 0), c.get("direct_other_route_batches", 0),
                     c.get("catalogue_context_batches", 0)] for c in carriers]
    story.append(_table(["Carrier", "Status", "Basis", "Direct", "Related", "Context", "Direct other-route", "Scope rows"],
                        carrier_rows or [["Not reported", "No evidence", "Not reported", 0, 0, 0, 0, 0]],
                        [20 * mm, 34 * mm, 36 * mm, 13 * mm, 14 * mm, 14 * mm, 23 * mm, 23 * mm], styles, 6.5))
    story.append(Spacer(1, 5))
    story.append(_p("No carrier is designated best or selected as an optimum. Never infer a carrier recommendation from other-ingredient context or catalogue frequency. A carrier with no supporting match is not recommended.", "Callout", styles))
    story.append(PageBreak())

    # 7 — Formulation batches
    story.append(_p("07 · Formulation batches", "Section", styles))
    story.append(_p("Each row traces a retrieved source batch. Study type is normalized only when the source label explicitly supports one of the work-order categories; otherwise it remains pending review.", "Body", styles))
    batch_rows = []
    for c in carriers:
        for x in c.get("candidates", []):
            source_ids = "Formulation " + _plain(x.get("Formulation_ID") or x.get("formulation_id"))
            paper_id = x.get("Paper_ID") or x.get("paper_id")
            ingredient_id = x.get("Ingredient_Record_ID") or x.get("ingredient_record_id")
            source_ids += f"; paper {_plain(paper_id)}" if paper_id else "; paper ID not reported"
            source_ids += f"; ingredient row {_plain(ingredient_id)}" if ingredient_id else ""
            match = x.get("match_class") or x.get("Evidence_Level") or x.get("evidence_level")
            ratio = x.get("composition") or x.get("Stated_Ratio_Original")
            measurements = f"Entrapment/loading (measured/source-reported): {_plain(x.get('Drug_Loading_EE_DL'))}; size/PDI/ζ (measured/source-reported): {_plain(x.get('Size_PDI_Zeta'))}"
            batch_rows.append([c.get("carrier"), match, source_ids, _study_type(x.get("Evidence_Level") or x.get("evidence_level")),
                               x.get("Route") or x.get("route"), ratio, measurements,
                               x.get("Evidence_Location") or x.get("evidence_location"),
                               x.get("DOI") or x.get("doi")])
    if not batch_rows:
        batch_rows = [["Not reported", "None", "No source batch rows retrieved", "Pending review", "Not reported", "Not reported", "Not reported", "Not reported", "Not reported"]]
    story.append(_table(["Carrier", "Match branch", "Source-row identifiers", "Study type", "Route", "Composition as reported", "Reported observations · measured text", "Evidence location", "DOI"],
                        batch_rows, [15 * mm, 17 * mm, 27 * mm, 19 * mm, 12 * mm, 22 * mm, 30 * mm, 19 * mm, 16 * mm], styles, 6.0))
    story.append(Spacer(1, 5))
    story.append(_p("Where supplied, match method, review status and “why this matched” are preserved below. Source text is not re-interpreted as a new measurement; unverified extraction remains pending review.", "Small", styles))
    match_rows = []
    for c in carriers:
        for x in c.get("candidates", []):
            match_rows.append([x.get("Formulation_ID") or x.get("formulation_id"), c.get("carrier"),
                               x.get("why_this_matched") or "No match explanation supplied",
                               x.get("match_method") or "Not reported", x.get("link_review_status") or "Pending review",
                               x.get("Evidence_Location") or x.get("evidence_location") or "Not reported",
                               f"Catalogue updated: {c.get('catalogue_updated_at') or 'date not recorded'}; extraction pending human review"] )
    if match_rows:
        story.append(_table(["Batch ID", "Carrier", "Why matched", "Match method", "Link review", "Evidence location", "Date / extraction note"],
                            match_rows, [14 * mm, 17 * mm, 47 * mm, 20 * mm, 19 * mm, 24 * mm, 37 * mm], styles, 6.2))
    story.append(PageBreak())

    # 8 — Experimental results
    story.append(_p("08 · Experimental results", "Section", styles))
    experimental_rows = []
    for c in carriers:
        for x in c.get("candidates", []):
            for label, key in (("Entrapment efficiency / drug loading", "Drug_Loading_EE_DL"),
                               ("Particle size / PDI / zeta potential", "Size_PDI_Zeta")):
                experimental_rows.append([c.get("carrier"), x.get("Formulation_ID") or x.get("formulation_id"),
                    label, x.get(key), "Measured as source-reported text" if x.get(key) not in (None, "", "nan") else "Missing",
                    x.get("Paper_ID") or x.get("paper_id"), x.get("Evidence_Location") or x.get("evidence_location")])
    story.append(_table(["Carrier", "Batch ID", "Endpoint", "Reported value / unit text", "Type", "Paper/source ID", "Evidence location"],
                        experimental_rows or [["Not reported", "Not reported", "No matched experimental observations", "Not reported", "Missing", "Not reported", "Not reported"]],
                        [14 * mm, 15 * mm, 27 * mm, 35 * mm, 21 * mm, 19 * mm, 47 * mm], styles, 6.4))
    story.append(Spacer(1, 5))
    story.append(_p("All values above are the original source wording unless explicitly marked as a descriptive calculation. No unit conversion, parsing of combined PDI/zeta text, or new-batch prediction is performed in this report.", "Body", styles))
    ratio_rows = []
    for c in carriers:
        for r in c.get("ratios", []):
            ratio_rows.append([c.get("carrier"), r.get("ingredient"), r.get("unit"), r.get("min"), r.get("median"), r.get("max"),
                               r.get("batches"), r.get("papers"), "Calculated descriptive summary; not optimized"])
    if ratio_rows:
        story.append(_p("Compatible-unit composition summaries (calculated from source observations; units remain separate)", "Sub", styles))
        story.append(_table(["Carrier", "Ingredient", "Unit", "Minimum", "Median", "Maximum", "Batches", "Papers", "Type / interpretation"],
                            ratio_rows, [17 * mm, 27 * mm, 15 * mm, 15 * mm, 15 * mm, 15 * mm, 15 * mm, 14 * mm, 35 * mm], styles, 6.2))
    else:
        story.append(_p("No compatible numeric composition summaries were calculated.", "Small", styles))
    story.append(PageBreak())

    # 9 — Biological evaluation
    story.append(_p("09 · Biological evaluation", "Section", styles))
    if not organ.get("available"):
        story.append(_p(organ.get("note") or "No organ-specific biological context was retrieved.", "Body", styles))
    else:
        story.append(_p(f"Separate organ-context branch for {_plain(organ.get('organ'))}. These records are not merged into direct formulation-batch evidence and do not prove targeting or efficacy.", "Callout", styles))
        biology_rows = []
        for b in organ.get("biology_records", []):
            biology_rows.append([b.get("level"), b.get("tissue_or_structure"), b.get("cell_types"), b.get("barriers"),
                                 b.get("receptors_and_markers"), b.get("targeting_strategies"), b.get("source_ids"),
                                 b.get("evidence_level") or "Pending review"])
        if biology_rows:
            story.append(_table(["Level", "Tissue / structure", "Cell types", "Barriers", "Markers", "Strategies as reported", "Source IDs", "Evidence type"],
                                biology_rows, [14 * mm, 24 * mm, 24 * mm, 28 * mm, 24 * mm, 29 * mm, 18 * mm, 17 * mm], styles, 5.9))
        matrix_rows = []
        for m in organ.get("organ_evidence_matrix", []):
            matrix_rows.append([m.get("vesicle_class"), m.get("n_formulation_records"), m.get("n_papers"),
                                m.get("papers_human_clinical"), m.get("papers_in_vivo_animal"), m.get("papers_ex_vivo"),
                                m.get("papers_in_vitro"), m.get("papers_physicochemical_only")])
        if matrix_rows:
            story.append(_p("Organ × carrier catalogue matrix (not ingredient-specific)", "Sub", styles))
            story.append(_table(["Carrier", "Batches", "Papers", "Human clinical", "Animal in vivo", "Ex vivo", "In vitro", "Physicochemical"],
                                matrix_rows, [23 * mm, 18 * mm, 18 * mm, 26 * mm, 25 * mm, 18 * mm, 20 * mm, 30 * mm], styles, 6.0))
        ex_rows = []
        for group in organ.get("targeting_examples_by_carrier", []):
            for branch, label in (("direct", "Direct exact ingredient"), ("same_class", "Related same-class"), ("context", "Other-ingredient context")):
                for x in group.get(branch, []):
                    ex_rows.append([group.get("carrier"), label, x.get("formulation_id") or x.get("paper_id"),
                        x.get("drug"), x.get("route"), _study_type(x.get("evidence_level")),
                        x.get("key_findings_as_reported_not_a_guarantee_of_targeting"), x.get("doi"), x.get("evidence_location")])
        if ex_rows:
            story.append(_p("Published organ examples by evidence branch", "Sub", styles))
            story.append(_table(["Carrier", "Branch", "Batch/paper ID", "Drug", "Route", "Study type", "Finding as reported", "DOI", "Source location"],
                                ex_rows, [16 * mm, 23 * mm, 19 * mm, 17 * mm, 15 * mm, 20 * mm, 34 * mm, 17 * mm, 17 * mm], styles, 5.8))
        story.append(_p(organ.get("data_quality_note") or "Organ workbook extraction status not recorded.", "Small", styles))
    story.append(PageBreak())

    # 10 — Release kinetics
    story.append(_p("10 · Release kinetics", "Section", styles))
    time_series = res.get("release_time_series") or []
    if not time_series:
        story.append(_p("No release time-series recorded.", "Callout", styles))
        story.append(_p("The input release goal, if any, is a user-defined objective and is not an observed result. Release-related narrative text is not converted into a time point or cumulative percentage. No curve is interpolated or extrapolated.", "Body", styles))
    else:
        release_rows = []
        for point in time_series:
            release_rows.append([point.get("formulation_id"), point.get("paper_id"), point.get("time"), point.get("time_unit"),
                                 point.get("release"), point.get("release_unit"), point.get("source_id"),
                                 point.get("evidence_location"), "Measured source time point"])
        story.append(_table(["Batch", "Paper", "Time", "Time unit", "Release value", "Release unit", "Source ID", "Location", "Type"],
                            release_rows, [18 * mm, 18 * mm, 14 * mm, 18 * mm, 22 * mm, 20 * mm, 22 * mm, 28 * mm, 18 * mm], styles, 6.2))
        story.append(_p("Only measured points are listed. No interpolation or extrapolation beyond recorded measurements is performed.", "Small", styles))
    story.append(PageBreak())

    # 11 — References
    story.append(_p("11 · References", "Section", styles))
    reference_rows = []
    for r in refs:
        reference_rows.append([r.get("title"), r.get("authors"), r.get("year"), r.get("doi"),
                              r.get("url") or r.get("doi_url"), r.get("verification") or "Verification not reported", r.get("evidence_level") or "Pending review"])
    story.append(_table(["Title", "Authors", "Year", "DOI", "Source URL", "Verification", "Study type"],
                        reference_rows or [["No references linked", "Not reported", "Not reported", "Not reported", "Not reported", "Not reported", "Pending review"]],
                        [42 * mm, 29 * mm, 12 * mm, 22 * mm, 26 * mm, 21 * mm, 26 * mm], styles, 6.1))
    story.append(Spacer(1, 5))
    story.append(_p("A DOI or URL is a route to the source, not verification of its claims. Check each cited primary paper and the stated evidence location.", "Small", styles))
    story.append(PageBreak())

    # 12 — AI interpretation
    story.append(_p("12 · AI interpretation", "Section", styles))
    ai = res.get("llm_explanation") or res.get("ai_interpretation")
    if ai:
        story.append(_p(ai, "Body", styles))
        story.append(_p(f"Model: {_plain(res.get('llm_model'))} · Generated: {_plain(res.get('llm_generated_at'))} · Citation validation: {_plain(res.get('llm_citation_status'))}", "Small", styles))
        story.append(_p("The explanation is limited to backend-retrieved records and remains subject to independent review. It is not clinical advice.", "Callout", styles))
        ai_sources = res.get("llm_sources") or []
        if ai_sources:
            story.append(_p("Citation ID crosswalk to retrieved source records", "Sub", styles))
            story.append(_table(["Citation ID", "Evidence branch", "Record ID", "Source ID", "Title", "DOI from source record"],
                                [[x.get("id"), x.get("source_type"), x.get("record_id"), x.get("source_id"), x.get("title"), x.get("doi")] for x in ai_sources],
                                [16 * mm, 25 * mm, 22 * mm, 20 * mm, 60 * mm, 35 * mm], styles, 6.2))
        else:
            story.append(_p("Citation crosswalk was not stored with this explanation.", "Small", styles))
    else:
        story.append(_p(f"No AI interpretation is stored for this analysis. Last explanation status: {_plain(res.get('llm_status'), 'not requested')}. The result summary and evidence tables are non-AI, source-grounded templates.", "Body", styles))
    story.append(PageBreak())

    # 13 — Limitations
    story.append(_p("13 · Limitations", "Section", styles))
    story.append(_p(res.get("method_note") or "No separate method note was stored.", "Body", styles))
    gaps = res.get("gaps") or []
    if gaps:
        story.append(_p("Request-specific evidence gaps", "Sub", styles))
        for gap in gaps:
            story.append(_p("• " + str(gap), "Body", styles))
    limitations = [
        "Catalogue scope is limited to the records bundled or imported into this installation; search failure is not evidence that literature does not exist.",
        "Study-type labels and source extraction may be pending human review; verify claims, units, and DOI resolution against the primary paper.",
        "Catalogue frequency, direct/related counts, and reported composition are not comparative efficacy or compatibility assessments.",
        "No unsupported carrier recommendation, optimized ratio, new-batch measurement, clinical efficacy, safety, or target-tissue delivery conclusion is provided.",
        "On Render Free, runtime history and user-generated records may be lost after a restart; export history before the service restarts.",
    ]
    for item in limitations:
        story.append(_p("• " + item, "Body", styles))
    story.append(PageBreak())

    # 14 — Appendix and glossary
    story.append(_p("14 · Appendix and glossary", "Section", styles))
    story.append(_p("Value-type legend", "Sub", styles))
    story.append(_table(["Type", "Meaning in this report"], [
        ["Measured", "Reported experimental measurement; wording/units remain as in source unless stated otherwise."],
        ["Retrieved", "Source-linked database observation; review status and retrieval date are shown where available."],
        ["Calculated", "A transparent descriptive calculation over source rows, not a prediction or optimization."],
        ["Predicted", "No prediction is generated by this report unless explicitly identified with a validated model record."],
        ["Manual", "User-provided value or design objective; it is not silently substituted for a source observation."],
        ["Missing", "Not reported or no exact identity/form observation; no value is inferred."],
        ["Pending review", "Source extraction or study classification has not been independently confirmed."],
    ], [25 * mm, 153 * mm], styles))
    story.append(_p("Glossary", "Sub", styles))
    story.append(_table(["Term", "Definition"], [
        ["PDI", "Polydispersity index; a measure of particle-size distribution breadth."],
        ["Zeta potential (ζ)", "Electrokinetic potential near the particle surface; method and medium matter."],
        ["Entrapment efficiency", "Fraction of drug associated with the carrier under the reported method."],
        ["Drug loading", "Drug amount relative to a stated formulation basis; basis and units must be checked."],
        ["Direct evidence", "Batch source row linked to the selected ingredient/form identity."],
        ["Related evidence", "Same-class evidence only; it is indirect and not a direct ingredient match."],
        ["Context evidence", "Other-ingredient or organ-level context; not drug-specific batch evidence."],
    ], [38 * mm, 140 * mm], styles))
    story.append(Spacer(1, 5))
    story.append(_p("Unicode rendering check: ± · µg/mL · ζ-potential · α · β · γ · CO₂ · H₂O. These symbols are embedded with the bundled DejaVu Sans font and are not ASCII-folded.", "Small", styles))

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
                            topMargin=16 * mm, bottomMargin=22 * mm,
                            title="SmartNanoFormulator source-traceable evidence report",
                            author="SmartNanoFormulator")
    doc.build(story, canvasmaker=_PageCountCanvas)
    return buf.getvalue()
