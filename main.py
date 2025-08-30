import asyncio
import json
from dataclasses import dataclass
from typing import Optional, Dict, Any

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Plain, At, AtAll, Reply
from astrbot.core.platform import AstrBotMessage


@dataclass
class ResponseData:
    should_reply: bool
    reply_content: str = ""
    source_agent: Optional[str] = None
    debug_info: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'ResponseData':
        return cls(
            should_reply=data.get("should_reply", False),
            reply_content=data.get("reply_content", ""),
            source_agent=data.get("source_agent"),
            debug_info=data.get("debug_info")
        )


def build_message_content(message_obj: AstrBotMessage) -> str:
    content_map = {}
    content_map["nickname"] = message_obj.sender.nickname
    content_map["user_id"] = message_obj.sender.user_id

    message_outline = ''
    reply_messages = []
    for i in message_obj.message:
        if isinstance(i, Plain):
            message_outline += i.text
        # elif isinstance(i, Image): // todo: 支持解析图片
        #     outline += f"[图片 | 链接: {i.url}]"
        elif isinstance(i, At):
            message_outline += f"@{i.name}"
        elif isinstance(i, AtAll):
            message_outline += "@全体成员"
        # elif isinstance(i, Forward):
        #     # 转发消息
        #     outline += "[转发消息]"
        elif isinstance(i, Reply):
            # 引用回复 // todo: 支持引用图片
            if i.message_str:
                reply_messages.append({"nickname": i.sender_nickname, "user_id": i.id, "message": i.message_str})
        else:
            message_outline += f"[{i.type}]"
        message_outline += " "
    content_map["reply_messages"] = reply_messages
    content_map["message"] = message_outline.strip()
    return json.dumps(content_map, ensure_ascii=False)


@register("dify_enhancement", "EndEdge", "dify增强插件，增加输入内容，适配特殊的输出格式", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 用于存储每个 conversation ID 对应的锁
        self.conversation_locks = {}
        # 保护 conversation_locks 的访问
        self.locks_lock = asyncio.Lock()

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""

    @filter.event_message_type(filter.EventMessageType.ALL, priority=99999)
    async def on_all_message(self, event: AstrMessageEvent):
        # 如果消息以 '/' 开头，跳过处理
        if event.message_str.startswith('/'):
            event.continue_event()
            return
        try:
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(event.unified_msg_origin)
            if curr_cid is None:
                curr_cid = await self.context.conversation_manager.new_conversation(event.unified_msg_origin)
            logger.info(f'curr_id: {str(curr_cid)}')
            conversation = await self.context.conversation_manager.get_conversation(event.unified_msg_origin, curr_cid)
            history = json.loads(conversation.history)
            curr_message = build_message_content(event.message_obj)

            logger.info(f"message object: {vars(event.message_obj)}")
            # logger.info(f"curr_message: {curr_message}")

            provider = self.context.get_using_provider()
            if provider is None:
                self.context.get_provider_by_id("QQ_GROUP")

            new_prompt = {
                "chat_history": history[-15:] if len(history) > 15 else history,
                "current_message": curr_message
            }

            # 获取或创建针对当前 conversation ID 的锁
            async with self.locks_lock:
                if curr_cid not in self.conversation_locks:
                    self.conversation_locks[curr_cid] = asyncio.Lock()
                conversation_lock = self.conversation_locks[curr_cid]

            # 使用锁保护对 conversation history 的更新操作
            async with conversation_lock:
                history = json.loads(conversation.history)
                history.append({"role": "user", "content": curr_message})
                history = history[-200:] if len(history) > 200 else history
                await self.context.conversation_manager.update_conversation(event.unified_msg_origin, curr_cid, history)

            response = ''
            logger.info(f"system_prompt: {json.dumps(new_prompt, ensure_ascii=False)}")
            try:
                llm_response = await provider.text_chat(
                    prompt=event.message_str,
                    session_id=None,
                    contexts=[],
                    image_urls=[],
                    func_tool=None,
                    system_prompt=json.dumps(new_prompt, ensure_ascii=False)
                )

                # 尝试解析文本内容中的 JSON
                response_text = llm_response.completion_text
                response_dict = json.loads(response_text)
                response_data = ResponseData.from_dict(response_dict)

                if response_data.should_reply:
                    response = response_data.reply_content
            except Exception as e:
                logger.info(f"获取 LLM 响应失败: {e}")

            if response is not None and len(response) > 0:
                yield event.plain_result(response)
                # 使用锁保护对 conversation history 的更新操作
                async with conversation_lock:
                    history = json.loads(conversation.history)
                    history.append({"role": "assistant", "content": response})
                    history = history[-200:] if len(history) > 200 else history
                    await self.context.conversation_manager.update_conversation(event.unified_msg_origin, curr_cid,
                                                                                history)

            event.stop_event()
        except Exception as e:
            logger.info(f"获取消息历史失败: {e}")
