"""paperplane 多平台机器人插件(AstrBot v4)。

功能:
  1. 收到转发消息时回复"收到"(支持 QQ/OneBot v11、飞书、Telegram)。
  2. 集成 opencode(AI 编程助手):通过 `/oc` 或 `/opencode` 指令调用
     opencode 智能回复;可配置收到转发消息时自动交给 opencode 处理。

平台转发检测说明:
  - QQ/OneBot v11(aiocqhttp):合并转发消息以 Forward/Nodes/Node 消息段到达,
    可检测;单条消息转发会以普通消息到达,属平台限制无法检测。
  - Telegram:原始消息(telegram.Message)带 forward_origin/forward_from/
    forward_date 字段,可检测。
  - 飞书(lark):普通转发无标记(以普通消息到达),仅能检测分享类消息
    (share_chat/share_user)与消息链中的 Forward/Nodes 段,属平台限制。
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Forward, Node, Nodes
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

from .opencode_client import OpencodeClient, OpencodeConfig

# 各平台适配器名称(对应 event.platform_meta.name)
PLATFORM_AIOCQHTTP = "aiocqhttp"  # QQ / OneBot v11
PLATFORM_TELEGRAM = "telegram"  # Telegram(纸飞机)
PLATFORM_LARK = "lark"  # 飞书

# 飞书分享类消息类型(非普通转发,尽力检测)
LARK_SHARE_MSG_TYPES = {"share_chat", "share_user"}


class PaperplanePlugin(Star):
    """收到转发消息回复"收到",并支持 opencode 智能回复。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        oc_cfg = OpencodeConfig(
            bin=self.config.get("oc_bin", "opencode"),
            dir=self.config.get("oc_dir", "/workspace"),
            model=self.config.get("oc_model", ""),
            session=self.config.get("oc_session", ""),
            attach_url=self.config.get("oc_attach_url", ""),
            timeout=int(self.config.get("oc_timeout", 180)),
            auto=bool(self.config.get("oc_auto", False)),
        )
        self.oc = OpencodeClient(oc_cfg)
        self.reply_text = self.config.get("reply_text", "收到")
        self.oc_enabled = bool(self.config.get("oc_enabled", True))
        self.oc_on_forward = bool(self.config.get("oc_on_forward", False))

    # ---------- 转发检测 ----------

    @staticmethod
    def _is_forward_in_chain(event: AstrMessageEvent) -> bool:
        """检查消息链中是否包含合并转发消息段(Forward/Nodes/Node)。

        适用于 QQ 合并转发等场景;所有平台统一检查。
        """
        message = getattr(event.message_obj, "message", None)
        if not message:
            return False
        return any(
            isinstance(comp, (Forward, Nodes, Node)) for comp in message
        )

    @staticmethod
    def _is_forward_telegram(event: AstrMessageEvent) -> bool:
        """Telegram:原始 Update 对象的 message 带转发字段即视为转发。"""
        raw = getattr(event.message_obj, "raw_message", None)
        msg = getattr(raw, "message", None)
        if msg is None:
            return False
        return bool(
            getattr(msg, "forward_origin", None)
            or getattr(msg, "forward_from", None)
            or getattr(msg, "forward_date", None)
        )

    @staticmethod
    def _is_forward_lark(event: AstrMessageEvent) -> bool:
        """飞书:尽力检测分享类消息(share_chat/share_user)。

        飞书普通转发以普通消息到达,无法从 API 区分,属平台限制。
        """
        raw = getattr(event.message_obj, "raw_message", None)
        msg_type = getattr(raw, "msg_type", None)
        return bool(msg_type and msg_type in LARK_SHARE_MSG_TYPES)

    def _is_forwarded(self, event: AstrMessageEvent) -> bool:
        """综合各平台检测消息是否为转发。"""
        if self._is_forward_in_chain(event):
            return True
        platform = getattr(event.platform_meta, "name", "")
        if platform == PLATFORM_TELEGRAM:
            return self._is_forward_telegram(event)
        if platform == PLATFORM_LARK:
            return self._is_forward_lark(event)
        return False

    # ---------- opencode 调用 ----------

    async def _call_opencode(self, prompt: str) -> str:
        """调用 opencode 并返回结果;异常时抛出 RuntimeError。"""
        return await self.oc.run(prompt)

    # ---------- 事件监听器:转发消息 ----------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """收到转发消息:回复 reply_text;若开启 oc_on_forward 则交给 opencode。"""
        if not self._is_forwarded(event):
            # 普通消息不拦截,继续走 AstrBot 默认流程(LLM 等)
            return

        platform = getattr(event.platform_meta, "name", "unknown")
        sender = event.get_sender_name()
        self.logger.info(
            f"[转发] platform={platform} sender={sender} msg_id={event.message_obj.message_id}"
        )

        if self.oc_enabled and self.oc_on_forward:
            # 把转发消息内容交给 opencode 处理,回复其结果
            prompt = event.message_str or "这是一条转发消息,请简要回复"
            try:
                result = await self._call_opencode(prompt)
                yield event.plain_result(result)
            except Exception as e:  # noqa: BLE001 防止插件崩溃
                self.logger.error(f"opencode 处理转发消息失败: {e}")
                yield event.plain_result(self.reply_text)
        else:
            yield event.plain_result(self.reply_text)

        # 停止事件传播,避免 AstrBot 默认流程(LLM 等)再次回复
        event.stop_event()

    # ---------- 指令:/oc <prompt> ----------

    @filter.command("oc", alias={"opencode"})
    async def oc(self, event: AstrMessageEvent, prompt: GreedyStr) -> None:
        """调用 opencode 智能回复。用法:/oc 你的问题"""
        if not self.oc_enabled:
            yield event.plain_result("opencode 集成未启用,请在插件配置中打开 oc_enabled")
            return

        prompt = prompt.strip()
        if not prompt:
            yield event.plain_result("用法:/oc 你的问题(例如:/oc 用 python 写一个斐波那契函数)")
            return

        # 先回复提示,避免用户以为没反应
        try:
            result = await self._call_opencode(prompt)
        except Exception as e:  # noqa: BLE001 防止插件崩溃
            self.logger.error(f"opencode 调用失败: {e}")
            yield event.plain_result(f"opencode 调用失败: {e}")
            return
        yield event.plain_result(result)

    async def terminate(self) -> None:
        """插件被卸载/停用时调用。"""
        self.logger.info("paperplane 插件已卸载")