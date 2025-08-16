import json
from dataclasses import dataclass
from typing import Optional, Dict, Any

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.provider.entities import ProviderRequest, LLMResponse


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


@register("dify_enhancement", "EndEdge", "dify增强插件，增加输入内容，适配特殊的输出格式", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        uid = event.unified_msg_origin
        curr_cid = await self.context.conversation_manager.get_curr_conversation_id(uid)
        conversation = await self.context.conversation_manager.get_conversation(uid, curr_cid)  # Conversation
        history = json.loads(conversation.history)  # 获取上下文

        # 构造新的 JSON 结构，只取最后10条消息
        new_prompt = {
            "chat_history": history[-10:] if len(history) > 10 else history,
            "current_message": req.prompt
        }

        # 将构造的 JSON 转换为字符串并赋值给 req.prompt
        req.prompt = json.dumps(new_prompt, ensure_ascii=False)

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        try:
            # 获取响应文本内容
            original_text = resp.completion_text

            # 尝试解析文本内容中的 JSON
            response_dict = json.loads(original_text)
            response_data = ResponseData.from_dict(response_dict)

            # 清空返回内容
            resp.completion_text = ""

            # 检查 should_reply 字段
            if response_data.should_reply:
                resp.completion_text = response_data.reply_content

        except Exception as e:
            # 清空返回内容
            resp.completion_text = ""
            logger.warning(f"Error processing LLM response, content cleared: {e}")