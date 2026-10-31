#!/usr/bin/env python3
"""Fetch an authenticated Echo360 lesson page using yt-dlp's cookie loader."""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.parse
from datetime import timedelta

from yt_dlp import YoutubeDL
from yt_dlp.cookies import load_cookies


def duration_seconds(value: str | None) -> int | None:
    if not value or not value.startswith("PT"):
        return None
    hours = minutes = seconds = 0.0
    match = re.fullmatch(r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if not match:
        return None
    hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return int(timedelta(hours=hours, minutes=minutes, seconds=seconds).total_seconds())


def normalized_media_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def media_filename(value: str) -> str:
    name = urllib.parse.urlsplit(value).path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def media_thumbnail(media: dict, fallback: str | None) -> str | None:
    thumbnail = media.get("thumbnail") or media.get("thumbnailUri") or media.get("poster")
    if isinstance(thumbnail, dict):
        thumbnail = thumbnail.get("uri")
    return thumbnail or fallback


def derived_thumbnail(value: str, fallback: str | None) -> str | None:
    match = re.search(r"/(s(\d+)_(?:v|av))\.m3u8$", urllib.parse.urlsplit(value).path)
    if not match:
        return fallback
    parsed = urllib.parse.urlsplit(value)
    thumbnail_path = parsed.path.rsplit("/", 1)[0] + f"/thumbnails{match.group(2)}/2160.jpg"
    return urllib.parse.urlunsplit(("https", "thumbnails.echo360.org.uk", thumbnail_path, "", ""))


def resolve_lesson(url: str) -> dict:
    ydl = YoutubeDL({"quiet": True})
    cookies = load_cookies(None, ("brave", "Default", None, None), ydl)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(request, timeout=30) as response:
        html = response.read().decode("utf-8")
    match = re.search(r'\["echoPlayerV2FullApp"\]\("((?:\\.|[^"])*)"\)', html)
    if not match:
        raise RuntimeError("The authenticated Echo360 lesson player data was not found")
    player = json.loads(json.loads(f'"{match.group(1)}"'))
    video = player.get("video") or {}
    posters = video.get("posterMedia") or []
    thumbnail = posters[0].get("uri") if posters else None
    formats = []
    seen_urls = set()
    for media in video.get("playableMedias") or []:
        media_url = normalized_media_url(media.get("uri"))
        if not media_url or media_url in seen_urls:
            continue
        filename = media_filename(media_url)
        filename_match = re.fullmatch(r"s\d+_(v|a|av)\.m3u8", urllib.parse.urlsplit(media_url).path.rsplit("/", 1)[-1])
        if not filename_match:
            continue
        suffix = filename_match.group(1)
        media_type = {"v": "Video", "a": "Audio", "av": "Video + audio"}[suffix]
        seen_urls.add(media_url)
        formats.append({
            "formatId": f"echo-{media_type.lower()}-{len(formats)}",
            "type": media_type,
            "filename": filename,
            "thumbnail": media_thumbnail(media, derived_thumbnail(media_url, thumbnail)),
            "quality": ", ".join(str(item) for item in (media.get("quality") or [])) or "HLS",
            "size": None,
            "url": media_url,
        })
    return {
        "title": player.get("title") or player.get("viewEmbedInfo", {}).get("mediaName") or "Echo360 recording",
        "duration": duration_seconds(video.get("duration")),
        "thumbnail": thumbnail,
        "formats": formats,
    }


def resolve_section(url: str) -> dict:
    ydl = YoutubeDL({"quiet": True})
    cookies = load_cookies(None, ("brave", "Default", None, None), ydl)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    section_id = re.search(r"/section/([^/]+)", urllib.parse.urlparse(url).path).group(1)
    with opener.open(urllib.request.Request(f"https://echo360.org.uk/section/{section_id}/syllabus", headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
        syllabus = json.loads(response.read().decode("utf-8"))
    lesson_entries = []
    for item in syllabus.get("data", []):
        lesson_info = item.get("lesson", {})
        lesson_data = lesson_info.get("lesson", {})
        available_video = any(
            media.get("mediaType") == "Video" and media.get("isAvailable") is True
            for media in lesson_info.get("medias", [])
        )
        if lesson_data.get("id") and available_video:
            lesson_entries.append(lesson_data)
    lectures = []
    for lesson_data in lesson_entries:
        title = lesson_data.get("title") or lesson_data.get("name") or f"Lecture {len(lectures) + 1}"
        lesson_url = f"https://echo360.org.uk/lesson/{lesson_data['id']}/classroom?focus=Video"
        lectures.append({
            "id": f"section-{len(lectures)}",
            "url": lesson_url,
            "lessonUrl": lesson_url,
            "course": "ECHO360 SECTION",
            "title": title,
            "meta": "Click to load streams",
            "thumbnail": None,
            "sources": [],
        })
    return {"lectures": lectures}


def main() -> None:
    url = sys.argv[1]
    if "/section/" in url:
        result = resolve_section(url)
    else:
        result = resolve_lesson(url)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
