from pathlib import Path

import blisolver.config as config
from blisolver.config import Settings, find_js_runtime


def test_load_reads_blisolver_env_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("BLISOLVER_COOKIES_BROWSER", "chrome")
    monkeypatch.setenv("BLISOLVER_COOKIES_PROFILE", "Default")
    monkeypatch.setenv("BLISOLVER_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("BLISOLVER_OUT_DIR", str(tmp_path / "o"))
    monkeypatch.setenv("BILI_COOKIES_BROWSER", "firefox")  # old key must be ignored
    s = Settings.load()
    assert s.cookies_browser == "chrome"
    assert s.cookies_profile == "Default"
    assert s.cache_dir == Path(tmp_path / "c")
    assert s.out_dir == Path(tmp_path / "o")


def test_chunk_window_s_default_is_60_for_minute_alignment():
    # 60s (the fallback wall-clock chunk width when frames don't dictate boundaries) so frameless
    # bundles bucket on clean minute marks — [0,60),[60,120)... — easier to reference by minute.
    assert Settings().chunk_window_s == 60.0


def test_danmaku_window_s_default_is_15_and_env_overridable(monkeypatch):
    # 15s: the empirically-validated danmaku bucket width (one crowd "beat" per window),
    # decoupled from the frame/transcript chunk cadence.
    monkeypatch.delenv("BLISOLVER_DANMAKU_WINDOW_S", raising=False)
    assert Settings.load().danmaku_window_s == 15.0
    monkeypatch.setenv("BLISOLVER_DANMAKU_WINDOW_S", "20")
    assert Settings.load().danmaku_window_s == 20.0


def test_danmaku_md_cap_default_is_15_and_env_overridable(monkeypatch):
    monkeypatch.delenv("BLISOLVER_DANMAKU_MD_CAP", raising=False)
    assert Settings.load().danmaku_md_cap == 15
    monkeypatch.setenv("BLISOLVER_DANMAKU_MD_CAP", "25")
    assert Settings.load().danmaku_md_cap == 25


def test_youtube_cookies_defaults_off(monkeypatch):
    monkeypatch.delenv("BLISOLVER_YT_COOKIES", raising=False)
    assert Settings.load().youtube_cookies is False


def test_youtube_cookies_opt_in_via_env(monkeypatch):
    for val in ("1", "true", "YES", "on"):
        monkeypatch.setenv("BLISOLVER_YT_COOKIES", val)
        assert Settings.load().youtube_cookies is True, val
    monkeypatch.setenv("BLISOLVER_YT_COOKIES", "0")
    assert Settings.load().youtube_cookies is False


# --- issue #5: JS runtime detection ------------------------------------------------------------


def _no_winget(monkeypatch):
    # Neutralize the winget glob fallback so tests only exercise the intended path.
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def test_find_js_runtime_prefers_deno_on_path(monkeypatch):
    found = {"deno": "X/deno", "node": "X/node"}
    monkeypatch.setattr(config.shutil, "which", lambda n: found.get(n))
    assert find_js_runtime() == ("deno", "X/deno")


def test_find_js_runtime_falls_back_to_node(monkeypatch, tmp_path):
    monkeypatch.setattr(config.shutil, "which", lambda n: "X/node" if n == "node" else None)
    monkeypatch.setenv("DENO_INSTALL", str(tmp_path / "empty"))  # no bin/deno.exe here
    _no_winget(monkeypatch)
    assert find_js_runtime() == ("node", "X/node")


def test_find_js_runtime_finds_deno_via_scripted_install_dir(monkeypatch, tmp_path):
    # The deno.land installer drops deno.exe in ~/.deno/bin and touches neither this process's
    # PATH nor winget, so the explicit install dir is the only way to find it.
    deno_root = tmp_path / ".deno"
    (deno_root / "bin").mkdir(parents=True)
    # The deno.land installer drops the binary in ~/.deno/bin; on Windows it's deno.exe, on
    # macOS/Linux just `deno`. find_js_runtime builds the path with the same os.name check.
    exe_name = "deno.exe" if config.os.name == "nt" else "deno"
    exe = deno_root / "bin" / exe_name
    exe.write_text("")
    monkeypatch.setattr(config.shutil, "which", lambda n: None)
    monkeypatch.setenv("DENO_INSTALL", str(deno_root))
    _no_winget(monkeypatch)
    assert find_js_runtime() == ("deno", str(exe))


def test_find_js_runtime_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(config.shutil, "which", lambda n: None)
    monkeypatch.setenv("DENO_INSTALL", str(tmp_path / "empty"))
    _no_winget(monkeypatch)
    assert find_js_runtime() is None


from blisolver.config import AutoSubNet, Settings


def test_settings_has_youtube_auto_net_defaults():
    s = Settings()
    assert isinstance(s.youtube_auto, AutoSubNet)
    assert s.youtube_auto.min_cues == 5
    assert s.youtube_auto.coverage_min == 0.70
    assert s.youtube_auto.coverage_max == 1.10
    assert s.youtube_auto.cps_min == 0.5


def test_two_settings_do_not_share_one_youtube_auto_instance():
    # default_factory, not a shared mutable default
    assert Settings().youtube_auto is not Settings().youtube_auto
