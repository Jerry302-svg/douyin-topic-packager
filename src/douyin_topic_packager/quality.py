from __future__ import annotations

from typing import Any, Dict, Iterable


UNSAFE_PHRASES = (
    "虚构",
    "编造",
    "帮你判断",
    "我帮你诊断",
    "我帮你看",
    "报出你的金额",
    "留下你的金额",
    "金额区间",
    "留下发现日期",
    "我帮你归",
    "帮你先归",
    "我帮你先归",
    "我帮你定位",
    "帮你定位",
)
GENERIC_AUDIENCES = {"", "目标用户", "相关用户", "当前选题对应的目标用户"}
CHECK_RECOMMENDATIONS = {
    "all_evidence_grounded": "重新绑定选题证据，确保每条引用都能在 evidence_refs 中逐字找到。",
    "all_pains_known": "删除输入痛点信号中不存在的选题，或重新关联正确的 pain_signal_id。",
    "no_unsafe_instructions": "移除虚构案例、承诺结果、个案诊断和敏感信息收集指令。",
    "titles_are_concise": "将标题压缩到 36 个字符以内，只保留一个核心判断。",
    "audiences_are_specific": "根据痛点、阶段或约束补充具体目标人群。",
    "required_generator_used": "检查模型配置与原始输出，避免把规则降级结果当成 LLM 实测。",
    "packages_generated": "降低筛选门槛或补充有效评论证据后重新生成选题包。",
    "external_verification_is_gated": "需要外部核验的选题必须降级为核验后使用。",
}


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def evaluate_topic_run(
    *,
    pain_signals: Iterable[Dict[str, Any]],
    topic_packages: Iterable[Dict[str, Any]],
    required_generator: str = "",
) -> Dict[str, Any]:
    signals = [item for item in pain_signals if isinstance(item, dict)]
    packages = [item for item in topic_packages if isinstance(item, dict)]
    known_pains = {str(item.get("pain_point") or "") for item in signals}

    evidence_total = 0
    evidence_grounded = 0
    unsafe_count = 0
    unknown_pain_count = 0
    long_title_count = 0
    generic_audience_count = 0
    generator_counts: Dict[str, int] = {}
    claim_status_counts: Dict[str, int] = {}
    review_gate_violations = 0
    experiment_ready_count = 0
    for package in packages:
        pain = str(package.get("pain_point") or "")
        if pain not in known_pains:
            unknown_pain_count += 1
        refs = package.get("evidence_refs") or []
        evidence = package.get("evidence") or []
        evidence_total += len(evidence)
        ref_texts = {_normalized_text(ref.get("text")) for ref in refs if isinstance(ref, dict)}
        evidence_grounded += sum(1 for item in evidence if _normalized_text(item) in ref_texts)
        long_title_count += int(len(_normalized_text(package.get("brief_title"))) > 36)
        generic_audience_count += int(_normalized_text(package.get("target_audience")) in GENERIC_AUDIENCES)
        generator = _normalized_text((package.get("metadata") or {}).get("generated_by")) or "unknown"
        generator_counts[generator] = generator_counts.get(generator, 0) + 1
        claim_status = _normalized_text(package.get("claim_status")) or "unknown"
        claim_status_counts[claim_status] = claim_status_counts.get(claim_status, 0) + 1
        review_gate_violations += int(
            bool(package.get("external_verification_required"))
            and package.get("confidence_level") == "publish_ready"
        )
        experiment_ready_count += int(len(package.get("experiment_variants") or []) >= 2)
        searchable = " ".join(
            str(package.get(key) or "")
            for key in (
                "proof_needed",
                "cta_direction",
                "comment_cta",
                "script_outline",
                "production_suggestions",
                "material_notes",
            )
        )
        unsafe_count += sum(searchable.count(phrase) for phrase in UNSAFE_PHRASES)
        if package.get("external_verification_required"):
            unsafe_count += searchable.count("我帮你")

    grounded_rate = round(evidence_grounded / evidence_total, 3) if evidence_total else 0.0
    checks = {
        "all_evidence_grounded": grounded_rate == 1.0,
        "all_pains_known": unknown_pain_count == 0,
        "no_unsafe_instructions": unsafe_count == 0,
        "titles_are_concise": long_title_count == 0,
        "audiences_are_specific": generic_audience_count == 0,
        "required_generator_used": not required_generator
        or generator_counts.get(required_generator, 0) == len(packages),
        "packages_generated": bool(packages),
        "external_verification_is_gated": review_gate_violations == 0,
    }
    failed_checks = [key for key, passed in checks.items() if not passed]
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": failed_checks,
        "recommendations": [CHECK_RECOMMENDATIONS[key] for key in failed_checks],
        "metrics": {
            "grounded_evidence_rate": grounded_rate,
            "unknown_pain_count": unknown_pain_count,
            "unsafe_instruction_count": unsafe_count,
            "long_title_count": long_title_count,
            "generic_audience_count": generic_audience_count,
            "generator_counts": generator_counts,
            "required_generator": required_generator,
            "package_count": len(packages),
            "publish_ready_count": len(
                [item for item in packages if item.get("confidence_level") == "publish_ready"]
            ),
            "review_required_count": len(
                [item for item in packages if item.get("confidence_level") == "review_required"]
            ),
            "claim_status_counts": claim_status_counts,
            "review_gate_violation_count": review_gate_violations,
            "experiment_ready_count": experiment_ready_count,
            "unique_user_signal_count": len(
                [item for item in signals if int(item.get("unique_user_count") or 0) >= 2]
            ),
            "cross_video_signal_count": len(
                [item for item in signals if int(item.get("unique_video_count") or 0) >= 2]
            ),
            "duplicate_evidence_count": sum(int(item.get("duplicate_evidence_count") or 0) for item in signals),
        },
    }
