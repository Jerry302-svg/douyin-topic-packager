from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .io_utils import read_json
from .schemas import TopicPackage


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
        matches = [item for item in record_list if _match_score(package, item) >= 0.58]
        if not matches:
            package.performance_calibration = {
                "status": "unavailable",
                "reason": "没有匹配到历史发布数据，保留证据评分。",
            }
            calibrated.append(package)
            continue
        total_impressions = sum(_number(item.get("impressions")) for item in matches)
        weighted_score = sum(_performance_score(item) * max(1.0, _number(item.get("impressions"))) for item in matches)
        total_weight = sum(max(1.0, _number(item.get("impressions"))) for item in matches)
        performance_score = round(weighted_score / total_weight, 1) if total_weight else 0.0
        blend_weight = 0.25 if total_impressions >= 1000 else 0.1
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
