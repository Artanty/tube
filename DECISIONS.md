# DECISIONS.md

Archived threads: see `DECISIONS-archive.md`.

## 2026-10-02 — Cleanup `back/` + build Rutube/Youtube/Vimeo downloader with Angular UI

### Analysis of `back/` (26 files)

State on entry: not a git repo, so deletion is unrecoverable. Files dated May 2025, never run since.

**Four near-identical Flask+Celery apps** — `app1.py`, `app2-utube-ok.py`, `app3-vimeo.py`, `app4-rutube.py`
are progressive copies of one another (15 KB each). Only `app2-utube-ok.py` parses.
- `app3-vimeo.py:63` — missing comma after `'hdrezka-broadchurch.net': {`, SyntaxError.
- `app4-rutube.py:382` — f-string reuses `'formats'` inside a `"`-quoted f-string, SyntaxError on py<3.12.

**Legacy hdrezka scrapers — all obsolete for the target feature set.**
`main.py`, `main zh decoded url!.py`, `download.py`, `rz_expl.py`, `parsers.py`, `helpers.py`,
plus the prototype pair `app.py` + `tasks.py` + `config.py`.

These scrape `landing.main.initPlayer` from hdrezka's inline JS, base64-decode the `streams` field,
guess a segment count, download `.ts` segments and concat with ffmpeg. Reasons to drop:
1. Scope is youtube/vimeo/rutube — all three are first-class in `yt-dlp`. No Selenium needed.
2. `download.py:39`, `parsers.py:44` call `eval()` on scraped page JS — arbitrary code execution.
3. `download.py:96`, `rz_expl.py:35` hardcode `TOTAL_SEGMENTS = 50` — produces truncated files.
4. `main.py:24` builds a Selenium driver at import time and `main.py:51` references
   `TimeoutException` without importing it — NameError at runtime.
5. `tasks.py:18` builds `f"video_{streams}.mp4"` where `streams` is a dict — nonsense filename.

**Junk.** `dump.rdb` (redis dump), `cookies.txt` (empty yt-dlp skeleton), `video_download.log` (0 B),
`.env-1` (superseded by `.env`), `requirements+.txt` (partial, no yt-dlp).

**Worth keeping** (folded into the new `app.py`):
- `app2`'s yt-dlp core: per-domain config, `merge_output_format`, retries, ff concat.
- Vimeo needs `Referer`/`Origin` headers + `skip_dash` (app4) — bundled 1080p streams fail without it.
- `vimeo-cookies.txt` (363 KB) — real browser cookies, required for private/age-gated Vimeo.
- `/api/formats` and `/api/download/format` endpoints — format picker.
- `/health` with redis + storage probes.
- Path-traversal guard in `serve_file`.
- Celery task-id polling already fits a progress UI.

**Gap:** no progress reporting anywhere. `tasks.py:69-72` has a commented-out progress stub.
Fix: yt-dlp `progress_hooks` → Celery `update_state`.

### Deletion list (pending user confirmation)
Delete: `app1.py`, `app2-utube-ok.py`, `app3-vimeo.py`, `app4-rutube.py`, `app.py`,
`main.py`, `main zh decoded url!.py`, `download.py`, `rz_expl.py`, `parsers.py`,
`helpers.py`, `tasks.py`, `config.py`, `requirements+.txt`, `.env-1`, `cookies.txt`,
`dump.rdb`, `video_download.log`.
Keep: `downloads/` (contains one 200 MB test mp4 — flagged to user, not code), `vimeo-cookies.txt`.

### Decisions (user confirmed)
- Keep the 200 MB `downloads/…_output.mp4` test file.
- **Drop Redis + Celery.** Flask + `ThreadPoolExecutor`, in-memory task store. Rationale: one
  process to run instead of three; Redis is not even running. Accepted tradeoff: tasks are lost on
  restart. Mitigated by a `GET /api/tasks` list so the UI can re-hydrate after a reload.
- Backend stays in `back/`, entrypoint `back/app.py`.

### Design
`back/app.py` — single service. Progress via yt-dlp `progress_hooks` writing to the task store,
polled by the UI. Dependency drop: flask, flask-cors, python-dotenv, yt-dlp. Gone: celery, redis,
selenium, webdriver_manager, requests.

Endpoints:
- `POST /api/download` `{url, format_id?}` → `{task_id}` (202)
- `GET  /api/task/<id>` → status, percent, bytes, speed, eta, title, filename, error
- `GET  /api/tasks` → all known tasks (UI re-hydration after browser refresh)
- `POST /api/formats` `{url}` → available formats + title/duration/thumbnail
- `GET  /api/files/<id>` → the finished file
- `GET  /api/health` → storage probe

Notes:
- Domain allowlist fixes an SSRF hole in the old `is_url_allowed`, which used
  `allowed in netloc` — that admits `youtube.com.evil.net` and `http://youtube.com@evil.net`.
  New check: exact host or `.domain` suffix, http(s) only.
- Files are served by task id, not filename, so there is no traversal surface at all.
- Files land in `downloads/<task_id>/`; the pre-existing 200 MB file sits beside them, untouched.

### Plan
1. Delete the 18 dead files.
2. Rewrite `back/app.py`; consolidate `requirements.txt`, `.env`; add `.env.example`.
3. `back/.venv` with deps installed.
4. `web/` — Angular 19 standalone, signals: URL input → submit → progress bar → download link.
   `proxy.conf.json` forwards `/api` to :5001.
5. Verify: syntax check, route map, live format probe against a real Rutube URL, `ng build`.

### Progress
Done. All steps complete and verified.

**Cleanup** — deleted 18 dead files. `back/` is now `.env`, `.env.example`, `app.py`,
`requirements.txt`, `README.md`, `downloads/`, `vimeo-cookies.txt`. The 200 MB hdrezka test mp4
was kept as agreed. Removed the now-unused empty `segments/` and `in_progress/` dirs.

**Backend** (`back/app.py`, ~380 lines, single file) — Flask + `ThreadPoolExecutor(3)`,
deps now just flask, flask-cors, python-dotenv, yt-dlp. Dropped: celery, redis, selenium,
webdriver_manager, requests.

**Frontend** (`web/`) — Angular 19.2.7, standalone, signals, no zone-heavy state management.
`proxy.conf.json` forwards `/api` to :5001.

### Bugs found and fixed while verifying

1. **Deadlock, my own.** `to_dict()` held a non-reentrant `threading.Lock` then called
   `percent()`, which re-acquired it. Every `/api/tasks` poll wedged permanently. `threading.RLock`.
2. **`eta` was a raw float** — `190.09331809526785`. Now `int()`.
3. **Progress bar jittered backwards.** yt-dlp revises `total_bytes_estimate` as it discovers HLS
   content (observed 358 → 362 → 356 → 344 → 340 MB).
   Tried latching `max(total)` — fixed the jitter but over-reported: settled at 450 MB when the
   file is really 266 MB, so the bar stalled at ~59%. Correct fix: keep the *latest* estimate for
   `total_bytes` and clamp only the reported `percent` to a monotonic `peak`.
4. **`restrictfilenames: True` destroyed titles.** Cyrillic `Бывшие, 1 сезон, 1 серия` became
   `1_1`. Removed.
5. **`trim_file_name: 120` chopped the closing `]`** off the filename. Removed — `%(title).120B`
   already bounds the name (79 bytes of 255 for the test case).
6. **Completed files became unreachable after a restart.** A direct consequence of dropping
   Celery: the store is in-memory, so a restart orphaned 266 MB on disk. Added `TaskStore.restore()`,
   which re-adopts `downloads/<32-hex>/` directories at startup. In-flight progress is still lost —
   that tradeoff was accepted deliberately.

### Verification performed

- `is_url_allowed` — 9 cases pass, including the three the old `allowed in netloc` check admitted:
  `rutube.ru.evil.net`, `youtube.com@evil.net`, `127.0.0.1`.
- `validate_url` — rejects empty, schemeless, `javascript:`.
- Live `/api/formats` on real Rutube URL: title `Бывшие, 1 сезон, 1 серия`, 24 formats, 3075s.
- Full download of that URL at `default-691-0`: 100%, 266,711,289 bytes, monotonic percent across
  10 polls, total converging 427 → 269 MB.
- `ffmpeg -f null -` decoded the whole file with zero errors; `ffprobe` → h264 848x480 + aac,
  3074.920s, matching the API-reported 3075s.
- `/api/files/<id>` served 266,711,289 bytes as `video/mp4`; unknown id → 404.
- Restore-after-restart: relaunched the API, the completed task came back and served its file.
- `npm run build` clean, 229 kB initial, no warnings (removed an unused `DatePipe` import).
- Proxy path verified: `localhost:4200/api/health` reaches the API.
- Deleted my own test downloads afterwards; only the user's 200 MB file remains in `downloads/`.

### Not done / open
- Nothing committed — no git repo here, and per AGENTS.md no commit was requested. `web/` was
  scaffolded with `--skip-git`.
- The Angular build targets `dist/web` and is not wired into Flask; dev-server proxy is the
  supported path. Serving the built app from Flask would need a static route.
- `web/dist/` and `web/.angular/` are build artifacts, present but untracked (no repo).

---

## 2026-10-02 — YouTube downloads have no audio

Reported as "rutube is good, youtube is without sound". Reproduced and fixed.

### Diagnosis

Rutube serves **muxed** streams — every one of its 24 formats already carries video *and* audio, so
no merge runs. YouTube serves **split** streams: video-only (`vcodec` set, `acodec: none`) plus
separate audio-only (`251` opus, `140` m4a). Nothing merges them unless the format selector asks
for it. Three distinct defects, in order of severity:

1. **The picker offered video-only formats and pre-selected the worst one.** `app.component.ts`
   did `formats.find(f => f.height)`. Formats are sorted by height descending, and on YouTube the
   top entries are all video-only, so the UI pre-selected `315` (3840x2160 vp9, `acodec: none`).
   Submitting that downloads video only and the task reports **`completed`**. Reproduced:
   `format_id=134` produced a file with exactly one stream, `0,h264,video`.

2. **The default selector fails outright on high-res YouTube video.** `bv*+ba/b` resolves to
   `401` (3840x2160 AV1) + `251` (opus), and YouTube returns **`HTTP Error 403: Forbidden`** on
   format 401 — confirmed by direct download. So "best available" was not merely silent, it failed.
   yt-dlp also warns: *"No supported JavaScript runtime could be found… YouTube extraction without a
   JS runtime has been deprecated, and some formats may be missing."* (deno is not installed).

3. **`merge_output_format: 'mp4'` does not transcode.** Tested `243+251` (vp9/webm + opus/webm)
   with the container forced to mp4: yt-dlp produced an `.mp4` holding **vp9 + opus**. Valid enough
   for ffmpeg, but not a portable mp4 — QuickTime and many players refuse it, while my
   `mimetypes` guess still labels it `video/mp4`.

Note this is a defect I introduced. The legacy `app1.py` I deleted had exactly the right selector —
`'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b'` — and I replaced it with a naive `bv*+ba/b`. I flagged the
old file as duplication across four copies without noticing that one of them encoded a real fix.

### Fix
1. **Never emit a video-only file.** `resolve_selector()` probes the chosen `format_id`; if it has
   `acodec: none` and an audio-only stream exists, the selector becomes
   `format_id+ba[ext=m4a]/format_id+ba`. Rutube's muxed formats are left alone, so this cannot break
   them. AAC rather than the best audio, because the output container is always mp4.
2. **Prefer h264 + AAC in mp4.** Default is
   `bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b`.
   `ext=mp4` alone was not enough: YouTube's 4K mp4 streams are AV1, which will not play in
   QuickTime and 403 intermittently. The avc1 filter lands on 1080p h264, which plays everywhere.
3. **Verify the output before declaring success.** After download, `ffprobe` the real file and raise
   if it has no audio stream. This is the safety net: a silent file can never again be reported as
   `completed`, whatever a future selector does.
4. **UI**: `pickDefault()` prefers a muxed format over a video-only one, and the picker labels
   video-only entries `· +audio` so the merge is visible rather than surprising.

The legacy `app1.py` selector `bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b` was the right instinct and I
reinstated it, with `[vcodec^=avc1]` added — the original would also have picked the 4K AV1 stream.

### Verification

- `resolve_selector` — YouTube default → `bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b`;
  video-only `134` → `134+ba[ext=m4a]/134+ba`; Rutube muxed `default-206-0` → unchanged. Confirmed
  the auto-merge fires **only** for YouTube (1 log line across the whole run).
- **The originally-silent case**, `format_id=134`: now `h264 640x360 + aac`. Before: 1 stream.
- YouTube default path: `h264 1920x1080 + aac`, 268,281,285 B, 634.62s vs source 635s, no error.
- **The user's own file**, `sb3FJdRk-tI`: `h264 640x480 + aac`, audio decodes clean under
  `ffmpeg -map 0:a -f null -`. 640x480 is genuinely the best h264 this video has (checked every
  format — max is 640x480), so the avc1 filter is not costing resolution.
- Rutube regression after changing the shared default: `h264 + aac`, 80 MB, no error.
- `assert_has_audio` refuses the known-silent file and passes the known-good one.
- `npm run build` clean, 229 kB, no warnings.

### Two false alarms worth recording
- A Rutube download failed with *"The downloaded file is empty"*. Not a product bug: my own polling
  loop timed out and `rm -rf`'d the task directory **while the download was still running**, so every
  fragment write hit `No such file or directory`. Re-ran cleanly, passed. Lesson: only delete test
  task directories once the task reports a terminal state.
- `resolve_selector` returned bare `134` instead of `134+ba` on one run. The probe metadata request
  had been throttled by my own repeated calls, so the `except` silently fell back. Added a single
  retry (2 attempts, 2s apart) — the real fix for transient probe failure.

### State of the user's downloads
All 7 previously-downloaded YouTube files are video-only and cannot be repaired:
`Jane's Addiction - Just Because` and 6 `Bloodlines` playlist videos. They came through with
`format_id: null`, i.e. the broken default path, so nothing about their selection was intentional.
Rutube downloads (`Братья` 1 and 2) are fine. The re-downloaded `Jane's Addiction` with audio is at
`downloads/8459b0c0557c4c628e9d2eb0c78ed5e1/`.

Two things noticed but not fixed (outside this bug's scope):
- `restore()` sets a task's URL to `restored://<id>`, so restored tasks lose the original link and
  the UI's title link is dead for them.
- `noplaylist: True` does not stop an explicitly-playlist URL, so `Bloodlines` produced 6 files in
  one task directory and the task reports only one of them.

### Progress
Fixed and verified. Both servers need restarting to pick up the backend change.