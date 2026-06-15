from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .collector import collect_comments_for_videos, collect_profile_videos
from .io_utils import read_json, write_json
from .llm import LLMClient
from .packager import generate_topic_packages
from .reports import render_topic_packages_markdown, write_markdown_report
from .schemas import CommentItem, TopicPackageRun, VideoItem
from .signals import build_angle_candidates, build_pain_signals, validate_angles


def load_videos(path: str | Path) -> List[VideoItem]:
    data = read_json(path)
    return [VideoItem(**item) for item in data]


def load_comments(path: str | Path) -> List[CommentItem]:
    data = read_json(path)
    return [CommentItem(**item) for item in data]


async def collect_profile_step(
    profile_url: str,
    *,
    output_dir: str | Path = "outputs/topic_packages",
    top_n: int = 20,
    storage_state_path: str | Path = "runtime/douyin_storage_state.json",
) -> Dict[str, str]:
    resolved_url, sec_uid, videos = await collect_profile_videos(
        profile_url,
        top_n=top_n,
        storage_state_path=storage_state_path,
    )
    root = Path(output_dir)
    meta = {"source_url": profile_url, "resolved_url": resolved_url, "sec_uid": sec_uid, "top_n": top_n}
    return {
        "resolved_url": resolved_url,
        "sec_uid": sec_uid,
        "profile_meta": write_json(meta, root / "profile_meta.json"),
        "profile_videos": write_json([item.to_dict() for item in videos], root / "profile_videos.json"),
    }


async def collect_comments_step(
    videos_path: str | Path,
    *,
    output_dir: str | Path = "outputs/topic_packages",
    storage_state_path: str | Path = "runtime/douyin_storage_state.json",
    max_comments_per_video: int = 0,
) -> Dict[str, str]:
    videos = load_videos(videos_path)
    comments = await collect_comments_for_videos(
        videos,
        storage_state_path=storage_state_path,
        max_comments_per_video=max_comments_per_video,
    )
    return {"comments": write_json([item.to_dict() for item in comments], Path(output_dir) / "comments.json")}


def analyze_comments_step(
    *,
    source_url: str,
    resolved_url: str,
    sec_uid: str,
    videos_path: str | Path,
    comments_path: str | Path,
    output_dir: str | Path = "outputs/topic_packages",
    llm_client: LLMClient | None = None,
    conversion_mode: str = "balanced",
    min_fit_score: int = 0,
    package_limit: int = 0,
) -> Dict[str, str]:
    videos = load_videos(videos_path)
    comments = load_comments(comments_path)
    pain_signals = build_pain_signals(videos, comments)
    angle_candidates = build_angle_candidates(pain_signals)
    scorecards = validate_angles(angle_candidates, pain_signals)
    packages = generate_topic_packages(
        videos,
        pain_signals,
        angle_candidates,
        scorecards,
        llm_client=llm_client,
        conversion_mode=conversion_mode,
        min_fit_score=min_fit_score,
        package_limit=package_limit,
    )

    root = Path(output_dir)
    run = TopicPackageRun(
        source_url=source_url,
        resolved_url=resolved_url,
        sec_uid=sec_uid,
        videos=videos,
        comments=comments,
        pain_signals=pain_signals,
        angle_candidates=angle_candidates,
        validation_scorecards=scorecards,
        topic_packages=packages,
    )
    markdown = render_topic_packages_markdown(
        source_url=source_url,
        resolved_url=resolved_url,
        sec_uid=sec_uid,
        videos=videos,
        pain_signals=pain_signals,
        scorecards=scorecards,
        topic_packages=packages,
    )
    return {
        "pain_signals": write_json([item.to_dict() for item in pain_signals], root / "pain_signals.json"),
        "angle_candidates": write_json([item.to_dict() for item in angle_candidates], root / "angle_candidates.json"),
        "validation_scorecards": write_json([item.to_dict() for item in scorecards], root / "validation_scorecards.json"),
        "topic_packages": write_json([item.to_dict() for item in packages], root / "topic_packages.json"),
        "run": write_json(run.to_dict(), root / "run.json"),
        "markdown": write_markdown_report(markdown, root / "topic_packages.md"),
    }


async def run_topic_package_pipeline(
    *,
    profile_url: str,
    output_dir: str | Path = "outputs/topic_packages",
    top_n: int = 20,
    storage_state_path: str | Path = "runtime/douyin_storage_state.json",
    max_comments_per_video: int = 0,
    llm_client: LLMClient | None = None,
    conversion_mode: str = "balanced",
    min_fit_score: int = 0,
    package_limit: int = 0,
) -> Dict[str, str]:
    collected = await collect_profile_step(
        profile_url,
        output_dir=output_dir,
        top_n=top_n,
        storage_state_path=storage_state_path,
    )
    commented = await collect_comments_step(
        collected["profile_videos"],
        output_dir=output_dir,
        storage_state_path=storage_state_path,
        max_comments_per_video=max_comments_per_video,
    )
    analyzed = analyze_comments_step(
        source_url=profile_url,
        resolved_url=collected["resolved_url"],
        sec_uid=collected["sec_uid"],
        videos_path=collected["profile_videos"],
        comments_path=commented["comments"],
        output_dir=output_dir,
        llm_client=llm_client,
        conversion_mode=conversion_mode,
        min_fit_score=min_fit_score,
        package_limit=package_limit,
    )
    return {**collected, **commented, **analyzed}
