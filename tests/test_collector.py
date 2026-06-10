from douyin_topic_packager.collector import extract_first_url, parse_sec_uid, rank_videos_by_comment_count
from douyin_topic_packager.schemas import VideoItem


def test_extract_first_url_from_share_text():
    text = "长按复制此条消息 https://v.douyin.com/abc123/ 打开抖音"
    assert extract_first_url(text) == "https://v.douyin.com/abc123"


def test_parse_sec_uid_from_profile_url():
    assert parse_sec_uid("https://www.douyin.com/user/MS4wLjABAAAAxxx?from_tab_name=main") == "MS4wLjABAAAAxxx"


def test_rank_videos_by_comment_count():
    videos = [
        VideoItem(aweme_id="1", comment_count=3, like_count=100),
        VideoItem(aweme_id="2", comment_count=9, like_count=1),
        VideoItem(aweme_id="3", comment_count=9, like_count=200),
    ]
    ranked = rank_videos_by_comment_count(videos, limit=2)
    assert [item.aweme_id for item in ranked] == ["3", "2"]
