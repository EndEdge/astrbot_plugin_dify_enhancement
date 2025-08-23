import json
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import BaseMessageComponent, Plain, At, AtAll, Forward, Reply


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


def get_outline_chain(chain: List[BaseMessageComponent]) -> str:
    outline = ""
    for i in chain:
        if isinstance(i, Plain):
            outline += i.text
        elif isinstance(i, At):
            outline += f"[At:{i.name} ({i.qq})]"
        elif isinstance(i, AtAll):
            outline += "[At:全体成员]"
        elif isinstance(i, Forward):
            # 转发消息
            outline += "[转发消息]"
        elif isinstance(i, Reply):
            # 引用回复
            if i.message_str:
                outline += f"[引用消息({i.sender_nickname}: {i.message_str})]"
            else:
                outline += "[引用消息]"
        else:
            outline += f"[{i.type}]"
        outline += " "
    return outline


@register("dify_enhancement", "EndEdge", "dify增强插件，增加输入内容，适配特殊的输出格式", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

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
            curr_message = f"\n[User ID: {event.message_obj.sender.user_id}, Nickname: {event.message_obj.sender.nickname}]\n{get_outline_chain(event.message_obj.message)}"

            logger.info(f"message object: {vars(event.message_obj)}")
            logger.info(f"curr_message: {curr_message}")

            provider = self.context.get_using_provider()
            if provider is None:
                self.context.get_provider_by_id("QQ_GROUP")

            new_prompt = {
                "chat_history": history[-15:] if len(history) > 15 else history,
                "current_message": curr_message
            }

            response = ''
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

            history.append({"role": "user", "content": curr_message})
            if response is not None and len(response) > 0:
                history.append({"role": "assistant", "content": response})

            history = history[-200:] if len(history) > 200 else history
            logger.info(f"short history: {history[-3:] if len(history) > 3 else history}")
            await self.context.conversation_manager.update_conversation(event.unified_msg_origin, curr_cid, history)
            event.stop_event()
        except Exception as e:
            logger.info(f"获取消息历史失败: {e}")

    # @filter.after_message_sent()
    # async def after_message_sent(self, event: AstrMessageEvent):
    #     pass
    #
    # @filter.on_llm_request()
    # async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
    #     uid = event.unified_msg_origin
    #     curr_cid = await self.context.conversation_manager.get_curr_conversation_id(uid)
    #     conversation = await self.context.conversation_manager.get_conversation(uid, curr_cid)  # Conversation
    #     history = json.loads(conversation.history)  # 获取上下文
    #
    #     # 构造新的 JSON 结构，只取最后10条消息
    #     new_prompt = {
    #         "chat_history": history[-15:] if len(history) > 15 else history,
    #         "current_message": f"\n[User ID: {event.message_obj.sender.user_id}, Nickname: {event.message_obj.sender.nickname}]\n{event.message_obj.message_str}"
    #     }
    #
    #     # 将构造的 JSON 转换为字符串并赋值给 req.prompt
    #     req.system_prompt = json.dumps(new_prompt, ensure_ascii=False)
    #     logger.info(req)
    #
    # @filter.on_llm_response()
    # async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
    #     logger.info(resp)
    #     try:
    #         # 获取响应文本内容
    #         original_text = resp.completion_text
    #
    #         # 尝试解析文本内容中的 JSON
    #         response_dict = json.loads(original_text)
    #         response_data = ResponseData.from_dict(response_dict)
    #
    #         # 清空返回内容
    #         resp.completion_text = ""
    #
    #         # 检查 should_reply 字段
    #         if response_data.should_reply:
    #             resp.completion_text = response_data.reply_content
    #
    #     except Exception as e:
    #         # 清空返回内容
    #         resp.completion_text = ""
    #         logger.warning(f"Error processing LLM response, content cleared: {e}")
