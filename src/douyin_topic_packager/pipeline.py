from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .collector import collect_comments_for_videos, collect_profile_videos
from .feedback import calibrate_topic_packages, load_performance_feedback
from .io_utils import read_json, write_json
from .llm import LLMClient
from .packager import generate_topic_packages
from .quality import evaluate_topic_run
from .reports import render_topic_packages_markdown, write_markdown_report
from .schemas import CommentItem, PainSignal, TopicPackageRun, VideoItem
from .signals import build_angle_candidates, build_pain_signals, validate_angles


PROFILE_ARTIFACT_SCHEMA_VERSION = 2
COMMENT_CHECKPOINT_SCHEMA_VERSION = 1
RUN_MANIFEST_SCHEMA_VERSION = 1
REQUIRED_COMPLETED_FILES = (
    "pain_signals",
    "topic_packages",
    "quality",
    "analysis_metadata",
    "markdown",
)
COMMENT_RESUME_PARAMETER_KEYS = (
    "profile_url",
    "top_n",
    "max_comments_per_video",
    "include_replies",
    "target_valid_comments",
    "max_comment_pages",
    "saturation_pages",
    "saturation_min_new_ratio",
    "redact_user_data",
)


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
    profile_url: str,
    top_n: int,
    max_comments_per_video: int,
    conversion_mode: str,
    min_fit_score: int,
    package_limit: int,
    min_evidence_count: int,
    scan_pages: int,
    include_replies: bool,
    comment_concurrency: int,
    target_valid_comments: int,
    max_comment_pages: int,
    saturation_pages: int,
    saturation_min_new_ratio: float,
    redact_user_data: bool,
    performance_feedback_path: str,
) -> Dict[str, Any]:
    return {
        "profile_url": str(profile_url or "").strip(),
        "top_n": int(top_n or 0),
        "max_comments_per_video": int(max_comments_per_video or 0),
        "conversion_mode": conversion_mode,
        "min_fit_score": int(min_fit_score or 0),
        "package_limit": int(package_limit or 0),
        "min_evidence_count": int(min_evidence_count or 0),
        "scan_pages": int(scan_pages or 0),
        "include_replies": bool(include_replies),
        "comment_concurrency": int(comment_concurrency or 0),
        "target_valid_comments": int(target_valid_comments or 0),
        "max_comment_pages": int(max_comment_pages or 0),
        "saturation_pages": int(saturation_pages or 0),
        "saturation_min_new_ratio": float(saturation_min_new_ratio),
        "redact_user_data": bool(redact_user_data),
        "performance_feedback_path": performance_feedback_path,
    }


def _parameter_hash(parameters: Dict[str, Any]) -> str:
    payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _value_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _file_hash(path: str | Path) -> str:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _resolve_manifest_file(manifest_path: Path, recorded_path: str | Path) -> Path:
    recorded = Path(recorded_path)
    candidates = [recorded] if recorded.is_absolute() else [
        recorded,
        manifest_path.parent / recorded,
        manifest_path.parent / recorded.name,
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def verify_run_manifest(
    path: str | Path,
    *,
    require_quality_pass: bool = True,
) -> Dict[str, Any]:
    target = Path(path)
    if target.is_dir():
        target = target / "run_manifest.json"
    errors: List[str] = []
    warnings: List[str] = []
    verified_artifacts: List[str] = []
    try:
        manifest = read_json(target)
    except (OSError, ValueError, TypeError) as exc:
        return {
            "passed": False,
            "manifest": str(target),
            "schema_version": None,
            "quality_gate_passed": False,
            "verified_artifacts": [],
            "warnings": [],
            "errors": [f"无法读取运行清单：{exc}"],
        }
    if not isinstance(manifest, dict):
        errors.append("运行清单必须是 JSON 对象")
        manifest = {}
    schema_version = manifest.get("artifact_schema_version")
    if schema_version is None:
        warnings.append("运行清单来自旧版本，未声明 artifact_schema_version")
    elif schema_version != RUN_MANIFEST_SCHEMA_VERSION:
        errors.append(
            f"运行清单版本不兼容：期望 {RUN_MANIFEST_SCHEMA_VERSION}，实际 {schema_version!r}"
        )
    files = manifest.get("files")
    provenance = manifest.get("provenance")
    if not isinstance(files, dict):
        errors.append("运行清单缺少 files")
        files = {}
    if not isinstance(provenance, dict):
        errors.append("运行清单缺少 provenance")
        provenance = {}
    file_hashes = provenance.get("file_hashes")
    if not isinstance(file_hashes, dict):
        errors.append("运行清单缺少 provenance.file_hashes")
        file_hashes = {}
    for name in REQUIRED_COMPLETED_FILES:
        if not files.get(name):
            errors.append(f"缺少核心产物路径：{name}")
        if not file_hashes.get(name):
            errors.append(f"缺少核心产物哈希：{name}")
    for name, expected_hash in file_hashes.items():
        if not expected_hash:
            continue
        recorded_path = files.get(name)
        if not recorded_path:
            errors.append(f"哈希没有对应产物路径：{name}")
            continue
        if not isinstance(recorded_path, (str, Path)):
            errors.append(f"产物路径格式无效：{name}")
            continue
        artifact_path = _resolve_manifest_file(target, recorded_path)
        actual_hash = _file_hash(artifact_path)
        if not actual_hash:
            errors.append(f"产物不存在：{artifact_path.name}")
        elif actual_hash != expected_hash:
            errors.append(f"产物哈希不匹配：{artifact_path.name}")
        else:
            verified_artifacts.append(name)
    quality_gate_passed = False
    quality_recorded_path = files.get("quality")
    if isinstance(quality_recorded_path, (str, Path)):
        quality_path = _resolve_manifest_file(target, quality_recorded_path)
        try:
            quality_result = read_json(quality_path)
            quality_gate_passed = bool(quality_result.get("passed"))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append(f"无法读取质量报告：{exc}")
    elif quality_recorded_path:
        errors.append("质量报告路径格式无效")
    if require_quality_pass and not quality_gate_passed:
        errors.append("质量门禁未通过")
    return {
        "passed": not errors,
        "manifest": str(target),
        "schema_version": schema_version,
        "quality_gate_passed": quality_gate_passed,
        "verified_artifacts": sorted(verified_artifacts),
        "warnings": warnings,
        "errors": errors,
    }


def _profile_resume_matches(
    meta: Dict[str, Any],
    profile_url: str,
    top_n: int,
    scan_pages: int,
    videos_path: str | Path,
) -> bool:
    if not meta:
        return False
    expected_hash = str(meta.get("profile_videos_hash") or "")
    source_matches = str(meta.get("source_url") or "").strip() == str(profile_url or "").strip()
    return (
        int(meta.get("artifact_schema_version") or 0) == PROFILE_ARTIFACT_SCHEMA_VERSION
        and source_matches
        and bool(str(meta.get("resolved_url") or "").strip() or str(meta.get("sec_uid") or "").strip())
        and int(meta.get("top_n") or 0) == int(top_n or 0)
        and int(meta.get("scan_pages") or 10) == int(scan_pages or 0)
        and bool(expected_hash)
        and expected_hash == _file_hash(videos_path)
    )


def _comments_resume_matches(root: Path, parameters: Dict[str, Any]) -> bool:
    manifest_path = root / "run_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(manifest, dict):
        return False
    previous = manifest.get("parameters") or {}
    if any(previous.get(key) != parameters.get(key) for key in COMMENT_RESUME_PARAMETER_KEYS):
        return False
    file_hashes = (manifest.get("provenance") or {}).get("file_hashes") or {}
    comments_path = root / "comments.json"
    status_path = root / "comments_status.json"
    return (
        bool(file_hashes.get("comments"))
        and bool(file_hashes.get("comments_status"))
        and file_hashes["comments"] == _file_hash(comments_path)
        and file_hashes["comments_status"] == _file_hash(status_path)
    )


def _comment_resume_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {key: parameters.get(key) for key in COMMENT_RESUME_PARAMETER_KEYS}


def _restore_comment_checkpoint(
    root: Path,
    parameters: Dict[str, Any],
    videos_path: str | Path,
) -> bool:
    checkpoint_path = root / "comments_checkpoint.json"
    if not checkpoint_path.exists():
        return False
    try:
        checkpoint = read_json(checkpoint_path)
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(checkpoint, dict):
        return False
    videos = load_videos(videos_path)
    expected_video_ids = [item.aweme_id for item in videos]
    expected_parameters = _comment_resume_parameters(parameters)
    comments = checkpoint.get("comments")
    statuses = checkpoint.get("statuses")
    if (
        int(checkpoint.get("artifact_schema_version") or 0) != COMMENT_CHECKPOINT_SCHEMA_VERSION
        or checkpoint.get("parameters") != expected_parameters
        or checkpoint.get("parameter_hash") != _parameter_hash(expected_parameters)
        or checkpoint.get("profile_videos_hash") != _file_hash(videos_path)
        or checkpoint.get("video_ids") != expected_video_ids
        or not isinstance(comments, list)
        or not isinstance(statuses, dict)
        or checkpoint.get("comments_hash") != _value_hash(comments)
        or checkpoint.get("statuses_hash") != _value_hash(statuses)
    ):
        return False
    valid_video_ids = set(expected_video_ids)
    if any(
        not isinstance(item, dict) or str(item.get("aweme_id") or "") not in valid_video_ids
        for item in comments
    ):
        return False
    if any(str(video_id) not in valid_video_ids for video_id in statuses):
        return False
    write_json(comments, root / "comments.json")
    write_json(statuses, root / "comments_status.json")
    return True


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
    restored_comment_checkpoint: bool = False,
    runtime_metadata: Dict[str, Any] | None = None,
) -> str:
    target = Path(output_dir) / "run_manifest.json"
    payload = {
        "artifact_schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": parameters,
        "parameter_hash": _parameter_hash(parameters),
        "resume": {
            "requested": bool(resume),
            "reused_profile": bool(reused_profile),
            "reused_comments": bool(reused_comments),
            "retried_comment_videos": int(retried_comment_videos or 0),
            "restored_comment_checkpoint": bool(restored_comment_checkpoint),
        },
        "counts": counts,
        "files": files,
        "provenance": {
            "file_hashes": {key: _file_hash(path) for key, path in files.items() if isinstance(path, (str, Path))},
            **(runtime_metadata or {}),
        },
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
    videos_path = write_json([item.to_dict() for item in videos], root / "profile_videos.json")
    meta = {
        "artifact_schema_version": PROFILE_ARTIFACT_SCHEMA_VERSION,
        "source_url": profile_url,
        "resolved_url": resolved_url,
        "sec_uid": sec_uid,
        "top_n": top_n,
        "scan_pages": scan_pages,
        "profile_videos_hash": _file_hash(videos_path),
    }
    return {
        "resolved_url": resolved_url,
        "sec_uid": sec_uid,
        "profile_meta": write_json(meta, root / "profile_meta.json"),
        "profile_videos": videos_path,
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
    target_valid_comments: int = 0,
    max_comment_pages: int = 0,
    saturation_pages: int = 3,
    saturation_min_new_ratio: float = 0.08,
    redact_user_data: bool = True,
    checkpoint_parameters: Dict[str, Any] | None = None,
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
    checkpoint_target = root / "comments_checkpoint.json"

    def checkpoint(partial: List[CommentItem], statuses: Dict[str, Dict[str, Any]]) -> None:
        merged_comments = [*kept_comments, *partial]
        merged_status = {**existing_status, **statuses}
        resume_parameters = _comment_resume_parameters(checkpoint_parameters or {})
        checkpoint_payload = {
            "artifact_schema_version": COMMENT_CHECKPOINT_SCHEMA_VERSION,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "parameters": resume_parameters,
            "parameter_hash": _parameter_hash(resume_parameters),
            "profile_videos_hash": _file_hash(videos_path),
            "video_ids": [item.aweme_id for item in all_videos],
            "comments": [item.to_dict() for item in merged_comments],
            "statuses": merged_status,
        }
        checkpoint_payload["comments_hash"] = _value_hash(checkpoint_payload["comments"])
        checkpoint_payload["statuses_hash"] = _value_hash(checkpoint_payload["statuses"])
        write_json(checkpoint_payload, checkpoint_target)
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
        target_valid_comments=target_valid_comments,
        max_comment_pages=max_comment_pages,
        saturation_pages=saturation_pages,
        saturation_min_new_ratio=saturation_min_new_ratio,
        redact_user_data=redact_user_data,
    )
    comments, statuses = collected
    checkpoint(comments, statuses)
    return {
        "comments": str(comments_target),
        "comments_status": str(status_target),
        "comments_checkpoint": str(checkpoint_target),
    }


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
    performance_feedback_path: str | Path | None = None,
) -> Dict[str, str]:
    root = Path(output_dir)
    videos = load_videos(videos_path)
    comments = load_comments(comments_path)
    pain_signals = filter_pain_signals(build_pain_signals(videos, comments), min_evidence_count=min_evidence_count)
    angle_candidates = build_angle_candidates(pain_signals)
    scorecards = validate_angles(angle_candidates, pain_signals)
    cache_stats: Dict[str, int] = {}
    packages = generate_topic_packages(
        videos,
        pain_signals,
        angle_candidates,
        scorecards,
        llm_client=llm_client,
        conversion_mode=conversion_mode,
        min_fit_score=min_fit_score,
        package_limit=package_limit,
        cache_dir=root / ".analysis_cache",
        cache_stats=cache_stats,
    )
    packages = calibrate_topic_packages(packages, load_performance_feedback(performance_feedback_path))

    required_generator = "llm" if llm_client is not None else ""
    quality_result = evaluate_topic_run(
        pain_signals=[item.to_dict() for item in pain_signals],
        topic_packages=[item.to_dict() for item in packages],
        required_generator=required_generator,
    )
    analysis_metadata = {
        "required_generator": required_generator,
        "generator_counts": quality_result["metrics"]["generator_counts"],
        "cache": cache_stats,
        "quality_gate_passed": quality_result["passed"],
        "failed_quality_checks": quality_result["failed_checks"],
    }
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
        quality_result=quality_result,
        analysis_metadata=analysis_metadata,
    )
    return {
        "pain_signals": write_json([item.to_dict() for item in pain_signals], root / "pain_signals.json"),
        "angle_candidates": write_json([item.to_dict() for item in angle_candidates], root / "angle_candidates.json"),
        "validation_scorecards": write_json([item.to_dict() for item in scorecards], root / "validation_scorecards.json"),
        "topic_packages": write_json([item.to_dict() for item in packages], root / "topic_packages.json"),
        "run": write_json(run.to_dict(), root / "run.json"),
        "quality": write_json(quality_result, root / "quality_report.json"),
        "analysis_metadata": write_json(analysis_metadata, root / "analysis_metadata.json"),
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
    target_valid_comments: int = 0,
    max_comment_pages: int = 0,
    saturation_pages: int = 3,
    saturation_min_new_ratio: float = 0.08,
    redact_user_data: bool = True,
    performance_feedback_path: str = "",
) -> Dict[str, str]:
    started = time.perf_counter()
    root = Path(output_dir)
    meta_path = root / "profile_meta.json"
    videos_path = root / "profile_videos.json"
    comments_path = root / "comments.json"
    comments_status_path = root / "comments_status.json"
    parameters = _run_parameters(
        profile_url=profile_url,
        top_n=top_n,
        max_comments_per_video=max_comments_per_video,
        conversion_mode=conversion_mode,
        min_fit_score=min_fit_score,
        package_limit=package_limit,
        min_evidence_count=min_evidence_count,
        scan_pages=scan_pages,
        include_replies=include_replies,
        comment_concurrency=comment_concurrency,
        target_valid_comments=target_valid_comments,
        max_comment_pages=max_comment_pages,
        saturation_pages=saturation_pages,
        saturation_min_new_ratio=saturation_min_new_ratio,
        redact_user_data=redact_user_data,
        performance_feedback_path=performance_feedback_path,
    )
    adaptive_comment_options: Dict[str, Any] = {}
    if target_valid_comments:
        adaptive_comment_options["target_valid_comments"] = target_valid_comments
    if max_comment_pages:
        adaptive_comment_options["max_comment_pages"] = max_comment_pages
    if saturation_pages != 3:
        adaptive_comment_options["saturation_pages"] = saturation_pages
    if saturation_min_new_ratio != 0.08:
        adaptive_comment_options["saturation_min_new_ratio"] = saturation_min_new_ratio
    if not redact_user_data:
        adaptive_comment_options["redact_user_data"] = False
    try:
        loaded_meta = read_json(meta_path) if meta_path.exists() else {}
    except (OSError, ValueError, TypeError):
        loaded_meta = {}
    meta = loaded_meta if isinstance(loaded_meta, dict) else {}
    reused_profile = False
    reused_comments = False
    retried_comment_videos = 0
    restored_comment_checkpoint = False
    if resume and videos_path.exists() and _profile_resume_matches(
        meta,
        profile_url,
        top_n,
        scan_pages,
        videos_path,
    ):
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
    comments_resume_allowed = False
    if resume and reused_profile:
        comments_resume_allowed = _comments_resume_matches(root, parameters)
        if not comments_resume_allowed:
            restored_comment_checkpoint = _restore_comment_checkpoint(
                root,
                parameters,
                collected["profile_videos"],
            )
            comments_resume_allowed = restored_comment_checkpoint
    if resume and reused_profile and comments_path.exists() and comments_resume_allowed:
        if comments_status_path.exists():
            try:
                loaded_status = read_json(comments_status_path)
            except (OSError, ValueError, TypeError):
                loaded_status = {}
            previous_status = loaded_status if isinstance(loaded_status, dict) else {}
            pending_ids = {
                video.aweme_id
                for video in load_videos(collected["profile_videos"])
                if (previous_status.get(video.aweme_id) or {}).get("status") != "success"
            }
        else:
            pending_ids = {video.aweme_id for video in load_videos(collected["profile_videos"])}
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
                checkpoint_parameters=parameters,
                **adaptive_comment_options,
            )
        else:
            commented = {
                "comments": str(comments_path),
                **({"comments_status": str(comments_status_path)} if comments_status_path.exists() else {}),
                **(
                    {"comments_checkpoint": str(root / "comments_checkpoint.json")}
                    if (root / "comments_checkpoint.json").exists()
                    else {}
                ),
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
            checkpoint_parameters=parameters,
            **adaptive_comment_options,
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
        performance_feedback_path=performance_feedback_path,
    )
    manifest = write_run_manifest(
        output_dir=output_dir,
        parameters=parameters,
        files={**collected, **commented, **analyzed},
        counts={
            "videos": len(load_videos(collected["profile_videos"])),
            "comments": len(load_comments(commented["comments"])),
            "valid_comments": sum(
                int(item.get("valid_comment_count") or 0)
                for item in (
                    read_json(commented["comments_status"]).values()
                    if commented.get("comments_status") and Path(commented["comments_status"]).exists()
                    else []
                )
            ),
            "saturated_comment_videos": len(
                [
                    item
                    for item in (
                        read_json(commented["comments_status"]).values()
                        if commented.get("comments_status") and Path(commented["comments_status"]).exists()
                        else []
                    )
                    if item.get("stop_reason") == "signal_saturation"
                ]
            ),
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
        restored_comment_checkpoint=restored_comment_checkpoint,
        runtime_metadata={
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "llm_provider": llm_client.config.normalized_provider if llm_client else "",
            "llm_model": llm_client.config.model if llm_client else "",
            "llm_metrics": (
                llm_client.snapshot_metrics()
                if llm_client is not None and callable(getattr(llm_client, "snapshot_metrics", None))
                else {}
            ),
            "analysis": read_json(analyzed["analysis_metadata"]),
        },
    )
    return {**collected, **commented, **analyzed, "run_manifest": manifest}
