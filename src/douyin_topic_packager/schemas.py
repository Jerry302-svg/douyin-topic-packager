from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List


@dataclass
class VideoItem:
    aweme_id: str
    url: str = ""
    title: str = ""
    desc: str = ""
    create_time: int = 0
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    collect_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CommentItem:
    aweme_id: str
    text: str
    cid: str = ""
    like_count: int = 0
    create_time: int = 0
    user_nickname: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PainSignal:
    pain_point: str
    evidence: List[str] = field(default_factory=list)
    evidence_count: int = 0
    source_video_ids: List[str] = field(default_factory=list)
    source_titles: List[str] = field(default_factory=list)
    signal_strength: int = 60
    confidence: float = 0.6
    evidence_level: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AngleCandidate:
    pain_point: str
    angle: str
    opening_hook: str
    cta_direction: str
    proof_needed: str
    target_audience: str = "当前选题对应的目标用户"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationScorecard:
    pain_point: str
    angle: str
    scores: Dict[str, int]
    total_score: int
    risk_notes: List[str] = field(default_factory=list)
    rewrite_suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TopicPackage:
    brief_title: str
    topic: str
    pain_point: str
    evidence: List[str]
    target_audience: str
    opening_hook: str
    recommended_angle: str
    proof_needed: str
    cta_direction: str
    risk_notes: List[str]
    production_suggestions: List[str]
    fit_score: int
    why_worth_shooting: str = ""
    cover_copy: str = ""
    first_three_seconds: str = ""
    script_outline: List[str] = field(default_factory=list)
    comment_cta: str = ""
    material_notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TopicPackageRun:
    source_url: str
    resolved_url: str
    sec_uid: str
    videos: List[VideoItem]
    comments: List[CommentItem]
    pain_signals: List[PainSignal]
    angle_candidates: List[AngleCandidate]
    validation_scorecards: List[ValidationScorecard]
    topic_packages: List[TopicPackage]
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data
