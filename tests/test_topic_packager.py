from douyin_topic_packager.packager import (
    build_topic_package_messages,
    fallback_topic_packages,
    filter_topic_packages,
    generate_topic_packages,
)
from douyin_topic_packager.reports import render_topic_packages_markdown
from douyin_topic_packager.schemas import CommentItem, PainSignal, TopicPackage, VideoItem
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


def _topic_package(title, score):
    return TopicPackage(
        brief_title=title,
        topic=title,
        pain_point=f"{title} pain",
        evidence=["comment"],
        target_audience="audience",
        opening_hook="hook",
        recommended_angle="angle",
        proof_needed="proof",
        cta_direction="cta",
        risk_notes=["risk"],
        production_suggestions=["suggestion"],
        fit_score=score,
    )


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


def test_build_pain_signals_clusters_similar_user_questions():
    comments = [
        CommentItem(aweme_id="100", text="我不知道第一步应该怎么做，有没有简单办法？", like_count=8),
        CommentItem(aweme_id="100", text="第一步不知道怎么做，怕试了还是没效果", like_count=5),
    ]

    signals = build_pain_signals([], comments)

    assert signals
    assert signals[0].evidence_count == 2
    assert "第一步" in signals[0].pain_point
    assert signals[0].evidence_level == "strong"


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
    assert "## 推荐拍摄顺序" in md
    assert "可直接使用的选题包" in md
    assert "<think>" not in md
    assert "```json" not in md


def test_markdown_report_prioritizes_packages_and_shortens_links():
    videos, comments = _sample_data()
    videos[0].url = "https://www.iesdouyin.com/share/video/100/?very=long&query=value"
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

    assert md.index("## 推荐拍摄顺序") < md.index("## 一、可直接使用的选题包")
    assert packages[0].brief_title in md.split("## 一、可直接使用的选题包", 1)[0]
    assert md.index("## 一、可直接使用的选题包") < md.index("## 二、Top 视频信号")
    assert "- 链接：[打开视频](" in md
    assert "- 链接：https://www.iesdouyin.com" not in md


def test_markdown_report_downgrades_weak_signals_and_adds_shooting_brief():
    videos, comments = _sample_data()
    strong_signal = PainSignal(
        pain_point="不知道第一步怎么做",
        evidence=["我不知道第一步怎么做", "第一步怕做错"],
        evidence_count=2,
        signal_strength=82,
        confidence=0.72,
        evidence_level="strong",
    )
    weak_signal = PainSignal(
        pain_point="单条评论里的偶发问题",
        evidence=["路过问一句"],
        evidence_count=1,
        signal_strength=48,
        confidence=0.48,
        evidence_level="weak",
    )
    package = _topic_package("第一步判断清单", 88)
    package.cover_copy = "先别急着做决定"
    package.first_three_seconds = "如果你不知道第一步怎么做，先看这三个判断。"
    package.script_outline = ["误区", "判断", "动作"]
    package.comment_cta = "评论区留下你卡在哪一步。"
    package.material_notes = ["准备一条真实评论"]

    md = render_topic_packages_markdown(
        source_url="https://v.douyin.com/test/",
        resolved_url="https://www.douyin.com/user/test",
        sec_uid="test",
        videos=videos,
        pain_signals=[strong_signal, weak_signal],
        scorecards=[],
        topic_packages=[package],
    )

    assert "拍摄简案" in md
    assert "封面文案：先别急着做决定" in md
    assert "弱证据观察" in md
    assert "单条评论里的偶发问题" in md
    assert md.index("不知道第一步怎么做") < md.index("## 四、弱证据观察")


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
    assert packages[0].cover_copy
    assert packages[0].first_three_seconds


def test_filter_topic_packages_applies_min_score_and_limit():
    packages = [
        _topic_package("A", 92),
        _topic_package("B", 77),
        _topic_package("C", 88),
        _topic_package("D", 81),
    ]

    filtered = filter_topic_packages(packages, min_fit_score=80, package_limit=2)

    assert [item.brief_title for item in filtered] == ["A", "C"]


def test_generate_topic_packages_filters_llm_results_by_score_and_limit():
    class FakeLLM:
        def complete(self, messages, temperature=0.3, max_tokens=5000):
            return (
                '{"topic_packages":['
                '{"brief_title":"high","topic":"topic high","pain_point":"pain",'
                '"evidence":["comment"],"target_audience":"audience",'
                '"opening_hook":"hook","recommended_angle":"angle high","proof_needed":"proof",'
                '"cta_direction":"cta","risk_notes":["risk"],'
                '"production_suggestions":["suggestion"],"fit_score":92,'
                '"why_worth_shooting":"worth"},'
                '{"brief_title":"low","topic":"topic low","pain_point":"pain",'
                '"evidence":["comment"],"target_audience":"audience",'
                '"opening_hook":"hook","recommended_angle":"angle low","proof_needed":"proof",'
                '"cta_direction":"cta","risk_notes":["risk"],'
                '"production_suggestions":["suggestion"],"fit_score":72,'
                '"why_worth_shooting":"worth"},'
                '{"brief_title":"mid","topic":"topic mid","pain_point":"pain",'
                '"evidence":["comment"],"target_audience":"audience",'
                '"opening_hook":"hook","recommended_angle":"angle mid","proof_needed":"proof",'
                '"cta_direction":"cta","risk_notes":["risk"],'
                '"production_suggestions":["suggestion"],"fit_score":86,'
                '"why_worth_shooting":"worth"}'
                "]}"
            )

    packages = generate_topic_packages(
        [],
        [],
        [],
        [],
        llm_client=FakeLLM(),
        min_fit_score=80,
        package_limit=1,
    )

    assert [item.brief_title for item in packages] == ["high"]
