import pytest

from douyin_topic_packager import __version__
from douyin_topic_packager.cli import main


def test_cli_version_prints_package_version(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["douyin-topic-packager", "--version"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    assert f"douyin-topic-packager {__version__}" in capsys.readouterr().out


def test_cli_verify_run_exits_nonzero_on_failed_acceptance(monkeypatch, capsys):
    monkeypatch.setattr(
        "douyin_topic_packager.cli.verify_run_manifest",
        lambda path, *, require_quality_pass: {"passed": False, "errors": ["tampered"]},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["douyin-topic-packager", "verify-run", "--manifest", "run_manifest.json"],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    assert "tampered" in capsys.readouterr().out
