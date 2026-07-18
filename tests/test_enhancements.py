import asyncio

from douyin_topic_packager.comments import CommentsCollector
from douyin_topic_packager.feedback import calibrate_topic_packages
from douyin_topic_packager.packager import audit_topic_packages, fallback_topic_packages
from douyin_topic_packager.privacy import sanitize_comment
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
    assert calibrated.fit_score != original_score


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
