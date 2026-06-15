from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_mentions_top20_and_no_transcription_step():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Top20" in readme
    assert "不下载视频、不转写视频" in readme


def test_project_does_not_ship_env_secret():
    assert not (ROOT / ".env").exists()


def test_readme_documents_dev_test_flow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'pip install -e ".[dev]"' in readme
    assert "python -m pytest -q" in readme
    assert "python -m compileall -q src tests" in readme


def test_pyproject_declares_supported_python_classifiers():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"Programming Language :: Python :: 3"' in pyproject
    assert '"Programming Language :: Python :: 3.10"' in pyproject
    assert '"Programming Language :: Python :: 3.11"' in pyproject
    assert '"Programming Language :: Python :: 3.12"' in pyproject


def test_ci_workflow_runs_tests_and_compile_check():
    workflow = ROOT / ".github" / "workflows" / "ci.yml"

    assert workflow.exists()
    content = workflow.read_text(encoding="utf-8")
    assert "python-version" in content
    assert 'pip install -e ".[dev]"' in content
    assert "python -m pytest -q" in content
    assert "python -m compileall -q src tests" in content
    assert "playwright install" not in content
