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
from .schemas import CommentItem, TopicPackageRun, VideoItem
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
) -> Dict[str, Any]:
    return {
        "top_n": int(top_n or 0),
        "max_comments_per_video": int(max_comments_per_video or 0),
        "conversion_mode": conversion_mode,
        "min_fit_score": int(min_fit_score or 0),
        "package_limit": int(package_limit or 0),
        "min_evidence_count": int(min_evidence_count or 0),
    }


def _parameter_hash(parameters: Dict[str, Any]) -> str:
    payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _profile_resume_matches(meta: Dict[str, Any], top_n: int) -> bool:
    if not meta:
        return True
    return int(meta.get("top_n") or 0) == int(top_n or 0)


def _comments_resume_matches(root: Path, parameters: Dict[str, Any]) -> bool:
    manifest_path = root / "run_manifest.json"
    if not manifest_path.exists():
        return int(parameters.get("max_comments_per_video") or 0) == 0
    manifest = read_json(manifest_path)
    previous = manifest.get("parameters") or {}
    return (
        int(previous.get("top_n") or 0) == int(parameters.get("top_n") or 0)
        and int(previous.get("max_comments_per_video") or 0) == int(parameters.get("max_comments_per_video") or 0)
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
) -> Dict[str, str]:
    root = Path(output_dir)
    meta_path = root / "profile_meta.json"
    videos_path = root / "profile_videos.json"
    comments_path = root / "comments.json"
    parameters = _run_parameters(
        top_n=top_n,
        max_comments_per_video=max_comments_per_video,
        conversion_mode=conversion_mode,
        min_fit_score=min_fit_score,
        package_limit=package_limit,
        min_evidence_count=min_evidence_count,
    )
    meta = read_json(meta_path) if meta_path.exists() else {}
    reused_profile = False
    reused_comments = False
    if resume and videos_path.exists() and _profile_resume_matches(meta, top_n):
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
        )
    if resume and reused_profile and comments_path.exists() and _comments_resume_matches(root, parameters):
        commented = {"comments": str(comments_path)}
        reused_comments = True
    else:
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
        },
        resume=resume,
        reused_profile=reused_profile,
        reused_comments=reused_comments,
    )
    return {**collected, **commented, **analyzed, "run_manifest": manifest}
