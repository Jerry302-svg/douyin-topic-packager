from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from math import ceil, floor
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List

from .io_utils import read_json
from .schemas import TopicPackage


MIN_RECORD_IMPRESSIONS = 100
MIN_TOTAL_IMPRESSIONS = 1000
RECENCY_HALF_LIFE_DAYS = 180


def load_performance_feedback(path: str | Path | None) -> List[Dict[str, Any]]:
    if not path or not Path(path).exists():
        return []
    data = read_json(path)
    if isinstance(data, dict):
        data = data.get("records") or data.get("items") or []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def calibrate_topic_packages(
    packages: Iterable[TopicPackage],
    records: Iterable[Dict[str, Any]],
) -> List[TopicPackage]:
    record_list = list(records)
    calibrated: List[TopicPackage] = []
    for package in packages:
        matched_records = [item for item in record_list if _match_score(package, item) >= 0.58]
        matches = [
            item
            for item in matched_records
            if _number(item.get("impressions")) >= MIN_RECORD_IMPRESSIONS
        ]
        if not matches:
            package.performance_calibration = {
                "status": "insufficient_data" if matched_records else "unavailable",
                "reason": (
                    f"匹配记录的单条曝光低于 {MIN_RECORD_IMPRESSIONS}，保留证据评分。"
                    if matched_records
                    else "没有匹配到历史发布数据，保留证据评分。"
                ),
            }
            calibrated.append(package)
            continue
        total_impressions = sum(_number(item.get("impressions")) for item in matches)
        if total_impressions < MIN_TOTAL_IMPRESSIONS:
            package.performance_calibration = {
                "status": "insufficient_data",
                "matched_records": len(matches),
                "impressions": int(total_impressions),
                "reason": f"累计曝光不足 {MIN_TOTAL_IMPRESSIONS}，暂不调整证据评分。",
            }
            calibrated.append(package)
            continue
        impression_cap = max(MIN_RECORD_IMPRESSIONS, median(_number(item.get("impressions")) for item in matches) * 4)
        raw_scores = [_performance_score(item) for item in matches]
        bounded_scores = _winsorize(raw_scores)
        weights = [
            min(_number(item.get("impressions")), impression_cap) * _recency_weight(item)
            for item in matches
        ]
        weighted_score = sum(score * weight for score, weight in zip(bounded_scores, weights))
        total_weight = sum(weights)
        performance_score = round(weighted_score / total_weight, 1) if total_weight else 0.0
        confidence = _calibration_confidence(len(matches), total_impressions)
        blend_weight = {"low": 0.1, "medium": 0.18, "high": 0.25}[confidence]
        original = int(package.fit_score or 0)
        package.fit_score = int(round(original * (1.0 - blend_weight) + performance_score * blend_weight))
        package.performance_calibration = {
            "status": "applied",
            "matched_records": len(matches),
            "impressions": int(total_impressions),
            "performance_score": performance_score,
            "original_fit_score": original,
            "calibrated_fit_score": package.fit_score,
            "blend_weight": blend_weight,
            "confidence": confidence,
            "excluded_low_impression_records": len(matched_records) - len(matches),
        }
        calibrated.append(package)
    return sorted(calibrated, key=lambda item: item.fit_score, reverse=True)


def _performance_score(record: Dict[str, Any]) -> float:
    three_second = _rate(record.get("three_second_rate"))
    completion = _rate(record.get("completion_rate"))
    save = _rate(record.get("save_rate"))
    comment = _rate(record.get("comment_rate"))
    return min(100.0, three_second * 35 + completion * 35 + min(save * 5, 1.0) * 20 + min(comment * 5, 1.0) * 10)


def _match_score(package: TopicPackage, record: Dict[str, Any]) -> float:
    package_text = _normalize(f"{package.pain_point} {package.topic} {package.brief_title}")
    record_text = _normalize(
        f"{record.get('pain_point') or ''} {record.get('topic') or ''} {record.get('title') or ''}"
    )
    return SequenceMatcher(None, package_text, record_text).ratio() if package_text and record_text else 0.0


def _winsorize(values: List[float]) -> List[float]:
    if len(values) < 4:
        return values
    ordered = sorted(values)
    lower = ordered[ceil((len(ordered) - 1) * 0.1)]
    upper = ordered[floor((len(ordered) - 1) * 0.9)]
    return [max(lower, min(value, upper)) for value in values]


def _recency_weight(record: Dict[str, Any]) -> float:
    raw = record.get("published_at") or record.get("created_at") or record.get("date")
    if not raw:
        return 1.0
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 86400)
    except (TypeError, ValueError):
        return 1.0
    return max(0.2, 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS))


def _calibration_confidence(record_count: int, impressions: float) -> str:
    if record_count >= 3 and impressions >= 10000:
        return "high"
    if record_count >= 2 and impressions >= 3000:
        return "medium"
    return "low"


def _normalize(value: Any) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rate(value: Any) -> float:
    rate = _number(value)
    if rate > 1.0:
        rate /= 100.0
    return max(0.0, min(rate, 1.0))
