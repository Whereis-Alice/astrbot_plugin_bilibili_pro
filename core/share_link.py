"""Extract Bilibili links from share cards and quoted message content."""

import html
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_HOSTS = {"b23.tv", "www.b23.tv", "bilibili.com", "www.bilibili.com",
          "m.bilibili.com", "t.bilibili.com", "live.bilibili.com", "space.bilibili.com"}
_TRACKING_PARAMS = {"spm_id_from", "share_source", "share_medium", "share_plat",
                    "share_session_id", "share_tag", "share_times", "timestamp",
                    "unique_k", "vd_source", "bbid", "broadcast_type", "is_story_h5",
                    "buvid", "from_spmid", "mid", "plat_id", "share_from", "spmid", "up_id"}
_URL_RE = re.compile(
    r"(?<![\w.@/-])(?:https?://|//)?(?:[a-z]+\.)?(?:bilibili\.com|b23\.tv)"
    r"/[^\s<>\"\[\]，。！？；（）]+", re.IGNORECASE
)
_CQ_JSON_RE = re.compile(r"\[CQ:json,data=(.*?)\]", re.DOTALL)


@dataclass(frozen=True)
class SharedLink:
    url: str
    title: str = ""


def normalize_bilibili_url(value: Any) -> str | None:
    """Validate the destination and strip share tracking, preserving p/t/id."""
    if not isinstance(value, str):
        return None
    value = html.unescape(value).strip()
    if not value or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return None
    if value.startswith("//"):
        value = "https:" + value
    elif not re.match(r"^[a-z][a-z0-9+.-]*:", value, re.IGNORECASE):
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (parts.scheme not in {"http", "https"} or host not in _HOSTS
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 80, 443}):
            return None
        if not parts.path or parts.path == "/":
            return None
        if host in {"b23.tv", "www.b23.tv"}:
            return urlunsplit(("https", "b23.tv", parts.path.rstrip("/"), "", ""))
        if host in {"bilibili.com", "m.bilibili.com"} and parts.path.startswith("/video/"):
            host = "www.bilibili.com"
        query = urlencode([(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                           if key.lower() not in _TRACKING_PARAMS and not (key == "p" and val == "1")])
        return urlunsplit(("https", host, parts.path, query, parts.fragment))
    except ValueError:
        return None


def is_b23_url(value: str) -> bool:
    return urlsplit(value).hostname == "b23.tv"


def extract_shared_links(message: Any) -> list[SharedLink]:
    """Read SDK components, OneBot segments or CQ strings; ignore image URLs."""
    links: dict[str, SharedLink] = {}

    def add(url: Any, title: Any = "") -> None:
        normalized = normalize_bilibili_url(url)
        if normalized and normalized not in links and len(links) < 10:
            links[normalized] = SharedLink(normalized, title.strip()[:512] if isinstance(title, str) else "")

    def card(payload: Any) -> None:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                return
        if not isinstance(payload, dict):
            return
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            return
        for key in ("detail_1", "detail", "news"):
            section = meta.get(key)
            if not isinstance(section, dict):
                continue
            title = (section.get("desc") or section.get("title") or "") if key != "news" else section.get("title", "")
            candidates = [url for field in ("qqdocurl", "jumpUrl", "jumpurl", "url")
                          if (url := normalize_bilibili_url(section.get(field)))]
            if candidates:
                # A card may include both a canonical page URL and its short
                # share URL. Use the short one without expanding it again.
                add(next((url for url in candidates if is_b23_url(url)), candidates[0]), title)

    def text(value: Any) -> None:
        if not isinstance(value, str) or len(value) > 262144:
            return
        if value.lstrip().startswith("{"):
            card(value)
            return
        for match in _CQ_JSON_RE.finditer(value):
            card(html.unescape(match[1]).replace("&#44;", ","))
        # Don't accidentally pick a thumbnail or other URL inside a CQ segment.
        plain = re.sub(r"\[CQ:[^\]]*\]", "", value)
        for match in _URL_RE.finditer(plain):
            add(match[0].rstrip(".,!?;:)'"))

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for segment in value:
                visit(segment)
        elif isinstance(value, str):
            text(value)
        elif isinstance(value, dict):
            kind = str(value.get("type", "")).lower()
            data = value.get("data")
            if kind == "json":
                card(data.get("data") if isinstance(data, dict) and "data" in data else data)
            elif kind in {"text", "plain"} and isinstance(data, dict):
                text(data.get("text"))
            elif not kind:
                card(value)
        else:
            kind = getattr(value, "type", "")
            kind = str(getattr(kind, "value", kind)).lower()
            if kind == "json":
                card(getattr(value, "data", None))
            elif kind == "plain":
                text(getattr(value, "text", None))

    visit(message)
    return list(links.values())
