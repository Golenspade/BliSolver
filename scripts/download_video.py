"""Download the original bilibili video (all parts) without running the ingest pipeline.

Reuses harvest's Settings + ydl_opts so auth (browser cookies / SESSDATA), Referer,
aria2c, and ffmpeg config match the project's normal download path.

Usage: .venv/bin/python scripts/download_video.py
"""

from __future__ import annotations

from pathlib import Path

import yt_dlp

from harvest.config import Settings
from harvest.subtitles import ydl_opts

BVID = "BV1QzwuzeEq1"
PARTS = 3
OUT_DIR = Path("out") / f"{BVID}-video"


def main() -> None:
    settings = Settings.load()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for part in range(1, PARTS + 1):
        url = f"https://www.bilibili.com/video/{BVID}/?p={part}"
        opts = ydl_opts(settings, skip_download=False)
        opts.pop("quiet", None)
        opts.pop("no_warnings", None)
        opts.update(
            {
                "format": "bv*+ba/b",
                "merge_output_format": "mp4",
                "outtmpl": str(OUT_DIR / f"{BVID}-p{part}.%(ext)s"),
            }
        )
        print(f"=== downloading part {part}/{PARTS}: {url} ===", flush=True)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    print("=== all parts done ===", flush=True)


if __name__ == "__main__":
    main()
