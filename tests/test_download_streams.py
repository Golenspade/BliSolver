"""Real yt-dlp against loopback HTTP; no provider cookies, external media, or ASR."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from blisolver.config import Settings
from blisolver.subtitles import ydl_opts


@pytest.fixture
def local_audio():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    payload = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/sample.wav":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


_DOWNLOAD = """
import json
import sys
from pathlib import Path
from blisolver.config import Settings
from blisolver.resolve import Canonical
from blisolver.transcribe import download_audio

settings = Settings(cache_dir=Path(sys.argv[2]), aria2c_path=sys.argv[3] or None)
# This calls the shared media stage directly; no provider URL resolution or authentication.
canonical = Canonical(sys.argv[4], 'local-audio', 1, sys.argv[1])
if settings.aria2c_path:
    # Bilibili's real cookies are unnecessary for a generated local WAV.
    settings.sessdata = 'test-only-placeholder'
try:
    path = download_audio(canonical, settings)
    print(json.dumps({'ok': True, 'path': str(path), 'size': path.stat().st_size}))
except Exception as exc:
    print(json.dumps({'ok': False, 'error_type': type(exc).__name__}))
"""


def _download(url: str, cache: Path, aria2c: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    return subprocess.run(
        [sys.executable, "-c", _DOWNLOAD, url, str(cache), aria2c,
         "bilibili.com" if aria2c else "youtube.com"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, check=True, timeout=30,
    )


def test_real_native_audio_download_keeps_result_json_clean(tmp_path: Path, local_audio) -> None:
    origin, payload = local_audio
    result = _download(f"{origin}/sample.wav", tmp_path / "cache")
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert Path(envelope["path"]).read_bytes() == payload
    assert envelope["size"] == len(payload)
    assert "[download]" in result.stderr
    assert "[download]" not in result.stdout


def test_real_download_failure_keeps_result_json_clean(tmp_path: Path, local_audio) -> None:
    origin, _ = local_audio
    result = _download(f"{origin}/missing.wav", tmp_path / "cache")
    assert json.loads(result.stdout) == {"ok": False, "error_type": "DownloadError"}
    assert "ERROR" in result.stderr
    assert "404" in result.stderr


def test_external_downloader_is_explicitly_directed_to_stderr() -> None:
    opts = ydl_opts(Settings(aria2c_path="/test/aria2c"))
    assert "--stderr=true" in opts["external_downloader_args"]["aria2c"]


@pytest.mark.skipif(os.name == "nt", reason="executable stand-in uses a POSIX shebang")
@pytest.mark.parametrize("failure", [False, True])
def test_external_downloader_console_stays_out_of_result_json(
    tmp_path: Path, local_audio, failure: bool,
) -> None:
    origin, payload = local_audio
    # Exercise yt-dlp's actual external-downloader subprocess dispatch even on machines without
    # aria2c installed. The stand-in models aria2c's documented --stderr switch and downloads
    # the same loopback WAV. No yt-dlp functions or process streams are monkeypatched.
    executable = tmp_path / "aria2c"
    executable.write_text(
        f"#!{sys.executable}\nfailure = {failure!r}\n" + """
import sys
import urllib.request
from pathlib import Path

if '-v' in sys.argv:
    print('aria2 version 1.37.0')
    raise SystemExit(0)
stream = sys.stderr if '--stderr=true' in sys.argv else sys.stdout
print('external-downloader-console', file=stream, flush=True)
Path(__file__).with_name('ran').touch()
if failure:
    raise SystemExit(2)
destination = Path(sys.argv[sys.argv.index('--dir') + 1])
destination /= sys.argv[sys.argv.index('--out') + 1]
url = sys.argv[sys.argv.index('--') + 1]
with urllib.request.urlopen(url, timeout=5) as response:
    destination.write_bytes(response.read())
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    result = _download(f"{origin}/sample.wav", tmp_path / "cache", str(executable))
    envelope = json.loads(result.stdout)
    assert (tmp_path / "ran").is_file()
    if failure:
        assert envelope == {"ok": False, "error_type": "DownloadError"}
        assert "external-downloader-console" in result.stderr
    else:
        assert envelope["ok"] is True, result.stderr
        assert Path(envelope["path"]).read_bytes() == payload
        # yt-dlp captures aria2c's stderr and forwards it only when the child fails.
        assert "[download]" in result.stderr
