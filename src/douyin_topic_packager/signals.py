from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List

from .schemas import AngleCandidate, CommentItem, PainSignal, ValidationScorecard, VideoItem


STOP_WORDS = {
    "这个", "那个", "就是", "还是", "真的", "可以", "怎么", "为什么", "是不是",
    "一个", "没有", "感觉", "老师", "博主", "视频", "内容", "问题", "的话",
}

GENERIC_HELP_TEXTS = {
    "老师你好",
    "老师你好我想你帮帮忙",
    "帮帮我",
    "求回复",
    "路过",
}


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _strip_hashtags(value: str) -> str:
    text = re.sub(r"#\S+", "", str(value or ""))
    return _clean_text(text).strip(" ，。！？；、")


def _label_from_text(value: str, fallback_keywords: List[str] | None = None, max_len: int = 46) -> str:
    text = _strip_hashtags(value)
    if not text:
        text = " / ".join((fallback_keywords or [])[:3])
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9，。！？；、：:,.?!\s]", "", text)
    text = _clean_text(text).strip(" ，。！？；、")
    if len(text) > max_len:
        text = text[:max_len].rstrip("，。！？；、 ")
    return text


def _cluster_label_from_comment(text: str, keywords: List[str]) -> str:
    clean = _clean_text(text)
    if "第一步" in clean and any(word in clean for word in ["不知道", "怎么", "怎么办", "怕"]):
        return "不知道第一步怎么做"
    if "违约金" in clean:
        return "担心违约金风险"
    if any(word in clean for word in ["被骗", "骗入职", "套路"]):
        return "担心被套路或被骗"
    if "能不能签" in clean or ("签" in clean and "能不能" in clean):
        return "担心合同或公会能不能签"
    if any(word in clean for word in ["没效果", "没用", "白忙"]):
        return "担心照做以后没有效果"
    return _label_from_text(clean, keywords)


def _keywords(text: str, limit: int = 8) -> List[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,}", text)
    cleaned = [token for token in tokens if token not in STOP_WORDS and len(token) >= 2]
    return cleaned[:limit]


def _question_or_pain_score(text: str) -> int:
    score = 0
    if any(mark in text for mark in ["？", "?", "怎么办", "怎么处理", "怎么解决", "能不能", "可不可以"]):
        score += 25
    if any(word in text for word in ["担心", "害怕", "纠结", "不知道", "不懂", "被骗", "损失", "失败", "没用", "没效果"]):
        score += 20
    if any(word in text for word in ["想问", "咨询", "求助", "请问", "有人知道", "有没有"]):
        score += 15
    return score


def _is_meaningful_comment(text: str) -> bool:
    clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", _clean_text(text))
    if len(clean) < 6:
        return False
    if clean in GENERIC_HELP_TEXTS:
        return False
    return _question_or_pain_score(text) > 0


def _evidence_level(evidence_count: int, confidence: float) -> str:
    if evidence_count >= 2 or confidence >= 0.68:
        return "strong"
    if evidence_count <= 1 and confidence < 0.6:
        return "weak"
    return "medium"


def build_pain_signals(videos: List[VideoItem], comments: List[CommentItem], limit: int = 12) -> List[PainSignal]:
    comments_by_video: Dict[str, List[CommentItem]] = defaultdict(list)
    videos_by_id = {video.aweme_id: video for video in videos}
    for comment in comments:
        if comment.text:
            comments_by_video[comment.aweme_id].append(comment)

    buckets: Dict[str, dict] = {}

    def add_signal(
        label: str,
        evidence: str,
        video: VideoItem | None,
        weight: int,
        *,
        signal_type: str,
        evidence_ref: dict,
    ) -> None:
        pain = _clean_text(label).strip("，。！？； ")
        evidence_text = _clean_text(evidence)
        if not pain or not evidence_text:
            return
        bucket_key = f"{signal_type}::{pain}"
        bucket = buckets.setdefault(
            bucket_key,
            {
                "pain": pain,
                "evidence": [],
                "evidence_refs": [],
                "count": 0,
                "video_ids": set(),
                "titles": set(),
                "score": 0,
                "signal_type": signal_type,
            },
        )
        if evidence_text not in bucket["evidence"]:
            bucket["evidence"].append(evidence_text)
        bucket["count"] += 1
        bucket["score"] += weight
        if evidence_ref not in bucket["evidence_refs"]:
            bucket["evidence_refs"].append(evidence_ref)
        if video:
            bucket["video_ids"].add(video.aweme_id)
            if video.title:
                bucket["titles"].add(video.title)

    for video in videos:
        title_keywords = _keywords(f"{video.title} {video.desc}", limit=4)
        title_label = _label_from_text(video.title or video.desc, title_keywords)
        if title_label:
            title_text = video.title or video.desc or title_label
            add_signal(
                title_label,
                title_text,
                video,
                12 + min(video.comment_count, 50),
                signal_type="content_hypothesis",
                evidence_ref={
                    "source_type": "video_title",
                    "source_id": video.aweme_id,
                    "aweme_id": video.aweme_id,
                    "url": video.url,
                    "text": title_text,
                },
            )

    for comment in comments:
        text = _clean_text(comment.text)
        if not text or not _is_meaningful_comment(text):
            continue
        video = videos_by_id.get(comment.aweme_id)
        kws = _keywords(text, limit=5)
        score = _question_or_pain_score(text) + min(comment.like_count, 30)
        if not kws:
            continue
        label = _cluster_label_from_comment(text, kws)
        add_signal(
            label,
            text[:180],
            video,
            max(8, score),
            signal_type="audience_pain",
            evidence_ref={
                "source_type": "comment",
                "source_id": comment.cid or f"{comment.aweme_id}:{comment.create_time}:{len(text)}",
                "aweme_id": comment.aweme_id,
                "text": text[:180],
            },
        )

    signals: List[PainSignal] = []
    for bucket in buckets.values():
        pain = bucket["pain"]
        evidence_count = int(bucket["count"])
        score = int(bucket["score"])
        strength = max(45, min(96, 45 + min(evidence_count * 4, 30) + min(score // 8, 21)))
        confidence = round(max(0.45, min(0.95, 0.45 + evidence_count * 0.03 + score / 600)), 2)
        signal_type = str(bucket["signal_type"])
        evidence_level = _evidence_level(evidence_count, confidence)
        if signal_type == "content_hypothesis":
            evidence_level = "weak"
            confidence = min(confidence, 0.55)
        signals.append(
            PainSignal(
                pain_point=pain,
                evidence=bucket["evidence"][:8],
                evidence_refs=bucket["evidence_refs"][:8],
                evidence_count=evidence_count,
                source_video_ids=sorted(bucket["video_ids"])[:8],
                source_titles=sorted(bucket["titles"])[:5],
                signal_strength=strength,
                confidence=confidence,
                evidence_level=evidence_level,
                signal_type=signal_type,
                is_actionable=signal_type == "audience_pain" and evidence_level != "weak",
            )
        )
    signals.sort(
        key=lambda item: (item.is_actionable, item.signal_type == "audience_pain", item.signal_strength, item.evidence_count),
        reverse=True,
    )
    return signals[:limit]


def build_angle_candidates(signals: Iterable[PainSignal], limit: int = 16) -> List[AngleCandidate]:
    candidates: List[AngleCandidate] = []
    seen: set[str] = set()
    for signal in signals:
        pain = signal.pain_point
        if not pain:
            continue
        options = [
            (
                f"{pain}，先拆成一个能马上执行的小动作",
                f"如果你也卡在“{pain[:24]}”，先别急着找万能答案，先看第一步。",
            ),
            (
                f"{pain}，先别急着补方法，先补判断",
                f"很多人遇到“{pain[:24]}”时，第一反应就错了。",
            ),
        ]
        for angle, hook in options:
            key = f"{pain}::{angle}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                AngleCandidate(
                    pain_point=pain,
                    angle=angle,
                    opening_hook=hook,
                    cta_direction=f"评论区留下你卡住的具体场景、已经试过的方法和最想解决的一步，我帮你判断“{pain[:24]}”先从哪里切。",
                    proof_needed="补一个真实场景、常见误区或前后对比，用来证明这条内容不是空泛建议。",
                )
            )
            if len(candidates) >= limit:
                return candidates
    return candidates[:limit]


def validate_angles(candidates: Iterable[AngleCandidate], signals: Iterable[PainSignal], limit: int = 12) -> List[ValidationScorecard]:
    signal_by_pain = {signal.pain_point: signal for signal in signals}
    scorecards: List[ValidationScorecard] = []
    seen_pains: Counter[str] = Counter()
    for candidate in candidates:
        signal = signal_by_pain.get(candidate.pain_point)
        evidence_strength = int(signal.signal_strength if signal else 62)
        seen_pains[candidate.pain_point] += 1
        audience_fit = 88 if signal and signal.is_actionable else 66
        novelty = max(62, 84 - (seen_pains[candidate.pain_point] - 1) * 10)
        conversion = min(92, 58 + evidence_strength // 3 + (8 if signal and signal.is_actionable else 0))
        production_ease = 80 if "对比" in candidate.proof_needed else 84
        sensitive = any(word in candidate.pain_point for word in ["退款", "法院", "诊断", "金额", "合同", "医疗"])
        compliance = 78 if sensitive else 86
        scores = {
            "evidence_strength": evidence_strength,
            "audience_fit": audience_fit,
            "novelty": novelty,
            "conversion_potential": conversion,
            "production_ease": production_ease,
            "compliance_safety": compliance,
        }
        total = int(sum(scores.values()) / len(scores))
        scorecards.append(
            ValidationScorecard(
                pain_point=candidate.pain_point,
                angle=candidate.angle,
                scores=scores,
                total_score=total,
                risk_notes=["不要承诺保证结果", "不要凭空编造案例、身份、金额或确定性结论"],
                rewrite_suggestion="" if total >= 75 else "把痛点说得更具体，补一个更真实的场景。",
                score_reasons={
                    "evidence_strength": f"来自 {signal.evidence_count if signal else 0} 条证据，类型为 {signal.signal_type if signal else 'unknown'}。",
                    "audience_fit": "真实评论痛点加分。" if signal and signal.is_actionable else "当前主要来自标题假设，受众匹配度降级。",
                    "novelty": f"同一痛点的第 {seen_pains[candidate.pain_point]} 个角度，重复角度递减。",
                    "conversion_potential": "根据证据强度和可行动性计算。",
                    "production_ease": "需要场景或对比素材，制作难度按素材要求计算。",
                    "compliance_safety": "敏感领域词会降低合规分。" if sensitive else "未检测到明显敏感领域词。",
                },
            )
        )
    scorecards.sort(key=lambda item: item.total_score, reverse=True)
    return scorecards[:limit]
