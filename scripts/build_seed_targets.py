#!/usr/bin/env python3
"""Build the versioned resolver-input CSV; this does not create or verify records."""
from __future__ import annotations
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "seed" / "ingredient_targets.csv"

TARGETS = {
    "vitamin": """
ascorbic acid
L-ascorbic acid
dehydroascorbic acid
cholecalciferol
ergocalciferol
Vitamin D3
calcifediol
calcitriol
retinol
retinal
retinoic acid
beta-carotene
alpha-tocopherol
alpha-tocopherol acetate
gamma-tocopherol
delta-tocopherol
alpha-tocotrienol
gamma-tocotrienol
thiamine
thiamine pyrophosphate
riboflavin
flavin mononucleotide
flavin adenine dinucleotide
niacin
nicotinic acid
nicotinamide
nicotinamide riboside
nicotinamide mononucleotide
pantothenic acid
calcium pantothenate
pyridoxine
pyridoxal
pyridoxamine
pyridoxal 5-phosphate
biotin
folic acid
folinic acid
levomefolate
cyanocobalamin
methylcobalamin
hydroxocobalamin
adenosylcobalamin
phylloquinone
menaquinone-4
menaquinone-7
para-aminobenzoic acid
""",
    "mineral": """
zinc
magnesium
iron
selenium
calcium
copper
iodine
chromium
molybdenum
manganese
phosphorus
potassium
sodium
chloride
fluoride
cobalt
boron
silicon
vanadium
nickel
sulfur
lithium
strontium
barium
germanium
tin
calcium carbonate
calcium citrate
calcium phosphate
calcium lactate
zinc gluconate
zinc sulfate
zinc citrate
zinc picolinate
magnesium oxide
magnesium citrate
magnesium sulfate
magnesium chloride
magnesium malate
ferrous sulfate
ferrous fumarate
ferric citrate
potassium iodide
sodium selenite
sodium molybdate
""",
    "amino_acid_sports": """
glycine
L-alanine
L-arginine
L-asparagine
L-aspartic acid
L-cysteine
L-glutamic acid
L-glutamine
L-histidine
L-isoleucine
L-leucine
L-lysine
L-methionine
L-phenylalanine
L-proline
L-serine
L-threonine
L-tryptophan
L-tyrosine
L-valine
beta-alanine
creatine
creatine monohydrate
taurine
L-carnitine
acetyl-L-carnitine
L-theanine
L-citrulline
L-ornithine
hydroxyproline
trans-4-hydroxy-L-proline
cystine
gamma-aminobutyric acid
beta-hydroxy-beta-methylbutyrate
N-acetyl-L-tyrosine
N-acetyl-L-cysteine
N-acetyl-L-glutamine
N-acetyl-L-phenylalanine
5-hydroxytryptophan
S-adenosyl-L-methionine
creatinol-O-phosphate
L-lysine hydrochloride
L-arginine hydrochloride
L-histidine hydrochloride
L-ornithine hydrochloride
L-carnitine tartrate
creatine citrate
betaine
phosphocreatine
inosine
carnosine
anserine
D-aspartic acid
D-serine
D-alanine
D-phenylalanine
L-homocysteine
L-norvaline
L-norleucine
L-alpha-aminobutyric acid
L-2-aminobutyric acid
""",
    "botanical_phytochemical": """
curcumin
resveratrol
quercetin
berberine
epigallocatechin gallate
6-gingerol
apigenin
astaxanthin
coenzyme Q10
luteolin
kaempferol
myricetin
fisetin
naringenin
hesperetin
hesperidin
rutin
chrysin
baicalein
baicalin
genistein
daidzein
formononetin
puerarin
nobiletin
tangeretin
catechin
epicatechin
epigallocatechin
epicatechin gallate
gallic acid
ellagic acid
caffeic acid
ferulic acid
p-coumaric acid
sinapic acid
rosmarinic acid
chlorogenic acid
protocatechuic acid
syringic acid
vanillic acid
gentisic acid
salicylic acid
eugenol
isoeugenol
thymol
carvacrol
anethole
cinnamaldehyde
vanillin
gingerol
zingerone
6-shogaol
piperine
allicin
diallyl disulfide
sulforaphane
indole-3-carbinol
3,3'-diindolylmethane
lycopene
lutein
zeaxanthin
beta-cryptoxanthin
canthaxanthin
fucoxanthin
crocin
crocetin
glycyrrhetinic acid
glycyrrhizin
silybin
silychristin
ursolic acid
oleanolic acid
betulinic acid
asiatic acid
madecassic acid
ginsenoside Rb1
ginsenoside Rg1
ginsenoside Re
ginsenoside Rd
ginsenoside Rg3
ginsenoside Rh2
tanshinone IIA
tanshinone I
cryptotanshinone
artemisinin
andrographolide
withaferin A
withanolide A
piperlongumine
evodiamine
rutaecarpine
honokiol
magnolol
hyperoside
isoquercitrin
nicotiflorin
diosmetin
diosmin
genistin
daidzin
ellagic acid
dihydroquercetin
phloretin
phloridzin
morin
isorhamnetin
sulfuretin
baicalein-7-O-glucuronide
wogonin
wogonoside
rotenone
""",
    "other": """
melatonin
choline
choline chloride
choline bitartrate
alpha-lipoic acid
R-alpha-lipoic acid
S-adenosylmethionine
inositol
myo-inositol
D-ribose
phosphatidylserine
ubiquinol
ergothioneine
paraxanthine
theobromine
theophylline
methylsulfonylmethane
D-mannose
N-acetylglucosamine
D-glucosamine hydrochloride
glucosamine sulfate
acetylsalicylic acid
adenosine
adenine
adenosine monophosphate
adenosine diphosphate
adenosine triphosphate
uridine
cytidine
inosine monophosphate
NAD+
NADH
nicotinamide adenine dinucleotide phosphate
creatine phosphate
D-pantethine
pantethine
D-chiro-inositol
alpha-glycerophosphocholine
citicoline
CDP-choline
L-ergothioneine
pyrroloquinoline quinone
PQQ disodium salt
spermidine
spermine
putrescine
betaine anhydrous
trimethylglycine
L-5-methyltetrahydrofolate
L-cysteine sulfinic acid
D-glucaric acid
""",
}

SPECIAL = [
    ("vitamin", "Vitamin C", "vitamin_family", "Label can denote different forms; resolve the form before assigning a single MW."),
    ("vitamin", "Vitamin D", "vitamin_family", "Family name; D2 and D3 are separate compounds."),
    ("vitamin", "Vitamin E", "vitamin_family", "Family name; do not assign a single molecular weight."),
    ("vitamin", "Vitamin B12", "vitamin_family", "Family name; ask which cobalamin form."),
    ("mineral", "magnesium glycinate", "chelate", "Chelate/form identity can vary; no parent MW inheritance."),
    ("botanical_phytochemical", "turmeric extract", "extract", "Plant extract; no single molecular weight; marker compounds are separate rows."),
    ("botanical_phytochemical", "turmeric", "extract", "Common botanical name; extract is not a defined compound and has no single molecular weight."),
    ("botanical_phytochemical", "ashwagandha extract", "extract", "Plant extract; no single molecular weight; marker compounds are separate rows."),
    ("botanical_phytochemical", "ashwagandha", "extract", "Common botanical name; extract is not a defined compound and has no single molecular weight."),
    ("other", "omega-3", "mixture", "Mixture; EPA, DHA and other constituents remain distinct compounds."),
    ("other", "fish oil", "mixture", "Product mixture; no single molecular weight."),
    ("other", "collagen peptides", "polymer", "Polymer/peptide distribution; no single molecular weight."),
    ("other", "hyaluronic acid", "polymer", "Polymer with a molecular-weight range; no single value."),
    ("other", "probiotics", "non_chemical", "Biological category, not a single chemical compound."),
    ("other", "Tylenol", "product", "Brand/product lookup only; keep product separate from active ingredient."),
    ("other", "Panadol", "product", "Brand/product lookup only; only link an active ingredient when a source does."),
]


def main():
    rows = []
    seen = set()
    for category, block in TARGETS.items():
        for line in block.splitlines():
            name = line.strip()
            if not name:
                continue
            key = name.casefold().replace("-", " ").replace("'", "")
            key = " ".join(key.split())
            if key in seen:
                continue
            seen.add(key)
            rows.append({"category": category, "name": name, "material_type": "single_compound", "scope_note": "Resolver target only; not a verified chemical identity."})
    for category, name, material_type, note in SPECIAL:
        key = name.casefold()
        if key in seen:
            raise SystemExit(f"special target duplicates an ordinary name: {name}")
        seen.add(key)
        rows.append({"category": category, "name": name, "material_type": material_type, "scope_note": note})
    if len(rows) < 300:
        raise SystemExit(f"Only {len(rows)} distinct targets; add names before building the seed.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["category", "name", "material_type", "scope_note"])
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    print(f"Wrote {len(rows)} unique resolver targets to {OUT}")
    print("Category counts:", counts)
    print("Unique single-compound targets:", sum(r["material_type"] == "single_compound" for r in rows))
    print("Special non-single/product targets:", sum(r["material_type"] != "single_compound" for r in rows))

if __name__ == "__main__":
    main()
