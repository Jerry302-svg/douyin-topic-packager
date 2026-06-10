from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from .llm import LLMClient, parse_json_from_llm_text
from .schemas import AngleCandidate, PainSignal, TopicPackage, ValidationScorecard, VideoItem


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


def build_topic_package_messages(
    videos: List[VideoItem],
    pain_signals: List[PainSignal],
    angle_candidates: List[AngleCandidate],
    scorecards: List[ValidationScorecard],
) -> List[Dict[str, str]]:
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


def normalize_llm_topic_packages(raw_text: str, pain_signals: List[PainSignal]) -> List[TopicPackage]:
    parsed = parse_json_from_llm_text(raw_text)
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
                metadata={"generated_by": "llm", "llm_raw": item},
            )
        )
    normalized.sort(key=lambda item: item.fit_score, reverse=True)
    return normalized[:8]


def fallback_topic_packages(
    pain_signals: List[PainSignal],
    candidates: List[AngleCandidate],
    scorecards: List[ValidationScorecard],
    limit: int = 6,
) -> List[TopicPackage]:
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
                cta_direction=candidate.cta_direction,
                risk_notes=(score.risk_notes if score else ["不要凭空编造案例或确定性结果"])[:5],
                production_suggestions=["适合口播", "不需要复杂场景", "用评论痛点开头", "适合 60-90 秒"],
                fit_score=int(score.total_score if score else signal.signal_strength),
                why_worth_shooting=f"评论和标题里已经出现相关信号，证据数 {signal.evidence_count}，适合做成可直接回应用户疑问的内容。",
                metadata={"generated_by": "fallback_rules"},
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
) -> List[TopicPackage]:
    if llm_client is not None:
        try:
            raw = llm_client.complete(
                build_topic_package_messages(videos, pain_signals, candidates, scorecards),
                temperature=0.35,
                max_tokens=6000,
            )
            packages = normalize_llm_topic_packages(raw, pain_signals)
            if packages:
                return packages
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] LLM 选题包生成失败，使用规则版结果：{exc}")
    return fallback_topic_packages(pain_signals, candidates, scorecards)
