import pytest

from douyin_topic_packager import __version__
from douyin_topic_packager.cli import main


def test_cli_version_prints_package_version(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["douyin-topic-packager", "--version"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    assert f"douyin-topic-packager {__version__}" in capsys.readouterr().out
