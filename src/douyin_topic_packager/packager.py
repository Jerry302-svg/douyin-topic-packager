from __future__ import annotations

import json
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


def normalize_conversion_mode(value: str | None) -> str:
    mode = (value or "balanced").strip().lower().replace("_", "-")
    return mode if mode in CONVERSION_MODE_INSTRUCTIONS else "balanced"


def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _fit_score(value: Any, default: int = 78) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = float(default)
    if 0 < score <= 10:
        score *= 10
    return max(0, min(int(round(score)), 100))


def _fallback_cta(pain_point: str, conversion_mode: str) -> str:
    pain = _text(pain_point)[:24] or "这个问题"
    mode = normalize_conversion_mode(conversion_mode)
    if mode == "conservative":
        return f"如果你也遇到过类似「{pain}」的情况，可以留言说一个大概场景，后续内容再拆常见判断思路。"
    if mode == "strong":
        return f"评论区说清楚你现在卡在「{pain}」的哪一步：刚开始、已经处理过，还是准备做决定，下一条按真实情况拆。"
    return f"评论区留下你卡住的具体场景、已经试过的方法和最想解决的一步，后续内容继续拆「{pain}」应该先从哪里切。"


def build_topic_package_messages(
    videos: List[VideoItem],
    pain_signals: List[PainSignal],
    angle_candidates: List[AngleCandidate],
    scorecards: List[ValidationScorecard],
    conversion_mode: str = "balanced",
) -> List[Dict[str, str]]:
    conversion_mode = normalize_conversion_mode(conversion_mode)
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
        "pain_signals": [item.to_dict() for item in pain_signals[:12]],
        "angle_candidates": [item.to_dict() for item in angle_candidates[:16]],
        "validation_scorecards": [item.to_dict() for item in scorecards[:12]],
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
        "The pain_point field must be a concise human-readable pain summary, not a copied title, hashtag, or raw comment."
        f" {CONVERSION_MODE_INSTRUCTIONS[conversion_mode]}"
    )
    user_prompt = (
        "请生成 3-8 个 topic_packages。每个对象必须包含："
        "brief_title, topic, pain_point, evidence, target_audience, opening_hook, "
        "recommended_angle, proof_needed, cta_direction, risk_notes, production_suggestions, "
        "fit_score, why_worth_shooting。\n\n"
        "质量要求：\n"
        "1. brief_title 要像用户能点击选择的选题标题。\n"
        "2. pain_point 必须来自输入信号，不得凭空创造行业和身份。\n"
        "3. evidence 必须引用评论、标题或描述里的真实表达。\n"
        "4. opening_hook 要像口播第一句话，具体、有代入感。\n"
        "5. recommended_angle 要说明这条视频怎么讲，不要复述痛点。\n"
        "6. CTA 要贴合痛点，不能固定写“评论行业”。\n"
        "7. 风险提醒适度即可，不要把表达全部压死。\n\n"
        f"输入数据：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def build_topic_package_repair_messages(raw_text: str) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a strict JSON repair tool. Return only one valid JSON object. "
        "Do not add markdown, explanations, code fences, XML tags, or hidden reasoning. "
        "Keep all Chinese content and field meanings unchanged. "
        "The final JSON object must contain a topic_packages array."
    )
    user_prompt = (
        "Repair the following model output into strict JSON. "
        "Preserve the topic_packages content as much as possible. "
        "If a value contains English double quotes, replace them with Chinese corner quotes. "
        "Output JSON only.\n\n"
        f"{raw_text[:12000]}"
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


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
    known_pains = {signal.pain_point for signal in pain_signals if signal.pain_point}
    evidence_by_pain = {signal.pain_point: signal.evidence for signal in pain_signals}
    normalized: List[TopicPackage] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        pain = _text(item.get("pain_point") or "")
        if known_pains and pain not in known_pains:
            matched = next((known for known in known_pains if pain and (known in pain or pain in known)), "")
            pain = matched or pain
        angle = _text(item.get("recommended_angle") or item.get("topic") or "")
        title = _text(item.get("brief_title") or angle or pain)
        if not pain or not angle or not title:
            continue
        key = f"{pain}::{title}"
        if key in seen:
            continue
        seen.add(key)
        evidence = item.get("evidence") or evidence_by_pain.get(pain) or []
        if isinstance(evidence, str):
            evidence = [evidence]
        risk_notes = item.get("risk_notes") or ["不要凭空编造案例、金额或确定性结果"]
        if isinstance(risk_notes, str):
            risk_notes = [risk_notes]
        suggestions = item.get("production_suggestions") or ["适合口播", "用真实评论或具体场景开头"]
        if isinstance(suggestions, str):
            suggestions = [suggestions]
        normalized.append(
            TopicPackage(
                brief_title=title[:80],
                topic=_text(item.get("topic") or angle or title),
                pain_point=pain,
                evidence=[_text(value) for value in evidence if _text(value)][:8],
                target_audience=_text(item.get("target_audience") or "当前选题对应的目标用户"),
                opening_hook=_text(item.get("opening_hook") or f"如果你也卡在“{pain[:24]}”，先别急着找万能答案。"),
                recommended_angle=angle,
                proof_needed=_text(item.get("proof_needed") or "补一个真实场景、常见误区或前后对比。"),
                cta_direction=_text(item.get("cta_direction") or f"评论区留下你卡住的具体场景，我帮你判断“{pain[:24]}”先从哪里切。"),
                risk_notes=[_text(value) for value in risk_notes if _text(value)][:6],
                production_suggestions=[_text(value) for value in suggestions if _text(value)][:6],
                fit_score=_fit_score(item.get("fit_score")),
                why_worth_shooting=_text(item.get("why_worth_shooting") or item.get("why_it_matters") or ""),
                metadata={"generated_by": "llm", "conversion_mode": conversion_mode, "llm_raw": item},
            )
        )
    normalized.sort(key=lambda item: item.fit_score, reverse=True)
    return normalized[:8]


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
                brief_title=candidate.angle[:60],
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
                return packages
            repaired = llm_client.complete(
                build_topic_package_repair_messages(raw),
                temperature=0.0,
                max_tokens=6000,
            )
            packages = normalize_llm_topic_packages(repaired, pain_signals, conversion_mode=conversion_mode)
            if packages:
                return packages
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] LLM 选题包生成失败，使用规则版结果：{exc}")
    return fallback_topic_packages(pain_signals, candidates, scorecards, conversion_mode=conversion_mode)
