import asyncio
import json
from pathlib import Path

from douyin_topic_packager.comments import CommentsCollector
from douyin_topic_packager.feedback import calibrate_topic_packages
from douyin_topic_packager.packager import (
    audit_topic_packages,
    fallback_topic_packages,
    generate_topic_packages,
)
from douyin_topic_packager.pipeline import analyze_comments_step
from douyin_topic_packager.privacy import sanitize_comment
from douyin_topic_packager.reports import render_topic_packages_markdown
from douyin_topic_packager.schemas import CommentItem, VideoItem
from douyin_topic_packager.signals import build_angle_candidates, build_pain_signals, validate_angles


class PagedCommentsAPI:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    async def get_aweme_comments(self, aweme_id, cursor, count, include_replies):
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return page


def test_adaptive_collector_stops_after_target_valid_comments():
    api = PagedCommentsAPI(
        [
            {
                "items": [
                    {"cid": "1", "text": "我不知道第一步应该怎么做？"},
                    {"cid": "2", "text": "担心这样处理以后没有效果怎么办？"},
                ],
                "has_more": True,
                "max_cursor": 20,
            }
        ]
    )
    collector = CommentsCollector(
        api,
        target_valid_comments=2,
        quality_predicate=lambda text: "？" in text,
        retry_delay=0,
    )

    comments = asyncio.run(collector.collect("100"))

    assert len(comments) == 2
    assert collector.stats["stop_reason"] == "target_valid_comments"
    assert collector.stats["valid_comment_count"] == 2


def test_semantic_signals_track_users_videos_and_duplicates():
    comments = [
        CommentItem(
            aweme_id="1",
            cid="c1",
            text="我不知道第一步应该怎么做，有没有简单办法？",
            metadata={"user_hash": "u1"},
        ),
        CommentItem(
            aweme_id="2",
            cid="c2",
            text="第一步不知道怎么做，担心做错了怎么办？",
            metadata={"user_hash": "u2"},
        ),
        CommentItem(
            aweme_id="2",
            cid="c3",
            text="第一步不知道怎么做，担心做错了怎么办？",
            metadata={"user_hash": "u2"},
        ),
    ]
    videos = [VideoItem(aweme_id="1", title="入门判断"), VideoItem(aweme_id="2", title="第一步怎么走")]

    signal = next(item for item in build_pain_signals(videos, comments) if item.signal_type == "audience_pain")

    assert signal.unique_user_count == 2
    assert signal.unique_video_count == 2
    assert signal.duplicate_evidence_count == 1
    assert signal.is_actionable is True


def test_high_stakes_topic_requires_external_verification():
    comments = [
        CommentItem(aweme_id="1", cid="1", text="合同违约金到底应该怎么判断？", metadata={"user_hash": "u1"}),
        CommentItem(aweme_id="2", cid="2", text="担心合同里的违约金有风险怎么办？", metadata={"user_hash": "u2"}),
    ]
    signals = build_pain_signals([], comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    packages = fallback_topic_packages(signals, candidates, scorecards)
    packages[0].cta_direction = "留言你的材料，我帮你梳理可能的追偿顺序。"
    packages[0].comment_cta = packages[0].cta_direction
    audited = audit_topic_packages(packages, signals, scorecards)

    assert audited
    assert audited[0].external_verification_required is True
    assert audited[0].claim_status == "needs_external_verification"
    assert audited[0].confidence_level == "review_required"
    assert "我帮你" not in audited[0].cta_direction
    assert len(audited[0].experiment_variants) == 2


def test_general_topic_keeps_helpful_cta_without_professional_review():
    comments = [
        CommentItem(
            aweme_id="1",
            cid="1",
            text="新手做蛋糕总是塌，不知道第一步怎么做？",
            metadata={"user_hash": "u1"},
        ),
        CommentItem(
            aweme_id="2",
            cid="2",
            text="第一次做蛋糕总塌，第一步不知道怎么做怎么办？",
            metadata={"user_hash": "u2"},
        ),
    ]
    signals = build_pain_signals([], comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    packages = fallback_topic_packages(signals, candidates, scorecards)
    packages[0].cta_direction = "留言你卡住的步骤，我帮你整理一个练习顺序。"
    packages[0].comment_cta = packages[0].cta_direction

    audited = audit_topic_packages(packages, signals, scorecards)

    assert audited
    assert audited[0].external_verification_required is False
    assert audited[0].claim_status == "supported"
    assert audited[0].confidence_level == "publish_ready"
    assert "我帮你整理" in audited[0].cta_direction


def test_default_general_cta_is_safe_and_not_rewritten():
    comments = [
        CommentItem(
            aweme_id="1",
            cid="1",
            text="新手做蛋糕总是塌，不知道第一步怎么做？",
            metadata={"user_hash": "u1"},
        ),
        CommentItem(
            aweme_id="2",
            cid="2",
            text="第一次做蛋糕总塌，第一步不知道怎么做怎么办？",
            metadata={"user_hash": "u2"},
        ),
    ]
    signals = build_pain_signals([], comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    package = fallback_topic_packages(signals, candidates, scorecards)[0]
    original_cta = package.cta_direction

    audited = audit_topic_packages([package], signals, scorecards)

    assert audited[0].cta_direction == original_cta
    assert "我帮你判断" not in audited[0].cta_direction
    assert not any("个案判断" in warning for warning in audited[0].quality_warnings)


def test_signal_intents_are_domain_neutral_but_high_stakes_still_require_review():
    general_comments = [
        CommentItem(aweme_id="1", cid="1", text="预算有限又没时间，不知道该怎么开始？", metadata={"user_hash": "u1"}),
        CommentItem(aweme_id="2", cid="2", text="没时间而且预算不够，第一步要怎么做？", metadata={"user_hash": "u2"}),
    ]
    high_stakes_comments = [
        CommentItem(aweme_id="3", cid="3", text="合同违约金到底应该怎么判断？", metadata={"user_hash": "u3"}),
        CommentItem(aweme_id="4", cid="4", text="担心合同里的违约金有风险怎么办？", metadata={"user_hash": "u4"}),
    ]

    general_signal = next(
        item for item in build_pain_signals([], general_comments) if item.signal_type == "audience_pain"
    )
    high_stakes_signal = next(
        item for item in build_pain_signals([], high_stakes_comments) if item.signal_type == "audience_pain"
    )
    high_stakes_candidates = build_angle_candidates([high_stakes_signal])
    scorecards = validate_angles(high_stakes_candidates, [high_stakes_signal])

    assert general_signal.pain_point in {
        "不知道第一步怎么做",
        "受到时间、预算或资源限制",
    }
    assert "合同" not in high_stakes_signal.pain_point
    assert scorecards[0].scores["compliance_safety"] < 86


def test_performance_feedback_calibrates_fit_score_without_overriding_evidence():
    comments = [
        CommentItem(aweme_id="1", cid="1", text="不知道第一步怎么做，有没有简单办法？", metadata={"user_hash": "u1"}),
        CommentItem(aweme_id="2", cid="2", text="第一步不知道怎么做，担心做错怎么办？", metadata={"user_hash": "u2"}),
    ]
    signals = build_pain_signals([], comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    package = audit_topic_packages(fallback_topic_packages(signals, candidates, scorecards), signals, scorecards)[0]
    original_score = package.fit_score

    calibrated = calibrate_topic_packages(
        [package],
        [
            {
                "pain_point": package.pain_point,
                "title": package.brief_title,
                "impressions": 5000,
                "three_second_rate": 90,
                "completion_rate": 80,
                "save_rate": 12,
                "comment_rate": 5,
            }
        ],
    )[0]

    assert calibrated.performance_calibration["status"] == "applied"
    assert calibrated.performance_calibration["original_fit_score"] == original_score
    assert calibrated.performance_calibration["confidence"] == "low"
    assert calibrated.fit_score != original_score


def test_performance_feedback_requires_enough_exposure_before_calibration():
    comments = [
        CommentItem(aweme_id="1", cid="1", text="不知道第一步怎么做，有没有简单办法？", metadata={"user_hash": "u1"}),
        CommentItem(aweme_id="2", cid="2", text="第一步不知道怎么做，担心做错怎么办？", metadata={"user_hash": "u2"}),
    ]
    signals = build_pain_signals([], comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    package = audit_topic_packages(fallback_topic_packages(signals, candidates, scorecards), signals, scorecards)[0]
    original_score = package.fit_score

    calibrated = calibrate_topic_packages(
        [package],
        [
            {
                "pain_point": package.pain_point,
                "title": package.brief_title,
                "impressions": 200,
                "three_second_rate": 95,
                "completion_rate": 90,
            }
        ],
    )[0]

    assert calibrated.performance_calibration["status"] == "insufficient_data"
    assert calibrated.fit_score == original_score


def test_report_uses_reader_facing_labels_and_next_actions():
    comments = [
        CommentItem(aweme_id="1", cid="1", text="不知道第一步怎么做，有没有简单办法？", metadata={"user_hash": "u1"}),
        CommentItem(aweme_id="2", cid="2", text="第一步不知道怎么做，担心做错怎么办？", metadata={"user_hash": "u2"}),
    ]
    signals = build_pain_signals([], comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    packages = audit_topic_packages(fallback_topic_packages(signals, candidates, scorecards), signals, scorecards)

    report = render_topic_packages_markdown(
        source_url="https://example.com",
        resolved_url="https://example.com/user",
        sec_uid="uid",
        videos=[],
        pain_signals=signals,
        scorecards=scorecards,
        topic_packages=packages,
    )

    assert "## 下一步动作" in report
    assert "使用建议：可直接使用" in report
    assert "publish_ready" not in report
    assert "audience_pain" not in report


def test_comment_sanitizer_removes_identity_and_contact_details():
    comment = CommentItem(
        aweme_id="1",
        cid="c1",
        text="加微信 wechat: abcdef12，手机 13800138000",
        user_nickname="张三",
        metadata={"ip_label": "北京", "uid": "123"},
    )

    safe = sanitize_comment(comment)

    assert safe.user_nickname == ""
    assert "ip_label" not in safe.metadata
    assert "uid" not in safe.metadata
    assert "13800138000" not in safe.text
    assert "abcdef12" not in safe.text


def test_llm_topic_package_cache_reuses_validated_completion(tmp_path):
    comments = [
        CommentItem(aweme_id="1", cid="1", text="不知道第一步怎么做，有没有简单办法？", metadata={"user_hash": "u1"}),
        CommentItem(aweme_id="2", cid="2", text="第一步不知道怎么做，担心做错怎么办？", metadata={"user_hash": "u2"}),
    ]
    signals = build_pain_signals([], comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    signal = signals[0]

    class CacheClient:
        class Config:
            normalized_provider = "fake"
            model = "cache-model"

        config = Config()

        def __init__(self):
            self.calls = 0

        def complete(self, messages, temperature=0.2, max_tokens=5000):
            self.calls += 1
            return json.dumps(
                {
                    "topic_packages": [
                        {
                            "pain_signal_id": "P1",
                            "brief_title": "第一步判断清单",
                            "topic": "不知道第一步时先做什么",
                            "pain_point": signal.pain_point,
                            "evidence": signal.evidence,
                            "target_audience": "刚开始行动但缺少路径的人",
                            "opening_hook": "第一步不清楚时，先做这三个判断。",
                            "recommended_angle": "给出三个低门槛动作",
                            "proof_needed": "展示步骤清单和适用边界",
                            "cta_direction": "留言你卡住的步骤。",
                            "fit_score": 88,
                        }
                    ]
                },
                ensure_ascii=False,
            )

    client = CacheClient()
    first_stats = {}
    second_stats = {}
    first = generate_topic_packages(
        [],
        signals,
        candidates,
        scorecards,
        llm_client=client,
        cache_dir=tmp_path,
        cache_stats=first_stats,
    )
    second = generate_topic_packages(
        [],
        signals,
        candidates,
        scorecards,
        llm_client=client,
        cache_dir=tmp_path,
        cache_stats=second_stats,
    )

    assert first and second
    assert client.calls == 1
    assert first_stats["misses"] == 1
    assert second_stats["hits"] == 1


def test_analysis_writes_quality_and_cache_metadata(tmp_path):
    videos_path = Path(tmp_path) / "profile_videos.json"
    comments_path = Path(tmp_path) / "comments.json"
    videos_path.write_text(
        json.dumps(
            [
                {"aweme_id": "1", "title": "第一步怎么做"},
                {"aweme_id": "2", "title": "新手行动清单"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    comments_path.write_text(
        json.dumps(
            [
                {"aweme_id": "1", "cid": "1", "text": "不知道第一步怎么做，有没有简单办法？", "metadata": {"user_hash": "u1"}},
                {"aweme_id": "2", "cid": "2", "text": "第一步不知道怎么做，担心做错怎么办？", "metadata": {"user_hash": "u2"}},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    outputs = analyze_comments_step(
        source_url="https://example.com",
        resolved_url="https://example.com/user",
        sec_uid="uid",
        videos_path=videos_path,
        comments_path=comments_path,
        output_dir=tmp_path,
    )
    quality = json.loads(Path(outputs["quality"]).read_text(encoding="utf-8"))
    metadata = json.loads(Path(outputs["analysis_metadata"]).read_text(encoding="utf-8"))

    assert quality["passed"] is True
    assert metadata["quality_gate_passed"] is True
    assert metadata["cache"] == {}
    assert "自动质量门禁：通过" in Path(outputs["markdown"]).read_text(encoding="utf-8")
