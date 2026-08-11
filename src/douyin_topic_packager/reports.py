from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .quality import evaluate_topic_run
from .schemas import PainSignal, TopicPackage, ValidationScorecard, VideoItem


CONFIDENCE_LABELS = {
    "publish_ready": "可直接使用",
    "review_required": "核验后使用",
    "exploratory": "探索性选题",
}
CLAIM_STATUS_LABELS = {
    "supported": "已有证据支持",
    "needs_verification": "需要补充证据",
    "needs_external_verification": "需要外部核验",
}
SIGNAL_TYPE_LABELS = {
    "audience_pain": "受众真实痛点",
    "content_hypothesis": "内容标题假设",
}
EVIDENCE_LEVEL_LABELS = {"strong": "强", "medium": "中", "weak": "弱"}
SCORE_LABELS = {
    "evidence_strength": "证据强度",
    "audience_fit": "受众适配",
    "novelty": "新颖度",
    "conversion_potential": "转化潜力",
    "production_ease": "制作难度",
    "compliance_safety": "安全与合规",
}


def render_topic_packages_markdown(
    *,
    source_url: str,
    resolved_url: str,
    sec_uid: str,
    videos: List[VideoItem],
    pain_signals: List[PainSignal],
    scorecards: List[ValidationScorecard],
    topic_packages: List[TopicPackage],
    min_evidence_count: int = 0,
    min_fit_score: int = 0,
    package_limit: int = 0,
    quality_result: Dict[str, Any] | None = None,
    analysis_metadata: Dict[str, Any] | None = None,
) -> str:
    quality_result = quality_result or evaluate_topic_run(
        pain_signals=[item.to_dict() for item in pain_signals],
        topic_packages=[item.to_dict() for item in topic_packages],
    )
    quality_metrics = quality_result["metrics"]
    cache_stats = (analysis_metadata or {}).get("cache") or {}
    actionable_signals = [
        item
        for item in pain_signals
        if getattr(item, "is_actionable", False)
    ]
    weak_signals = [item for item in pain_signals if item not in actionable_signals]
    publish_ready_packages = [item for item in topic_packages if item.confidence_level == "publish_ready"]
    review_required_packages = [item for item in topic_packages if item.confidence_level == "review_required"]
    exploratory_packages = [item for item in topic_packages if item.confidence_level == "exploratory"]
    lines: List[str] = [
        "# 抖音对标账号选题包",
        "",
        f"- 原始链接：{_link('打开原始链接', source_url)}",
        f"- 解析链接：{_link('打开解析页', resolved_url)}",
        f"- sec_uid：{sec_uid}",
        f"- 视频样本：Top {len(videos)}，按评论数排序",
        "",
        "## 运行摘要",
        "",
        f"- 痛点信号：{len(pain_signals)} 个",
        f"- 高可信痛点：{len(actionable_signals)} 个",
        f"- 弱证据观察：{len(weak_signals)} 个",
        f"- 多用户支持信号：{len([item for item in pain_signals if item.unique_user_count >= 2])} 个",
        f"- 跨视频支持信号：{len([item for item in pain_signals if item.unique_video_count >= 2])} 个",
        f"- 角度评分：{len(scorecards)} 个",
        f"- 选题包：{len(topic_packages)} 个",
        f"- 自动质量门禁：{'通过' if quality_result['passed'] else '需要复核'}",
        f"- 证据可回溯率：{quality_metrics['grounded_evidence_rate']:.0%}",
        f"- 生成来源：{_generator_summary(quality_metrics.get('generator_counts') or {})}",
        f"- 最小证据数：{max(0, int(min_evidence_count or 0))}",
        f"- 最小适配分：{max(0, int(min_fit_score or 0))}",
        f"- 选题包数量上限：{max(0, int(package_limit or 0)) or '不限制'}",
    ]
    if cache_stats:
        lines.append(
            f"- 模型缓存：命中 {cache_stats.get('hits', 0)} 次，"
            f"新请求 {cache_stats.get('misses', 0)} 次，修复 {cache_stats.get('repairs', 0)} 次"
        )
    if not quality_result["passed"]:
        lines.append("- 质量修复建议：")
        for recommendation in quality_result["recommendations"]:
            lines.append(f"  - {recommendation}")
    lines.extend([
        "",
        "## 下一步动作",
        "",
    ])
    if publish_ready_packages:
        lines.append(f"- 优先拍摄前 {min(3, len(publish_ready_packages))} 条“可直接使用”选题，并保留对应证据截图。")
    elif review_required_packages:
        lines.append("- 先补充公开来源或对应领域人工审核，核验完成后再进入拍摄。")
    else:
        lines.append("- 当前证据不足，先补评论、访谈或搜索反馈，不建议直接进入正式拍摄。")
    lines.extend(
        [
            "- 发布后回填曝光、3 秒留存、完播、收藏和评论数据，用于下一轮校准。",
            "",
        "## 推荐拍摄顺序"
        if publish_ready_packages
        else "## 核验优先级（审核后再拍）"
        if review_required_packages
        else "## 探索优先级（补证据后再拍）",
        "",
        ]
    )
    _append_shooting_order(lines, publish_ready_packages or review_required_packages or exploratory_packages)

    if publish_ready_packages:
        lines.extend(["## 一、可直接使用的选题包", ""])
        _append_topic_packages(lines, publish_ready_packages)
    if review_required_packages:
        lines.extend(["## 待核验选题（核验后再发布）", ""])
        lines.extend(["这些选题涉及高风险事实或标题假设，需要公开来源、对应领域审核或人工复核。", ""])
        _append_topic_packages(lines, review_required_packages)
    if exploratory_packages:
        lines.extend(["## 探索性选题（需补证据）", ""])
        lines.extend(["这些选题当前主要来自弱证据或标题假设，补充真实评论或用户访谈后再决定拍摄。", ""])
        _append_topic_packages(lines, exploratory_packages)

    lines.extend(["## 二、Top 视频信号", ""])
    for index, video in enumerate(videos, 1):
        lines.extend(
            [
                f"### {index}. {video.title or video.aweme_id}",
                "",
                f"- 评论数：{video.comment_count}",
                f"- 点赞数：{video.like_count}",
                f"- 分享数：{video.share_count}",
                f"- 链接：{_link('打开视频', video.url)}",
                "",
            ]
        )

    lines.extend(["## 三、评论痛点信号", ""])
    if not actionable_signals:
        lines.extend(["暂无足够评论信号。", ""])
    _append_pain_signals(lines, actionable_signals)

    if weak_signals:
        lines.extend(["## 四、弱证据观察", ""])
        lines.append("这些信号证据较少，适合当作灵感观察，不建议直接作为优先拍摄选题。")
        lines.append("")
        _append_pain_signals(lines, weak_signals)

    scorecard_section_number = "五" if weak_signals else "四"
    lines.extend([f"## {scorecard_section_number}、角度验证评分", ""])
    for index, scorecard in enumerate(scorecards, 1):
        score_text = "，".join(f"{SCORE_LABELS.get(key, key)}: {value}" for key, value in scorecard.scores.items())
        lines.extend(
            [
                f"### {index}. {scorecard.angle}",
                "",
                f"- 对应痛点：{scorecard.pain_point}",
                f"- 总分：{scorecard.total_score}",
                f"- 分项：{score_text}",
                f"- 风险：{'；'.join(scorecard.risk_notes) or '暂无'}",
                "",
            ]
        )
        if scorecard.score_reasons:
            lines.insert(
                len(lines) - 1,
                "- 评分依据："
                + "；".join(f"{SCORE_LABELS.get(key, key)}: {value}" for key, value in scorecard.score_reasons.items()),
            )

    return "\n".join(lines).strip() + "\n"


def _append_topic_packages(lines: List[str], topic_packages: List[TopicPackage]) -> None:
    if not topic_packages:
        lines.extend(["没有生成可用选题包。", ""])
        return
    for index, package in enumerate(topic_packages, 1):
        lines.extend(
            [
                f"### {index}. {package.brief_title}",
                "",
                f"- 适配分：{package.fit_score}",
                f"- 使用建议：{CONFIDENCE_LABELS.get(package.confidence_level, package.confidence_level)}",
                f"- 证据状态：{CLAIM_STATUS_LABELS.get(package.claim_status, package.claim_status)}",
                f"- 外部核验：{'需要' if package.external_verification_required else '不需要'}",
                f"- 新颖度：{package.novelty_score}",
                f"- 这条视频讲什么：{package.topic}",
                f"- 痛点：{package.pain_point}",
                f"- 目标用户：{package.target_audience}",
                f"- 开头建议：{package.opening_hook}",
                f"- 推荐角度：{package.recommended_angle}",
                f"- 需要补的证明：{package.proof_needed}",
                f"- CTA 方向：{package.cta_direction}",
                f"- 为什么值得拍：{package.why_worth_shooting or '来自评论、标题或账号内容中的真实信号。'}",
                "- 拍摄简案：",
                f"  - 封面文案：{package.cover_copy or _short_text(package.brief_title, limit=24)}",
                f"  - 前3秒：{package.first_three_seconds or package.opening_hook}",
                f"  - 评论引导：{package.comment_cta or package.cta_direction}",
            ]
        )
        for outline in (package.script_outline or [])[:5]:
            lines.append(f"  - 结构：{outline}")
        for note in (package.material_notes or [])[:4]:
            lines.append(f"  - 素材：{note}")
        if package.performance_calibration:
            lines.append(f"- 效果校准：{_calibration_summary(package.performance_calibration)}")
        if package.experiment_variants:
            lines.append("- A/B 实验：")
            for experiment in package.experiment_variants[:2]:
                lines.append(
                    f"  - {experiment.get('variant')}：{experiment.get('hook')} "
                    f"（观察 {experiment.get('primary_metric')}）"
                )
        lines.extend(
            [
                "- 证据：",
            ]
        )
        for evidence in package.evidence[:6]:
            lines.append(f"  - {evidence}")
        if package.quality_warnings:
            lines.append("- 质量提醒：")
            for warning in package.quality_warnings[:5]:
                lines.append(f"  - {warning}")
        lines.append("- 风险提醒：")
        for risk in package.risk_notes[:5]:
            lines.append(f"  - {risk}")
        lines.append("- 拍摄建议：")
        for suggestion in package.production_suggestions[:6]:
            lines.append(f"  - {suggestion}")
        lines.append("")


def _append_pain_signals(lines: List[str], pain_signals: List[PainSignal]) -> None:
    for index, signal in enumerate(pain_signals, 1):
        lines.extend(
            [
                f"### {index}. {signal.pain_point}",
                "",
                f"- 证据等级：{EVIDENCE_LEVEL_LABELS.get(getattr(signal, 'evidence_level', 'medium'), getattr(signal, 'evidence_level', 'medium'))}",
                f"- 证据数：{signal.evidence_count}",
                f"- 独立用户：{signal.unique_user_count}",
                f"- 涉及视频：{signal.unique_video_count}",
                f"- 重复证据：{signal.duplicate_evidence_count}",
                f"- 信号强度：{signal.signal_strength}",
                f"- 置信度：{signal.confidence}",
                f"- 信号类型：{SIGNAL_TYPE_LABELS.get(getattr(signal, 'signal_type', 'audience_pain'), getattr(signal, 'signal_type', 'audience_pain'))}",
                "- 代表证据：",
            ]
        )
        for evidence in signal.evidence[:5]:
            lines.append(f"  - {evidence}")
        lines.append("")


def _append_shooting_order(lines: List[str], topic_packages: List[TopicPackage]) -> None:
    if not topic_packages:
        lines.extend(["暂无可排序选题。", ""])
        return
    for index, package in enumerate(topic_packages[:5], 1):
        hook = _short_text(package.opening_hook or package.recommended_angle or package.topic, limit=70)
        lines.append(f"- {index}. **{package.brief_title}**（适配分：{package.fit_score}）：{hook}")
    lines.append("")


def write_markdown_report(content: str, path: str | Path) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(f"{target.suffix}.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(target)
    return str(target)


def _link(label: str, url: str) -> str:
    value = (url or "").strip()
    if not value:
        return "未提供"
    return f"[{label}]({value})"


def _short_text(value: str, limit: int = 70) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _calibration_summary(value: dict) -> str:
    if value.get("status") == "applied":
        confidence = {"low": "低", "medium": "中", "high": "高"}.get(value.get("confidence"), "未知")
        return (
            f"已应用（可信度：{confidence}，匹配 {value.get('matched_records', 0)} 条，"
            f"累计曝光 {value.get('impressions', 0)}，评分 {value.get('original_fit_score', 0)}"
            f" → {value.get('calibrated_fit_score', 0)}）"
        )
    return str(value.get("reason") or "没有足够历史数据，保留证据评分。")


def _generator_summary(counts: Dict[str, int]) -> str:
    if not counts:
        return "无可用选题包"
    labels = {"llm": "LLM", "fallback_rules": "规则降级", "unknown": "未知"}
    return "、".join(f"{labels.get(key, key)} {value} 条" for key, value in sorted(counts.items()))
