from __future__ import annotations

import json
import urllib.request

from audioplayer.constants import RELEASE_LATEST_API_URL, RELEASES_LATEST_PAGE_URL


def version_tuple(version_text: str) -> tuple[int, ...]:
    raw = str(version_text or "").strip()
    if raw.lower().startswith("v"):
        raw = raw[1:]
    raw = raw.split("-", 1)[0]
    parts: list[int] = []
    for segment in raw.split("."):
        segment = segment.strip()
        if not segment:
            break
        digits = "".join(ch for ch in segment if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def compare_versions(left: str, right: str) -> int:
    left_parts = version_tuple(left)
    right_parts = version_tuple(right)
    size = max(len(left_parts), len(right_parts))
    left_parts += (0,) * (size - len(left_parts))
    right_parts += (0,) * (size - len(right_parts))
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def latest_release_info() -> tuple[str, str]:
    request = urllib.request.Request(
        RELEASE_LATEST_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AudioPlayer",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = response.read().decode("utf-8", "replace")
    data = json.loads(payload)
    tag_name = str(data.get("tag_name") or data.get("name") or "").strip()
    html_url = str(data.get("html_url") or RELEASES_LATEST_PAGE_URL).strip()

    latest_version = tag_name
    if latest_version.lower().startswith("v"):
        latest_version = latest_version[1:]
    latest_version = latest_version.strip()

    download_url = ""
    assets = data.get("assets")
    if isinstance(assets, list):
        dmg_urls: list[str] = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_url = str(asset.get("browser_download_url") or "").strip()
            if asset_url.lower().endswith(".dmg"):
                dmg_urls.append(asset_url)
        if dmg_urls:
            mac_urls = [url for url in dmg_urls if "mac" in url.lower()]
            download_url = mac_urls[0] if mac_urls else dmg_urls[0]
    if not download_url:
        download_url = html_url
    return latest_version, download_url
