import asyncio
import html
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot.api.message_components import Json, Plain, Reply
from astrbot_plugin_bilibili_pro.bili_client import BiliClient
from astrbot_plugin_bilibili_pro.core.share_link import (
    SharedLink, extract_shared_links, normalize_bilibili_url,
)
from astrbot_plugin_bilibili_pro.main import Main
from astrbot_plugin_bilibili_pro.services.share_link_service import (
    QuotedMessageError, format_shared_links, read_quoted_links,
)


VIDEO = "https://www.bilibili.com/video/BV1xx411c7mD"
SHORT = "https://b23.tv/AbCd123"


def miniapp(url=SHORT):
    return {"app": "com.tencent.miniapp_01", "meta": {"detail_1": {
        "title": "哔哩哔哩", "desc": "视频标题", "qqdocurl": url,
        "preview": "https://example.com/cover.jpg",
    }}}


def quoted_event(chain=None, text="", reply_id="123", platform="aiocqhttp"):
    parts = [Reply(id=reply_id, chain=chain or [], message_str=text), Plain("/bili_link")]
    return SimpleNamespace(
        message_obj=SimpleNamespace(message=parts, raw_message={"self_id": 999}),
        get_platform_name=lambda: platform,
        get_group_id=lambda: "456",
        bot=SimpleNamespace(call_action=AsyncMock()), send=AsyncMock(),
    )


@pytest.mark.parametrize("message", [
    miniapp(), [Json(data=miniapp())],
    [{"type": "json", "data": {"data": json.dumps(miniapp(), ensure_ascii=False)}}],
    json.dumps(miniapp(), ensure_ascii=False),
    "[CQ:json,data=" + html.escape(json.dumps(miniapp(), ensure_ascii=False), quote=False).replace(",", "&#44;") + "]",
])
def test_sdk_raw_json_and_cq_cards_extract_same_link(message):
    assert extract_shared_links(message) == [SharedLink(SHORT, "视频标题")]


def test_news_card_and_missing_labels_are_recognized_by_link():
    card = {"meta": {"news": {"title": "动态", "jumpUrl": "https://t.bilibili.com/123?share_source=copy"}}}
    assert extract_shared_links(card) == [SharedLink("https://t.bilibili.com/123", "动态")]


@pytest.mark.parametrize("url,expected", [
    (VIDEO + "?p=2&t=42&share_source=copy&vd_source=abc", VIDEO + "?p=2&t=42"),
    ("//live.bilibili.com/123?broadcast_type=0", "https://live.bilibili.com/123"),
    ("m.bilibili.com/video/BV1xx411c7mD?p=3&amp;share_source=qq", VIDEO + "?p=3"),
    (SHORT + "?share_source=qq", SHORT),
    ("https://www.bilibili.com/bangumi/play/ep123", "https://www.bilibili.com/bangumi/play/ep123"),
])
def test_tracking_removed_but_content_parameters_preserved(url, expected):
    assert normalize_bilibili_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://b23.tv.evil.test/abc", "https://b23.tv@127.0.0.1/abc",
    "https://evil.test@b23.tv/abc", "http://127.0.0.1/abc", "file:///tmp/abc",
    "https://www.bilibili.com:8080/video/test", "https://b23.tv\\@evil.test/abc",
    "https://b23.tv/a\r\nbc", "javascript:alert(1)", "https://b23.tv/", None,
])
def test_non_bilibili_and_invalid_urls_rejected(url):
    assert normalize_bilibili_url(url) is None


@pytest.mark.parametrize("message", [
    {"meta": None}, {"meta": {"detail_1": []}}, {"meta": {"detail_1": {"qqdocurl": [SHORT]}}},
    "{broken json", "{\"meta\":{\"detail_1\":{\"preview\":\"" + VIDEO + "\"}}}",
    [{"type": "image", "data": {"file": VIDEO}}],
])
def test_malformed_cards_and_thumbnail_urls_are_ignored(message):
    assert extract_shared_links(message) == []


def test_quoted_chain_used_without_extra_api_or_login():
    event = quoted_event([Json(data=miniapp())])
    assert asyncio.run(read_quoted_links(event)) == [SharedLink(SHORT, "视频标题")]
    event.bot.call_action.assert_not_awaited()


def test_quoted_text_link_is_supported_and_not_current_command():
    event = quoted_event(text=VIDEO + "?p=2&share_source=copy")
    assert asyncio.run(read_quoted_links(event)) == [SharedLink(VIDEO + "?p=2")]
    event.bot.call_action.assert_not_awaited()


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("cq_string", [False, True])
def test_missing_chain_fetches_onebot_message_with_account_routing(wrapped, cq_string):
    event = quoted_event()
    segments = [{"type": "json", "data": {"data": json.dumps(miniapp())}}]
    if cq_string:
        segments = "[CQ:json,data=" + json.dumps(miniapp()).replace(",", "&#44;") + "]"
    result = {"message": segments, "group_id": 456}
    event.bot.call_action.return_value = {"data": result} if wrapped else result
    assert asyncio.run(read_quoted_links(event)) == [SharedLink(SHORT, "视频标题")]
    event.bot.call_action.assert_awaited_once_with(action="get_msg", message_id=123, self_id=999)


def test_non_share_quote_does_not_fetch_other_message():
    event = quoted_event([Plain("普通聊天")])
    assert asyncio.run(read_quoted_links(event)) == []
    event.bot.call_action.assert_not_awaited()


@pytest.mark.parametrize("case", ["no_reply", "invalid_id", "other_platform", "api_failure", "cross_group"])
def test_quote_failures_have_user_readable_message(case):
    event = quoted_event()
    if case == "no_reply":
        event.message_obj.message = [Plain("/bili_link")]
    elif case == "invalid_id":
        event.message_obj.message[0].id = "abc"
    elif case == "other_platform":
        event.get_platform_name = lambda: "telegram"
    elif case == "api_failure":
        event.bot.call_action.side_effect = RuntimeError("not found")
    elif case == "cross_group":
        event.bot.call_action.return_value = {"group_id": 777, "message": [Json(data=miniapp())]}
    with pytest.raises(QuotedMessageError):
        asyncio.run(read_quoted_links(event))


def test_manual_command_works_when_automatic_parsing_disabled_and_is_plain_text():
    plugin = SimpleNamespace(enable_parse_miniapp=False,
                             bili_client=SimpleNamespace(b23_to_bv=AsyncMock(return_value=VIDEO)))
    event = quoted_event([Json(data=miniapp())])
    result = asyncio.run(Main.quoted_miniapp_link(plugin, event))
    assert result.get_plain_text() == "视频标题\n链接: " + SHORT
    plugin.bili_client.b23_to_bv.assert_not_awaited()
    assert result.use_t2i_ is False
    assert asyncio.run(Main.parse_miniapp(plugin, event)) is None
    event.send.assert_not_awaited()


def test_auto_parser_uses_same_conversion_and_does_not_parse_reply_again():
    plugin = SimpleNamespace(enable_parse_miniapp=True,
                             bili_client=SimpleNamespace(b23_to_bv=AsyncMock(return_value=VIDEO)))
    event = quoted_event([Json(data=miniapp())])
    asyncio.run(Main.parse_miniapp(plugin, event))
    event.send.assert_not_awaited()
    event.message_obj.message = [Json(data=miniapp()), Json(data=miniapp())]
    asyncio.run(Main.parse_miniapp(plugin, event))
    event.send.assert_awaited_once()
    assert event.send.await_args.args[0].get_plain_text() == "视频标题\n链接: " + SHORT
    plugin.bili_client.b23_to_bv.assert_not_awaited()


@pytest.mark.parametrize("command", ["bili_link", "小程序转链", "转直链"])
def test_astrbot_registered_command_and_aliases_accept_no_arguments(command):
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.register.star_handler import get_handler_full_name
    from astrbot.core.star.star_handler import star_handlers_registry

    metadata = star_handlers_registry.get_handler_by_full_name(get_handler_full_name(Main.quoted_miniapp_link))
    assert metadata is not None
    command_filter = next(item for item in metadata.event_filters if isinstance(item, CommandFilter))
    assert command in {command_filter.command_name, *command_filter.alias}
    assert command_filter.validate_and_convert_params([], command_filter.handler_params) == {}


def test_share_response_matches_requested_two_line_format():
    title = "【4K Hi-Res 黑胶试听】草原啊，草原 Полюшко-поле 亚历山大红旗歌舞团典藏"
    assert format_shared_links([SharedLink("https://b23.tv/7NrE40R", title)]) == (
        title + "\n链接: https://b23.tv/7NrE40R"
    )


def test_card_prefers_short_url_when_it_also_contains_long_url():
    card = miniapp(VIDEO + "?buvid=abc&from_spmid=search&mid=xyz&p=1&plat_id=116&share_from=ugc&spmid=player&up_id=123")
    card["meta"]["detail_1"]["url"] = "https://b23.tv/7NrE40R"
    assert extract_shared_links(card) == [SharedLink("https://b23.tv/7NrE40R", "视频标题")]


def test_long_link_only_removes_reported_tracking_and_default_page():
    long_url = (VIDEO + "?buvid=abc&from_spmid=search.search-result.0.0&mid=J5M%2BLX&p=1"
                "&plat_id=116&share_from=ugc&spmid=player%C3%97tamp%3D1793457950&up_id=3268763")
    assert format_shared_links([SharedLink(long_url, "视频标题")]) == "视频标题\n链接: " + VIDEO
    assert normalize_bilibili_url(long_url.replace("p=1", "p=2") + "&t=42") == VIDEO + "?p=2&t=42"


def test_share_response_without_title_or_with_duplicate_urls_is_compact():
    assert format_shared_links([SharedLink(SHORT), SharedLink(SHORT)]) == "链接: " + SHORT


class FakeResponse:
    def __init__(self, status, location=None):
        self.status = status
        self.headers = {"Location": location} if location else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, **kwargs):
        self.requests.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def resolve_with_fake(monkeypatch, responses):
    from astrbot_plugin_bilibili_pro import bili_client as module
    session = FakeSession(responses)
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda **kwargs: session)
    client = BiliClient.__new__(BiliClient)
    client.proxy = "http://proxy:7890"
    return client, session


def test_shortlink_redirect_keeps_episode_selection_and_uses_proxy(monkeypatch):
    client, session = resolve_with_fake(monkeypatch, [FakeResponse(302, VIDEO + "?p=3&t=12&share_source=qq")])
    assert asyncio.run(client.b23_to_bv(SHORT)) == VIDEO + "?p=3&t=12"
    assert session.requests[0]["proxy"] == client.proxy
    assert session.requests[0]["allow_redirects"] is False


def test_relative_shortlink_redirect_can_be_followed(monkeypatch):
    client, session = resolve_with_fake(monkeypatch, [FakeResponse(302, "/Second"), FakeResponse(302, VIDEO)])
    assert asyncio.run(client.b23_to_bv(SHORT)) == VIDEO
    assert session.requests[1]["url"] == "https://b23.tv/Second"


@pytest.mark.parametrize("responses", [
    [FakeResponse(200)], [FakeResponse(404)], [FakeResponse(302)],
    [FakeResponse(302, "https://evil.test/abc")], [FakeResponse(302, SHORT)],
    [RuntimeError("timeout")],
])
def test_shortlink_failure_returns_none_instead_of_false_direct_link(monkeypatch, responses):
    client, session = resolve_with_fake(monkeypatch, responses)
    assert asyncio.run(client.b23_to_bv(SHORT)) is None
    assert len(session.requests) == 1
