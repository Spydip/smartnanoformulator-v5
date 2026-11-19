"""Import the organ-specific knowledge workbook into the SmartNanoFormulator DB.
Uses Python's standard library to read XLSX XML; source strings are preserved.
"""
from __future__ import annotations
import os, re, sqlite3, zipfile, xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
SHEETS = {
    "Target Organ & Tissue Database": "organ_tissue",
    "Nanovesicle Knowledge Base": "nanovesicle_knowledge",
    "Modification Matrix": "modification_matrix",
    "Organ x Vesicle Evidence": "organ_vesicle_evidence",
    "Organ Targeting Examples": "organ_targeting_examples",
    "Scientific References": "organ_scientific_references",
}

def snake(value: str) -> str:
    value = re.sub(r"\([^)]*\)", lambda m: " " + m.group(0)[1:-1] + " ", str(value or ""))
    value = value.replace("&", " and ").replace("%", " percent ")
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return re.sub(r"_+", "_", value) or "field"

def read_xlsx(path: str | Path) -> dict[str, list[dict[str, str]]]:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in item.findall(".//m:t", NS)))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rel_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rels = {r.attrib["Id"]: r.attrib["Target"] for r in rel_root}
        out = {}
        for sheet in wb.findall(".//m:sheets/m:sheet", NS):
            name = sheet.attrib["name"]
            target = rels[sheet.attrib[f"{{{NS['r']}}}id"]]
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            target = os.path.normpath(target)
            root = ET.fromstring(z.read(target))
            raw_rows = []
            for row in root.findall(".//m:sheetData/m:row", NS):
                cells = {}
                for cell in row.findall("m:c", NS):
                    ref = cell.attrib.get("r", "")
                    col = re.match(r"[A-Z]+", ref).group(0)
                    typ = cell.attrib.get("t")
                    val = cell.find("m:v", NS)
                    inline = cell.find("m:is", NS)
                    if typ == "s" and val is not None:
                        value = shared[int(val.text)]
                    elif typ == "inlineStr" and inline is not None:
                        value = "".join(t.text or "" for t in inline.findall(".//m:t", NS))
                    else:
                        value = val.text if val is not None else ""
                    cells[col] = value
                if cells:
                    raw_rows.append(cells)
            if not raw_rows:
                continue
            headers = raw_rows[0]
            rows = []
            for row in raw_rows[1:]:
                record = {}
                for col, value in row.items():
                    original = headers.get(col, "").strip()
                    if original:
                        record[snake(original)] = value
                if any(v not in (None, "") for v in record.values()):
                    rows.append(record)
            out[name] = rows
        return out

def import_workbook(db_path: str | Path, workbook_path: str | Path):
    data = read_xlsx(workbook_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        for sheet_name, table in SHEETS.items():
            rows = data.get(sheet_name, [])
            if not rows:
                raise ValueError(f"Required sheet is missing or empty: {sheet_name}")
            columns = list(rows[0].keys())
            quoted_cols = ", ".join('"' + c + '" TEXT' for c in columns)
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            con.execute(f'CREATE TABLE "{table}" ({quoted_cols})')
            marks = ",".join("?" for _ in columns)
            names = ",".join('"' + c + '"' for c in columns)
            con.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({marks})',
                            [[r.get(c, "") for c in columns] for r in rows])
        con.execute('CREATE INDEX IF NOT EXISTS idx_organ_tissue_organ ON organ_tissue(organ)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_organ_tissue_record ON organ_tissue(record_id)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_organ_examples_organ ON organ_targeting_examples(target_organ)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_organ_examples_carrier ON organ_targeting_examples(vesicle_class)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_organ_examples_drug ON organ_targeting_examples(drug)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_organ_knowledge_carrier ON nanovesicle_knowledge(nanovesicle_system)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_organ_evidence_organ ON organ_vesicle_evidence(target_organ)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_organ_modification_carrier ON modification_matrix(nanovesicle_system)')
        con.execute('''CREATE TABLE IF NOT EXISTS source_manifest(
            source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL, source_role TEXT NOT NULL,
            record_count INTEGER NOT NULL, notes TEXT NOT NULL)''')
        manifest = [
          ("FORMULATION_CATALOGUE", "Published Formulations.csv + Scientific Formulation Database.xlsx", "Batch-level formulation catalogue", 1087,
           "Original uploaded ZIP source. Contains 1,087 distinct formulation batches, 5,453 ingredient rows, 64 physicochemical drug profiles and 196 scientific references in the built database."),
          ("ORGAN_KNOWLEDGE", Path(workbook_path).name, "Organ, tissue, nanovesicle and targeting knowledge workbook", sum(len(data.get(s, [])) for s in SHEETS),
           "Source workbook includes 135 organ/tissue hierarchy rows, 323 knowledge statements, 30 modification rows, 66 organ-vesicle evidence rows, 315 organ-targeting examples and 196 references. Extraction in source workbook is described as LLM-assisted; verify primary papers before high-stakes interpretation."),
        ]
        con.executemany('INSERT OR REPLACE INTO source_manifest VALUES(?,?,?,?,?)', manifest)
        con.commit()
        return {table: len(data[sheet]) for sheet, table in SHEETS.items()}
    finally:
        con.close()

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="smartnano.db")
    p.add_argument("--workbook", default="data_src/Organ-Specific Nanovesicle Research Database.xlsx")
    a = p.parse_args()
    counts = import_workbook(a.db, a.workbook)
    print("Imported organ-specific workbook:")
    for k, v in counts.items(): print(f"  {k}: {v}")
