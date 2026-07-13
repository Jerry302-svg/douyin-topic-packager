from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List

from .llm import LLMClient, parse_json_from_llm_text
from .schemas import AngleCandidate, PainSignal, TopicPackage, ValidationScorecard, VideoItem


CONVERSION_MODE_INSTRUCTIONS = {
    "balanced": (
        "conversion_mode=balanced. CTA can guide comments with a concrete situation, "
        "but must not promise a result or pretend to diagnose individual cases."
    ),
    "conservative": (
        "conversion_mode=conservative. CTA should be soft and educational. "
        "avoid direct diagnosis, avoid asking for sensitive amounts, and prefer "
        "phrases like asking users to describe a general scenario for future content."
    ),
    "strong": (
        "conversion_mode=strong. CTA can be more direct and conversion-oriented, "
        "asking users to describe their specific stage, obstacle, or decision point. "
        "Still avoid guaranteed outcomes, fabricated authority, or absolute promises."
    ),
}

UNSAFE_CTA_PHRASES = (
    "帮你判断",
    "我告诉你能不能",
    "我帮你诊断",
    "我帮你看",
    "报出你的金额",
    "留下你的金额",
    "金额区间",
    "留下发现日期",
)


def normalize_conversion_mode(value: str | None) -> str:
    mode = (value or "balanced").strip().lower().replace("_", "-")
    return mode if mode in CONVERSION_MODE_INSTRUCTIONS else "balanced"


def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _concise_title(value: Any, max_length: int = 32) -> str:
    text = _text(value)
    if len(text) <= max_length:
        return text
    boundaries = [
        match.end()
        for match in re.finditer(r"[。！？!?；;，,]", text[: max_length + 1])
        if match.end() >= 14
    ]
    if boundaries:
        return text[: boundaries[-1]].rstrip("。；;，, ")
    return f"{text[: max_length - 1].rstrip()}…"


def _fit_score(value: Any, default: int = 78) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = float(default)
    if 0 < score <= 10:
        score *= 10
    return max(0, min(int(round(score)), 100))


def filter_topic_packages(
    packages: List[TopicPackage],
    min_fit_score: int = 0,
    package_limit: int = 0,
) -> List[TopicPackage]:
    min_score = max(0, min(int(min_fit_score or 0), 100))
    limit = max(0, int(package_limit or 0))
    filtered = [item for item in packages if int(item.fit_score or 0) >= min_score]
    if limit:
        return filtered[:limit]
    return filtered


def _fallback_cta(pain_point: str, conversion_mode: str) -> str:
    pain = _text(pain_point)[:24] or "这个问题"
    mode = normalize_conversion_mode(conversion_mode)
    if mode == "conservative":
        return f"如果你也遇到过类似「{pain}」的情况，可以留言说一个大概场景，后续内容再拆常见判断思路。"
    if mode == "strong":
        return f"评论区说清楚你现在卡在「{pain}」的哪一步：刚开始、已经处理过，还是准备做决定，下一条按真实情况拆。"
    return f"评论区留下你卡住的具体场景、已经试过的方法和最想解决的一步，后续内容继续拆「{pain}」应该先从哪里切。"


def _cover_copy(title: str, pain_point: str) -> str:
    pain = _text(pain_point)[:18] or "先判断这一步"
    title_text = _text(title)[:20]
    return title_text or f"{pain}，先别急"


def _first_three_seconds(pain_point: str, opening_hook: str) -> str:
    if _text(opening_hook):
        return _text(opening_hook)
    pain = _text(pain_point)[:24] or "这个问题"
    return f"如果你也卡在「{pain}」，先别急着照搬方法。"


def _script_outline(pain_point: str, angle: str) -> List[str]:
    pain = _text(pain_point)[:22] or "用户痛点"
    angle_text = _text(angle)[:28] or "给一个可执行判断"
    return [
        f"开头点破痛点：{pain}",
        f"中段拆判断：{angle_text}",
        "结尾给动作：让用户留下具体阶段或场景",
    ]


def _material_notes(evidence: Iterable[Any]) -> List[str]:
    notes = ["准备一条真实评论或标题截图作为开头证据"]
    first = next((_text(item) for item in evidence if _text(item)), "")
    if first:
        notes.append(f"可引用证据：{first[:42]}")
    return notes


def build_topic_package_messages(
    videos: List[VideoItem],
    pain_signals: List[PainSignal],
    angle_candidates: List[AngleCandidate],
    scorecards: List[ValidationScorecard],
    conversion_mode: str = "balanced",
) -> List[Dict[str, str]]:
    conversion_mode = normalize_conversion_mode(conversion_mode)
    signal_ids = {item.pain_point: f"P{index}" for index, item in enumerate(pain_signals[:12], 1)}
    payload = {
        "videos": [
            {
                "aweme_id": video.aweme_id,
                "title": video.title,
                "desc": video.desc,
                "like_count": video.like_count,
                "comment_count": video.comment_count,
                "share_count": video.share_count,
            }
            for video in videos[:20]
        ],
        "pain_signals": [
            {"pain_signal_id": signal_ids[item.pain_point], **item.to_dict()}
            for item in pain_signals[:12]
        ],
        "angle_candidates": [
            {"pain_signal_id": signal_ids.get(item.pain_point, ""), **item.to_dict()}
            for item in angle_candidates[:16]
        ],
        "validation_scorecards": [
            {"pain_signal_id": signal_ids.get(item.pain_point, ""), **item.to_dict()}
            for item in scorecards[:12]
        ],
    }
    system_prompt = (
        "你是短视频深度选题研究员。你的任务是把对标账号的视频标题、评论痛点、角度候选和验证评分，"
        "整理成用户可直接选择的选题包。"
        "不要默认任何行业、身份、立场或业务类型；只能根据输入里的真实信号判断。"
        "不要写报告腔，不要写“围绕某痛点讲一条内容”。"
        "最终只能输出严格 JSON object，不要 markdown、解释或思考过程。"
    )
    system_prompt += (
        " JSON must be the whole response: start with { and end with }. "
        "Do not output markdown, comments, code fences, XML tags, or hidden reasoning. "
        "If a string needs quotation marks, use Chinese corner quotes instead of raw English double quotes. "
        "The pain_point field must be a concise human-readable pain summary, not a copied title, hashtag, or raw comment. "
        "Never request fabricated cases, fake amounts, fake screenshots, or invented proof. "
        "Do not offer individual legal, medical, or financial diagnosis in a CTA."
        " Keep brief_title within 14-28 Chinese characters and do not concatenate the full pain and angle."
        " Every package must copy one pain_signal_id exactly from the input (for example P1)."
        " Use different pain_signal_id values before creating a second angle for the same signal."
        f" {CONVERSION_MODE_INSTRUCTIONS[conversion_mode]}"
    )
    user_prompt = (
        "请生成 3-8 个 topic_packages。每个对象必须包含："
        "pain_signal_id, brief_title, topic, pain_point, evidence, target_audience, opening_hook, "
        "recommended_angle, proof_needed, cta_direction, risk_notes, production_suggestions, "
        "fit_score, why_worth_shooting, cover_copy, first_three_seconds, script_outline, "
        "comment_cta, material_notes。\n\n"
        "质量要求：\n"
        "1. brief_title 要像用户能点击选择的选题标题。\n"
        "2. pain_signal_id 必须逐字复制输入中的 P 编号；pain_point 可以概括表达，但不得改变含义。\n"
        "3. evidence 必须引用评论、标题或描述里的真实表达。\n"
        "4. opening_hook 要像口播第一句话，具体、有代入感。\n"
        "5. recommended_angle 要说明这条视频怎么讲，不要复述痛点。\n"
        "6. CTA 要贴合痛点，不能固定写“评论行业”。\n"
        "7. cover_copy 要像封面短句；first_three_seconds 要能直接口播。\n"
        "8. script_outline 给 3-5 段拍摄结构；material_notes 写素材准备。\n"
        "9. 风险提醒适度即可，不要把表达全部压死。\n\n"
        f"输入数据：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def build_topic_package_repair_messages(
    raw_text: str,
    pain_signals: List[PainSignal] | None = None,
) -> List[Dict[str, str]]:
    allowed_signals = [
        {
            "pain_signal_id": f"P{index}",
            "pain_point": signal.pain_point,
            "evidence": signal.evidence,
        }
        for index, signal in enumerate((pain_signals or [])[:12], 1)
    ]
    system_prompt = (
        "You are a strict JSON repair tool. Return only one valid JSON object. "
        "Do not add markdown, explanations, code fences, XML tags, or hidden reasoning. "
        "Keep all Chinese content and field meanings unchanged. "
        "The final JSON object must contain a topic_packages array. "
        "When allowed pain signals are provided, every package must use the matching pain_signal_id exactly."
    )
    user_prompt = (
        "Repair the following model output into strict JSON. "
        "Preserve the topic_packages content as much as possible. "
        "If a value contains English double quotes, replace them with Chinese corner quotes. "
        "Match each package to the allowed signal whose evidence it quotes. Output JSON only.\n\n"
        f"Allowed pain signals:\n{json.dumps(allowed_signals, ensure_ascii=False, indent=2)}\n\n"
        f"{raw_text[:12000]}"
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def _signal_is_actionable(signal: PainSignal) -> bool:
    return bool(
        signal.is_actionable
        or (signal.signal_type == "audience_pain" and signal.evidence_level in {"medium", "strong"})
    )


def _normalized_match_text(value: Any) -> str:
    return "".join(char.lower() for char in _text(value) if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _ground_evidence(requested: Iterable[Any], signal: PainSignal) -> tuple[List[str], List[Dict[str, Any]]]:
    refs = signal.evidence_refs or [
        {"source_type": "legacy", "source_id": f"evidence-{index}", "text": text}
        for index, text in enumerate(signal.evidence, 1)
    ]
    grounded_refs: List[Dict[str, Any]] = []
    requested_values = [_normalized_match_text(value) for value in requested if _normalized_match_text(value)]
    for ref in refs:
        source_text = _text(ref.get("text"))
        normalized_source = _normalized_match_text(source_text)
        if not normalized_source:
            continue
        if requested_values and not any(
            value == normalized_source or value in normalized_source or normalized_source in value
            for value in requested_values
        ):
            continue
        grounded_refs.append({**dict(ref), "text": source_text})
    if not grounded_refs:
        grounded_refs = [
            {**dict(ref), "text": _text(ref.get("text"))}
            for ref in refs
            if _text(ref.get("text"))
        ][:6]
    evidence = [_text(ref.get("text")) for ref in grounded_refs if _text(ref.get("text"))]
    return evidence[:8], grounded_refs[:8]


def _match_signal_by_evidence(
    requested: Iterable[Any],
    pain_signals: List[PainSignal],
) -> PainSignal | None:
    requested_values = {_normalized_match_text(value) for value in requested if _normalized_match_text(value)}
    if not requested_values:
        return None
    scored: List[tuple[int, PainSignal]] = []
    for signal in pain_signals:
        source_values = {
            _normalized_match_text(ref.get("text"))
            for ref in signal.evidence_refs
            if _normalized_match_text(ref.get("text"))
        } or {_normalized_match_text(value) for value in signal.evidence if _normalized_match_text(value)}
        score = sum(
            1
            for requested_value in requested_values
            if any(
                requested_value == source_value
                or requested_value in source_value
                or source_value in requested_value
                for source_value in source_values
            )
        )
        if score:
            scored.append((score, signal))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def normalize_llm_topic_packages(
    raw_text: str,
    pain_signals: List[PainSignal],
    conversion_mode: str = "balanced",
) -> List[TopicPackage]:
    conversion_mode = normalize_conversion_mode(conversion_mode)
    try:
        parsed = parse_json_from_llm_text(raw_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(parsed, list):
        parsed = {"topic_packages": parsed}
    if not isinstance(parsed, dict):
        return []
    items = parsed.get("topic_packages") or parsed.get("production_briefs") or parsed.get("briefs") or []
    if not isinstance(items, list):
        return []
    signal_by_pain = {signal.pain_point: signal for signal in pain_signals if signal.pain_point}
    signal_by_id = {f"P{index}": signal for index, signal in enumerate(pain_signals, 1)}
    known_pains = set(signal_by_pain)
    normalized: List[TopicPackage] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        pain_signal_id = _text(item.get("pain_signal_id")).upper()
        signal = signal_by_id.get(pain_signal_id)
        pain = _text(item.get("pain_point") or "")
        if signal is not None:
            pain = signal.pain_point
        elif pain not in known_pains:
            matched = next((known for known in known_pains if pain and (known in pain or pain in known)), "")
            if not matched:
                signal = _match_signal_by_evidence(evidence, pain_signals)
                if signal is None:
                    continue
                pain = signal.pain_point
            else:
                pain = matched
        angle = _text(item.get("recommended_angle") or item.get("topic") or "")
        title = _text(item.get("brief_title") or angle or pain)
        if not pain or not angle or not title:
            continue
        key = f"{pain}::{title}"
        if key in seen:
            continue
        seen.add(key)
        signal = signal_by_pain[pain]
        risk_notes = item.get("risk_notes") or ["不要凭空编造案例、金额或确定性结果"]
        if isinstance(risk_notes, str):
            risk_notes = [risk_notes]
        suggestions = item.get("production_suggestions") or ["适合口播", "用真实评论或具体场景开头"]
        if isinstance(suggestions, str):
            suggestions = [suggestions]
        comment_cta = _text(item.get("comment_cta") or item.get("cta_direction") or _fallback_cta(pain, conversion_mode))
        evidence_clean, evidence_refs = _ground_evidence(evidence, signal)
        normalized.append(
            TopicPackage(
                brief_title=_concise_title(title),
                topic=_text(item.get("topic") or angle or title),
                pain_point=pain,
                evidence=evidence_clean,
                target_audience=_text(item.get("target_audience") or "当前选题对应的目标用户"),
                opening_hook=_text(item.get("opening_hook") or f"如果你也卡在“{pain[:24]}”，先别急着找万能答案。"),
                recommended_angle=angle,
                proof_needed=_text(item.get("proof_needed") or "补一个真实场景、常见误区或前后对比。"),
                cta_direction=_text(item.get("cta_direction") or comment_cta),
                risk_notes=[_text(value) for value in risk_notes if _text(value)][:6],
                production_suggestions=[_text(value) for value in suggestions if _text(value)][:6],
                fit_score=_fit_score(item.get("fit_score")),
                why_worth_shooting=_text(item.get("why_worth_shooting") or item.get("why_it_matters") or ""),
                cover_copy=_text(item.get("cover_copy") or _cover_copy(title, pain)),
                first_three_seconds=_first_three_seconds(pain, _text(item.get("first_three_seconds") or item.get("opening_hook") or "")),
                script_outline=[
                    _text(value)
                    for value in (item.get("script_outline") if isinstance(item.get("script_outline"), list) else [])
                    if _text(value)
                ]
                or _script_outline(pain, angle),
                comment_cta=comment_cta,
                material_notes=[
                    _text(value)
                    for value in (item.get("material_notes") if isinstance(item.get("material_notes"), list) else [])
                    if _text(value)
                ]
                or _material_notes(evidence_clean),
                evidence_refs=evidence_refs,
                confidence_level="publish_ready" if _signal_is_actionable(signal) else "exploratory",
                metadata={
                    "generated_by": "llm",
                    "conversion_mode": conversion_mode,
                    "pain_signal_id": pain_signal_id,
                    "llm_raw": item,
                },
            )
        )
    normalized.sort(key=lambda item: item.fit_score, reverse=True)
    return normalized[:8]


def audit_topic_packages(
    packages: List[TopicPackage],
    pain_signals: List[PainSignal],
    scorecards: List[ValidationScorecard],
    *,
    conversion_mode: str = "balanced",
) -> List[TopicPackage]:
    """Ground, de-duplicate and safety-check packages before they reach reports."""
    signal_by_pain = {item.pain_point: item for item in pain_signals if item.pain_point}
    score_by_angle = {item.angle: item for item in scorecards}
    accepted: List[TopicPackage] = []
    accepted_pain_counts: Dict[str, int] = {}
    for package in packages:
        signal = signal_by_pain.get(package.pain_point)
        if signal is None:
            continue
        max_per_pain = 2 if _signal_is_actionable(signal) else 1
        if accepted_pain_counts.get(package.pain_point, 0) >= max_per_pain:
            continue
        package.brief_title = _concise_title(package.brief_title or package.topic)
        warnings = list(package.quality_warnings)
        grounded_evidence, grounded_refs = _ground_evidence(package.evidence, signal)
        if package.evidence != grounded_evidence:
            warnings.append("模型证据未能逐字匹配，已替换为真实来源证据。")
        if not grounded_evidence:
            continue
        package.evidence = grounded_evidence
        package.evidence_refs = grounded_refs

        if any(word in package.proof_needed for word in ["虚构", "编造", "假装真实", "伪造"]):
            package.proof_needed = "补充真实、可脱敏且有使用权限的场景、材料或公开来源；没有真实材料时明确使用示意图。"
            warnings.append("已移除要求虚构证明材料的指令。")

        cta_text = " ".join(
            [package.cta_direction, package.comment_cta, *package.script_outline]
        )
        unsafe_cta = any(phrase in cta_text for phrase in UNSAFE_CTA_PHRASES)
        if unsafe_cta:
            package.cta_direction = _fallback_cta(package.pain_point, conversion_mode)
            package.comment_cta = package.cta_direction
            package.script_outline = [
                line
                for line in package.script_outline
                if not any(phrase in line for phrase in UNSAFE_CTA_PHRASES)
            ]
            package.script_outline.append("结尾邀请用户描述非敏感的处理阶段或常见障碍，不收集金额和个案材料。")
            warnings.append("已将个案判断或敏感信息收集 CTA 改为一般性场景讨论。")

        if signal.signal_type == "content_hypothesis" and "权威" in package.proof_needed:
            package.proof_needed = (
                "把原视频标题作为待核验假设，并补充可公开查验的来源或专业审核意见；"
                "不能把对标账号标题本身当作权威依据。"
            )
            warnings.append("已将标题来源从权威依据降级为待核验假设。")

        duplicate = next(
            (
                existing
                for existing in accepted
                if SequenceMatcher(
                    None,
                    _normalized_match_text(existing.brief_title or existing.topic),
                    _normalized_match_text(package.brief_title or package.topic),
                ).ratio()
                >= 0.82
            ),
            None,
        )
        if duplicate is not None:
            continue

        scorecard = score_by_angle.get(package.recommended_angle)
        deterministic_score = scorecard.total_score if scorecard else signal.signal_strength
        package.fit_score = int(round(package.fit_score * 0.7 + deterministic_score * 0.3))
        package.confidence_level = "publish_ready" if _signal_is_actionable(signal) else "exploratory"
        if package.confidence_level == "exploratory":
            warnings.append("当前主要是弱证据或标题假设，只能作为探索性选题。")
            source_label = "真实用户评论" if signal.signal_type == "audience_pain" else "原视频标题"
            package.why_worth_shooting = (
                f"{source_label}中出现了这个方向，但当前只有 {signal.evidence_count} 条证据；"
                "适合先补充评论、访谈或搜索反馈，再决定是否进入正式拍摄。"
            )
            if package.target_audience in {"", "目标用户", "相关用户", "当前选题对应的目标用户"}:
                package.target_audience = (
                    f"可能关注“{_concise_title(package.pain_point, max_length=24)}”的人，"
                    "具体场景仍需进一步验证"
                )
            if signal.signal_type == "content_hypothesis":
                package.production_suggestions = [
                    item for item in package.production_suggestions if "评论痛点" not in item
                ]
                package.production_suggestions.extend(
                    ["用原视频标题中的问题开头", "发布前补充真实评论或用户访谈"]
                )
                package.production_suggestions = list(dict.fromkeys(package.production_suggestions))[:6]
        package.quality_warnings = list(dict.fromkeys(warnings))
        package.metadata = {
            **(package.metadata or {}),
            "audit": {
                "grounded": True,
                "confidence_level": package.confidence_level,
                "warning_count": len(package.quality_warnings),
            },
        }
        accepted.append(package)
        accepted_pain_counts[package.pain_point] = accepted_pain_counts.get(package.pain_point, 0) + 1
    accepted.sort(key=lambda item: item.fit_score, reverse=True)
    return accepted


def fallback_topic_packages(
    pain_signals: List[PainSignal],
    candidates: List[AngleCandidate],
    scorecards: List[ValidationScorecard],
    limit: int = 6,
    conversion_mode: str = "balanced",
) -> List[TopicPackage]:
    conversion_mode = normalize_conversion_mode(conversion_mode)
    signal_by_pain = {item.pain_point: item for item in pain_signals}
    score_by_angle = {item.angle: item for item in scorecards}
    packages: List[TopicPackage] = []
    for candidate in candidates:
        score = score_by_angle.get(candidate.angle)
        signal = signal_by_pain.get(candidate.pain_point)
        if not signal:
            continue
        packages.append(
            TopicPackage(
                brief_title=_concise_title(candidate.angle),
                topic=candidate.angle,
                pain_point=candidate.pain_point,
                evidence=signal.evidence[:6],
                target_audience=candidate.target_audience,
                opening_hook=candidate.opening_hook,
                recommended_angle=candidate.angle,
                proof_needed=candidate.proof_needed,
                cta_direction=_fallback_cta(candidate.pain_point, conversion_mode),
                risk_notes=(score.risk_notes if score else ["不要凭空编造案例或确定性结果"])[:5],
                production_suggestions=["适合口播", "不需要复杂场景", "用评论痛点开头", "适合 60-90 秒"],
                fit_score=int(score.total_score if score else signal.signal_strength),
                why_worth_shooting=f"评论和标题里已经出现相关信号，证据数 {signal.evidence_count}，适合做成可直接回应用户疑问的内容。",
                cover_copy=_cover_copy(candidate.angle, candidate.pain_point),
                first_three_seconds=_first_three_seconds(candidate.pain_point, candidate.opening_hook),
                script_outline=_script_outline(candidate.pain_point, candidate.angle),
                comment_cta=_fallback_cta(candidate.pain_point, conversion_mode),
                material_notes=_material_notes(signal.evidence),
                evidence_refs=signal.evidence_refs,
                confidence_level="publish_ready" if _signal_is_actionable(signal) else "exploratory",
                metadata={"generated_by": "fallback_rules", "conversion_mode": conversion_mode},
            )
        )
        if len(packages) >= limit:
            break
    return packages


def generate_topic_packages(
    videos: List[VideoItem],
    pain_signals: List[PainSignal],
    candidates: List[AngleCandidate],
    scorecards: List[ValidationScorecard],
    llm_client: LLMClient | None = None,
    conversion_mode: str = "balanced",
    min_fit_score: int = 0,
    package_limit: int = 0,
) -> List[TopicPackage]:
    conversion_mode = normalize_conversion_mode(conversion_mode)
    if llm_client is not None:
        try:
            raw = llm_client.complete(
                build_topic_package_messages(videos, pain_signals, candidates, scorecards, conversion_mode=conversion_mode),
                temperature=0.35,
                max_tokens=6000,
            )
            packages = normalize_llm_topic_packages(raw, pain_signals, conversion_mode=conversion_mode)
            if packages:
                packages = audit_topic_packages(
                    packages,
                    pain_signals,
                    scorecards,
                    conversion_mode=conversion_mode,
                )
                return filter_topic_packages(packages, min_fit_score=min_fit_score, package_limit=package_limit)
            repaired = llm_client.complete(
                build_topic_package_repair_messages(raw, pain_signals),
                temperature=0.0,
                max_tokens=6000,
            )
            packages = normalize_llm_topic_packages(repaired, pain_signals, conversion_mode=conversion_mode)
            if packages:
                packages = audit_topic_packages(
                    packages,
                    pain_signals,
                    scorecards,
                    conversion_mode=conversion_mode,
                )
                return filter_topic_packages(packages, min_fit_score=min_fit_score, package_limit=package_limit)
            print("[WARN] LLM 输出未通过痛点与证据校验，已降级为规则版选题包。")
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] LLM 选题包生成失败，使用规则版结果：{exc}")
    packages = fallback_topic_packages(pain_signals, candidates, scorecards, conversion_mode=conversion_mode)
    packages = audit_topic_packages(packages, pain_signals, scorecards, conversion_mode=conversion_mode)
    return filter_topic_packages(packages, min_fit_score=min_fit_score, package_limit=package_limit)
