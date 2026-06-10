from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_mentions_top20_and_no_transcription_step():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Top20" in readme
    assert "不下载视频、不转写视频" in readme


def test_project_does_not_ship_env_secret():
    assert not (ROOT / ".env").exists()
