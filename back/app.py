"""Video downloader service for Rutube, YouTube and Vimeo."""

import json
import logging
import mimetypes
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import yt_dlp
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("app")

DOWNLOAD_DIR = os.path.abspath(os.getenv("DOWNLOAD_FOLDER", "./downloads"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "3"))
VIMEO_COOKIES = os.getenv("VIMEO_COOKIES_FILE", "./vimeo-cookies.txt")
FFMPEG_LOCATION = os.getenv("FFMPEG_LOCATION") or None

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
CORS(app, resources={r"/api/*": {"origins": os.getenv("ALLOWED_ORIGINS", "*")}})

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

ALLOWED_DOMAINS = ("rutube.ru", "rutube.com", "youtube.com", "youtu.be", "vimeo.com")

VIMEO_HEADERS = {
    "Referer": "https://vimeo.com/",
    "Origin": "https://vimeo.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}

TERMINAL = ("completed", "error")

# Rutube serves muxed streams, so bv*+ba/b is enough there. YouTube splits video and audio, and
# "best" is AV1/VP9 in a webm-adjacent container: AV1-in-mp4 will not play in QuickTime, and the
# highest streams 403 without the JS runtime YouTube increasingly demands. Prefer h264 + AAC in a
# real mp4 — 1080p, plays everywhere. The picker still offers AV1/VP9 if you want the resolution.
DEFAULT_SELECTOR = "bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b"

FFPROBE = "ffprobe" if not FFMPEG_LOCATION else os.path.join(os.path.dirname(FFMPEG_LOCATION), "ffprobe")


def domain_of(url):
    try:
        return (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


def is_url_allowed(url):
    host = domain_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def human_bytes(n):
    if not n or n < 0:
        return None
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < step:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= step
    return f"{n:.1f} PB"


def has_audio_stream(path):
    """True when the file on disk really contains an audio track.

    This is the guard against the silent-download bug: a selector that resolves to a video-only
    format will download cleanly, look successful, and produce a file that plays with no sound.
    """
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", path],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ffprobe unavailable, cannot verify audio: %s", exc)
        return None
    if out.returncode != 0:
        log.warning("ffprobe failed on %s: %s", path, out.stderr.strip()[:200])
        return None
    streams = json.loads(out.stdout or "{}").get("streams") or []
    return any(s.get("codec_type") == "audio" for s in streams)


def assert_has_audio(path):
    result = has_audio_stream(path)
    if result is False:
        raise RuntimeError(
            "download produced a video-only file with no audio track — "
            "the chosen format had no usable audio stream"
        )


def resolve_selector(url, format_id, opts):
    """Turn a chosen format_id into a selector that always ends up with sound.

    Rutube hands out muxed formats, so a combined format_id is used as-is. YouTube splits video and
    audio, so a video-only format_id gets an audio track appended for ffmpeg to merge. Verified
    per-platform rather than assumed, because appending audio to an already-muxed format would
    produce a file with two audio tracks. m4a/AAC is preferred over the best available audio because
    the output container is always mp4, and AAC-in-mp4 is the combination every player accepts.
    """
    if not format_id:
        return DEFAULT_SELECTOR

    probe = {k: v for k, v in opts.items() if k in ("http_headers", "extractor_args", "cookiefile")}
    probe.update({"quiet": True, "no_warnings": True, "skip_download": True, "logger": log})

    formats = None
    last_error = None
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(probe) as ydl:
                info = ydl.extract_info(url, download=False)
                if info.get("_type") == "playlist":
                    info = info["entries"][0]
            formats = info.get("formats") or []
            break
        except Exception as exc:
            last_error = exc
            log.warning("audio probe attempt %s for %s failed: %s", attempt + 1, format_id, exc)
            time.sleep(2)

    if formats is None:
        # Cannot tell whether this format is muxed, so leave it alone. assert_has_audio still
        # refuses to call a silent file a success, so a wrong guess here fails loudly, not quietly.
        log.error("giving up on audio probe for %s: %s", format_id, last_error)
        return format_id

    chosen = next((f for f in formats if f.get("format_id") == format_id), None)
    if chosen is None:
        return format_id
    if chosen.get("acodec") not in (None, "none"):
        return format_id
    has_audio_only = any(
        f.get("acodec") not in (None, "none") and f.get("vcodec") == "none" for f in formats
    )
    if not has_audio_only:
        log.warning("%s is video-only and the video has no audio track at all", format_id)
        return format_id
    log.info("format %s is video-only, adding an audio track", format_id)
    return f"{format_id}+ba[ext=m4a]/{format_id}+ba"


class Task:
    def __init__(self, url, format_id=None):
        self.id = uuid.uuid4().hex
        self.url = url
        self.format_id = format_id
        self.status = "queued"
        self.error = None
        self.title = None
        self.duration = None
        self.thumbnail = None
        self.filename = None
        self.filesize = None
        self.downloaded_bytes = 0
        self.total_bytes = None
        self.speed = None
        self.eta = None
        self.phase = None
        self.created_at = time.time()
        self.finished_at = None
        self.parts = {}
        self.peak = 0.0
        self.lock = threading.RLock()

    def progress_hook(self, payload):
        name = payload.get("filename") or ""
        downloaded = payload.get("downloaded_bytes") or 0
        total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
        with self.lock:
            self.parts[name] = (downloaded, total or self.parts.get(name, (0, 0))[1])
            self.downloaded_bytes = sum(d for d, _ in self.parts.values())
            totals = [t for _, t in self.parts.values() if t]
            self.total_bytes = sum(totals) if totals else None
            if payload.get("status") == "downloading":
                self.status = "downloading"
                self.phase = None
                self.speed = payload.get("speed")
                eta = payload.get("eta")
                self.eta = int(eta) if eta is not None else None
            elif payload.get("status") == "finished":
                self.status = "downloading"
                self.phase = "processing"

    def percent(self):
        if self.status == "completed":
            return 100.0
        with self.lock:
            if self.total_bytes:
                raw = min(self.downloaded_bytes / self.total_bytes * 100, 99.0)
                self.peak = max(self.peak, raw)
                return round(self.peak, 1)
        return round(self.peak, 1)

    def to_dict(self):
        with self.lock:
            return {
                "id": self.id,
                "url": self.url,
                "format_id": self.format_id,
                "status": self.status,
                "phase": self.phase,
                "error": self.error,
                "title": self.title,
                "duration": self.duration,
                "thumbnail": self.thumbnail,
                "filename": self.filename,
                "filesize": self.filesize,
                "downloaded_bytes": self.downloaded_bytes,
                "total_bytes": self.total_bytes,
                "downloaded_human": human_bytes(self.downloaded_bytes),
                "total_human": human_bytes(self.total_bytes),
                "speed": self.speed,
                "speed_human": f"{human_bytes(self.speed)}/s" if self.speed else None,
                "eta": self.eta,
                "percent": self.percent(),
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "finished": self.status in TERMINAL,
                "file_url": f"/api/files/{self.id}" if self.status == "completed" else None,
            }

    def run(self):
        output_dir = os.path.join(DOWNLOAD_DIR, self.id)
        os.makedirs(output_dir, exist_ok=True)
        opts = {
            "outtmpl": os.path.join(output_dir, "%(title).120B [%(id)s].%(ext)s"),
            "progress_hooks": [self.progress_hook],
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "logger": log,
        }
        if FFMPEG_LOCATION:
            opts["ffmpeg_location"] = FFMPEG_LOCATION

        host = domain_of(self.url)
        if "vimeo" in host:
            opts["http_headers"] = VIMEO_HEADERS
            opts["extractor_args"] = {"vimeo": {"skip_dash": True}}
            if os.path.exists(VIMEO_COOKIES):
                opts["cookiefile"] = VIMEO_COOKIES
        opts["format"] = resolve_selector(self.url, self.format_id, opts)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                if info.get("_type") == "playlist":
                    info = info["entries"][0]
                self.title = info.get("title")
                self.duration = info.get("duration")
                self.thumbnail = info.get("thumbnail")
                path = ydl.prepare_filename(info)
                candidates = [path]
                for ext in (".mp4", ".webm", ".mkv", ".mov"):
                    candidates.append(path.rsplit(".", 1)[0] + ext if "." in os.path.basename(path) else path + ext)
                final = next((c for c in candidates if os.path.exists(c)), None)
                if final is None:
                    raise RuntimeError("download finished but no output file was produced")
                self.filename = os.path.basename(final)
                self.filesize = os.path.getsize(final)
                self.phase = "checking"
                assert_has_audio(final)
                self.status = "completed"
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)
            log.exception("download failed for %s", self.url)
        finally:
            self.finished_at = time.time()
            if self.status == "completed":
                self.phase = None


class TaskStore:
    def __init__(self, workers):
        self._tasks = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dl")

    def submit(self, task):
        with self._lock:
            self._tasks[task.id] = task
        self._pool.submit(task.run)
        return task

    def get(self, task_id):
        with self._lock:
            return self._tasks.get(task_id)

    def all(self):
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def restore(self):
        for name in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, name)
            if not os.path.isdir(path) or not re.fullmatch(r"[0-9a-f]{32}", name):
                continue
            entries = [f for f in os.listdir(path) if not f.startswith(".")]
            if not entries:
                continue
            filename = max(entries, key=lambda f: os.path.getsize(os.path.join(path, f)))
            full = os.path.join(path, filename)
            task = Task("restored://" + name)
            task.id = name
            task.status = "completed"
            task.filename = filename
            task.title = filename
            task.filesize = os.path.getsize(full)
            task.created_at = task.finished_at = os.path.getmtime(full)
            with self._lock:
                self._tasks.setdefault(name, task)
            log.info("restored %s (%s)", name, filename)


store = TaskStore(MAX_WORKERS)
store.restore()


def body():
    return request.get_json(silent=True) or {}


def validate_url(url):
    if not url:
        return "URL is required"
    if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
        return "URL must start with http:// or https://"
    if not is_url_allowed(url):
        return f"Unsupported domain. Allowed: {', '.join(ALLOWED_DOMAINS)}"
    return None


@app.get("/api/health")
def health():
    writable = os.access(DOWNLOAD_DIR, os.W_OK)
    return jsonify(
        {
            "status": "ok" if writable else "degraded",
            "storage_writable": writable,
            "download_dir": DOWNLOAD_DIR,
            "workers": MAX_WORKERS,
            "active": sum(1 for t in store.all() if t.status not in TERMINAL),
        }
    ), 200 if writable else 503


@app.post("/api/download")
def start_download():
    data = body()
    url = (data.get("url") or "").strip()
    problem = validate_url(url)
    if problem:
        return jsonify({"error": problem}), 400
    task = store.submit(Task(url, data.get("format_id")))
    log.info("queued %s for %s", task.id, url)
    return jsonify(task.to_dict()), 202


@app.get("/api/tasks")
def list_tasks():
    return jsonify({"tasks": [t.to_dict() for t in store.all()]}), 200


@app.get("/api/task/<task_id>")
def get_task(task_id):
    task = store.get(task_id)
    if task is None:
        return jsonify({"error": "Unknown task"}), 404
    return jsonify(task.to_dict()), 200


@app.post("/api/formats")
def list_formats():
    data = body()
    url = (data.get("url") or "").strip()
    problem = validate_url(url)
    if problem:
        return jsonify({"error": problem}), 400

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "logger": log}
    host = domain_of(url)
    if "vimeo" in host:
        opts["http_headers"] = VIMEO_HEADERS
        opts["extractor_args"] = {"vimeo": {"skip_dash": True}}
        if os.path.exists(VIMEO_COOKIES):
            opts["cookiefile"] = VIMEO_COOKIES

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist":
                info = info["entries"][0]
    except Exception as exc:
        log.warning("format probe failed for %s: %s", url, exc)
        return jsonify({"error": str(exc)}), 422

    formats = []
    for f in info.get("formats") or []:
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue
        formats.append(
            {
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "resolution": f.get("resolution") or (f"{f['width']}x{f['height']}" if f.get("width") else None),
                "height": f.get("height"),
                "fps": f.get("fps"),
                "vcodec": f.get("vcodec"),
                "acodec": f.get("acodec"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "filesize_human": human_bytes(f.get("filesize") or f.get("filesize_approx")),
                "note": f.get("format_note"),
                "has_video": f.get("vcodec") not in (None, "none"),
                "has_audio": f.get("acodec") not in (None, "none"),
            }
        )
    formats.sort(key=lambda f: (f["height"] or 0, f["filesize"] or 0), reverse=True)

    return jsonify(
        {
            "video": {
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "uploader": info.get("uploader"),
            },
            "formats": formats,
        }
    ), 200


@app.get("/api/files/<task_id>")
def get_file(task_id):
    task = store.get(task_id)
    if task is None or task.status != "completed" or not task.filename:
        return jsonify({"error": "File not available"}), 404
    path = os.path.join(DOWNLOAD_DIR, task_id, task.filename)
    if not os.path.isfile(path):
        return jsonify({"error": "File missing on disk"}), 404
    mimetype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return send_file(path, mimetype=mimetype, as_attachment=True, download_name=task.filename)


if __name__ == "__main__":
    log.info("download dir: %s", DOWNLOAD_DIR)
    log.info("workers: %s  domains: %s", MAX_WORKERS, ", ".join(ALLOWED_DOMAINS))
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5001")),
        debug=os.getenv("DEBUG", "false").lower() == "true",
        threaded=True,
    )