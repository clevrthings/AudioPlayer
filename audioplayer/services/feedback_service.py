from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from audioplayer.constants import APP_VERSION, FEEDBACK_WORKER_DEFAULT_URL, FEEDBACK_WORKER_ENV_KEY, FEEDBACK_WORKER_ENV_URL


def post_feedback_issue(
    *,
    issue_kind: str,
    title: str,
    details: str,
    reporter_name: str,
    guest_mode: bool,
    language: str,
    worker_url: str,
    worker_key: str,
    txt,
) -> tuple[bool, str, str]:
    resolved_url = worker_url.strip() or os.getenv(FEEDBACK_WORKER_ENV_URL, "").strip() or FEEDBACK_WORKER_DEFAULT_URL
    if not resolved_url:
        return (
            False,
            txt("Feedback service is niet geconfigureerd.", "Feedback service is not configured."),
            "",
        )

    clean_title = title.strip()
    clean_details = details.strip()
    clean_reporter = reporter_name.strip()
    reporter = txt("Gast", "Guest") if guest_mode else (clean_reporter or txt("Onbekend", "Unknown"))
    if not clean_title or not clean_details:
        return (
            False,
            txt("Titel en beschrijving zijn verplicht.", "Title and description are required."),
            "",
        )

    issue_label = "bug" if issue_kind == "bug" else "enhancement"
    prefix = "Bug" if issue_kind == "bug" else "Feature"
    payload = {
        "kind": issue_label,
        "title": f"[{prefix}] {clean_title}",
        "details": clean_details,
        "reporter": reporter,
        "language": language,
        "app_version": APP_VERSION,
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "AudioPlayer-App",
    }
    resolved_key = worker_key.strip() or os.getenv(FEEDBACK_WORKER_ENV_KEY, "").strip()
    if resolved_key:
        headers["X-Feedback-Key"] = resolved_key

    req = urllib.request.Request(
        resolved_url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            url = str(data.get("issue_url", ""))
            success_message = str(data.get("message", "")).strip()
            if not success_message:
                success_message = txt("Issue succesvol geplaatst.", "Issue created successfully.")
            return True, success_message, url
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            raw = ""
        message = ""
        if raw:
            try:
                parsed = json.loads(raw)
                message = str(parsed.get("message", "")).strip()
            except Exception:  # noqa: BLE001
                message = raw.strip()
        if not message:
            message = str(exc)
        return (
            False,
            txt(
                f"Feedback service weigerde de aanvraag: {message}",
                f"Feedback service rejected the request: {message}",
            ),
            "",
        )
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            txt(f"Kon feedback niet posten: {exc}", f"Could not post feedback: {exc}"),
            "",
        )
