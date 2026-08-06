import asyncio
import json

from douyin_topic_packager.comments import CommentsCollector
from douyin_topic_packager.collector import collect_profile_pages
from douyin_topic_packager.packager import audit_topic_packages, normalize_llm_topic_packages
from douyin_topic_packager.quality import evaluate_topic_run
from douyin_topic_packager.reports import render_topic_packages_markdown
from douyin_topic_packager.schemas import PainSignal, TopicPackage, VideoItem
from douyin_topic_packager.signals import build_angle_candidates, build_pain_signals, validate_angles


def _grounded_signal(level="strong"):
    return PainSignal(
        pain_point="不知道第一步怎么做",
        evidence=["我不知道第一步怎么做，有没有简单办法？"],
        evidence_refs=[
            {
                "source_type": "comment",
                "source_id": "c1",
                "aweme_id": "100",
                "text": "我不知道第一步怎么做，有没有简单办法？",
            }
        ],
        evidence_count=2 if level == "strong" else 1,
        signal_strength=82,
        confidence=0.78 if level == "strong" else 0.48,
        evidence_level=level,
        signal_type="audience_pain",
        is_actionable=level != "weak",
    )


def _package(**overrides):
    values = {
        "brief_title": "第一步判断清单",
        "topic": "不知道第一步时先做什么",
        "pain_point": "不知道第一步怎么做",
        "evidence": ["模型编出来的证据"],
        "target_audience": "正在犹豫的用户",
        "opening_hook": "第一步不清楚时，先别急着找答案。",
        "recommended_angle": "给出三个判断动作",
        "proof_needed": "虚构一个合理案例",
        "cta_direction": "报出你的金额，我帮你判断具体情况",
        "risk_notes": [],
        "production_suggestions": ["口播"],
        "fit_score": 88,
    }
    values.update(overrides)
    return TopicPackage(**values)


def test_noise_comments_do_not_become_pain_signals():
    from douyin_topic_packager.schemas import CommentItem

    comments = [
        CommentItem(aweme_id="100", cid="1", text="[爱心][爱心][爱心]"),
        CommentItem(aweme_id="100", cid="2", text="老师你好，我想你帮帮忙"),
    ]

    assert build_pain_signals([], comments) == []


def test_title_hypotheses_are_separate_from_audience_pain():
    signals = build_pain_signals(
        [VideoItem(aweme_id="100", title="三步解决常见问题")],
        [],
    )

    assert len(signals) == 1
    assert signals[0].signal_type == "content_hypothesis"
    assert signals[0].is_actionable is False
    assert signals[0].evidence_refs[0]["source_type"] == "video_title"


def test_unknown_llm_pain_is_rejected_and_evidence_is_grounded():
    signal = _grounded_signal()
    unknown = json.dumps(
        {
            "topic_packages": [
                {
                    "brief_title": "未知痛点",
                    "topic": "未知",
                    "pain_point": "输入中从未出现的痛点",
                    "evidence": ["捏造证据"],
                    "recommended_angle": "讲方法",
                }
            ]
        },
        ensure_ascii=False,
    )
    assert normalize_llm_topic_packages(unknown, [signal]) == []

    grounded = json.dumps(
        {
            "topic_packages": [
                {
                    "brief_title": "第一步怎么判断",
                    "topic": "第一步判断",
                    "pain_point": signal.pain_point,
                    "evidence": ["模型捏造的原话"],
                    "recommended_angle": "给三个动作",
                }
            ]
        },
        ensure_ascii=False,
    )
    package = normalize_llm_topic_packages(grounded, [signal])[0]
    assert package.evidence == signal.evidence
    assert package.evidence_refs == signal.evidence_refs


def test_signal_id_keeps_llm_package_when_pain_is_paraphrased():
    signal = _grounded_signal()
    raw = json.dumps(
        {
            "topic_packages": [
                {
                    "pain_signal_id": "P1",
                    "brief_title": "先做这一步判断",
                    "topic": "给出三个判断动作",
                    "pain_point": "模型重新概括过的痛点",
                    "evidence": signal.evidence,
                    "recommended_angle": "给出三个判断动作",
                }
            ]
        },
        ensure_ascii=False,
    )

    package = normalize_llm_topic_packages(raw, [signal])[0]

    assert package.pain_point == signal.pain_point
    assert package.metadata["generated_by"] == "llm"
    assert package.metadata["pain_signal_id"] == "P1"


def test_evidence_recovers_signal_when_llm_omits_signal_id():
    signal = _grounded_signal()
    raw = json.dumps(
        {
            "topic_packages": [
                {
                    "brief_title": "证据反向绑定",
                    "topic": "按证据找到痛点",
                    "pain_point": "完全重新概括的说法",
                    "evidence": signal.evidence,
                    "recommended_angle": "给出三个判断动作",
                }
            ]
        },
        ensure_ascii=False,
    )

    package = normalize_llm_topic_packages(raw, [signal])[0]

    assert package.pain_point == signal.pain_point
    assert package.metadata["generated_by"] == "llm"
    assert package.evidence == signal.evidence


def test_second_pass_audit_removes_fabrication_and_individual_diagnosis():
    package = _package(
        comment_cta="留下你的材料，我来帮你判断第一步",
        script_outline=["结尾让用户留下发现日期，我帮你看起算点"],
    )
    audited = audit_topic_packages([package], [_grounded_signal()], [], conversion_mode="balanced")

    assert len(audited) == 1
    assert audited[0].evidence == ["我不知道第一步怎么做，有没有简单办法？"]
    assert "虚构" not in audited[0].proof_needed
    assert "我帮你判断" not in audited[0].cta_direction
    assert "帮你判断" not in audited[0].comment_cta
    assert all("留下发现日期" not in line for line in audited[0].script_outline)
    assert all("我帮你看" not in line for line in audited[0].script_outline)
    assert audited[0].quality_warnings
    assert audited[0].confidence_level == "publish_ready"


def test_angle_scores_have_real_reasons_and_no_index_parity_scoring():
    signal = _grounded_signal()
    candidates = build_angle_candidates([signal])
    scorecards = validate_angles(candidates, [signal])

    assert scorecards
    assert scorecards[0].score_reasons["evidence_strength"]
    assert scorecards[0].scores["production_ease"] != 88
    assert scorecards[0].scores["compliance_safety"] != 90


def test_comment_collector_retries_transient_failure():
    class FakeAPI:
        def __init__(self):
            self.calls = 0

        async def get_aweme_comments(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("temporary")
            return {"items": [{"cid": "1", "text": "第一步怎么办？"}], "has_more": False}

    api = FakeAPI()
    collector = CommentsCollector(api, retry_delay=0, max_retries=3)
    result = asyncio.run(collector.collect("100"))

    assert api.calls == 2
    assert result[0]["cid"] == "1"
    assert collector.last_error == ""


def test_profile_collection_paginates_and_deduplicates_before_ranking():
    class FakeAPI:
        async def get_user_post(self, sec_uid, max_cursor=0, count=20):
            if max_cursor == 0:
                return {
                    "items": [{"aweme_id": "1"}, {"aweme_id": "2"}],
                    "has_more": True,
                    "max_cursor": 10,
                }
            return {
                "items": [{"aweme_id": "2"}, {"aweme_id": "3"}],
                "has_more": False,
                "max_cursor": 10,
            }

    items = asyncio.run(collect_profile_pages(FakeAPI(), "uid", scan_pages=5))

    assert [item["aweme_id"] for item in items] == ["1", "2", "3"]


def test_weak_only_report_labels_packages_as_exploratory():
    signal = _grounded_signal(level="weak")
    package = _package(
        evidence=signal.evidence,
        evidence_refs=signal.evidence_refs,
        confidence_level="exploratory",
    )
    markdown = render_topic_packages_markdown(
        source_url="https://example.com",
        resolved_url="https://example.com/user",
        sec_uid="uid",
        videos=[],
        pain_signals=[signal],
        scorecards=[],
        topic_packages=[package],
    )

    assert "探索性选题" in markdown
    assert "可直接使用的选题包" not in markdown


def test_offline_quality_gate_rejects_ungrounded_or_unsafe_packages():
    signal = _grounded_signal()
    package = _package()
    result = evaluate_topic_run(
        pain_signals=[signal.to_dict()],
        topic_packages=[package.to_dict()],
    )

    assert result["passed"] is False
    assert result["metrics"]["grounded_evidence_rate"] == 0.0
    assert result["metrics"]["unsafe_instruction_count"] >= 1
    assert "all_evidence_grounded" in result["failed_checks"]
    assert any("evidence_refs" in item for item in result["recommendations"])

    markdown = render_topic_packages_markdown(
        source_url="https://example.com",
        resolved_url="https://example.com/user",
        sec_uid="uid",
        videos=[],
        pain_signals=[signal],
        scorecards=[],
        topic_packages=[package],
    )
    assert "质量修复建议" in markdown


def test_grounded_evidence_normalizes_whitespace_in_text_and_reference():
    signal = _grounded_signal()
    signal.evidence = ["标题里有  两个空格"]
    signal.evidence_refs[0]["text"] = "标题里有  两个空格"
    raw = json.dumps(
        {
            "topic_packages": [
                {
                    "brief_title": "空格归一化检查",
                    "topic": "检查证据",
                    "pain_point": signal.pain_point,
                    "evidence": ["标题里有 两个空格"],
                    "recommended_angle": "解释判断方法",
                }
            ]
        },
        ensure_ascii=False,
    )

    package = normalize_llm_topic_packages(raw, [signal])[0]
    result = evaluate_topic_run(
        pain_signals=[signal.to_dict()],
        topic_packages=[package.to_dict()],
    )

    assert package.evidence == ["标题里有 两个空格"]
    assert package.evidence_refs[0]["text"] == package.evidence[0]
    assert result["checks"]["all_evidence_grounded"] is True


def test_audit_limits_exploratory_duplicate_pains_and_compacts_titles():
    signal = _grounded_signal(level="weak")
    packages = [
        _package(
            brief_title="这是一个把完整痛点和完整角度全部拼接在一起导致特别冗长而且无法直接用于封面的标题",
            evidence=signal.evidence,
            evidence_refs=signal.evidence_refs,
            fit_score=90,
        ),
        _package(
            brief_title="同一痛点的第二个探索性角度不应挤占有限报告位置",
            evidence=signal.evidence,
            evidence_refs=signal.evidence_refs,
            fit_score=80,
        ),
    ]

    audited = audit_topic_packages(packages, [signal], [])

    assert len(audited) == 1
    assert len(audited[0].brief_title) <= 32


def test_audit_makes_exploratory_title_hypothesis_transparent():
    signal = _grounded_signal(level="weak")
    signal.signal_type = "content_hypothesis"
    package = _package(
        target_audience="当前选题对应的目标用户",
        why_worth_shooting="适合直接拍摄",
        proof_needed="把原视频标题作为权威表达",
        production_suggestions=["适合口播", "用评论痛点开头"],
        evidence=signal.evidence,
        evidence_refs=signal.evidence_refs,
    )

    audited = audit_topic_packages([package], [signal], [])
    result = evaluate_topic_run(
        pain_signals=[signal.to_dict()],
        topic_packages=[audited[0].to_dict()],
    )

    assert "先补充评论、访谈或搜索反馈" in audited[0].why_worth_shooting
    assert "具体场景仍需进一步验证" in audited[0].target_audience
    assert "用评论痛点开头" not in audited[0].production_suggestions
    assert "用原视频标题中的问题开头" in audited[0].production_suggestions
    assert "不能把对标账号标题本身当作权威依据" in audited[0].proof_needed
    assert result["checks"]["audiences_are_specific"] is True


def test_quality_gate_can_require_llm_generator():
    signal = _grounded_signal()
    package = _package(
        evidence=signal.evidence,
        evidence_refs=signal.evidence_refs,
        metadata={"generated_by": "fallback_rules"},
    )

    failed = evaluate_topic_run(
        pain_signals=[signal.to_dict()],
        topic_packages=[package.to_dict()],
        required_generator="llm",
    )
    package.metadata["generated_by"] = "llm"
    passed = evaluate_topic_run(
        pain_signals=[signal.to_dict()],
        topic_packages=[package.to_dict()],
        required_generator="llm",
    )

    assert failed["checks"]["required_generator_used"] is False
    assert failed["metrics"]["generator_counts"] == {"fallback_rules": 1}
    assert passed["checks"]["required_generator_used"] is True
