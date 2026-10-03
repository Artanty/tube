# Tube

Video downloader for **Rutube**, **YouTube** and **Vimeo**, with a web UI.

```
back/   Flask API + yt-dlp downloader
web/    Angular 19 frontend
```

## Run it

Two terminals.

```bash
# 1. API on :5001
cd back
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py

# 2. UI on :4200
cd web
npm install
npm start
```

Open <http://localhost:4200>. Paste a link, press **Download**, watch the progress bar.
The Angular dev server proxies `/api` to `127.0.0.1:5001` (`web/proxy.conf.json`).

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/download` | `{url, format_id?}` → task, `202` |
| `GET` | `/api/tasks` | all known tasks |
| `GET` | `/api/task/<id>` | one task with progress |
| `POST` | `/api/formats` | `{url}` → available qualities |
| `GET` | `/api/files/<id>` | the finished file |
| `GET` | `/api/health` | storage probe |

Progress comes from yt-dlp `progress_hooks`: `percent`, `downloaded_human`, `total_human`,
`speed_human`, `eta`. The UI polls `/api/tasks` once a second while anything is running.

```bash
curl -X POST localhost:5001/api/download \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://rutube.ru/video/1dcc35d6575bc881dc2ece8c1efcdc35/"}'
```

## Config

`back/.env`, see `back/.env.example`.

| Var | Default | Notes |
| --- | --- | --- |
| `PORT` | `5001` | |
| `DOWNLOAD_FOLDER` | `./downloads` | one subdirectory per task |
| `MAX_WORKERS` | `3` | parallel downloads |
| `ALLOWED_ORIGINS` | `*` | CORS |
| `VIMEO_COOKIES_FILE` | `./vimeo-cookies.txt` | for private / age-gated Vimeo |
| `FFMPEG_LOCATION` | `/opt/homebrew/bin/ffmpeg` | required for merging |

## Notes

- Downloads run on a `ThreadPoolExecutor`, three at a time. **Task state is in memory**, so
  restarting the API loses progress for in-flight downloads. Completed files are picked back up
  from `downloads/<task_id>/` on startup, so finished files stay downloadable.
- Only `rutube.ru`, `rutube.com`, `youtube.com`, `youtu.be`, `vimeo.com` are accepted. The check
  is on the parsed hostname, so `rutube.ru.evil.net` and `youtube.com@evil.net` are rejected.
- Files are served by task id, never by filename, so there is no path-traversal surface.
- `ffmpeg` must be on the machine for the video+audio merge.

## Audio guarantee

YouTube stores video and audio as separate streams, so a naive download produces a silent file.
Three things prevent that here:

- The default selector prefers a real h264 + AAC mp4:
  `bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b`. The `[vcodec^=avc1]` filter matters —
  YouTube's highest-quality mp4 streams are AV1, which will not play in QuickTime.
- Picking a video-only `format_id` from the picker is fine. It is rewritten to
  `<id>+ba[ext=m4a]`, so the audio is merged rather than dropped. Rutube's formats are already muxed
  and pass through untouched.
- After the download, `ffprobe` checks the real file. **A file with no audio stream can never be
  reported as `completed`** — the task fails with an error instead. If you see a failed task, that
  guard fired, and the usual cause is a missing `ffmpeg`.

## History

This replaced an earlier hdrezka scraper that drove headless Chrome, base64-decoded an inline
`landing.main.initPlayer` blob and concatenated HLS segments with a hardcoded `TOTAL_SEGMENTS = 50`
(which silently truncated long videos) and `eval()` on scraped page JS. All three target platforms
are first-class in yt-dlp, so none of that is needed.