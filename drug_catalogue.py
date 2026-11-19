"""Friendly type-ahead for medicines and supplements.

Brand aliases are search aids only. The canonical ingredient is selected explicitly,
and product variants with multiple actives are flagged for the user to verify.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
import sqlite3
from typing import Any

import engine
import ingredient_resolver
import ingredient_store

# Small, explicit synonym seed for common examples. Keep this conservative:
# a brand may cover multiple product variants and strengths.
ALIASES = {
    "acetaminophen": {
        "canonical": "Acetaminophen",
        "aliases": ["paracetamol", "apap", "acetominophen"],
        "brands": ["panadol", "tylenol"],
        "brand_warning": "Panadol/Tylenol product families include different variants. Confirm the exact package active ingredients; combination versions may contain additional actives.",
    },
    "ibuprofen": {
        "canonical": "Ibuprofen", "aliases": ["ibu", "ibuprofenum"],
        "brands": ["advil", "motrin"],
        "brand_warning": "Advil/Motrin products vary by strength and formulation. Check the exact package ingredients before treating the product as ibuprofen alone.",
    },
    "ascorbic_acid": {
        "canonical": "L-ascorbic acid", "aliases": ["ascorbic acid", "vitamin c", "vit c", "l ascorbate"],
        "brands": [], "brand_warning": None,
    },
    "vitamin_d3": {
        "canonical": "cholecalciferol", "aliases": ["vitamin d3", "vitamin d", "d3"],
        "brands": [], "brand_warning": None,
    },
    "vitamin_b12": {
        "canonical": "cyanocobalamin", "aliases": ["vitamin b12", "b12", "cobalamin"],
        "brands": [], "brand_warning": None,
    },
    "coq10": {
        "canonical": "coenzyme Q10", "aliases": ["coq10", "ubiquinone", "coenzyme q-10"],
        "brands": [], "brand_warning": None,
    },
    "folate": {
        "canonical": "folic acid", "aliases": ["folate", "vitamin b9", "b9"],
        "brands": [], "brand_warning": None,
    },
    "aspirin": {
        "canonical": "Aspirin", "aliases": ["acetylsalicylic acid", "asa"],
        "brands": ["bayer aspirin"],
        "brand_warning": "Bayer-branded products include multiple formulations and combinations. Confirm the exact active ingredients on the package.",
    },
}


def _norm(value: Any) -> str:
    return ingredient_store.normalize_name(str(value or ""))


def _ingredient_row(con: sqlite3.Connection, canonical: str):
    key = _norm(canonical)
    return con.execute(
        "SELECT ingredient_id,preferred_name,category,legacy_drug_name,review_status FROM ingredient WHERE normalized_name=? LIMIT 1",
        (key,),
    ).fetchone()


def _profile(con: sqlite3.Connection, name: str) -> dict[str, Any]:
    row = engine.lookup_drug(con, name)
    if not row:
        return {}
    return {
        "mw": row.get("mw"), "logp": row.get("xlogp_computed"),
        "tpsa": row.get("tpsa"), "hbd": row.get("hbd"), "hba": row.get("hba"),
        "pka_text": row.get("pka_experimental"),
        "solubility_text": row.get("solubility_experimental"),
        "stability_text": row.get("stability_experimental"),
        "molecular_formula": row.get("smiles") and row.get("molecular_formula"),
        "source": row.get("source"),
    }


def _item_for(con: sqlite3.Connection, ingredient_id: int, query: str,
              matched_alias: str, is_brand: bool, warning: str | None, score: float = 1.0):
    item = ingredient_resolver._candidate(con, ingredient_id, None,
        match_type="brand_alias" if is_brand else ("synonym" if _norm(matched_alias) != _norm(query) else "canonical"),
        matched_alias=matched_alias, score=score)
    if not item:
        return None
    profile = _profile(con, item.get("name") or item.get("canonical_name") or "")
    item.update({
        "candidate_key": f"local:ingredient:{ingredient_id}",
        "canonical_name": item.get("name") or item.get("canonical_name"),
        "display_name": (f"{matched_alias}  →  {item.get('name')}" if is_brand or _norm(matched_alias) != _norm(item.get("name")) else item.get("name")),
        "matched_alias": matched_alias,
        "is_brand_alias": bool(is_brand),
        "brand_warning": warning if is_brand else None,
        "legacy_profile": profile,
        "category": item.get("category") or "ingredient/supplement",
    })
    return item


def suggest(con: sqlite3.Connection, query: str, limit: int = 10) -> dict[str, Any]:
    query = str(query or "").strip()[:160]
    norm = _norm(query)
    if len(norm) < 2:
        return {"query": query, "results": [], "status": "short_query"}

    results: list[dict[str, Any]] = []
    seen: set[int] = set()

    # Curated synonyms and brand-to-ingredient search aliases.
    for key, cfg in ALIASES.items():
        canonical = cfg["canonical"]
        names = list(cfg.get("aliases", [])) + list(cfg.get("brands", [])) + [canonical]
        exact = next((n for n in names if _norm(n) == norm), None)
        prefix = next((n for n in names if _norm(n).startswith(norm)), None)
        if exact or prefix:
            match = exact or prefix
            row = _ingredient_row(con, canonical)
            if row:
                is_brand = _norm(match) in {_norm(x) for x in cfg.get("brands", [])}
                item = _item_for(con, int(row["ingredient_id"]), query, match, is_brand,
                                 cfg.get("brand_warning"), 1.0 if exact else 0.94)
                if item:
                    results.append(item); seen.add(int(row["ingredient_id"]))

    # Canonical names, catalogue aliases, drugs and supplements all share one list.
    rows = con.execute(
        "SELECT ingredient_id,preferred_name,normalized_name,category FROM ingredient ORDER BY preferred_name"
    ).fetchall()
    aliases = con.execute(
        "SELECT ingredient_id,alias,normalized_alias,alias_type FROM ingredient_alias WHERE ingredient_id IS NOT NULL AND form_id IS NULL ORDER BY alias"
    ).fetchall()
    alias_by_id: dict[int, list[sqlite3.Row]] = {}
    for alias in aliases:
        alias_by_id.setdefault(int(alias["ingredient_id"]), []).append(alias)

    scored = []
    for row in rows:
        iid = int(row["ingredient_id"])
        if iid in seen:
            continue
        names = [(str(row["preferred_name"]), str(row["normalized_name"]))]
        names.extend((str(a["alias"]), str(a["normalized_alias"])) for a in alias_by_id.get(iid, []))
        exact = next((pair for pair in names if pair[1] == norm), None)
        prefix = next((pair for pair in names if pair[1].startswith(norm)), None)
        best = exact or prefix
        if best:
            score = 1.0 if exact else 0.94
            match = best[0]
        else:
            score, match = max(((SequenceMatcher(None, norm, n).ratio(), a) for a, n in names), default=(0.0, ""))
            if score < 0.88:
                continue
        scored.append((score, iid, str(row["preferred_name"]), match))
    scored.sort(key=lambda x: (-x[0], x[2].casefold()))
    for score, iid, name, match in scored:
        if iid in seen: continue
        item = _item_for(con, iid, query, match, False, None, score)
        if item:
            results.append(item); seen.add(iid)
        if len(results) >= max(1, min(limit, 20)):
            break

    results.sort(key=lambda x: (-float(x.get("score") or 0), (x.get("name") or "").casefold()))
    results = results[:max(1, min(limit, 20))]
    return {
        "query": query,
        "results": results,
        "status": "matches_found" if results else "no_local_match",
        "message": "Select a suggestion to use its canonical ingredient identity. Brand names are search aliases, not confirmation of a particular product variant." if results else "No local match. You can search external chemical/label databases or continue with a manually entered name.",
    }
