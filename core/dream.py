"""
梦境生成模块
在睡眠时段生成象征性、情感驱动的意识片段
"""

import re
import datetime
from typing import Optional
from astrbot.api import logger

from .dependency import DependencyManager
from .message_cache import MessageCache
from .persona_utils import PersonaConfigMixin


class DreamGenerator(PersonaConfigMixin):
    """梦境生成器"""

    def __init__(self, context, config: dict, dependency_manager: DependencyManager, message_cache: MessageCache):
        self.context = context
        self.config = config
        self.dependency_manager = dependency_manager
        self.message_cache = message_cache

    async def generate(
        self,
        current_time: str,
        session_id: Optional[str] = None,
        persona_name: Optional[str] = None,
        persona_desc: Optional[str] = None,
        current_mood: Optional[dict] = None,
        last_awareness_text: Optional[str] = None,
        previous_dream: Optional[str] = None,
    ) -> Optional[str]:
        try:
            canonical_persona = self._canonical_persona_name(persona_name)

            recent_messages = []
            if session_id:
                context_rounds = self._safe_non_negative_int(
                    self._persona_value(canonical_persona, "context_rounds", 2), default=2
                )
                recent_messages = await self.message_cache.get_recent_messages(session_id, context_rounds)
                recent_messages = self._sanitize_recent_messages(recent_messages)

            resolved_name = canonical_persona
            resolved_desc = persona_desc
            if session_id and (not resolved_name or not resolved_desc):
                persona_ctx = await self.dependency_manager.resolve_persona_context(session_id)
                resolved_name = resolved_name or self._canonical_persona_name(
                    persona_ctx.get("persona_name") or persona_ctx.get("persona_id")
                )
                resolved_desc = resolved_desc or persona_ctx.get("persona_desc")

            if self.config.get("debug_mode", False):
                logger.info(
                    f"[DreamGenerator][debug] generate params: session={session_id}, persona={resolved_name}, "
                    f"recent_messages={len(recent_messages)}, mood={current_mood}, "
                    f"last_awareness_len={len(last_awareness_text or '')}, "
                    f"has_previous_dream={previous_dream is not None}"
                )

            prompt = self._build_prompt(
                current_time=current_time,
                recent_messages=recent_messages,
                persona_name=resolved_name,
                persona_desc=resolved_desc,
                current_mood=current_mood,
                last_awareness_text=last_awareness_text,
                previous_dream=previous_dream,
            )
            result = await self._call_llm(prompt, resolved_name)
            if result:
                return self._format_result(current_time, result)
            return None
        except Exception as e:
            logger.error(f"[DreamGenerator] 生成梦境失败: {e}", exc_info=True)
            return None

    def _build_prompt(
        self,
        current_time: str,
        recent_messages: list[str],
        persona_name: Optional[str] = None,
        persona_desc: Optional[str] = None,
        current_mood: Optional[dict] = None,
        last_awareness_text: Optional[str] = None,
        previous_dream: Optional[str] = None,
    ) -> str:
        template = self._get_dream_template(persona_name)

        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekday_names[datetime.datetime.now().weekday()]

        recent_messages_str = "\n".join(recent_messages) if recent_messages else "（暂无最近对话）"

        mood_text = "（暂无心情信息）"
        if current_mood and isinstance(current_mood, dict):
            label = current_mood.get("label", "")
            reason = current_mood.get("reason", "")
            if label:
                mood_text = f"入睡前心情：{label}"
                if reason:
                    mood_text += f"（{reason}）"

        last_awareness_text = last_awareness_text or "（暂无入睡前的思考）"
        previous_dream_text = previous_dream or "（这是今晚的第一个梦）"

        persona_name_text = persona_name or "（未绑定人格）"
        persona_desc_text = persona_desc or "（未获取到人格设定文本，请保持稳定自然的语气）"

        try:
            prompt = template.format(
                time=current_time,
                weekday=weekday,
                persona_name=persona_name_text,
                persona_desc=persona_desc_text,
                recent_messages=recent_messages_str,
                mood_info=mood_text,
                last_awareness=last_awareness_text,
                previous_dream=previous_dream_text,
            )
        except KeyError as e:
            logger.warning(f"[DreamGenerator] 模板变量缺失: {e}")
            prompt = template

        if self.config.get("debug_mode", False):
            logger.info(
                f"[DreamGenerator][debug] prompt mood={mood_text[:100]}, persona={persona_name_text}"
            )
        return prompt

    def _get_dream_template(self, persona_name: str | None = None) -> str:
        override = str(self._persona_value(persona_name, "dream_prompt_template_override", "") or "").strip()
        if override:
            return override
        default_template = str(self.config.get("default_dream_prompt_template", "") or "").strip()
        if default_template:
            return default_template
        return self._get_default_template()

    def _get_default_template(self) -> str:
        return """请你以第一人称，写出一段梦境。

这不是思考，不是日记，也不是在复述白天发生的事——这是梦。在梦里，逻辑是松动的，场景是流动的，情感是放大的。

## 当前身份
- 当前人格名称：{persona_name}
- 当前人格设定：
{persona_desc}

## 入睡时刻
- 时间：{time}（{weekday}）

## 入睡前的最后意识
{last_awareness}

## 心情底色
{mood_info}

## 对话余温
入睡前感知到的对话与互动：
{recent_messages}

## 前梦余韵
{previous_dream}

## 梦境规则（绝对不可违背）

1. 【这是梦，不是思考】
不需要逻辑连贯，不需要交代因果。场景可以跳跃，时间可以扭曲，人物可以变形。感官和情感优先于叙事。

2. 【现实是素材，不是约束】
对话中的人、话题、场景可以变形出现，但不必忠于原貌。一个白天说过的话，在梦里可能变成一段旋律；一个见过的人，可能只剩一个轮廓。不要复述白天的经历，要让它变形、蒸发、重组。

3. 【情绪是梦境的核心驱动力】
入睡前的心情决定了梦的基调。不安的梦会扭曲熟悉的地方，安心的梦会让时间变慢。情绪比情节重要。

4. 【碎片化是正常的】
梦境不需要完整的故事线。一个画面、一种触感、一段没有来由的对话、一个突然切换的场景——这些都是梦的真实形态。

5. 【不要解释】
梦里没有旁白，没有"因为所以"，没有自我分析。它就是这样发生了。

6. 【前梦余韵只做微弱延续】
如果这不是今晚的第一个梦，可以带一点前梦的余韵，但不必强求连续。梦与梦之间可以毫无关联。

7. 【不要写成对话回复】
这是梦，不是在跟谁说话。不要打招呼、不要回应、不要解释。

## 输出规范
控制在80-200字以内。
直接输出第一人称的梦境片段，不要任何标题、说明、前缀、括号注释。
写完即止，不需要收束或总结。"""

    def _sanitize_recent_messages(self, messages: list[str]) -> list[str]:
        sanitized: list[str] = []
        for msg in messages:
            text = (msg or "").strip()
            if not text:
                continue
            text = re.sub(r"\(ID:[^)]+\)", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            sanitized.append(text)
        return sanitized

    def _safe_non_negative_int(self, value, default: int = 2) -> int:
        try:
            return max(int(value), 0)
        except Exception:
            return default

    async def _call_llm(self, prompt: str, persona_name: str | None = None) -> Optional[str]:
        provider_id = self._persona_value(persona_name, "dream_provider_id", "")
        if not provider_id:
            provider_id = self._persona_value(persona_name, "thinking_provider_id", "")
        try:
            if not provider_id:
                provider_id = await self._get_default_provider_id()
            if not provider_id:
                logger.error("[DreamGenerator] 梦境失败[provider_missing]: 没有配置梦境/思考模型提供商")
                return None
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt
            )
            if response is None:
                logger.error(f"[DreamGenerator] 梦境失败[empty_response]: provider={provider_id} 返回空响应对象")
                return None
            completion_text = getattr(response, "completion_text", None)
            if completion_text and completion_text.strip():
                return completion_text.strip()
            logger.error(f"[DreamGenerator] 梦境失败[empty_completion]: provider={provider_id} completion_text为空")
            return None
        except Exception as e:
            logger.error(f"[DreamGenerator] 梦境失败[provider_exception]: provider={provider_id}, error={e}")
            return None

    async def _get_default_provider_id(self) -> Optional[str]:
        try:
            if hasattr(self.context, "get_using_provider"):
                provider = self.context.get_using_provider()
                if provider:
                    meta = provider.meta()
                    if meta and getattr(meta, "id", None):
                        return str(meta.id).strip() or None
            if hasattr(self.context, "provider_manager"):
                pm = self.context.provider_manager
                if hasattr(pm, "get_using_provider"):
                    provider = pm.get_using_provider()
                    if provider:
                        meta = provider.meta()
                        if meta and getattr(meta, "id", None):
                            return str(meta.id).strip() or None
        except Exception:
            pass
        return None

    def _format_result(self, time_str: str, result: str) -> str:
        if re.match(r'^\d{1,2}:\d{2}', result):
            return result
        return f"{time_str} {result}"

    def generate_dream_mood(self, dreams: list[str], persona_name: str | None = None) -> dict:
        """从梦境内容提取低强度心情余韵"""
        if not dreams:
            return {
                "label": "平静",
                "sub_labels": [],
                "reason": "梦境余韵：无梦境内容",
                "updated_at": datetime.datetime.now().isoformat(),
                "source": "dream",
            }

        combined = " ".join(dreams)
        from .mood import LOCAL_MOOD_KEYWORDS, MOOD_LABELS, MOOD_PRIORITY_INDEX, NEGATION_PREFIXES, SELF_NEGATED_TERM_PREFIXES

        mood_scores = {label: 0 for label in MOOD_LABELS}
        for label, rule in LOCAL_MOOD_KEYWORDS.items():
            for phrase in rule.get("phrases", []):
                if phrase in combined:
                    mood_scores[label] += 2
            for keyword in rule.get("keywords", []):
                if keyword in combined:
                    mood_scores[label] += 1

        scored = {label: score for label, score in mood_scores.items() if score > 0}
        if not scored:
            return {
                "label": "平静",
                "sub_labels": [],
                "reason": "梦境余韵：梦境中未检测到明显情绪倾向",
                "updated_at": datetime.datetime.now().isoformat(),
                "source": "dream",
            }

        best_label = max(scored, key=lambda k: (scored[k], -MOOD_PRIORITY_INDEX.get(k, 99)))
        top_score = scored[best_label]

        return {
            "label": best_label,
            "sub_labels": [],
            "reason": f"梦境余韵：从梦境中检测到{best_label}的倾向（得分：{top_score}）",
            "updated_at": datetime.datetime.now().isoformat(),
            "source": "dream",
        }
