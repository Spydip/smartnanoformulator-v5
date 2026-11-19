"""Compact, demand-loaded projections for saved analysis results."""
from __future__ import annotations

from typing import Any


CARRIER_FIELDS = (
    "carrier", "status", "evidence_basis", "direct_batches", "direct_papers",
    "analogue_batches", "analogue_papers", "other_context_batches",
    "other_context_papers", "direct_other_route_batches",
    "analogue_other_route_batches", "other_context_other_route_batches",
    "catalogue_context_batches", "catalogue_context_papers",
    "has_matched_evidence", "has_direct_evidence", "catalogue_updated_at",
)


def summary(result: dict[str, Any], analysis_id: int | None = None,
            created: str | None = None) -> dict[str, Any]:
    """Return only first-screen content; details remain available by section."""
    carriers = [
        {key: carrier.get(key) for key in CARRIER_FIELDS if key in carrier}
        for carrier in result.get("carrier_results", [])
    ]
    organ = result.get("organ_context") or {}
    organ_rows = organ.get("targeting_examples_by_carrier", [])
    organ_summary = {
        "available": bool(organ.get("available")),
        "organ": organ.get("organ"),
        "direct_examples": sum(int(row.get("direct_examples") or 0) for row in organ_rows),
        "same_class_examples": sum(int(row.get("same_class_examples") or 0) for row in organ_rows),
        "other_ingredient_context_examples": sum(int(row.get("other_drug_context_examples") or 0) for row in organ_rows),
        "route_mismatch_examples": sum(int(row.get("route_mismatch_examples") or 0) for row in organ_rows),
        "note": organ.get("note"),
    }
    # Preserve the identity and the three evidence branches; do not expose detail rows here.
    return {
        "analysis_id": analysis_id if analysis_id is not None else result.get("analysis_id"),
        "created": created if created is not None else result.get("created"),
        "input": result.get("input", {}),
        "decision": result.get("decision"),
        "decision_status": result.get("decision_status"),
        "carrier_results": carriers,
        "organ_context": organ_summary,
        "direct_batch_count": sum(int(row.get("direct_batches") or 0) for row in carriers),
        "related_batch_count": sum(int(row.get("analogue_batches") or 0) for row in carriers),
        "other_context_batch_count": sum(int(row.get("other_context_batches") or 0) for row in carriers),
        "references_count": len(result.get("references", [])),
        "catalogue_updated_at": max(
            (str(row.get("catalogue_updated_at")) for row in carriers if row.get("catalogue_updated_at")),
            default=None,
        ),
        "llm_status": result.get("llm_status", "not_requested"),
        "design_hypotheses_available": bool(result.get("design_hypotheses")),
        "design_hypothesis_mode": (result.get("design_hypotheses") or {}).get("mode"),
        "design_candidate_count": len((result.get("design_hypotheses") or {}).get("candidates", [])),
    }


def detail(result: dict[str, Any], section: str) -> dict[str, Any]:
    """Return one explicitly requested report section, never a merged evidence score."""
    sections = {
        "profile": {
            "input": result.get("input", {}),
            "drug_profile": result.get("drug_profile"),
            "drug_class": result.get("drug_class"),
            "values_used": result.get("values_used", {}),
            "provenance": result.get("provenance", {}),
            "property_sources": result.get("property_sources", {}),
        },
        "carriers": {"carrier_results": result.get("carrier_results", [])},
        "organ": {"organ_context": result.get("organ_context", {})},
        "references": {"references": result.get("references", [])},
        "design": {"design_hypotheses": result.get("design_hypotheses")},
        "endpoints": {
            "endpoint_results": result.get("endpoint_results", []),
            "ml": result.get("ml", {}),
            "gaps": result.get("gaps", []),
            "disclaimer": result.get("disclaimer"),
            "method_note": result.get("method_note"),
            "llm_explanation": result.get("llm_explanation"),
            "llm_model": result.get("llm_model"),
            "llm_generated_at": result.get("llm_generated_at"),
            "llm_citation_status": result.get("llm_citation_status"),
            "llm_sources": result.get("llm_sources", []),
            "llm_status": result.get("llm_status"),
            "llm_usage": result.get("llm_usage"),
        },
    }
    if section not in sections:
        raise ValueError("Unknown analysis detail section.")
    return {"section": section, **sections[section]}
