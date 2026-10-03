# Tube — backend

Flask API that downloads from Rutube, YouTube and Vimeo with yt-dlp. See the
[root README](../README.md) for the full picture.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py          # http://127.0.0.1:5001
```

Requires `ffmpeg` on `PATH` (or set `FFMPEG_LOCATION`) to merge separate video and audio streams.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/download` | `{url, format_id?}` → task, `202` |
| `GET` | `/api/tasks` | all known tasks |
| `GET` | `/api/task/<id>` | one task with progress |
| `POST` | `/api/formats` | `{url}` → available qualities |
| `GET` | `/api/files/<id>` | the finished file |
| `GET` | `/api/health` | storage probe |

`format_id` is optional; omit it for the best available quality.

YouTube separates video and audio, so selection goes through `resolve_selector()`. It probes the
requested `format_id`; if that stream has no audio and the video has an audio-only companion, the
selector becomes `<id>+ba[ext=m4a]` so the two get merged. Muxed formats (Rutube) are returned
unchanged. The result is verified with `ffprobe` before the task is marked `completed` — a silent
file is always an error, never a success.

## Layout

Everything is in `app.py`. `TaskStore` owns a `ThreadPoolExecutor` and an in-memory dict of
`Task` objects; each `Task` installs a yt-dlp `progress_hooks` callback that updates its own
fields under a lock, and the UI polls them.

Completed downloads are restored from `downloads/<task_id>/` at startup so finished files stay
reachable across a restart. In-flight progress does not survive a restart.

Output filenames come from `%(title).120B [%(id)s].%(ext)s`, so Cyrillic and other non-ASCII titles
are preserved — do not set `restrictfilenames` or `trim_file_name`, they strip or cut them.

## Config

Copy `.env.example` to `.env`.

| Var | Default | Notes |
| --- | --- | --- |
| `HOST` / `PORT` | `127.0.0.1` / `5001` | |
| `DOWNLOAD_FOLDER` | `./downloads` | one subdirectory per task |
| `MAX_WORKERS` | `3` | parallel downloads |
| `ALLOWED_ORIGINS` | `*` | CORS |
| `VIMEO_COOKIES_FILE` | `./vimeo-cookies.txt` | private / age-gated Vimeo |
| `FFMPEG_LOCATION` | `/opt/homebrew/bin/ffmpeg` | |

`vimeo-cookies.txt` is a real Netscape cookie jar. It authenticates as whoever exported it — treat
it as a secret and do not commit it.