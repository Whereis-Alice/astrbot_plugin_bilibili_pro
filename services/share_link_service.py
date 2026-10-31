import asyncio

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Reply

from ..core.share_link import SharedLink, extract_shared_links, normalize_bilibili_url


class QuotedMessageError(Exception):
    """A user-readable failure to retrieve the quoted message."""


async def read_quoted_links(event: AstrMessageEvent) -> list[SharedLink]:
    """Use Reply.chain first, then OneBot get_msg when content is unavailable."""
    reply = next((part for part in event.message_obj.message if isinstance(part, Reply)), None)
    if reply is None:
        raise QuotedMessageError("请先引用一条 B 站小程序分享消息，再发送 /bili_link（或 /小程序转链）。")
    links = extract_shared_links(reply.chain)
    if not links:
        links = extract_shared_links(reply.message_str or reply.text)
    if links:
        return links
    # Complete quoted content with no link needs no extra API request.
    if reply.chain:
        return []
    bot = getattr(event, "bot", None)
    if event.get_platform_name() != "aiocqhttp" or bot is None:
        raise QuotedMessageError("当前平台没有提供引用消息内容。请重新发送 B 站小程序卡片或其分享链接。")
    try:
        message_id = int(reply.id)
    except (TypeError, ValueError, OverflowError):
        raise QuotedMessageError("引用消息 ID 无效，请重新引用原消息。") from None
    raw = getattr(event.message_obj, "raw_message", None)
    self_id = raw.get("self_id") if isinstance(raw, dict) else getattr(raw, "self_id", None)
    routing = {"self_id": self_id} if self_id else {}
    try:
        result = await asyncio.wait_for(
            bot.call_action(action="get_msg", message_id=message_id, **routing), timeout=10
        )
    except Exception:
        raise QuotedMessageError("无法读取引用消息，可能已撤回或超出 QQ 客户端缓存。请重新分享后再引用。") from None
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        result = result["data"]
    if not isinstance(result, dict):
        raise QuotedMessageError("QQ 接口未返回引用消息内容，请重新分享后再引用。")
    group_id = result.get("group_id")
    if group_id and str(group_id) != str(event.get_group_id()):
        raise QuotedMessageError("引用消息不属于当前会话，请引用本会话内的分享消息。")
    return extract_shared_links(result.get("message") or result.get("raw_message"))


def format_shared_links(links: list[SharedLink]) -> str:
    """Emit title + clickable URL, keeping the original b23 share link short."""
    lines = []
    seen = set()
    for link in links:
        url = normalize_bilibili_url(link.url)
        if not url or url in seen:
            continue
        seen.add(url)
        title = " ".join(link.title.split())
        block = f"{title}\n" if title else ""
        block += f"链接: {url}"
        lines.append(block)
    return "\n\n".join(lines)
