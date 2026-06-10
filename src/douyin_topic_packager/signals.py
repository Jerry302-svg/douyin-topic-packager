from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List

from .schemas import AngleCandidate, CommentItem, PainSignal, ValidationScorecard, VideoItem


STOP_WORDS = {
    "这个", "那个", "就是", "还是", "真的", "可以", "怎么", "为什么", "是不是",
    "一个", "没有", "感觉", "老师", "博主", "视频", "内容", "问题", "的话",
}


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


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


def build_pain_signals(videos: List[VideoItem], comments: List[CommentItem], limit: int = 12) -> List[PainSignal]:
    comments_by_video: Dict[str, List[CommentItem]] = defaultdict(list)
    videos_by_id = {video.aweme_id: video for video in videos}
    for comment in comments:
        if comment.text:
            comments_by_video[comment.aweme_id].append(comment)

    buckets: Dict[str, dict] = {}

    def add_signal(label: str, evidence: str, video: VideoItem | None, weight: int) -> None:
        pain = _clean_text(label).strip("，。！？； ")
        evidence_text = _clean_text(evidence)
        if not pain or not evidence_text:
            return
        bucket = buckets.setdefault(
            pain,
            {
                "evidence": [],
                "count": 0,
                "video_ids": set(),
                "titles": set(),
                "score": 0,
            },
        )
        if evidence_text not in bucket["evidence"]:
            bucket["evidence"].append(evidence_text)
        bucket["count"] += 1
        bucket["score"] += weight
        if video:
            bucket["video_ids"].add(video.aweme_id)
            if video.title:
                bucket["titles"].add(video.title)

    for video in videos:
        title_keywords = _keywords(f"{video.title} {video.desc}", limit=4)
        if title_keywords:
            add_signal(" / ".join(title_keywords[:3]), video.title or video.desc, video, 12 + min(video.comment_count, 50))

    for comment in comments:
        text = _clean_text(comment.text)
        if not text:
            continue
        video = videos_by_id.get(comment.aweme_id)
        kws = _keywords(text, limit=5)
        score = _question_or_pain_score(text) + min(comment.like_count, 30)
        if not kws:
            continue
        if score <= 0 and len(text) < 8:
            continue
        label = " / ".join(kws[:3])
        add_signal(label, text[:180], video, max(8, score))

    signals: List[PainSignal] = []
    for pain, bucket in buckets.items():
        evidence_count = int(bucket["count"])
        score = int(bucket["score"])
        strength = max(45, min(96, 45 + min(evidence_count * 4, 30) + min(score // 8, 21)))
        confidence = round(max(0.45, min(0.95, 0.45 + evidence_count * 0.03 + score / 600)), 2)
        signals.append(
            PainSignal(
                pain_point=pain,
                evidence=bucket["evidence"][:8],
                evidence_count=evidence_count,
                source_video_ids=sorted(bucket["video_ids"])[:8],
                source_titles=sorted(bucket["titles"])[:5],
                signal_strength=strength,
                confidence=confidence,
            )
        )
    signals.sort(key=lambda item: (item.signal_strength, item.evidence_count), reverse=True)
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
    for index, candidate in enumerate(candidates, 1):
        signal = signal_by_pain.get(candidate.pain_point)
        evidence_strength = int(signal.signal_strength if signal else 62)
        scores = {
            "evidence_strength": evidence_strength,
            "audience_fit": min(95, 68 + evidence_strength // 5),
            "novelty": 78 if index % 2 else 72,
            "conversion_potential": min(92, 62 + evidence_strength // 4),
            "production_ease": 88,
            "compliance_safety": 90,
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
            )
        )
    scorecards.sort(key=lambda item: item.total_score, reverse=True)
    return scorecards[:limit]
