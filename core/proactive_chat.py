"""
思考后主动对话模块
负责构建 Tool、检测 Tool 调用、执行主动对话流程
"""

import asyncio
import datetime
import json
from typing import Optional, Any

from astrbot.api import logger

from .persona_utils import PersonaConfigMixin

try:
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    HAS_TOOL_API = True
except ImportError:
    HAS_TOOL_API = False

try:
    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        TextPart,
    )
    HAS_NEW_MESSAGE_API = True
except ImportError:
    HAS_NEW_MESSAGE_API = False

try:
    from astrbot.core.message.message_event_result import MessageChain
    from astrbot.core.message.components import Plain
    HAS_MESSAGE_CHAIN = True
except ImportError:
    HAS_MESSAGE_CHAIN = False

TOOL_NAME = "daymind_want_to_chat"

TOOL_DESCRIPTIONS = {
    "低频": (
        "调用此工具可以向对方主动发送一条消息。"
        "仅在你在思考中明确产生了强烈的想和对方说话、想分享什么、想找对方聊天的冲动时才调用。"
        "如果你的思考中没有这种强烈的主动交流倾向，请不要调用此工具。"
    ),
    "普通": (
        "调用此工具可以向对方主动发送一条消息。"
        "如果你觉得此刻有想和对方说话的念头，就可以调用。"
    ),
}

PROACTIVE_SYSTEM_HINT = "你因为刚才的思考现在想和对方分享一下，请自然发起对话。不要提及「思考」「刚才在想」等元信息，直接以你自己的方式自然开口即可。"


class ProactiveChatManager(PersonaConfigMixin):
    """思考后主动对话管理器"""

    def __init__(self, context, config: dict, dependency_manager):
        self.context = context
        self.config = config
        self.dependency_manager = dependency_manager

    def get_proactive_chat_mode(self, persona_name: str | None = None) -> str:
        mode = self._persona_value(persona_name, "proactive_chat_mode", "关闭")
        return str(mode).strip() if mode else "关闭"

    def get_push_target(self, persona_name: str | None = None) -> str:
        target = self._persona_value(persona_name, "proactive_chat_push_target", "")
        return str(target).strip() if target else ""

    def get_cooldown_minutes(self, persona_name: str | None = None) -> int:
        try:
            return max(int(self._persona_value(persona_name, "proactive_chat_cooldown_minutes", 90) or 90), 1)
        except Exception:
            return 90

    def is_proactive_chat_enabled(self, persona_name: str | None = None) -> bool:
        return self.get_proactive_chat_mode(persona_name) != "关闭"

    def build_tool_set(self, persona_name: str | None = None) -> Optional[Any]:
        if not HAS_TOOL_API:
            return None
        mode = self.get_proactive_chat_mode(persona_name)
        if mode == "关闭":
            return None
        description = TOOL_DESCRIPTIONS.get(mode, TOOL_DESCRIPTIONS["普通"])
        tool = FunctionTool(
            name=TOOL_NAME,
            description=description,
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
        tool_set = ToolSet()
        tool_set.add_tool(tool)
        return tool_set

    def check_tool_called(self, llm_response) -> bool:
        if llm_response is None:
            return False
        tool_names = getattr(llm_response, "tools_call_name", None) or []
        return TOOL_NAME in tool_names

    def is_cooled_down(self, persona_name: str, target: str, persona_state: dict) -> bool:
        cooldown_minutes = self.get_cooldown_minutes(persona_name)
        last_times = persona_state.get("last_proactive_chat_time") or {}
        last_time_str = last_times.get(target)
        if not last_time_str:
            return True
        try:
            last_time = datetime.datetime.fromisoformat(last_time_str)
            elapsed = (datetime.datetime.now() - last_time).total_seconds() / 60.0
            return elapsed >= cooldown_minutes
        except Exception:
            return True

    def record_proactive_chat_time(self, persona_state: dict, target: str):
        last_times = persona_state.setdefault("last_proactive_chat_time", {})
        last_times[target] = datetime.datetime.now().isoformat()

    async def execute_proactive_chat(
        self,
        persona_name: str,
        push_target: str,
        persona_state: dict,
    ) -> dict[str, Any]:
        result = {
            "status": "skipped",
            "message": "",
            "target": push_target,
            "persona_name": persona_name,
        }

        if not push_target:
            result["message"] = "未配置推送目标"
            return result

        if not self.is_cooled_down(persona_name, push_target, persona_state):
            cooldown_minutes = self.get_cooldown_minutes(persona_name)
            result["message"] = f"推送目标 {push_target} 仍在冷却中（冷却时间 {cooldown_minutes} 分钟）"
            logger.info(f"[ProactiveChat] {result['message']}")
            return result

        try:
            provider_id = await self._get_chat_provider_id(push_target)
        except Exception as e:
            result["message"] = f"获取对话模型提供商失败: {e}"
            logger.warning(f"[ProactiveChat] {result['message']}")
            return result

        if not provider_id:
            result["message"] = "未找到可用的对话模型提供商"
            logger.warning(f"[ProactiveChat] {result['message']}")
            return result

        system_prompt = await self._build_system_prompt(push_target)

        conversation_context = await self._prepare_conversation_context(push_target)

        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt="请自然地发起对话。",
                contexts=conversation_context.get("history", []),
                system_prompt=system_prompt,
            )
        except Exception as e:
            result["message"] = f"对话模型调用失败: {e}"
            logger.error(f"[ProactiveChat] {result['message']}")
            return result

        if not response or not response.completion_text or not response.completion_text.strip():
            result["message"] = "对话模型返回空内容"
            logger.warning(f"[ProactiveChat] {result['message']}")
            return result

        message_text = response.completion_text.strip()

        send_ok = await self._send_message(push_target, message_text)
        if not send_ok:
            result["message"] = f"消息发送失败: {push_target}"
            return result

        await self._save_to_conversation_history(push_target, message_text, conversation_context)

        self.record_proactive_chat_time(persona_state, push_target)

        result["status"] = "success"
        result["message"] = f"已向 {push_target} 发送主动对话"
        result["sent_text"] = message_text[:100]
        logger.info(f"[ProactiveChat] 主动对话已发送: persona={persona_name}, target={push_target}, text_len={len(message_text)}")
        return result

    async def _get_chat_provider_id(self, session_id: str) -> str | None:
        try:
            return await self.context.get_current_chat_provider_id(session_id)
        except Exception:
            pass
        try:
            provider = self.context.get_using_provider(umo=session_id)
            if provider:
                meta = provider.meta()
                if meta and getattr(meta, "id", None):
                    return str(meta.id).strip() or None
        except Exception:
            pass
        return None

    async def _build_system_prompt(self, session_id: str) -> str:
        base_prompt = ""
        try:
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if conv_mgr:
                conv_id = await conv_mgr.get_curr_conversation_id(session_id)
                if conv_id:
                    conversation = await conv_mgr.get_conversation(session_id, conv_id)
                    if conversation and conversation.persona_id:
                        persona = await self.context.persona_manager.get_persona(conversation.persona_id)
                        if persona:
                            base_prompt = persona.system_prompt or ""
        except Exception:
            pass

        if not base_prompt:
            try:
                default_persona = await self.context.persona_manager.get_default_persona_v3(umo=session_id)
                if default_persona:
                    base_prompt = default_persona.get("prompt", "") or ""
            except Exception:
                pass

        base_prompt = await self._apply_on_llm_request_hooks(session_id, base_prompt)

        if base_prompt:
            return base_prompt + f"\n\n{PROACTIVE_SYSTEM_HINT}"
        return PROACTIVE_SYSTEM_HINT

    async def _apply_on_llm_request_hooks(self, session_id: str, system_prompt: str) -> str:
        try:
            from astrbot.core.provider.entities import ProviderRequest
            from astrbot.core.star.star_handler import EventType, star_handlers_registry
        except ImportError:
            return system_prompt

        handlers = star_handlers_registry.get_handlers_by_event_type(EventType.OnLLMRequestEvent)
        if not handlers:
            return system_prompt

        parsed = self._parse_session_id(session_id)
        if not parsed:
            return system_prompt

        platform_name, msg_type_str, target_id = parsed

        platform_inst = None
        for p in self.context.platform_manager.platform_insts:
            if p.meta().id == platform_name:
                platform_inst = p
                break
        if not platform_inst:
            for p in self.context.platform_manager.platform_insts:
                if p.meta().name == platform_name:
                    platform_inst = p
                    break
        if not platform_inst:
            return system_prompt

        try:
            from astrbot.core.platform.astrbot_message import AstrBotMessage, Group, MessageMember
            from astrbot.core.platform.message_type import MessageType
        except ImportError:
            return system_prompt

        try:
            from astrbot.api.event import AstrMessageEvent as EventCls
        except ImportError:
            try:
                from astrbot.core.platform.astr_message_event import AstrMessageEvent as EventCls
            except ImportError:
                return system_prompt

        message_obj = AstrBotMessage()
        if "Friend" in msg_type_str:
            message_obj.type = MessageType.FRIEND_MESSAGE
        elif "Group" in msg_type_str:
            message_obj.type = MessageType.GROUP_MESSAGE
            message_obj.group = Group(group_id=target_id)
        else:
            message_obj.type = MessageType.FRIEND_MESSAGE
        message_obj.session_id = target_id
        message_obj.message = []
        message_obj.self_id = "bot"
        message_obj.sender = MessageMember(user_id=target_id)
        message_obj.message_str = ""
        message_obj.raw_message = None
        message_obj.message_id = ""

        event = EventCls(
            message_str="",
            message_obj=message_obj,
            platform_meta=platform_inst.meta(),
            session_id=target_id,
        )

        req = ProviderRequest()
        req.session_id = session_id
        req.system_prompt = system_prompt

        for handler in handlers:
            try:
                await handler.handler(event, req)
            except Exception as e:
                logger.debug(f"[ProactiveChat] on_llm_request 钩子执行失败: {handler.handler_full_name}, error={e}")

        return req.system_prompt

    async def _prepare_conversation_context(self, session_id: str) -> dict:
        context_result = {"conv_id": None, "history": []}

        try:
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if not conv_mgr:
                return context_result

            conv_id = await conv_mgr.get_curr_conversation_id(session_id)
            if not conv_id:
                try:
                    conv_id = await conv_mgr.new_conversation(session_id)
                except Exception as e:
                    logger.debug(f"[ProactiveChat] 创建新对话失败: {e}")
                    return context_result

            context_result["conv_id"] = conv_id

            conversation = await conv_mgr.get_conversation(session_id, conv_id)
            if conversation and conversation.history:
                if isinstance(conversation.history, str):
                    try:
                        context_result["history"] = json.loads(conversation.history)
                    except (json.JSONDecodeError, TypeError):
                        context_result["history"] = []
                elif isinstance(conversation.history, list):
                    context_result["history"] = conversation.history
        except Exception as e:
            logger.debug(f"[ProactiveChat] 准备对话上下文失败: {e}")

        return context_result

    async def _send_message(self, session_id: str, text: str) -> bool:
        if not HAS_MESSAGE_CHAIN:
            logger.error("[ProactiveChat] MessageChain 不可用，无法发送消息")
            return False

        try:
            chain = await self._trigger_decorating_hooks(session_id, [Plain(text=text)])
            if not chain:
                chain = [Plain(text=text)]
            message_chain = MessageChain(chain)
            return await self.context.send_message(session_id, message_chain)
        except Exception as e:
            logger.error(f"[ProactiveChat] 消息发送失败: {e}")
            return False

    async def _trigger_decorating_hooks(self, session_id: str, components: list) -> list:
        try:
            from astrbot.core.star.star_handler import EventType, star_handlers_registry
        except ImportError:
            return components

        parsed = self._parse_session_id(session_id)
        if not parsed:
            return components

        platform_name, msg_type_str, target_id = parsed

        platform_inst = None
        for p in self.context.platform_manager.platform_insts:
            if p.meta().id == platform_name:
                platform_inst = p
                break
        if not platform_inst:
            for p in self.context.platform_manager.platform_insts:
                if p.meta().name == platform_name:
                    platform_inst = p
                    break
        if not platform_inst:
            return components

        try:
            from astrbot.core.platform.astrbot_message import AstrBotMessage, Group, MessageMember
            from astrbot.core.platform.message_type import MessageType
        except ImportError:
            return components

        try:
            from astrbot.api.event import AstrMessageEvent as EventCls
        except ImportError:
            try:
                from astrbot.core.platform.astr_message_event import AstrMessageEvent as EventCls
            except ImportError:
                return components

        try:
            from astrbot.core.message.message_event_result import MessageEventResult
        except ImportError:
            return components

        message_obj = AstrBotMessage()
        if "Friend" in msg_type_str:
            message_obj.type = MessageType.FRIEND_MESSAGE
        elif "Group" in msg_type_str:
            message_obj.type = MessageType.GROUP_MESSAGE
            message_obj.group = Group(group_id=target_id)
        else:
            message_obj.type = MessageType.FRIEND_MESSAGE
        message_obj.session_id = target_id
        message_obj.message = components
        message_obj.self_id = "bot"
        message_obj.sender = MessageMember(user_id=target_id)
        message_obj.message_str = ""
        message_obj.raw_message = None
        message_obj.message_id = ""

        event = EventCls(
            message_str="",
            message_obj=message_obj,
            platform_meta=platform_inst.meta(),
            session_id=target_id,
        )

        res = MessageEventResult()
        res.chain = components
        event.set_result(res)

        handlers = star_handlers_registry.get_handlers_by_event_type(EventType.OnDecoratingResultEvent)
        for handler in handlers:
            try:
                await handler.handler(event)
            except Exception as e:
                logger.debug(f"[ProactiveChat] 装饰钩子执行失败: {handler.handler_full_name}, error={e}")

        final_res = event.get_result()
        if final_res is not None and final_res.chain is not None:
            return final_res.chain
        return components

    async def _save_to_conversation_history(
        self,
        session_id: str,
        assistant_text: str,
        conversation_context: dict,
    ):
        if not HAS_NEW_MESSAGE_API:
            return

        conv_id = conversation_context.get("conv_id")
        if not conv_id:
            return

        try:
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if not conv_mgr:
                return

            user_msg = UserMessageSegment(content=[TextPart(text="[DayMind 主动对话触发]")])
            assistant_msg = AssistantMessageSegment(content=[TextPart(text=assistant_text)])

            await conv_mgr.add_message_pair(
                cid=conv_id,
                user_message=user_msg,
                assistant_message=assistant_msg,
            )
            logger.debug(f"[ProactiveChat] 已将主动对话写入对话历史: session={session_id}, conv_id={conv_id}")
        except Exception as e:
            logger.warning(f"[ProactiveChat] 写入对话历史失败: {e}")

    def _parse_session_id(self, session_id: str) -> tuple[str, str, str] | None:
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        parts = session_id.split(":", 2)
        if len(parts) != 3:
            return None
        platform_id, msg_type, target_id = parts
        if not platform_id or not target_id:
            return None
        return platform_id, msg_type, target_id
