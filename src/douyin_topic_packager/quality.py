from __future__ import annotations

from typing import Any, Dict, Iterable


UNSAFE_PHRASES = (
    "虚构",
    "编造",
    "我帮你判断",
    "我帮你诊断",
    "报出你的金额",
    "留下你的金额",
)


def evaluate_topic_run(
    *,
    pain_signals: Iterable[Dict[str, Any]],
    topic_packages: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    signals = [item for item in pain_signals if isinstance(item, dict)]
    packages = [item for item in topic_packages if isinstance(item, dict)]
    known_pains = {str(item.get("pain_point") or "") for item in signals}

    evidence_total = 0
    evidence_grounded = 0
    unsafe_count = 0
    unknown_pain_count = 0
    for package in packages:
        pain = str(package.get("pain_point") or "")
        if pain not in known_pains:
            unknown_pain_count += 1
        refs = package.get("evidence_refs") or []
        evidence = package.get("evidence") or []
        evidence_total += len(evidence)
        ref_texts = {str(ref.get("text") or "").strip() for ref in refs if isinstance(ref, dict)}
        evidence_grounded += sum(1 for item in evidence if str(item).strip() in ref_texts)
        searchable = " ".join(
            str(package.get(key) or "")
            for key in ("proof_needed", "cta_direction", "comment_cta", "production_suggestions")
        )
        unsafe_count += sum(searchable.count(phrase) for phrase in UNSAFE_PHRASES)

    grounded_rate = round(evidence_grounded / evidence_total, 3) if evidence_total else 0.0
    checks = {
        "all_evidence_grounded": grounded_rate == 1.0,
        "all_pains_known": unknown_pain_count == 0,
        "no_unsafe_instructions": unsafe_count == 0,
        "packages_generated": bool(packages),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": {
            "grounded_evidence_rate": grounded_rate,
            "unknown_pain_count": unknown_pain_count,
            "unsafe_instruction_count": unsafe_count,
            "package_count": len(packages),
            "publish_ready_count": len(
                [item for item in packages if item.get("confidence_level") == "publish_ready"]
            ),
        },
    }
