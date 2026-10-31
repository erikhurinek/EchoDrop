#!/usr/bin/env python3
"""Local EchoDrop API and static-file server.

The browser never receives the cookie. The API reads it from the local config
file and supplies it to yt-dlp only for the duration of a request.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import uuid
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("ECHODROP_CONFIG_DIR", Path.home() / ".config" / "echodrop"))
COOKIE_FILE = CONFIG_DIR / "cookie-header.txt"
MODE_FILE = CONFIG_DIR / "browser-cookie-mode"
BRAVE_COOKIE_DATABASES = [
    Path.home() / ".var" / "app" / "com.brave.Browser" / "config" / "BraveSoftware" / "Brave-Browser" / "Default" / "Cookies",
    Path.home() / ".config" / "BraveSoftware" / "Brave-Browser" / "Default" / "Cookies",
]
DOWNLOAD_DIR = Path(os.environ.get("ECHODROP_DOWNLOAD_DIR", Path.home() / "Downloads" / "Lectures")).expanduser()
PORT = int(os.environ.get("ECHODROP_PORT", "8787"))
USE_BRAVE_COOKIES = MODE_FILE.is_file() and MODE_FILE.read_text(encoding="utf-8").strip() == "brave"
DOWNLOAD_JOBS: dict[str, dict] = {}
DOWNLOAD_JOBS_LOCK = threading.Lock()


def read_cookie() -> str:
    try:
        return COOKIE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def yt_dlp_path() -> str | None:
    configured_path = os.environ.get("ECHODROP_YT_DLP")
    if configured_path:
        candidate = Path(configured_path).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    executable = shutil.which("yt-dlp")
    if executable:
        return executable
    return None


def cookie_args() -> list[str]:
    if USE_BRAVE_COOKIES:
        return ["--cookies-from-browser", "brave:Default"]
    cookie = read_cookie()
    if not cookie:
        return []
    return ["--add-header", f"Cookie:{cookie}"]


def run_yt_dlp(args: list[str], timeout: int = 120, progress_callback=None) -> tuple[str, str]:
    executable = yt_dlp_path()
    if not executable:
        raise RuntimeError("yt-dlp is not installed. Install it with: python3 -m pip install -U yt-dlp")
    command = [executable, *cookie_args(), *args]
    environment = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="echodrop-yt-dlp-") as temporary_home:
        if USE_BRAVE_COOKIES:
            brave_config = next((path.parent.parent.parent.parent for path in BRAVE_COOKIE_DATABASES if path.is_file()), None)
            if brave_config is None:
                raise RuntimeError("Flatpak Brave cookie database was not found")
            temporary_config = Path(temporary_home) / ".config"
            temporary_config.mkdir()
            (temporary_config / "BraveSoftware").symlink_to(brave_config / "BraveSoftware", target_is_directory=True)
            environment["HOME"] = temporary_home
        if progress_callback:
            process = subprocess.Popen(
                command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
            )
            stdout_lines = []
            assert process.stdout is not None
            for line in process.stdout:
                stdout_lines.append(line)
                match = re.search(r"\[download\]\s+([\d.]+)%.*?at\s+(\S+/s)", line)
                if match:
                    progress_callback(float(match.group(1)), match.group(2))
            stderr = process.stderr.read() if process.stderr else ""
            process.wait(timeout=timeout)
            completed = subprocess.CompletedProcess(command, process.returncode, "".join(stdout_lines), stderr)
        else:
            completed = subprocess.run(
                command, cwd=ROOT, env=environment, capture_output=True,
                text=True, timeout=timeout, check=False,
            )
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "yt-dlp exited with an error"
        raise RuntimeError(detail)
    return completed.stdout, completed.stderr


def media_duration(path: str) -> float | None:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def run_ffmpeg(command: list[str], duration: float | None, progress_callback=None) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        [*command[:-1], "-progress", "pipe:1", "-nostats", command[-1]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    progress: dict[str, str] = {}
    assert process.stdout is not None
    for line in process.stdout:
        key, _, value = line.strip().partition("=")
        if not key:
            continue
        progress[key] = value
        if key == "progress" and progress_callback:
            elapsed = int(progress.get("out_time_ms", "0")) / 1_000_000
            percent = min(100, elapsed / duration * 100) if duration and duration > 0 else None
            progress_callback(percent, progress.get("speed", ""))
    stderr = process.stderr.read() if process.stderr else ""
    process.wait()
    return subprocess.CompletedProcess(command, process.returncode, "", stderr)


def resolve_echo_lesson(url: str) -> dict:
    executable = yt_dlp_path()
    if not executable:
        raise RuntimeError("yt-dlp is not installed. Install it with: python3 -m pip install -U yt-dlp")
    # The yt-dlp launcher may be installed in ~/.local/bin without a sibling
    # Python executable. The server's interpreter is the environment where
    # the resolver dependency is installed.
    python_executable = sys.executable
    helper = ROOT / "echo_resolver.py"
    environment = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="echodrop-yt-dlp-") as temporary_home:
        brave_config = next((path.parent.parent.parent.parent for path in BRAVE_COOKIE_DATABASES if path.is_file()), None)
        if USE_BRAVE_COOKIES and brave_config is None:
            raise RuntimeError("Flatpak Brave cookie database was not found")
        if USE_BRAVE_COOKIES:
            temporary_config = Path(temporary_home) / ".config"
            temporary_config.mkdir()
            (temporary_config / "BraveSoftware").symlink_to(brave_config / "BraveSoftware", target_is_directory=True)
            environment["HOME"] = temporary_home
        completed = subprocess.run(
            [python_executable, str(helper), url],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "Echo360 lesson could not be resolved"
        raise RuntimeError(detail)
    return json.loads(completed.stdout)


class EchoDropHandler(BaseHTTPRequestHandler):
    server_version = "EchoDrop/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[echodrop] {format % args}")

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise ValueError("Request body is too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json({
                "configured": USE_BRAVE_COOKIES or bool(read_cookie()),
                "ytDlpInstalled": bool(yt_dlp_path()),
                "ytDlpPath": yt_dlp_path(),
                "downloadDir": str(DOWNLOAD_DIR),
                "braveCookiesDetected": any(path.is_file() for path in BRAVE_COOKIE_DATABASES),
                "useBraveCookies": USE_BRAVE_COOKIES,
            })
            return
        if parsed.path == "/api/jobs":
            with DOWNLOAD_JOBS_LOCK:
                self.send_json({"jobs": list(DOWNLOAD_JOBS.values())})
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            data = self.read_json()
            if parsed.path == "/api/config":
                self.save_config(data)
            elif parsed.path == "/api/resolve":
                self.resolve_recording(data)
            elif parsed.path == "/api/resolve-section":
                self.resolve_section(data)
            elif parsed.path == "/api/resolve-lesson":
                self.resolve_lesson(data)
            elif parsed.path == "/api/download":
                self.start_download(data)
            else:
                self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)

    def save_config(self, data: dict) -> None:
        global DOWNLOAD_DIR, USE_BRAVE_COOKIES
        cookie = data.get("cookie", "").strip()
        download_dir = data.get("downloadDir", str(DOWNLOAD_DIR)).strip()
        use_brave_cookies = bool(data.get("useBraveCookies", False))
        if use_brave_cookies and not any(path.is_file() for path in BRAVE_COOKIE_DATABASES):
            raise ValueError("Flatpak Brave Cookies database was not found")
        if cookie and ("\n" in cookie or "\r" in cookie):
            raise ValueError("Cookie header must be a single line")
        CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        COOKIE_FILE.write_text(cookie, encoding="utf-8")
        COOKIE_FILE.chmod(0o600)
        DOWNLOAD_DIR = Path(download_dir).expanduser()
        USE_BRAVE_COOKIES = use_brave_cookies
        MODE_FILE.write_text("brave" if USE_BRAVE_COOKIES else "header", encoding="utf-8")
        MODE_FILE.chmod(0o600)
        self.send_json({"ok": True, "configured": USE_BRAVE_COOKIES or bool(cookie), "downloadDir": str(DOWNLOAD_DIR)})

    def resolve_recording(self, data: dict) -> None:
        url = str(data.get("url", "")).strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Enter a valid http(s) Echo360 recording URL")
        if "echo360.org" in parsed.netloc:
            info = resolve_echo_lesson(url)
        else:
            output, _ = run_yt_dlp(["--dump-single-json", "--no-warnings", "--skip-download", url])
            info = json.loads(output)
        if "formats" in info and info["formats"] and str(info["formats"][0].get("formatId", "")).startswith("echo-"):
            self.send_json(info)
            return
        formats = [
            {
                "formatId": item.get("format_id"),
                "type": "Audio" if item.get("vcodec") == "none" else "Video",
                "quality": item.get("format_note") or item.get("resolution") or item.get("acodec") or "Source",
                "size": item.get("filesize") or item.get("filesize_approx"),
                "url": item.get("url"),
            }
            for item in info.get("formats", [])
            if item.get("url") and (item.get("vcodec") == "none" or item.get("acodec") == "none" or item.get("vcodec"))
        ]
        self.send_json({"title": info.get("title", "Echo360 recording"), "duration": info.get("duration"), "thumbnail": info.get("thumbnail"), "formats": formats})

    def resolve_section(self, data: dict) -> None:
        url = str(data.get("url", "")).strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or "echo360.org" not in parsed.netloc or not parsed.path.startswith("/section/"):
            raise ValueError("Enter an Echo360 section URL such as https://echo360.org.uk/section/.../home")
        info = resolve_echo_lesson(url)
        self.send_json(info)

    def resolve_lesson(self, data: dict) -> None:
        url = str(data.get("url") or data.get("lessonUrl") or "").strip()
        parsed = urllib.parse.urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme not in {"http", "https"}
            or "echo360.org" not in parsed.netloc
            or "lesson" not in path_parts
            or path_parts.index("lesson") + 1 >= len(path_parts)
        ):
            raise ValueError("A valid Echo360 lesson URL is required")
        self.send_json(resolve_echo_lesson(url))

    def start_download(self, data: dict) -> None:
        url = str(data.get("url", "")).strip()
        if not url:
            raise ValueError("A recording URL is required")
        DOWNLOAD_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
        title = str(data.get("title", "")).strip() or "echo360-recording"
        safe_title = "".join(character if character.isalnum() or character in " ._-" else "_" for character in title).strip(" .")
        output_path = str(DOWNLOAD_DIR / f"{safe_title or 'echo360-recording'}.mp4")
        video_url = str(data.get("videoUrl", "")).strip()
        audio_url = str(data.get("audioUrl", "")).strip()
        if video_url or audio_url:
            thread_args = (video_url, audio_url, output_path)
        else:
            video_id = str(data.get("videoFormatId", "")).strip()
            audio_id = str(data.get("audioFormatId", "")).strip()
            format_selector = f"{video_id}+{audio_id}" if video_id and audio_id and video_id != audio_id else video_id or audio_id
            args = ["--newline", "--no-warnings", "--merge-output-format", "mp4"]
            if format_selector:
                args.extend(["-f", format_selector])
            args.extend(["-o", output_path, url])
            thread_args = (args,)
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "title": title, "status": "Starting", "path": output_path}
        with DOWNLOAD_JOBS_LOCK:
            DOWNLOAD_JOBS[job_id] = job
        thread = threading.Thread(target=self.download_worker, args=(*thread_args, job_id), daemon=True)
        thread.start()
        self.send_json({"accepted": True, "message": "Download started", "job": job})

    @staticmethod
    def download_worker(*args: object) -> None:
        job_id = str(args[-1])
        download_args = args[:-1]
        def update(status: str) -> None:
            with DOWNLOAD_JOBS_LOCK:
                if job_id in DOWNLOAD_JOBS:
                    DOWNLOAD_JOBS[job_id]["status"] = status
        def update_progress(percent: float | None, speed: str, phase: str) -> None:
            with DOWNLOAD_JOBS_LOCK:
                if job_id in DOWNLOAD_JOBS:
                    DOWNLOAD_JOBS[job_id]["status"] = phase
                    if percent is not None:
                        DOWNLOAD_JOBS[job_id]["progress"] = round(percent, 1)
                    if speed:
                        DOWNLOAD_JOBS[job_id]["speed"] = speed
        try:
            update("Downloading")
            if len(download_args) == 1:
                run_yt_dlp(download_args[0], timeout=24 * 60 * 60, progress_callback=lambda percent, speed: update_progress(percent, speed, "Downloading"))
                update("Completed")
                return
            video_url, audio_url, output_path = download_args
            with tempfile.TemporaryDirectory(prefix="echodrop-media-") as directory:
                inputs = []
                if video_url:
                    video_path = str(Path(directory) / "video.%(ext)s")
                    run_yt_dlp(["--newline", "--no-warnings", "-o", video_path, str(video_url)], timeout=24 * 60 * 60, progress_callback=lambda percent, speed: update_progress(percent, speed, "Video"))
                    video_file = next(Path(directory).glob("video.*"))
                    inputs.extend(["-i", str(video_file)])
                if audio_url:
                    audio_path = str(Path(directory) / "audio.%(ext)s")
                    run_yt_dlp(["--newline", "--no-warnings", "-o", audio_path, str(audio_url)], timeout=24 * 60 * 60, progress_callback=lambda percent, speed: update_progress(percent, speed, "Audio"))
                    audio_file = next(Path(directory).glob("audio.*"))
                    inputs.extend(["-i", str(audio_file)])
                destination = str(output_path)
                if video_url:
                    command = ["ffmpeg", "-y", *inputs, "-map", "0:v:0"]
                    if audio_url:
                        command.extend(["-map", "1:a:0"])
                    command.extend(["-c:v", "copy", "-c:a", "aac", destination])
                else:
                    command = ["ffmpeg", "-y", *inputs, "-map", "0:a:0", "-vn", "-c:a", "aac", destination]
                update("Merging")
                duration = media_duration(str(video_file if video_url else audio_file))
                completed = run_ffmpeg(
                    command,
                    duration,
                    lambda percent, speed: update_progress(percent, speed, "Merging"),
                )
                if completed.returncode:
                    raise RuntimeError(completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "ffmpeg merge failed")
            update("Completed")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            print(f"[echodrop] download failed: {error}")
            update(f"Failed: {error}")

    def serve_static(self, path: str) -> None:
        relative = urllib.parse.unquote(path.lstrip("/")) or "index.html"
        candidate = (ROOT / relative).resolve()
        if ROOT not in candidate.parents and candidate != ROOT:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = "text/html; charset=utf-8" if candidate.suffix == ".html" else "text/css; charset=utf-8" if candidate.suffix == ".css" else "application/javascript; charset=utf-8"
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"EchoDrop running at http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), EchoDropHandler).serve_forever()
