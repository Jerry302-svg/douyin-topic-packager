from __future__ import annotations

from pathlib import Path
from typing import List

from .schemas import PainSignal, TopicPackage, ValidationScorecard, VideoItem


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
) -> str:
    lines: List[str] = [
        "# 抖音对标账号选题包",
        "",
        f"- 原始链接：{source_url}",
        f"- 解析链接：{resolved_url}",
        f"- sec_uid：{sec_uid}",
        f"- 视频样本：Top {len(videos)}，按评论数排序",
        "",
        "## 运行摘要",
        "",
        f"- 痛点信号：{len(pain_signals)} 个",
        f"- 角度评分：{len(scorecards)} 个",
        f"- 选题包：{len(topic_packages)} 个",
        f"- 最小证据数：{max(0, int(min_evidence_count or 0))}",
        f"- 最小适配分：{max(0, int(min_fit_score or 0))}",
        f"- 选题包数量上限：{max(0, int(package_limit or 0)) or '不限制'}",
        "",
        "## 一、Top 视频信号",
        "",
    ]
    for index, video in enumerate(videos, 1):
        lines.extend(
            [
                f"### {index}. {video.title or video.aweme_id}",
                "",
                f"- 评论数：{video.comment_count}",
                f"- 点赞数：{video.like_count}",
                f"- 分享数：{video.share_count}",
                f"- 链接：{video.url}",
                "",
            ]
        )

    lines.extend(["## 二、评论痛点信号", ""])
    if not pain_signals:
        lines.extend(["暂无足够评论信号。", ""])
    for index, signal in enumerate(pain_signals, 1):
        lines.extend(
            [
                f"### {index}. {signal.pain_point}",
                "",
                f"- 证据数：{signal.evidence_count}",
                f"- 信号强度：{signal.signal_strength}",
                f"- 置信度：{signal.confidence}",
                "- 代表证据：",
            ]
        )
        for evidence in signal.evidence[:5]:
            lines.append(f"  - {evidence}")
        lines.append("")

    lines.extend(["## 三、角度验证评分", ""])
    for index, scorecard in enumerate(scorecards, 1):
        score_text = "，".join(f"{key}: {value}" for key, value in scorecard.scores.items())
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

    lines.extend(["## 四、可直接使用的选题包", ""])
    if not topic_packages:
        lines.extend(["没有生成可用选题包。", ""])
    for index, package in enumerate(topic_packages, 1):
        lines.extend(
            [
                f"### {index}. {package.brief_title}",
                "",
                f"- 适配分：{package.fit_score}",
                f"- 这条视频讲什么：{package.topic}",
                f"- 痛点：{package.pain_point}",
                f"- 目标用户：{package.target_audience}",
                f"- 开头建议：{package.opening_hook}",
                f"- 推荐角度：{package.recommended_angle}",
                f"- 需要补的证明：{package.proof_needed}",
                f"- CTA 方向：{package.cta_direction}",
                f"- 为什么值得拍：{package.why_worth_shooting or '来自评论、标题或账号内容中的真实信号。'}",
                "- 证据：",
            ]
        )
        for evidence in package.evidence[:6]:
            lines.append(f"  - {evidence}")
        lines.append("- 风险提醒：")
        for risk in package.risk_notes[:5]:
            lines.append(f"  - {risk}")
        lines.append("- 拍摄建议：")
        for suggestion in package.production_suggestions[:6]:
            lines.append(f"  - {suggestion}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_markdown_report(content: str, path: str | Path) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)
