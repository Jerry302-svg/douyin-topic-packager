from pathlib import Path

from douyin_topic_packager.diagnostics import diagnostics_has_errors, format_diagnostics, run_diagnostics


def test_run_diagnostics_reports_missing_cookie_and_optional_llm(tmp_path):
    checks = run_diagnostics(
        state_path=tmp_path / "missing_state.json",
        env={},
        python_version=(3, 10, 0),
        playwright_available=True,
    )

    names = {item["name"]: item for item in checks}

    assert names["python"]["status"] == "ok"
    assert names["cookie"]["status"] == "warn"
    assert names["llm"]["status"] == "warn"
    assert not diagnostics_has_errors(checks)


def test_format_diagnostics_is_human_readable(tmp_path):
    state = Path(tmp_path) / "state.json"
    state.write_text("{}", encoding="utf-8")
    checks = run_diagnostics(
        state_path=state,
        env={"LLM_PROVIDER": "minimax-cn", "LLM_MODEL": "MiniMax-M3", "LLM_API_KEY": "test"},
        python_version=(3, 12, 0),
        playwright_available=True,
    )
    text = format_diagnostics(checks)

    assert "Python" in text
    assert "Cookie" in text
    assert "LLM" in text
    assert "OK" in text
