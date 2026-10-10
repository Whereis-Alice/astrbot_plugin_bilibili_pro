import asyncio
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_bilibili_pro.core.models import SubscriptionRecord
from astrbot_plugin_bilibili_pro.main import Main
from astrbot_plugin_bilibili_pro.services.listener import DynamicListener
from astrbot_plugin_bilibili_pro.services.dispatcher import SubscriptionNotificationDispatcher
from astrbot_plugin_bilibili_pro.services.renderer import Renderer


def dynamic_item(dyn_id="200", pinned=False, text="New artwork"):
    modules = {
        "module_author": {"name": "Creator", "face": ""},
        "module_dynamic": {
            "topic": None,
            "major": {
                "type": "MAJOR_TYPE_OPUS",
                "opus": {
                    "summary": {"text": text, "rich_text_nodes": []},
                    "jump_url": "//www.bilibili.com/opus/" + dyn_id,
                    "pics": [],
                },
            },
        },
    }
    if pinned:
        modules["module_tag"] = {"text": "置顶"}
    return {"id_str": dyn_id, "type": "DYNAMIC_TYPE_DRAW", "modules": modules}


def make_listener(record):
    listener = DynamicListener.__new__(DynamicListener)
    listener.renderer = Renderer.__new__(Renderer)

    async def update_live_status(session, uid, live):
        record.is_live = live

    listener.data_manager = SimpleNamespace(
        update_live_status=AsyncMock(side_effect=update_live_status)
    )
    listener._check_atall_permission = AsyncMock(return_value=True)
    listener._send_live_payload = AsyncMock(return_value=True)
    listener._handle_new_dynamic = AsyncMock()
    listener.logo_path_provider = lambda: str(
        Path(__file__).resolve().parents[1] / "assets" / "Astrbot.png"
    )
    return listener


def run_test_command(record, items):
    listener = make_listener(record)
    event = SimpleNamespace(unified_msg_origin="qq:GroupMessage:123", stop_event=lambda: None)
    plugin = SimpleNamespace(
        bili_client=SimpleNamespace(get_latest_dynamics=AsyncMock(return_value={"items": items})),
        data_manager=SimpleNamespace(get_subscription=lambda session, uid: record),
        dynamic_listener=listener,
    )
    result = asyncio.run(Main.sub_test(plugin, event, str(record.uid)))
    return listener, result


def test_seen_dynamic_can_be_tested_without_changing_deduplication():
    record = SubscriptionRecord(uid=192360, last="200", recent_ids=["200", "100"])
    before = deepcopy(record)
    listener, result = run_test_command(record, [dynamic_item()])
    assert result is None
    listener._handle_new_dynamic.assert_awaited_once()
    args = listener._handle_new_dynamic.await_args
    assert args.args[1].text == "New artwork"
    assert args.args[2] is None
    assert record == before
    assert listener._parse_and_filter_dynamics({"items": [dynamic_item()]}, record) == []


def test_test_command_prefers_non_pinned_dynamic():
    record = SubscriptionRecord(uid=192360)
    listener, _ = run_test_command(record, [dynamic_item("100", True, "Pinned"), dynamic_item()])
    assert listener._handle_new_dynamic.await_args.args[1].text == "New artwork"


def test_pinned_only_account_has_test_fallback():
    record = SubscriptionRecord(uid=192360, last="100")
    listener, _ = run_test_command(record, [dynamic_item("100", True, "Pinned")])
    assert listener._handle_new_dynamic.await_args.args[1].text == "Pinned"


def test_test_command_respects_subscription_filters():
    record = SubscriptionRecord(uid=192360, filter_regex=["artwork"])
    listener, result = run_test_command(record, [dynamic_item()])
    listener._handle_new_dynamic.assert_not_awaited()
    assert result is not None


def test_background_push_still_skips_pinned_and_seen_items():
    record = SubscriptionRecord(uid=192360, last="100")
    listener = make_listener(record)
    items = [dynamic_item("900", True), dynamic_item("200"), dynamic_item("100")]
    results = listener._parse_and_filter_dynamics({"items": items}, record)
    assert [r.dyn_id for r in results] == ["200"]


def test_live_start_end_duration_and_no_duplicate_pushes():
    record = SubscriptionRecord(uid=192360)
    listener = make_listener(record)
    room = {"live_status": 1, "live_time": 1, "room_id": 123, "uname": "Streamer",
            "area_v2_parent_name": "Games", "area_v2_name": "Console", "online": 100}

    async def check():
        await listener._handle_live_status("qq:GroupMessage:123", record, room)
        await listener._handle_live_status("qq:GroupMessage:123", record, room)
        assert listener._send_live_payload.await_count == 1
        started = listener._send_live_payload.await_args.args[1]
        assert started.type == "LIVE_ON"
        assert "Games" in started.text and "100" in started.text
        room["live_status"] = 0
        await listener._handle_live_status("qq:GroupMessage:123", record, room)
        await listener._handle_live_status("qq:GroupMessage:123", record, room)
        assert listener._send_live_payload.await_count == 2
        ended = listener._send_live_payload.await_args.args[1]
        assert ended.type == "LIVE_OFF"
        assert "本场直播时长" in ended.text
        assert listener._send_live_payload.await_args.kwargs["is_offline"] is True
        assert listener._check_atall_permission.await_args.args[1] is False
        assert record.last_live_start_ts == 0

    asyncio.run(check())


def test_filtered_live_end_still_updates_state():
    record = SubscriptionRecord(uid=192360, is_live=True, filter_types=["live_end"])
    listener = make_listener(record)
    asyncio.run(listener._handle_live_status("qq:GroupMessage:123", record, {"live_status": 0}))
    listener._send_live_payload.assert_not_awaited()
    assert record.is_live is False


def test_live_duration_format():
    assert DynamicListener._format_live_duration_text(3661) == "1小时1分钟1秒"
    assert DynamicListener._format_live_duration_text(0) == ""


def test_failed_live_notification_retries_before_acknowledging_transition():
    record = SubscriptionRecord(uid=192360)
    listener = make_listener(record)
    listener.rai = False
    listener.node = False
    listener._send_live_payload = DynamicListener._send_live_payload.__get__(listener)
    context = SimpleNamespace(send_message=AsyncMock(side_effect=[RuntimeError("Offline"), None, RuntimeError("Offline"), None]))
    listener.dispatcher = SubscriptionNotificationDispatcher(context)
    room = {"live_status": 1, "live_time": 1, "room_id": 123, "uname": "Streamer"}

    async def check():
        await listener._handle_live_status("qq:GroupMessage:123", record, room)
        assert record.is_live is False
        await listener._handle_live_status("qq:GroupMessage:123", record, room)
        assert record.is_live is True
        room["live_status"] = 0
        await listener._handle_live_status("qq:GroupMessage:123", record, room)
        assert record.is_live is True and record.last_live_start_ts == 1
        await listener._handle_live_status("qq:GroupMessage:123", record, room)
        assert record.is_live is False and record.last_live_start_ts == 0
        assert context.send_message.await_count == 4

    asyncio.run(check())
