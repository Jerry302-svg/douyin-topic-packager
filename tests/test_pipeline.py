import asyncio
import json
from pathlib import Path

import pytest

from douyin_topic_packager import pipeline


def test_run_topic_package_pipeline_resume_reuses_existing_files_and_filters_evidence(tmp_path, monkeypatch):
    root = Path(tmp_path)
    (root / "profile_meta.json").write_text(
        json.dumps(
            {
                "source_url": "https://v.douyin.com/example/",
                "resolved_url": "https://www.douyin.com/user/test",
                "sec_uid": "test_sec_uid",
                "top_n": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "profile_videos.json").write_text(
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
    (root / "comments.json").write_text(
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

    async def fake_collect(profile_url, *, output_dir, top_n, storage_state_path):
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

    async def fake_comments(videos_path, *, output_dir, storage_state_path, max_comments_per_video):
        calls["comments"] += 1
        assert max_comments_per_video == 9
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
