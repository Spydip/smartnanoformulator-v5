"""
knowledge.py - the science lives here.  Edit THIS file (not engine.py) to change the rules.

WARNING: everything below is EXPERT JUDGEMENT encoding widely-reported formulation trends.
It is NOT fitted to data.  Professors: review every number and rationale.
When real rows are added (data/formulations.csv or the Lab loop) the engine learns
corrections on top of these priors (engine.Surrogate), so wrong priors get fixed by data.
"""

VESICLES = ["Liposome", "Niosome", "Transfersome", "Ethosome", "Phytosome"]
ROUTES = ["Oral", "Topical / transdermal", "Intravenous", "Ocular"]
RELEASES = ["Immediate", "Sustained", "Triggered (pH / temperature)"]
IONIZATION = ["Neutral", "Weak acid", "Weak base"]

OUTPUTS = ["size_nm", "pdi", "zeta_mv", "ee_pct"]
OUT_LABEL = {"size_nm": "Size (nm)", "pdi": "PDI", "zeta_mv": "Zeta (mV)", "ee_pct": "EE (%)"}
DESIGN_COLS = ["modifier_pct", "lipid_conc_pct", "drug_to_lipid"]
STEP = {"modifier_pct": 1.0, "lipid_conc_pct": 0.1, "drug_to_lipid": 0.01}   # practical rounding

# Approximate molecular weights (g/mol) used for mol% <-> mass conversions in the bench recipe
MW = {"Soy PC": 770.0, "HSPC": 790.0, "Cholesterol": 386.7, "DSPE-mPEG2000": 2805.5,
      "Span 60": 430.6, "Span 80": 428.6, "Tween 80": 1310.0, "Sodium cholate": 430.6}

# ---------------------------------------------------------------- 1. CARRIER RULES
# Base fit of each vesicle for each route (0 = not used for this route).
ROUTE_SCORE = {
    "Oral":                  {"Liposome": 1.5, "Niosome": 2.0, "Transfersome": 0.0, "Ethosome": 0.0, "Phytosome": 2.0},
    "Topical / transdermal": {"Liposome": 1.0, "Niosome": 2.0, "Transfersome": 3.0, "Ethosome": 3.0, "Phytosome": 1.0},
    "Intravenous":           {"Liposome": 3.0, "Niosome": 1.5, "Transfersome": 0.0, "Ethosome": 0.0, "Phytosome": 0.0},
    "Ocular":                {"Liposome": 1.5, "Niosome": 2.5, "Transfersome": 0.5, "Ethosome": 0.0, "Phytosome": 0.5},
}
ROUTE_WHY = {
    ("Intravenous", "Liposome"): "Clinically established for IV delivery (e.g. Doxil, AmBisome).",
    ("Topical / transdermal", "Transfersome"): "Designed for skin penetration (Cevc & Blume 1992).",
    ("Topical / transdermal", "Ethosome"): "Designed for skin penetration (Touitou 2000).",
    ("Oral", "Phytosome"): "Phospholipid complexes improve oral absorption of polar natural compounds.",
    ("Ocular", "Niosome"): "Non-ionic vesicles are well tolerated on the eye.",
}
UNSUITABLE = {
    ("Oral", "Transfersome"): "Skin-penetration carrier; not an oral system.",
    ("Oral", "Ethosome"): "Ethanol-rich skin carrier; not an oral system.",
    ("Intravenous", "Transfersome"): "Skin-penetration carrier; not used IV.",
    ("Intravenous", "Ethosome"): "20-45 % ethanol is unsuitable for IV.",
    ("Intravenous", "Phytosome"): "Phospholipid-drug complex is an oral/topical absorption system, not an IV carrier.",
    ("Ocular", "Ethosome"): "High ethanol content irritates the eye.",
}

# Target site: expected size window (nm), small score bonuses, and a practical note.
SITES = {
    "Oral": {
        "GI tract (local)": dict(size=(0, 400), bonus={"Niosome": 0.5},
            note="Local GI action: vesicles must survive bile salts and lipases; rigid, cholesterol-rich bilayers help."),
        "Systemic (absorption)": dict(size=(0, 300), bonus={"Niosome": 0.5},
            note="Absorption route: bile-salt-stable vesicles or phospholipid complexes improve exposure of poorly absorbed drugs."),
    },
    "Topical / transdermal": {
        "Skin surface / stratum corneum": dict(size=(0, 400), bonus={"Niosome": 1.0, "Liposome": 0.5},
            note="Local effect: larger vesicles stay in the upper layers and form a depot."),
        "Deep skin / systemic via skin": dict(size=(0, 250), bonus={"Transfersome": 1.0, "Ethosome": 1.0},
            note="Deformable (transfersome) or ethanolic (ethosome) vesicles reach deeper layers; aim for <= 200-250 nm."),
    },
    "Intravenous": {
        "Tumour (EPR effect)": dict(size=(70, 160), bonus={"Liposome": 1.0},
            note="Passive targeting via the EPR effect: roughly 70-160 nm, PEGylated (stealth) liposomes."),
        "Liver / spleen (RES)": dict(size=(100, 300), bonus={"Liposome": 1.0, "Niosome": 0.5},
            note="Macrophage (RES) uptake is the default fate of non-PEGylated vesicles: larger, conventional vesicles work."),
        "Brain (BBB)": dict(size=(0, 100), bonus={"Liposome": 0.5},
            note="Hard target: small (<100 nm), PEGylated and usually ligand-decorated. Treat predictions as exploratory."),
        "Systemic (long-circulating)": dict(size=(70, 150), bonus={"Liposome": 1.0},
            note="Long circulation: roughly 70-150 nm with ~5 mol % PEG-lipid."),
    },
    "Ocular": {
        "Anterior segment (cornea / aqueous)": dict(size=(0, 300), bonus={"Niosome": 0.75, "Liposome": 0.25},
            note="Non-ionic vesicles are well tolerated; a positive charge (stearylamine / chitosan) can prolong corneal residence."),
        "Posterior segment (retina)": dict(size=(0, 200), bonus={"Liposome": 0.5},
            note="Topical delivery to the retina is very limited; consider intravitreal dosing. Treat predictions as exploratory."),
    },
}

# Declarative rules: all conditions in `when` must hold. Fields: logp, mw, pka, ionization,
# solubility (mg/mL), labile, phyto, route, site, release.   pts = points per vesicle.
RULES = [
    dict(group="Drug properties", label="Hydrophilic drug (logP < 1)", when=[("logp", "<", 1.0)],
         pts={"Liposome": 1.0, "Niosome": 0.75},
         why="The aqueous core holds hydrophilic drugs (EE is often <30 % without remote loading)."),
    dict(group="Drug properties", label="Poorly soluble (< 0.1 mg/mL)", when=[("solubility", "<", 0.1)],
         pts={"Liposome": 0.5, "Niosome": 0.5, "Phytosome": 0.5},
         why="Lipid carriers solubilise poorly soluble drugs."),
    dict(group="Drug properties", label="Weak base with pKa >= 6.5 (remote-loadable)",
         when=[("ionization", "==", "Weak base"), ("pka", ">=", 6.5)], pts={"Liposome": 1.0},
         why="Ammonium-sulfate gradients load weak bases at >90 % EE (Haran 1993; Doxil)."),
    dict(group="Drug properties", label="Polar phytoconstituent", when=[("phyto", "==", True)],
         pts={"Phytosome": 2.5},
         why="Phytosomes rely on H-bonding between the PC head group and polar groups of the compound."),
    dict(group="Drug properties", label="Not a polar phytoconstituent", when=[("phyto", "==", False)],
         pts={"Phytosome": -1.5},
         why="Without H-bond donors/acceptors, complexation with PC is usually weak."),
    dict(group="Drug properties", label="MW > 500 Da on skin", when=[("mw", ">", 500), ("route", "==", "Topical / transdermal")],
         pts={"Transfersome": 0.5, "Ethosome": 0.5},
         why="Above ~500 Da passive skin permeation is poor; deformable or ethanolic vesicles help."),
    dict(group="Drug properties", label="Hydrophilic drug on skin", when=[("logp", "<", 1.0), ("route", "==", "Topical / transdermal")],
         pts={"Ethosome": 0.5}, why="Ethanol helps hydrophilic drugs partition into skin (e.g. acyclovir ethosomes)."),
    dict(group="Drug properties", label="Lipophilic drug on skin", when=[("logp", ">", 3.0), ("route", "==", "Topical / transdermal")],
         pts={"Transfersome": 0.5}, why="Edge-activator vesicles carry lipophilic NSAIDs deep (e.g. ketoprofen transfersome gel)."),
    dict(group="Stability", label="Chemically labile drug", when=[("labile", "==", True)],
         pts={"Liposome": 0.5, "Niosome": 0.5, "Phytosome": 0.5},
         why="Encapsulation or complexation can protect labile drugs from light, oxygen and water."),
    dict(group="Release", label="Sustained release wanted", when=[("release", "==", "Sustained")],
         pts={"Liposome": 0.5, "Niosome": 0.5},
         why="Rigid, cholesterol-rich bilayers act as reservoirs."),
    dict(group="Release", label="Triggered release wanted", when=[("release", "==", "Triggered (pH / temperature)")],
         pts={"Liposome": 1.0, "Niosome": 0.25},
         why="pH-sensitive and thermosensitive liposomes are established; pH-sensitive niosomes are reported."),
]

# ---------------------------------------------------------------- 2. DESIGN SPACES
# modifier_pct: Liposome/Niosome = cholesterol (mol %); Transfersome = edge activator (% w/w of lipid);
#               Ethosome = ethanol (% v/v); Phytosome = unused.
# lipid_conc_pct: total phospholipid (% w/v) - free only for ethosomes.
# drug_to_lipid: w/w.  Phytosome range is derived from a PC:drug molar window (below) and the drug MW.
DESIGN = {
    "Liposome":     dict(free=["modifier_pct", "drug_to_lipid"],
                         nominal={"modifier_pct": (20, 50), "lipid_conc_pct": (1, 10), "drug_to_lipid": (0.02, 0.30)},
                         fixed={"lipid_conc_pct": 5.0}),
    "Niosome":      dict(free=["modifier_pct", "drug_to_lipid"],
                         nominal={"modifier_pct": (25, 60), "lipid_conc_pct": (1, 10), "drug_to_lipid": (0.02, 0.30)},
                         fixed={"lipid_conc_pct": 3.0}),
    "Transfersome": dict(free=["modifier_pct", "drug_to_lipid"],
                         nominal={"modifier_pct": (5, 25), "lipid_conc_pct": (1, 10), "drug_to_lipid": (0.01, 0.15)},
                         fixed={"lipid_conc_pct": 5.0}),
    "Ethosome":     dict(free=["modifier_pct", "lipid_conc_pct", "drug_to_lipid"],
                         nominal={"modifier_pct": (20, 45), "lipid_conc_pct": (0.5, 5), "drug_to_lipid": (0.02, 0.25)},
                         fixed={}),
    "Phytosome":    dict(free=["drug_to_lipid"],
                         nominal={"modifier_pct": (0, 1), "lipid_conc_pct": (0, 1), "drug_to_lipid": (0.1, 1.2)},
                         fixed={"modifier_pct": 0.0, "lipid_conc_pct": 0.0}),
}
PHYTO_MOLAR_RANGE = (0.8, 3.0)      # PC : drug (mol/mol) search window

# Release profile shifts the cholesterol window (rigid bilayer = slower release)
RELEASE_RANGE = {
    ("Liposome", "Immediate"): (20, 35), ("Liposome", "Sustained"): (35, 50), ("Liposome", "Triggered (pH / temperature)"): (15, 30),
    ("Niosome", "Immediate"): (25, 45),  ("Niosome", "Sustained"): (45, 60),  ("Niosome", "Triggered (pH / temperature)"): (25, 45),
}

LABELS = {
    "Liposome":     {"modifier_pct": "Cholesterol (mol %)", "lipid_conc_pct": "Total lipid (% w/v)", "drug_to_lipid": "Drug : lipid (w/w)"},
    "Niosome":      {"modifier_pct": "Cholesterol (mol %)", "lipid_conc_pct": "Total surfactant (% w/v)", "drug_to_lipid": "Drug : lipid (w/w)"},
    "Transfersome": {"modifier_pct": "Edge activator (% w/w)", "lipid_conc_pct": "Total lipid (% w/v)", "drug_to_lipid": "Drug : lipid (w/w)"},
    "Ethosome":     {"modifier_pct": "Ethanol (% v/v)", "lipid_conc_pct": "Phospholipid (% w/v)", "drug_to_lipid": "Drug : lipid (w/w)"},
    "Phytosome":    {"modifier_pct": "-", "lipid_conc_pct": "-", "drug_to_lipid": "Drug : phospholipid (w/w)"},
}

# ---------------------------------------------------------------- 3. PRIORS FOR PROPERTIES
# Conservative calibration anchors from SmartNano's literature catalogue.
# Numeric medians were taken only from clearly labelled, parseable Size/PDI/EE text fields;
# radii, unlabeled values, PDI percentages and ambiguous measurement modalities were excluded.
# These are heterogeneous literature summaries, NOT a clean supervised training matrix.
# Counts below are approximate parseable batch / paper counts from the reviewed catalogue.
CALIBRATION_SOURCE = "SmartNanoFormulator literature catalogue; explicit-label parsing only; reviewed 2026-11"
CALIBRATION_COUNTS = {
    "Liposome": {"size_batches":184,"size_papers":41,"pdi_batches":166,"pdi_papers":35},
    "Niosome": {"size_batches":223,"size_papers":25,"pdi_batches":166,"pdi_papers":25},
    "Transfersome": {"size_batches":207,"size_papers":18,"pdi_batches":None,"pdi_papers":None},
    "Ethosome": {"size_batches":89,"size_papers":13,"pdi_batches":None,"pdi_papers":None},
    "Phytosome": {"size_batches":8,"size_papers":5,"pdi_batches":4,"pdi_papers":4},
}
SIZE_BASE = {"Liposome": 128, "Niosome": 216, "Transfersome": 190, "Ethosome": 207, "Phytosome": 250}   # nm; phytosome retained due sparse data
SIZE_SLOPE_DL = 0.25                 # ln-size change across the drug:lipid range (more drug -> larger)
SIZE_SLOPE_MOD = {"Liposome": 0.15, "Niosome": 0.25, "Transfersome": -0.35, "Ethosome": -0.40}
SIZE_SLOPE_CONC = {"Ethosome": 0.30}
PDI_BASE = {"Liposome": 0.21, "Niosome": 0.26, "Transfersome": 0.27, "Ethosome": 0.29, "Phytosome": 0.30}  # phytosome retained due sparse data
PDI_SLOPE_DL = 0.05
PDI_SLOPE_MOD = {"Transfersome": 0.06, "Ethosome": -0.04}
ZETA_BASE = {"Liposome": -12, "Niosome": -28, "Transfersome": -20, "Ethosome": -30, "Phytosome": -15}  # mV
ZETA_ION = {"Neutral": 0.0, "Weak acid": -4.0, "Weak base": 4.0}
ZETA_MOD_SLOPE = {"Ethosome": -10.0}          # more ethanol -> more negative
EE_LIPO, EE_HYDRO = 82.0, 20.0                 # passive EE for lipophilic (logP>=3) / hydrophilic (logP<=0) drugs
EE_OFFSET = {"Liposome": 0.0, "Niosome": -2.0, "Transfersome": -2.0, "Ethosome": 0.0}
EE_REMOTE = 88.0                               # weak bases in liposomes with a gradient
EE_DL_SLOPE = -18.0                            # EE points lost across the drug:lipid range
EE_MOD_CURVE = 10.0                            # EE points lost at the edge of the modifier range vs its optimum
EE_MOD_OPT = {"Liposome": 35, "Niosome": 45, "Transfersome": 13, "Ethosome": 32}
EE_CONC_SLOPE = {"Ethosome": 8.0}
EE_PHYTO, EE_PHYTO_CURVE, PHYTO_MOLAR_OPT = 88.0, 10.0, 1.5   # complexation efficiency
# How wrong the priors may be (1 sd); intentionally widened for between-paper spread.
# size is in ln units (0.50 is roughly 65% multiplicative one-SD uncertainty).
# Zeta and EE remain weakly supported; these wider errors prevent false precision.
PRIOR_SD = {"size_nm": 0.50, "pdi": 0.14, "zeta_mv": 20.0, "ee_pct": 30.0}
PRIOR_SD_BY_VESICLE = {v: dict(PRIOR_SD) for v in VESICLES}

# Desirability weights (higher = matters more)
WEIGHTS = {"size_nm": 1.0, "pdi": 1.0, "zeta_mv": 0.5, "ee_pct": 1.5}

# ---------------------------------------------------------------- 4. TEXT
INFO = {
    "Liposome": dict(
        what="Phospholipid bilayer vesicles: hydrophilic drugs sit in the aqueous core, lipophilic drugs in the bilayer. Clinically established for IV use (e.g. Doxil, AmBisome).",
        method="Thin-film hydration above the lipid Tm, then extrusion through 100 nm membranes (>= 5 passes). Remote loading for weak bases.",
        tips=["Hydrate above the Tm of the highest-melting lipid (HSPC/DSPC about 51-55 C).",
              "Measure EE only after removing free drug (dialysis, size-exclusion or ultracentrifugation)."]),
    "Niosome": dict(
        what="Non-ionic surfactant vesicles (Span/Tween + cholesterol): inexpensive, chemically stable (no phospholipid oxidation), good for lipophilic and amphiphilic drugs.",
        method="Thin-film hydration at about 60 C (above the Tm of Span 60), then probe sonication or extrusion for size control.",
        tips=["Span 60 (saturated C18, HLB 4.7) usually entraps best; Tween 60 needs more cholesterol to form vesicles.",
              "Add 5 mol % DCP (negative) or stearylamine (positive) if |zeta| is too low."]),
    "Transfersome": dict(
        what="Ultra-deformable phospholipid vesicles containing an edge activator (single-chain surfactant); they squeeze through skin pores narrower than themselves (Cevc & Blume 1992).",
        method="Dissolve PC + edge activator + drug in organic solvent, form a film, hydrate with buffer, then sonicate/extrude (e.g. 200 then 100 nm).",
        tips=["Keep the edge activator below about 25 % w/w: above that, mixed micelles form and EE drops.",
              "Screen Tween 80, Span 80 and sodium cholate as edge activators in round 1."]),
    "Ethosome": dict(
        what="Phospholipid vesicles containing 20-45 % ethanol; ethanol fluidises both the vesicle bilayer and skin lipids and gives a negative surface charge (Touitou 2000).",
        method="Cold method: dissolve PC + drug in ethanol (+/- propylene glycol) at room temperature, add water slowly while stirring (700-1000 rpm), then sonicate or extrude.",
        tips=["Above about 45 % ethanol vesicles become leaky and unstable.", "Seal containers: ethanol evaporates and changes the composition."]),
    "Phytosome": dict(
        what="Phospholipid-phytoconstituent complex (typically 1:1 to 2:1 PC:drug, molar) held by H-bonds between the PC head group and polar groups of the compound; improves absorption of polyphenols.",
        method="React drug and PC in an aprotic solvent (THF, dichloromethane or acetone) at 40-60 C for 2-3 h, evaporate, optionally precipitate with n-hexane, then hydrate.",
        tips=["Prove complexation (FTIR, DSC, XRD, 1H-NMR): 'EE' here means complexation efficiency.",
              "Protect from light and oxygen if the compound is labile."]),
}

# Example scenarios. Physicochemical values are APPROXIMATE literature values - verify (PubChem / DrugBank)
PRESETS = {
    "Doxorubicin -> tumour (IV)": dict(name="Doxorubicin", mw=543.5, logp=1.3, ionization="Weak base", pka=8.2, sol=10.0,
        labile=False, phyto=False, route="Intravenous", site="Tumour (EPR effect)", release="Sustained"),
    "Paclitaxel -> tumour (IV)": dict(name="Paclitaxel", mw=853.9, logp=3.5, ionization="Neutral", pka=None, sol=0.0003,
        labile=False, phyto=False, route="Intravenous", site="Tumour (EPR effect)", release="Sustained"),
    "Curcumin -> oral": dict(name="Curcumin", mw=368.4, logp=3.0, ionization="Weak acid", pka=8.5, sol=0.0006,
        labile=True, phyto=True, route="Oral", site="Systemic (absorption)", release="Sustained"),
    "Diclofenac -> deep skin": dict(name="Diclofenac", mw=296.2, logp=4.5, ionization="Weak acid", pka=4.0, sol=0.0024,
        labile=False, phyto=False, route="Topical / transdermal", site="Deep skin / systemic via skin", release="Sustained"),
    "Acyclovir -> deep skin": dict(name="Acyclovir", mw=225.2, logp=-1.6, ionization="Neutral", pka=None, sol=1.3,
        labile=False, phyto=False, route="Topical / transdermal", site="Deep skin / systemic via skin", release="Immediate"),
    "Ibuprofen -> oral": dict(name="Ibuprofen", mw=206.3, logp=4.0, ionization="Weak acid", pka=4.4, sol=0.021,
        labile=False, phyto=False, route="Oral", site="Systemic (absorption)", release="Sustained"),
}

# Key references behind the rules (typed from memory - VERIFY each before citing)
REFS = [
    "Touitou E. et al. Ethosomes - novel vesicular carriers for enhanced delivery. J Control Release 2000;65:403-418.",
    "Cevc G., Blume G. Lipid vesicles penetrate into intact skin owing to transdermal osmotic gradients and hydration force. Biochim Biophys Acta 1992;1104:226-232.",
    "Haran G. et al. Transmembrane ammonium sulfate gradients in liposomes produce efficient and stable entrapment of amphipathic weak bases. Biochim Biophys Acta 1993;1151:201-215.",
    "Barenholz Y. Doxil - the first FDA-approved nano-drug: lessons learned. J Control Release 2012;160:117-134.",
    "Uchegbu I.F., Vyas S.P. Non-ionic surfactant based vesicles (niosomes) in drug delivery. Int J Pharm 1998;172:33-70.",
]
