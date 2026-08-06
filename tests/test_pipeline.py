import asyncio
import json
from pathlib import Path

import pytest

from douyin_topic_packager import pipeline


def test_run_topic_package_pipeline_resume_reuses_existing_files_and_filters_evidence(tmp_path, monkeypatch):
    root = Path(tmp_path)
    videos_path = root / "profile_videos.json"
    videos_path.write_text(
        json.dumps(
            [
                {
                    "aweme_id": "100",
                    "title": "很多人卡在第一步，不知道该怎么判断",
                    "comment_count": 20,
                    "like_count": 100,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "profile_meta.json").write_text(
        json.dumps(
            {
                "artifact_schema_version": pipeline.PROFILE_ARTIFACT_SCHEMA_VERSION,
                "source_url": "https://v.douyin.com/example/",
                "resolved_url": "https://www.douyin.com/user/test",
                "sec_uid": "test_sec_uid",
                "top_n": 2,
                "scan_pages": 10,
                "profile_videos_hash": pipeline._file_hash(videos_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    comments_path = root / "comments.json"
    comments_path.write_text(
        json.dumps(
            [
                {"aweme_id": "100", "text": "我不知道第一步怎么做，有没有简单办法？", "like_count": 8},
                {"aweme_id": "100", "text": "我不知道第一步怎么做，有没有简单办法？", "like_count": 5},
                {"aweme_id": "100", "text": "单条证据应该被过滤", "like_count": 1},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    comments_status_path = root / "comments_status.json"
    comments_status_path.write_text(
        json.dumps({"100": {"status": "success", "comment_count": 3}}),
        encoding="utf-8",
    )
    (root / "run_manifest.json").write_text(
        json.dumps(
            {
                "parameters": {
                    "profile_url": "https://v.douyin.com/example/",
                    "top_n": 2,
                    "max_comments_per_video": 0,
                    "include_replies": False,
                    "target_valid_comments": 0,
                    "max_comment_pages": 0,
                    "saturation_pages": 3,
                    "saturation_min_new_ratio": 0.08,
                    "redact_user_data": True,
                },
                "provenance": {
                    "file_hashes": {
                        "comments": pipeline._file_hash(comments_path),
                        "comments_status": pipeline._file_hash(comments_status_path),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    async def forbidden_collect(*args, **kwargs):
        raise AssertionError("resume should reuse existing profile files")

    async def forbidden_comments(*args, **kwargs):
        raise AssertionError("resume should reuse existing comments file")

    monkeypatch.setattr(pipeline, "collect_profile_step", forbidden_collect)
    monkeypatch.setattr(pipeline, "collect_comments_step", forbidden_comments)

    outputs = asyncio.run(
        pipeline.run_topic_package_pipeline(
            profile_url="https://v.douyin.com/example/",
            output_dir=root,
            top_n=2,
            resume=True,
            min_evidence_count=2,
            min_fit_score=0,
            package_limit=3,
        )
    )

    pain_signals = json.loads(Path(outputs["pain_signals"]).read_text(encoding="utf-8"))
    report = Path(outputs["markdown"]).read_text(encoding="utf-8")

    assert pain_signals
    assert all(item["evidence_count"] >= 2 for item in pain_signals)
    assert "## 运行摘要" in report
    assert "最小证据数：2" in report


def test_run_topic_package_pipeline_resume_recollects_when_parameters_change(tmp_path, monkeypatch):
    root = Path(tmp_path)
    (root / "profile_meta.json").write_text(
        json.dumps(
            {
                "source_url": "https://v.douyin.com/example/",
                "resolved_url": "https://www.douyin.com/user/old",
                "sec_uid": "old_sec_uid",
                "top_n": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "profile_videos.json").write_text(
        json.dumps(
            [{"aweme_id": "old", "title": "旧样本", "comment_count": 1}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "comments.json").write_text(
        json.dumps([{"aweme_id": "old", "text": "旧评论"}], ensure_ascii=False),
        encoding="utf-8",
    )
    calls = {"collect": 0, "comments": 0}

    async def fake_collect(profile_url, *, output_dir, top_n, storage_state_path, scan_pages):
        calls["collect"] += 1
        assert top_n == 5
        (root / "profile_meta.json").write_text(
            json.dumps(
                {
                    "source_url": profile_url,
                    "resolved_url": "https://www.douyin.com/user/new",
                    "sec_uid": "new_sec_uid",
                    "top_n": top_n,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "profile_videos.json").write_text(
            json.dumps(
                [{"aweme_id": "new", "title": "新样本", "comment_count": 3}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {
            "resolved_url": "https://www.douyin.com/user/new",
            "sec_uid": "new_sec_uid",
            "profile_meta": str(root / "profile_meta.json"),
            "profile_videos": str(root / "profile_videos.json"),
        }

    async def fake_comments(
        videos_path,
        *,
        output_dir,
        storage_state_path,
        max_comments_per_video,
        include_replies,
        max_concurrency,
        checkpoint_parameters,
    ):
        calls["comments"] += 1
        assert max_comments_per_video == 9
        assert checkpoint_parameters["top_n"] == 5
        (root / "comments.json").write_text(
            json.dumps(
                [
                    {"aweme_id": "new", "text": "不知道第一步怎么做？", "like_count": 8},
                    {"aweme_id": "new", "text": "第一步怕做错怎么办", "like_count": 6},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {"comments": str(root / "comments.json")}

    monkeypatch.setattr(pipeline, "collect_profile_step", fake_collect)
    monkeypatch.setattr(pipeline, "collect_comments_step", fake_comments)

    outputs = asyncio.run(
        pipeline.run_topic_package_pipeline(
            profile_url="https://v.douyin.com/example/",
            output_dir=root,
            top_n=5,
            max_comments_per_video=9,
            resume=True,
        )
    )

    run_manifest = json.loads(Path(outputs["run_manifest"]).read_text(encoding="utf-8"))

    assert calls == {"collect": 1, "comments": 1}
    assert outputs["sec_uid"] == "new_sec_uid"
    assert run_manifest["parameters"]["top_n"] == 5
    assert run_manifest["parameters"]["max_comments_per_video"] == 9


def test_run_topic_package_pipeline_fails_when_profile_collection_is_empty(tmp_path, monkeypatch):
    root = Path(tmp_path)

    async def fake_collect(profile_url, *, output_dir, top_n, storage_state_path, scan_pages):
        (root / "profile_meta.json").write_text(
            json.dumps(
                {
                    "source_url": profile_url,
                    "resolved_url": "https://www.douyin.com/user/empty",
                    "sec_uid": "empty_sec_uid",
                    "top_n": top_n,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "profile_videos.json").write_text("[]", encoding="utf-8")
        return {
            "resolved_url": "https://www.douyin.com/user/empty",
            "sec_uid": "empty_sec_uid",
            "profile_meta": str(root / "profile_meta.json"),
            "profile_videos": str(root / "profile_videos.json"),
        }

    async def forbidden_comments(*args, **kwargs):
        raise AssertionError("empty profile should stop before comment collection")

    monkeypatch.setattr(pipeline, "collect_profile_step", fake_collect)
    monkeypatch.setattr(pipeline, "collect_comments_step", forbidden_comments)

    with pytest.raises(RuntimeError, match="未采集到视频"):
        asyncio.run(
            pipeline.run_topic_package_pipeline(
                profile_url="https://v.douyin.com/example/",
                output_dir=root,
                top_n=8,
            )
        )


def test_resume_retries_only_failed_comment_videos(tmp_path, monkeypatch):
    root = Path(tmp_path)
    videos = [
        {"aweme_id": "100", "title": "第一步怎么办", "comment_count": 2},
        {"aweme_id": "200", "title": "第二条", "comment_count": 1},
    ]
    videos_path = root / "profile_videos.json"
    videos_path.write_text(json.dumps(videos, ensure_ascii=False), encoding="utf-8")
    (root / "profile_meta.json").write_text(
        json.dumps(
            {
                "artifact_schema_version": pipeline.PROFILE_ARTIFACT_SCHEMA_VERSION,
                "source_url": "https://v.douyin.com/example/",
                "resolved_url": "https://www.douyin.com/user/test",
                "sec_uid": "uid",
                "top_n": 2,
                "scan_pages": 10,
                "profile_videos_hash": pipeline._file_hash(videos_path),
            }
        ),
        encoding="utf-8",
    )
    comments_path = root / "comments.json"
    comments_path.write_text(
        json.dumps([{"aweme_id": "100", "cid": "c1", "text": "第一步不知道怎么做？"}], ensure_ascii=False),
        encoding="utf-8",
    )
    comments_status_path = root / "comments_status.json"
    comments_status_path.write_text(
        json.dumps(
            {
                "100": {"status": "success", "comment_count": 1, "error": ""},
                "200": {"status": "failed", "comment_count": 0, "error": "timeout"},
            }
        ),
        encoding="utf-8",
    )
    (root / "run_manifest.json").write_text(
        json.dumps(
            {
                "parameters": {
                    "profile_url": "https://v.douyin.com/example/",
                    "top_n": 2,
                    "max_comments_per_video": 10,
                    "include_replies": False,
                    "target_valid_comments": 0,
                    "max_comment_pages": 0,
                    "saturation_pages": 3,
                    "saturation_min_new_ratio": 0.08,
                    "redact_user_data": True,
                },
                "provenance": {
                    "file_hashes": {
                        "comments": pipeline._file_hash(comments_path),
                        "comments_status": pipeline._file_hash(comments_status_path),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    async def forbidden_collect(*args, **kwargs):
        raise AssertionError("profile should be reused")

    calls = []

    async def retry_failed(videos_path, **kwargs):
        calls.append(kwargs["only_video_ids"])
        merged = [
            {"aweme_id": "100", "cid": "c1", "text": "第一步不知道怎么做？"},
            {"aweme_id": "200", "cid": "c2", "text": "第二步做错了怎么办？"},
        ]
        (root / "comments.json").write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        (root / "comments_status.json").write_text(
            json.dumps(
                {
                    "100": {"status": "success", "comment_count": 1, "error": ""},
                    "200": {"status": "success", "comment_count": 1, "error": ""},
                }
            ),
            encoding="utf-8",
        )
        return {
            "comments": str(root / "comments.json"),
            "comments_status": str(root / "comments_status.json"),
        }

    monkeypatch.setattr(pipeline, "collect_profile_step", forbidden_collect)
    monkeypatch.setattr(pipeline, "collect_comments_step", retry_failed)

    outputs = asyncio.run(
        pipeline.run_topic_package_pipeline(
            profile_url="https://v.douyin.com/example/",
            output_dir=root,
            top_n=2,
            max_comments_per_video=10,
            resume=True,
        )
    )
    manifest = json.loads(Path(outputs["run_manifest"]).read_text(encoding="utf-8"))

    assert calls == [{"200"}]
    assert manifest["resume"]["retried_comment_videos"] == 1
    assert manifest["counts"]["failed_comment_videos"] == 0


def test_resume_restores_atomic_comment_checkpoint_without_final_manifest(tmp_path, monkeypatch):
    root = Path(tmp_path)
    videos = [
        {"aweme_id": "100", "title": "第一步怎么办", "comment_count": 2},
        {"aweme_id": "200", "title": "第二条", "comment_count": 1},
    ]
    videos_path = root / "profile_videos.json"
    videos_path.write_text(json.dumps(videos, ensure_ascii=False), encoding="utf-8")
    (root / "profile_meta.json").write_text(
        json.dumps(
            {
                "artifact_schema_version": pipeline.PROFILE_ARTIFACT_SCHEMA_VERSION,
                "source_url": "https://v.douyin.com/example/",
                "resolved_url": "https://www.douyin.com/user/test",
                "sec_uid": "uid",
                "top_n": 2,
                "scan_pages": 10,
                "profile_videos_hash": pipeline._file_hash(videos_path),
            }
        ),
        encoding="utf-8",
    )
    parameters = pipeline._run_parameters(
        profile_url="https://v.douyin.com/example/",
        top_n=2,
        max_comments_per_video=10,
        conversion_mode="balanced",
        min_fit_score=0,
        package_limit=0,
        min_evidence_count=0,
        scan_pages=10,
        include_replies=False,
        comment_concurrency=2,
        target_valid_comments=0,
        max_comment_pages=0,
        saturation_pages=3,
        saturation_min_new_ratio=0.08,
        redact_user_data=True,
        performance_feedback_path="",
    )
    checkpoint_path = root / "comments_checkpoint.json"
    checkpoint_comments = [
        {"aweme_id": "100", "cid": "c1", "text": "第一步不知道怎么做？"}
    ]
    checkpoint_statuses = {
        "100": {"status": "success", "comment_count": 1, "error": ""},
        "200": {"status": "failed", "comment_count": 0, "error": "timeout"},
    }
    checkpoint_path.write_text(
        json.dumps(
            {
                "artifact_schema_version": pipeline.COMMENT_CHECKPOINT_SCHEMA_VERSION,
                "parameters": pipeline._comment_resume_parameters(parameters),
                "parameter_hash": pipeline._parameter_hash(
                    pipeline._comment_resume_parameters(parameters)
                ),
                "profile_videos_hash": pipeline._file_hash(videos_path),
                "video_ids": ["100", "200"],
                "comments": checkpoint_comments,
                "comments_hash": pipeline._value_hash(checkpoint_comments),
                "statuses": checkpoint_statuses,
                "statuses_hash": pipeline._value_hash(checkpoint_statuses),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def forbidden_collect(*args, **kwargs):
        raise AssertionError("profile should be reused")

    calls = []

    async def retry_failed(videos_path, **kwargs):
        calls.append(kwargs["only_video_ids"])
        merged = [
            {"aweme_id": "100", "cid": "c1", "text": "第一步不知道怎么做？"},
            {"aweme_id": "200", "cid": "c2", "text": "第二步做错了怎么办？"},
        ]
        (root / "comments.json").write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        (root / "comments_status.json").write_text(
            json.dumps(
                {
                    "100": {"status": "success", "comment_count": 1, "error": ""},
                    "200": {"status": "success", "comment_count": 1, "error": ""},
                }
            ),
            encoding="utf-8",
        )
        return {
            "comments": str(root / "comments.json"),
            "comments_status": str(root / "comments_status.json"),
            "comments_checkpoint": str(checkpoint_path),
        }

    monkeypatch.setattr(pipeline, "collect_profile_step", forbidden_collect)
    monkeypatch.setattr(pipeline, "collect_comments_step", retry_failed)

    outputs = asyncio.run(
        pipeline.run_topic_package_pipeline(
            profile_url="https://v.douyin.com/example/",
            output_dir=root,
            top_n=2,
            max_comments_per_video=10,
            resume=True,
        )
    )
    manifest = json.loads(Path(outputs["run_manifest"]).read_text(encoding="utf-8"))
    comments = json.loads(Path(outputs["comments"]).read_text(encoding="utf-8"))

    assert calls == [{"200"}]
    assert len(comments) == 2
    assert manifest["resume"]["retried_comment_videos"] == 1
    assert manifest["resume"]["restored_comment_checkpoint"] is True


def test_collect_comments_step_writes_combined_atomic_checkpoint(tmp_path, monkeypatch):
    root = Path(tmp_path)
    videos_path = root / "profile_videos.json"
    videos_path.write_text(
        json.dumps([{"aweme_id": "100", "title": "第一步怎么办"}], ensure_ascii=False),
        encoding="utf-8",
    )
    parameters = {"profile_url": "https://v.douyin.com/example/", "top_n": 1}
    comment = pipeline.CommentItem(aweme_id="100", cid="c1", text="第一步不知道怎么做？")
    statuses = {"100": {"status": "success", "comment_count": 1, "error": ""}}

    async def fake_collect(videos, **kwargs):
        kwargs["progress_callback"]([comment], statuses)
        return [comment], statuses

    monkeypatch.setattr(pipeline, "collect_comments_for_videos", fake_collect)

    outputs = asyncio.run(
        pipeline.collect_comments_step(
            videos_path,
            output_dir=root,
            checkpoint_parameters=parameters,
        )
    )
    checkpoint = json.loads(Path(outputs["comments_checkpoint"]).read_text(encoding="utf-8"))
    expected_parameters = pipeline._comment_resume_parameters(parameters)

    assert checkpoint["profile_videos_hash"] == pipeline._file_hash(videos_path)
    assert checkpoint["parameter_hash"] == pipeline._parameter_hash(expected_parameters)
    assert checkpoint["comments_hash"] == pipeline._value_hash(checkpoint["comments"])
    assert checkpoint["statuses_hash"] == pipeline._value_hash(checkpoint["statuses"])
    assert checkpoint["comments"][0]["cid"] == "c1"
    assert checkpoint["statuses"]["100"]["status"] == "success"


def test_profile_resume_rejects_missing_metadata_cross_account_and_tampering(tmp_path):
    videos_path = Path(tmp_path) / "profile_videos.json"
    videos_path.write_text('[{"aweme_id": "100"}]', encoding="utf-8")
    meta = {
        "artifact_schema_version": pipeline.PROFILE_ARTIFACT_SCHEMA_VERSION,
        "source_url": "https://v.douyin.com/account-a/",
        "resolved_url": "https://www.douyin.com/user/a",
        "sec_uid": "uid-a",
        "top_n": 20,
        "scan_pages": 10,
        "profile_videos_hash": pipeline._file_hash(videos_path),
    }

    assert pipeline._profile_resume_matches(
        meta,
        "https://v.douyin.com/account-a/",
        20,
        10,
        videos_path,
    )
    assert not pipeline._profile_resume_matches({}, "https://v.douyin.com/account-a/", 20, 10, videos_path)
    assert not pipeline._profile_resume_matches(
        meta,
        "https://v.douyin.com/account-b/",
        20,
        10,
        videos_path,
    )

    videos_path.write_text('[{"aweme_id": "changed"}]', encoding="utf-8")
    assert not pipeline._profile_resume_matches(
        meta,
        "https://v.douyin.com/account-a/",
        20,
        10,
        videos_path,
    )


def test_comment_resume_requires_matching_parameters_status_and_hashes(tmp_path):
    root = Path(tmp_path)
    comments_path = root / "comments.json"
    status_path = root / "comments_status.json"
    comments_path.write_text("[]", encoding="utf-8")
    status_path.write_text('{"100": {"status": "success"}}', encoding="utf-8")
    parameters = {
        "profile_url": "https://v.douyin.com/example/",
        "top_n": 2,
        "max_comments_per_video": 10,
        "include_replies": False,
        "target_valid_comments": 0,
        "max_comment_pages": 0,
        "saturation_pages": 3,
        "saturation_min_new_ratio": 0.08,
        "redact_user_data": True,
    }
    (root / "run_manifest.json").write_text(
        json.dumps(
            {
                "parameters": parameters,
                "provenance": {
                    "file_hashes": {
                        "comments": pipeline._file_hash(comments_path),
                        "comments_status": pipeline._file_hash(status_path),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert pipeline._comments_resume_matches(root, parameters)
    assert not pipeline._comments_resume_matches(root, {**parameters, "saturation_pages": 5})

    status_path.unlink()
    assert not pipeline._comments_resume_matches(root, parameters)
