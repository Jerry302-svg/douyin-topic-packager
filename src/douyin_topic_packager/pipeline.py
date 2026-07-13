from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .collector import collect_comments_for_videos, collect_profile_videos
from .io_utils import read_json, write_json
from .llm import LLMClient
from .packager import generate_topic_packages
from .reports import render_topic_packages_markdown, write_markdown_report
from .schemas import CommentItem, PainSignal, TopicPackageRun, VideoItem
from .signals import build_angle_candidates, build_pain_signals, validate_angles


def load_videos(path: str | Path) -> List[VideoItem]:
    data = read_json(path)
    return [VideoItem(**item) for item in data]


def load_comments(path: str | Path) -> List[CommentItem]:
    data = read_json(path)
    return [CommentItem(**item) for item in data]


def filter_pain_signals(pain_signals: List[PainSignal], min_evidence_count: int = 0) -> List[PainSignal]:
    min_count = max(0, int(min_evidence_count or 0))
    if not min_count:
        return pain_signals
    return [item for item in pain_signals if int(item.evidence_count or 0) >= min_count]


def _run_parameters(
    *,
    top_n: int,
    max_comments_per_video: int,
    conversion_mode: str,
    min_fit_score: int,
    package_limit: int,
    min_evidence_count: int,
    scan_pages: int,
    include_replies: bool,
    comment_concurrency: int,
) -> Dict[str, Any]:
    return {
        "top_n": int(top_n or 0),
        "max_comments_per_video": int(max_comments_per_video or 0),
        "conversion_mode": conversion_mode,
        "min_fit_score": int(min_fit_score or 0),
        "package_limit": int(package_limit or 0),
        "min_evidence_count": int(min_evidence_count or 0),
        "scan_pages": int(scan_pages or 0),
        "include_replies": bool(include_replies),
        "comment_concurrency": int(comment_concurrency or 0),
    }


def _parameter_hash(parameters: Dict[str, Any]) -> str:
    payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _profile_resume_matches(meta: Dict[str, Any], top_n: int, scan_pages: int) -> bool:
    if not meta:
        return True
    return (
        int(meta.get("top_n") or 0) == int(top_n or 0)
        and int(meta.get("scan_pages") or 10) == int(scan_pages or 0)
    )


def _comments_resume_matches(root: Path, parameters: Dict[str, Any]) -> bool:
    manifest_path = root / "run_manifest.json"
    if not manifest_path.exists():
        return int(parameters.get("max_comments_per_video") or 0) == 0
    manifest = read_json(manifest_path)
    previous = manifest.get("parameters") or {}
    return (
        int(previous.get("top_n") or 0) == int(parameters.get("top_n") or 0)
        and int(previous.get("max_comments_per_video") or 0) == int(parameters.get("max_comments_per_video") or 0)
        and bool(previous.get("include_replies")) == bool(parameters.get("include_replies"))
    )


def write_run_manifest(
    *,
    output_dir: str | Path,
    parameters: Dict[str, Any],
    files: Dict[str, str],
    counts: Dict[str, int],
    resume: bool,
    reused_profile: bool,
    reused_comments: bool,
    retried_comment_videos: int = 0,
) -> str:
    target = Path(output_dir) / "run_manifest.json"
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": parameters,
        "parameter_hash": _parameter_hash(parameters),
        "resume": {
            "requested": bool(resume),
            "reused_profile": bool(reused_profile),
            "reused_comments": bool(reused_comments),
            "retried_comment_videos": int(retried_comment_videos or 0),
        },
        "counts": counts,
        "files": files,
    }
    return write_json(payload, target)


async def collect_profile_step(
    profile_url: str,
    *,
    output_dir: str | Path = "outputs/topic_packages",
    top_n: int = 20,
    storage_state_path: str | Path = "runtime/douyin_storage_state.json",
    scan_pages: int = 10,
) -> Dict[str, str]:
    resolved_url, sec_uid, videos = await collect_profile_videos(
        profile_url,
        top_n=top_n,
        storage_state_path=storage_state_path,
        scan_pages=scan_pages,
    )
    if not videos:
        raise RuntimeError("未采集到视频，请检查 Cookie 是否过期、主页链接是否正确，或稍后重试")
    root = Path(output_dir)
    meta = {
        "source_url": profile_url,
        "resolved_url": resolved_url,
        "sec_uid": sec_uid,
        "top_n": top_n,
        "scan_pages": scan_pages,
    }
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
    include_replies: bool = False,
    max_concurrency: int = 2,
    existing_comments_path: str | Path | None = None,
    existing_status_path: str | Path | None = None,
    only_video_ids: set[str] | None = None,
) -> Dict[str, str]:
    all_videos = load_videos(videos_path)
    videos = [item for item in all_videos if not only_video_ids or item.aweme_id in only_video_ids]
    existing_comments = load_comments(existing_comments_path) if existing_comments_path and Path(existing_comments_path).exists() else []
    existing_status = read_json(existing_status_path) if existing_status_path and Path(existing_status_path).exists() else {}
    replaced_ids = {item.aweme_id for item in videos}
    kept_comments = [item for item in existing_comments if item.aweme_id not in replaced_ids]
    root = Path(output_dir)
    comments_target = root / "comments.json"
    status_target = root / "comments_status.json"

    def checkpoint(partial: List[CommentItem], statuses: Dict[str, Dict[str, Any]]) -> None:
        merged_comments = [*kept_comments, *partial]
        merged_status = {**existing_status, **statuses}
        write_json([item.to_dict() for item in merged_comments], comments_target)
        write_json(merged_status, status_target)

    collected = await collect_comments_for_videos(
        videos,
        storage_state_path=storage_state_path,
        max_comments_per_video=max_comments_per_video,
        include_replies=include_replies,
        max_concurrency=max_concurrency,
        progress_callback=checkpoint,
        return_status=True,
    )
    comments, statuses = collected
    checkpoint(comments, statuses)
    return {"comments": str(comments_target), "comments_status": str(status_target)}


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
    min_evidence_count: int = 0,
) -> Dict[str, str]:
    videos = load_videos(videos_path)
    comments = load_comments(comments_path)
    pain_signals = filter_pain_signals(build_pain_signals(videos, comments), min_evidence_count=min_evidence_count)
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
        min_evidence_count=min_evidence_count,
        min_fit_score=min_fit_score,
        package_limit=package_limit,
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
    min_evidence_count: int = 0,
    resume: bool = False,
    scan_pages: int = 10,
    include_replies: bool = False,
    comment_concurrency: int = 2,
) -> Dict[str, str]:
    root = Path(output_dir)
    meta_path = root / "profile_meta.json"
    videos_path = root / "profile_videos.json"
    comments_path = root / "comments.json"
    comments_status_path = root / "comments_status.json"
    parameters = _run_parameters(
        top_n=top_n,
        max_comments_per_video=max_comments_per_video,
        conversion_mode=conversion_mode,
        min_fit_score=min_fit_score,
        package_limit=package_limit,
        min_evidence_count=min_evidence_count,
        scan_pages=scan_pages,
        include_replies=include_replies,
        comment_concurrency=comment_concurrency,
    )
    meta = read_json(meta_path) if meta_path.exists() else {}
    reused_profile = False
    reused_comments = False
    retried_comment_videos = 0
    if resume and videos_path.exists() and _profile_resume_matches(meta, top_n, scan_pages):
        collected = {
            "resolved_url": meta.get("resolved_url", ""),
            "sec_uid": meta.get("sec_uid", ""),
            "profile_meta": str(meta_path),
            "profile_videos": str(videos_path),
        }
        reused_profile = True
    else:
        collected = await collect_profile_step(
            profile_url,
            output_dir=output_dir,
            top_n=top_n,
            storage_state_path=storage_state_path,
            scan_pages=scan_pages,
        )
    if not load_videos(collected["profile_videos"]):
        raise RuntimeError("未采集到视频，请检查 Cookie 是否过期、主页链接是否正确，或稍后重试")
    if resume and reused_profile and comments_path.exists() and _comments_resume_matches(root, parameters):
        if comments_status_path.exists():
            previous_status = read_json(comments_status_path)
            pending_ids = {
                video.aweme_id
                for video in load_videos(collected["profile_videos"])
                if (previous_status.get(video.aweme_id) or {}).get("status") != "success"
            }
        else:
            pending_ids = set()
        if pending_ids:
            retried_comment_videos = len(pending_ids)
            commented = await collect_comments_step(
                collected["profile_videos"],
                output_dir=output_dir,
                storage_state_path=storage_state_path,
                max_comments_per_video=max_comments_per_video,
                include_replies=include_replies,
                max_concurrency=comment_concurrency,
                existing_comments_path=comments_path,
                existing_status_path=comments_status_path,
                only_video_ids=pending_ids,
            )
        else:
            commented = {
                "comments": str(comments_path),
                **({"comments_status": str(comments_status_path)} if comments_status_path.exists() else {}),
            }
            reused_comments = True
    else:
        commented = await collect_comments_step(
            collected["profile_videos"],
            output_dir=output_dir,
            storage_state_path=storage_state_path,
            max_comments_per_video=max_comments_per_video,
            include_replies=include_replies,
            max_concurrency=comment_concurrency,
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
        min_evidence_count=min_evidence_count,
    )
    manifest = write_run_manifest(
        output_dir=output_dir,
        parameters=parameters,
        files={**collected, **commented, **analyzed},
        counts={
            "videos": len(load_videos(collected["profile_videos"])),
            "comments": len(load_comments(commented["comments"])),
            "pain_signals": len(read_json(analyzed["pain_signals"])),
            "topic_packages": len(read_json(analyzed["topic_packages"])),
            "failed_comment_videos": len(
                [
                    item
                    for item in (
                        read_json(commented["comments_status"]).values()
                        if commented.get("comments_status") and Path(commented["comments_status"]).exists()
                        else []
                    )
                    if item.get("status") != "success"
                ]
            ),
        },
        resume=resume,
        reused_profile=reused_profile,
        reused_comments=reused_comments,
        retried_comment_videos=retried_comment_videos,
    )
    return {**collected, **commented, **analyzed, "run_manifest": manifest}
