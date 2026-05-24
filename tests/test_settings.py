from pathlib import Path

from fileaxa_batch.core.settings import AppSettings, Mode


def test_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    s = AppSettings(
        download_dir=tmp_path / "dl",
        mode=Mode.API,
        free_timer_seconds=42,
        captcha_timeout_seconds=300,
    )
    s.save()
    loaded = AppSettings.load()
    assert loaded.download_dir == tmp_path / "dl"
    assert loaded.mode == Mode.API
    assert loaded.free_timer_seconds == 42
    assert loaded.captcha_timeout_seconds == 300


def test_load_missing_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    s = AppSettings.load()
    assert s.mode == Mode.ANONYMOUS
    assert s.free_timer_seconds == 25
    assert s.captcha_timeout_seconds == 120
    assert isinstance(s.download_dir, Path)


def test_load_corrupt_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = tmp_path / "fileaxa-batch" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{ not valid json")
    s = AppSettings.load()
    assert s.mode == Mode.ANONYMOUS
