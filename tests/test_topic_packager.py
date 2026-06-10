from douyin_topic_packager.packager import build_topic_package_messages, fallback_topic_packages, generate_topic_packages
from douyin_topic_packager.reports import render_topic_packages_markdown
from douyin_topic_packager.schemas import CommentItem, VideoItem
from douyin_topic_packager.signals import build_angle_candidates, build_pain_signals, validate_angles


def _sample_data():
    videos = [
        VideoItem(
            aweme_id="100",
            title="很多人卡在第一步，不知道该怎么判断",
            comment_count=20,
            like_count=100,
        )
    ]
    comments = [
        CommentItem(aweme_id="100", text="我就是不知道第一步应该怎么做，有没有简单办法？", like_count=8),
        CommentItem(aweme_id="100", text="这个问题我也遇到了，最怕试了还是没效果", like_count=5),
    ]
    return videos, comments


def test_build_topic_package_chain_without_llm():
    videos, comments = _sample_data()
    signals = build_pain_signals(videos, comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    packages = fallback_topic_packages(signals, candidates, scorecards)

    assert signals
    assert candidates
    assert scorecards
    assert packages
    assert packages[0].evidence


def test_markdown_report_is_clean_result():
    videos, comments = _sample_data()
    signals = build_pain_signals(videos, comments)
    candidates = build_angle_candidates(signals)
    scorecards = validate_angles(candidates, signals)
    packages = fallback_topic_packages(signals, candidates, scorecards)
    md = render_topic_packages_markdown(
        source_url="https://v.douyin.com/test/",
        resolved_url="https://www.douyin.com/user/test",
        sec_uid="test",
        videos=videos,
        pain_signals=signals,
        scorecards=scorecards,
        topic_packages=packages,
    )

    assert "# 抖音对标账号选题包" in md
    assert "可直接使用的选题包" in md
    assert "<think>" not in md
    assert "```json" not in md


def test_generate_topic_packages_repairs_invalid_llm_json_once():
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, temperature=0.3, max_tokens=5000):
            self.calls += 1
            if self.calls == 1:
                return '{"topic_packages":[{"brief_title":"bad "quote""}]}'
            return (
                '{"topic_packages":[{"brief_title":"fixed","topic":"fixed topic",'
                '"pain_point":"pain","evidence":["comment"],"target_audience":"audience",'
                '"opening_hook":"hook","recommended_angle":"angle","proof_needed":"proof",'
                '"cta_direction":"cta","risk_notes":["risk"],'
                '"production_suggestions":["suggestion"],"fit_score":88,'
                '"why_worth_shooting":"worth"}]}'
            )

    packages = generate_topic_packages([], [], [], [], llm_client=FakeLLM())

    assert packages[0].brief_title == "fixed"
    assert packages[0].metadata["generated_by"] == "llm"


def test_build_topic_package_messages_include_conversion_mode_instruction():
    messages = build_topic_package_messages([], [], [], [], conversion_mode="conservative")
    combined = "\n".join(item["content"] for item in messages)

    assert "conservative" in combined
    assert "avoid direct diagnosis" in combined


def test_generate_topic_packages_marks_conversion_mode():
    class FakeLLM:
        def complete(self, messages, temperature=0.3, max_tokens=5000):
            return (
                '{"topic_packages":[{"brief_title":"title","topic":"topic",'
                '"pain_point":"pain","evidence":["comment"],"target_audience":"audience",'
                '"opening_hook":"hook","recommended_angle":"angle","proof_needed":"proof",'
                '"cta_direction":"cta","risk_notes":["risk"],'
                '"production_suggestions":["suggestion"],"fit_score":88,'
                '"why_worth_shooting":"worth"}]}'
            )

    packages = generate_topic_packages([], [], [], [], llm_client=FakeLLM(), conversion_mode="strong")

    assert packages[0].metadata["generated_by"] == "llm"
    assert packages[0].metadata["conversion_mode"] == "strong"
