import json
from pathlib import Path

from douyin_topic_packager.collector import normalize_aweme_item, normalize_comment


FIXTURES = Path(__file__).parent / "fixtures"


def test_recorded_comment_contract_normalizes_and_redacts_user_data():
    page = json.loads((FIXTURES / "comment_page.json").read_text(encoding="utf-8"))

    comment = normalize_comment("7390000000000000001", page["comments"][0])

    assert comment.cid == "comment-1"
    assert comment.like_count == 8
    assert comment.user_nickname == ""
    assert comment.metadata["reply_comment_total"] == 2
    assert comment.metadata["user_hash"]
    assert "ip_label" not in comment.metadata


def test_recorded_aweme_contract_keeps_counts_and_identity():
    video = normalize_aweme_item(
        {
            "aweme_id": "7390000000000000001",
            "desc": "录制视频样本",
            "create_time": 1710000000,
            "statistics": {"digg_count": 120, "comment_count": 25, "share_count": 3},
        }
    )

    assert video.aweme_id == "7390000000000000001"
    assert video.comment_count == 25
    assert video.like_count == 120
