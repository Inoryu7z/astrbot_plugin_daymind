"""
思考生成模块
负责生成Bot的自我思考内容
"""

import re
import datetime
from typing import Any, Optional
from astrbot.api import logger

from .dependency import DependencyManager
from .message_cache import MessageCache
from .persona_utils import PersonaConfigMixin


class ReflectionGenerator(PersonaConfigMixin):
    """思考生成器"""

    FUTURE_TIME_PATTERNS = [
        "明天",
        "明早",
        "明晚",
        "后天",
        "下周",
        "过几天",
        "未来",
        "改天",
    ]

    SCHEDULE_SLOT_PATTERN = re.compile(
        r"──\s*第\s*\d+\s*项\s*──\s*\n"
        r"🕐\s*(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})\s*\n"
        r"📌\s*(.+?)\s*\n"
        r"📄\s*([\s\S]*?)(?=\n\n──\s*第\s*\d+\s*项\s*──|\n👗|\Z)",
    )

    def __init__(self, context, config: dict, dependency_manager: DependencyManager, message_cache: MessageCache):
        self.context = context
        self.config = config
        self.dependency_manager = dependency_manager
        self.message_cache = message_cache

    @staticmethod
    def _parse_time_to_minutes(time_str: str) -> int:
        try:
            parts = time_str.strip().split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return -1

    def _extract_current_schedule_slot(self, schedule_text: str, current_time_str: str) -> dict:
        if not schedule_text or not schedule_text.strip():
            return {"found": False, "slots_text": "", "fallback": True}
        slots = []
        for m in self.SCHEDULE_SLOT_PATTERN.finditer(schedule_text):
            start_str = m.group(1).strip()
            end_str = m.group(2).strip()
            title = m.group(3).strip()
            detail = m.group(4).strip()
            start_min = self._parse_time_to_minutes(start_str)
            end_min = self._parse_time_to_minutes(end_str)
            if start_min < 0 or end_min < 0:
                continue
            slots.append({
                "start_str": start_str,
                "end_str": end_str,
                "start_min": start_min,
                "end_min": end_min,
                "title": title,
                "detail": detail,
                "raw": f"🕐 {start_str}-{end_str}\n📌 {title}\n📄 {detail}",
            })
        if not slots:
            return {"found": False, "slots_text": schedule_text, "fallback": True}
        current_min = self._parse_time_to_minutes(current_time_str)
        if current_min < 0:
            return {"found": False, "slots_text": schedule_text, "fallback": True}
        current_idx = -1
        for i, slot in enumerate(slots):
            if slot["start_min"] <= current_min < slot["end_min"]:
                current_idx = i
                break
        if current_idx < 0:
            min_dist = float("inf")
            for i, slot in enumerate(slots):
                dist = min(abs(current_min - slot["start_min"]), abs(current_min - slot["end_min"]))
                if dist < min_dist:
                    min_dist = dist
                    current_idx = i
        start = max(0, current_idx - 1)
        end = min(len(slots), current_idx + 2)
        selected = slots[start:end]
        parts = []
        for s in selected:
            marker = "【当前时段】" if s is slots[current_idx] else ""
            parts.append(f"{marker}🕐 {s['start_str']}-{s['end_str']}\n📌 {s['title']}\n📄 {s['detail']}")
        return {
            "found": True,
            "slots_text": "\n\n".join(parts),
            "fallback": False,
            "current_slot": slots[current_idx] if current_idx >= 0 else None,
        }

    def _format_timeline_slot(self, slot: dict) -> str:
        start = str(slot.get("time_start", ""))
        end = str(slot.get("time_end", ""))
        title = str(slot.get("title", ""))
        detail = str(slot.get("detail", ""))
        text = f"{start}-{end} {title}"
        if detail:
            text += f"\n{detail}"
        return text

    def _extract_sub_events_text(self, sub_events, current_timeline_idx: int, current_min: int) -> str:
        if not sub_events or not isinstance(sub_events, list):
            return ""
        target_block = None
        for block in sub_events:
            if not isinstance(block, dict):
                continue
            if block.get("source_index") == current_timeline_idx:
                target_block = block
                break
        if not target_block:
            return ""
        items = target_block.get("items", [])
        if not items:
            return ""
        parts = []
        for item in items:
            if not isinstance(item, dict):
                continue
            start = str(item.get("time_start", ""))
            end = str(item.get("time_end", ""))
            title = str(item.get("title", ""))
            detail = str(item.get("detail", ""))
            item_start_min = self._parse_time_to_minutes(start)
            item_end_min = self._parse_time_to_minutes(end)
            is_current = item_start_min >= 0 and item_end_min >= 0 and item_start_min <= current_min < item_end_min
            marker = " ←此刻" if is_current else ""
            line = f"  {start}-{end} {title}{marker}"
            if detail:
                line += f"\n    {detail}"
            parts.append(line)
        return "\n".join(parts)

    def _build_enhanced_slot_text(self, schedule_data: dict, current_time_str: str) -> str:
        timeline = schedule_data.get("timeline")
        if not timeline or not isinstance(timeline, list):
            return ""
        current_min = self._parse_time_to_minutes(current_time_str)
        if current_min < 0:
            return ""
        current_idx = -1
        for i, slot in enumerate(timeline):
            if not isinstance(slot, dict):
                continue
            start_min = self._parse_time_to_minutes(str(slot.get("time_start", "")))
            end_min = self._parse_time_to_minutes(str(slot.get("time_end", "")))
            if start_min < 0 or end_min < 0:
                continue
            if start_min <= current_min < end_min:
                current_idx = i
                break
        if current_idx < 0:
            min_dist = float("inf")
            for i, slot in enumerate(timeline):
                if not isinstance(slot, dict):
                    continue
                start_min = self._parse_time_to_minutes(str(slot.get("time_start", "")))
                end_min = self._parse_time_to_minutes(str(slot.get("time_end", "")))
                if start_min < 0 or end_min < 0:
                    continue
                dist = min(abs(current_min - start_min), abs(current_min - end_min))
                if dist < min_dist:
                    min_dist = dist
                    current_idx = i
        if current_idx < 0:
            return ""
        parts = []
        if current_idx > 0:
            prev = timeline[current_idx - 1]
            if isinstance(prev, dict):
                parts.append(f"## 前一时段\n{self._format_timeline_slot(prev)}")
            else:
                parts.append("## 前一时段\n（今日起始时段）")
        else:
            parts.append("## 前一时段\n（今日起始时段）")
        curr = timeline[current_idx]
        parts.append(f"## 当前时段\n{self._format_timeline_slot(curr)}")
        sub_events = schedule_data.get("sub_events")
        sub_event_text = self._extract_sub_events_text(sub_events, current_idx, current_min)
        if sub_event_text:
            parts.append(f"## 当前时段细分\n{sub_event_text}")
        if current_idx < len(timeline) - 1:
            next_slot = timeline[current_idx + 1]
            if isinstance(next_slot, dict):
                parts.append(f"## 后一时段\n{self._format_timeline_slot(next_slot)}")
            else:
                parts.append("## 后一时段\n（已是今日最后时段）")
        else:
            parts.append("## 后一时段\n（已是今日最后时段）")
        return "\n\n".join(parts)

    def _parse_delta_and_body(self, result: str) -> str:
        delta_match = re.match(r"^【变】(.+?)(?:\n|\r\n)([\s\S]*)", result)
        if delta_match:
            body = delta_match.group(2).strip()
            if body:
                return body
        return result

    def _get_thinking_template(self, persona_name: str | None = None) -> str:
        override = str(self._persona_value(persona_name, "thinking_prompt_template_override", "") or "").strip()
        if override:
            return override
        default_template = str(self.config.get("default_thinking_prompt_template", "") or "").strip()
        if default_template:
            return default_template
        return self._get_default_template()

    async def generate(
        self,
        current_time: str,
        session_id: Optional[str] = None,
        last_awareness_text: Optional[str] = None,
        persona_name: Optional[str] = None,
        persona_desc: Optional[str] = None,
        tools=None,
    ) -> tuple[Optional[str], Any]:
        """生成思考内容"""
        try:
            canonical_persona = self._canonical_persona_name(persona_name)
            auto_ensure_schedule = bool(self._persona_value(canonical_persona, "reflection_auto_ensure_today_schedule", True))
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            if auto_ensure_schedule:
                schedule_result = await self.dependency_manager.ensure_today_schedule(
                    session_id=session_id,
                    persona_name=canonical_persona,
                    persona_desc=persona_desc,
                    target_date=today_str,
                    debug=bool(self.config.get("debug_mode", False)),
                )
                schedule_data = schedule_result.get("data") or {}
                if schedule_result.get("status") == "failed":
                    if self.config.get("debug_mode", False):
                        logger.info(
                            f"[ReflectionGenerator][debug] ensure_today_schedule failed for reflection: "
                            f"persona={canonical_persona}, session={session_id}, target_date={today_str}, reason={schedule_result.get('message', '')}"
                        )
                    schedule_data = {}
            else:
                schedule_data = await self.dependency_manager.get_schedule_data(
                    session_id=session_id,
                    persona_name=canonical_persona,
                    target_date=today_str,
                    debug=bool(self.config.get("debug_mode", False)),
                )

            recent_messages = []
            counterpart_info = {
                "sender_id": None,
                "sender_name": None,
                "group_id": None,
                "display_name": "当前对象",
            }
            if session_id:
                context_rounds = self._safe_non_negative_int(self._persona_value(canonical_persona, "context_rounds", 2), default=2)
                recent_messages = await self.message_cache.get_recent_messages(session_id, context_rounds)
                counterpart_info = await self.message_cache.get_latest_counterpart(session_id)
                recent_messages = self._sanitize_recent_messages(recent_messages, counterpart_info)

            resolved_name = canonical_persona
            resolved_desc = persona_desc
            if session_id and (not resolved_name or not resolved_desc):
                persona_ctx = await self.dependency_manager.resolve_persona_context(session_id)
                resolved_name = resolved_name or self._canonical_persona_name(persona_ctx.get("persona_name") or persona_ctx.get("persona_id"))
                resolved_desc = resolved_desc or persona_ctx.get("persona_desc")

            if self.config.get("debug_mode", False):
                logger.info(
                    f"[ReflectionGenerator][debug] generate params: session={session_id}, persona={resolved_name}, "
                    f"recent_messages={len(recent_messages)}, last_awareness_len={len(last_awareness_text or '')}, "
                    f"schedule_outfit={str(schedule_data.get('outfit', ''))[:120]}, schedule={str(schedule_data.get('schedule', ''))[:300]}, "
                    f"auto_ensure_schedule={auto_ensure_schedule}, target_date={today_str}"
                )

            prompt = self._build_prompt(
                current_time,
                schedule_data,
                recent_messages,
                last_awareness_text,
                resolved_name,
                resolved_desc,
            )
            result, raw_response = await self._call_llm(prompt, resolved_name, tools=tools)
            if result:
                body = self._parse_delta_and_body(result)
                return self._format_result(current_time, body), raw_response
            return None, raw_response
        except Exception as e:
            logger.error(f"[ReflectionGenerator] 生成思考失败: {e}", exc_info=True)
            return None, None

    def _build_prompt(
        self,
        current_time: str,
        schedule_data: dict,
        recent_messages: list[str],
        last_awareness_text: Optional[str] = None,
        persona_name: Optional[str] = None,
        persona_desc: Optional[str] = None,
    ) -> str:
        """构建思考提示词"""
        template = self._get_thinking_template(persona_name)
        template = self._ensure_recent_awareness_placeholder(template)
        template = self._ensure_mode_definition_placeholder(template)
        template = self._ensure_current_slot_placeholder(template)
        template = self._ensure_delta_placeholder(template)

        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekday_names[datetime.datetime.now().weekday()]

        mode = self._persona_value(persona_name, "thinking_mode", "适量")
        if mode not in ("简洁", "适量", "丰富"):
            mode = "丰富"
        if mode == "简洁":
            length_hint = "控制在30字以内"
            mode_definition_enhanced = "- 简洁：一瞬间的意识截面（30字内）。只写此刻最核心的一个念头或感受。"
            mode_definition_basic = "- 简洁：一瞬间的意识截面（30字内）。只写此刻最核心的一个念头或感受。"
        elif mode == "适量":
            length_hint = "控制在80字以内"
            mode_definition_enhanced = "- 适量：一小段完整的当下体验（80字内）。围绕当前细分活动，写出此刻在这个活动里的具体经历。"
            mode_definition_basic = "- 适量：一小段完整的当下体验（80字内）。围绕当前活动，写出此刻在这个活动里的具体经历。"
        else:
            length_hint = "控制在120-150字"
            mode_definition_enhanced = "- 丰富：更有生活颗粒度的现场切片（120-150字）。围绕当前细分活动，三个维度须自然融合：活动中的具体经历、环境感知、从当前经历自然生发的意识偏移。"
            mode_definition_basic = "- 丰富：更有生活颗粒度的现场切片（120-150字）。围绕当前活动，三个维度须自然融合：活动中的具体经历、环境感知、从当前经历自然生发的意识偏移。"

        recent_messages_str = "\n".join(recent_messages) if recent_messages else "（暂无最近对话）"

        outfit = schedule_data.get("outfit", "")
        schedule = schedule_data.get("schedule", "")
        state_info = outfit if outfit else "（暂无穿着信息）"

        timeline = schedule_data.get("timeline")
        has_valid_timeline = isinstance(timeline, list) and len(timeline) > 0
        used_enhanced_format = False

        if has_valid_timeline:
            current_slot_text = self._build_enhanced_slot_text(schedule_data, current_time)
            if current_slot_text:
                used_enhanced_format = True
            else:
                slot_result = self._extract_current_schedule_slot(schedule, current_time)
                current_slot_text = slot_result["slots_text"] if slot_result["found"] else ""
        else:
            slot_result = self._extract_current_schedule_slot(schedule, current_time)
            current_slot_text = slot_result["slots_text"] if slot_result["found"] else ""

        persona_name_text = persona_name or "（未绑定人格）"
        persona_desc_text = persona_desc or "（未获取到人格设定文本，请保持稳定自然、贴近既有人设气质的语气）"
        recent_awareness_text = last_awareness_text or "（暂无最近思考）"

        if used_enhanced_format:
            rule_1 = """活动锚定——细分定锚，骨架定向
当前细分活动是此刻的精确锚点，当前日程骨架是今日的方向感知。
思考不重复活动名称，而是写出活动中的具体经历。
- 思考必须从当前细分活动出发，不得矛盾
- 思考可体现角色对今日轨迹的模糊感知，但这是自然感受而非日程复述"""
            rule_3 = """- 第一优先：当前细分活动中的具体经历
- 第二优先：从当前经历中自然生发的偏移
- 第三优先：此刻最突出的情绪
- 第四优先：对今日轨迹的模糊感知
- 第五优先：最近对话带来的轻微情绪残留（最多一句话带过，不可展开）
- 第六优先：最近思考提供的连续性线索"""
            mode_definition = mode_definition_enhanced
        else:
            rule_1 = """活动锚定——日程定锚
当前时段日程是此刻的方向感知。
思考不重复活动名称，而是写出活动中的具体经历。
- 思考必须与当前时段日程对齐，不得矛盾
- 思考可体现角色对今日轨迹的模糊感知，但这是自然感受而非日程复述"""
            rule_3 = """- 第一优先：当前活动中的具体经历
- 第二优先：从当前经历中自然生发的偏移
- 第三优先：此刻最突出的情绪
- 第四优先：对今日轨迹的模糊感知
- 第五优先：最近对话带来的轻微情绪残留（最多一句话带过，不可展开）
- 第六优先：最近思考提供的连续性线索"""
            mode_definition = mode_definition_basic

        format_kwargs = dict(
            time=current_time,
            weekday=weekday,
            persona_name=persona_name_text,
            persona_desc=persona_desc_text,
            state_info=state_info.strip(),
            current_slot=current_slot_text,
            recent_messages=recent_messages_str,
            mode_definition=mode_definition,
            length_hint=length_hint,
            recent_awareness=recent_awareness_text,
            rule_1=rule_1,
            rule_3=rule_3,
        )
        try:
            prompt = template.format(**format_kwargs)
        except KeyError as e:
            missing_key = str(e).strip("'")
            logger.warning(f"[ReflectionGenerator] 模板变量缺失: {missing_key}，使用部分格式化")
            from string import Formatter
            known_keys = set(format_kwargs.keys())
            used_keys = {fname for _, fname, _, _ in Formatter().parse(template) if fname}
            for k in used_keys - known_keys:
                format_kwargs[k] = f"{{{{{k}}}}}"
            prompt = template.format(**format_kwargs)

        if self.config.get("debug_mode", False):
            logger.info(
                f"[ReflectionGenerator][debug] prompt state_info={state_info.strip()[:500]}, "
                f"used_enhanced_format={used_enhanced_format}, persona={persona_name_text}"
            )
        return prompt

    def _ensure_recent_awareness_placeholder(self, template: str) -> str:
        if "{recent_awareness}" in template or "## 最近思考" in template:
            return template
        marker = "## 核心规则"
        awareness_block = "\n\n## 最近思考\n{recent_awareness}\n"
        if marker in template:
            return template.replace(marker, awareness_block + "\n" + marker, 1)
        return template + awareness_block

    def _ensure_mode_definition_placeholder(self, template: str) -> str:
        if "{mode_definition}" in template:
            return template
        pattern = r"【模式定义】\s*[\s\S]*?(?=\n\n【兜底规则】|\n【兜底规则】|$)"
        replacement = "【模式定义】\n{mode_definition}"
        if re.search(pattern, template):
            return re.sub(pattern, replacement, template, count=1)
        marker = "【兜底规则】"
        mode_block = "\n\n【模式定义】\n{mode_definition}\n"
        if marker in template:
            return template.replace(marker, mode_block + "\n" + marker, 1)
        return template + mode_block

    def _ensure_current_slot_placeholder(self, template: str) -> str:
        if "{current_slot}" in template or "## 当前时段日程" in template:
            return template
        marker = "## 当前互动背景"
        slot_block = "\n## 当前时段日程（必须以此为思考主线）\n{current_slot}\n\n"
        if marker in template:
            return template.replace(marker, slot_block + marker, 1)
        marker2 = "## 核心规则"
        if marker2 in template:
            return template.replace(marker2, slot_block + "\n" + marker2, 1)
        return slot_block + template

    def _ensure_delta_placeholder(self, template: str) -> str:
        if "【变】" in template:
            return template
        marker = "【模式定义】"
        delta_hint = "\n输出格式（严格遵守）：\n第一行：【变】用一句话概括本次思考与上一条思考的差异点\n第二行起：第一人称正文\n\n"
        if marker in template:
            return template.replace(marker, delta_hint + marker, 1)
        return template + delta_hint

    def _get_default_template(self) -> str:
        return """请你以第一人称，写出“此刻脑海里最鲜活的一小段意识切片”——记录我现在这一刻正处于怎样的状态、注意力落在哪里、心里掠过了什么。

## 当前身份
- 当前人格名称：{persona_name}
- 当前人格设定：
{persona_desc}

## 当前时空锚点
- 时间：{time}（{weekday}）
- 当前穿着：
{state_info}

## 当前时段日程（必须以此为思考主线）
{current_slot}

## 当前互动背景
以下对话仅用于感知此刻的互动余温，不作为思考素材。
{recent_messages}

## 最近思考
{recent_awareness}

【兜底规则】
- 若【当前时段日程】为空，默认处于自然放松的普通日常状态，结合互动背景写此刻意识流。
- 若互动背景也为空，就聚焦当前场景中的身体状态、注意力落点和自然情绪。

## 核心规则（绝对不可违背）

1. 【活动锚定】
{rule_1}

2. 【思考不是复述日程】
日程只是现实骨架，不是正文模板。
- 你要写的是“这一刻真正浮上来的意识内容”。
- 在日程提供的场景框架内，写出真实的内心体验和意识流动。

3. 【信息选择顺序必须明确】
{rule_3}

4. 【对话内容严格降权】
最近对话只能提供极其轻微的情绪底色，绝对不能：
- 让对话中的具体事件、措辞、人物成为思考的核心内容
- 反复回想对方说过的原话
- 把思考写成对对话的回应或延续
- 若对话内容与当前日程场景无关，直接忽略。

5. 【服装只可一笔带过】
即使穿着信息里存在大量描写，正文也不得展开复述服装细节。
- 服装仅在确实与当前行动直接相关时，才可一笔带过。
- 若确实必须提到，最多一句概括带过，禁止详细描述材质、款式、颜色、搭配、饰品。

6. 【必须写出与上一条思考的差异】
- 禁止重复上一条思考的情绪焦点、动作焦点或场景焦点。
- 以下四类至少变化其一：动作推进、注意力转移、情绪浓淡变化、环境或位置变化。

7. 【最近思考只用于保持连续性】
上方【最近思考】只用于帮助你知道“今天已经想过什么”。
- 禁止只换措辞而不推进内容。

8. 【时间边界只限今天与此刻】
- 正文只落在“当前时刻”与“今天范围内”的体验。
- 禁止在正文出现指向未来的时间词：明天、明早、明晚、后天、下周、过几天、改天 等。
- 如果出现轻微挂念，也要立刻收回到此刻。

9. 【朴素自然】
- 用朴素的文字写出真实的想法，禁止夸张化、文学化、强行升华。
- 允许只写单一意识落点（一个念头、一丝动作情绪或一点余韵），不必凑满“动作 + 身体感受 + 内心想法”三项。
（丰富模式须自然覆盖“活动经历 / 环境感知 / 意识偏移”三维度，不必机械平均用力，也不得写成 1./2./3. 并列清单；简洁与适量模式不受此限。）

10. 【这是自我思考，不是对外说话】
- 正文以第一人称内省；若提及互动对象，一律用第三人称。
- 禁止写成打招呼、安慰、回应、汇报。

【模式定义】
{mode_definition}

## 输出规范
输出长度要求：{length_hint}

输出格式（严格遵守）：
第一行：【变】用一句话概括本次思考与上一条思考的差异点
第二行起：第一人称正文

示例（仅作格式参考，措辞与结构均可变化）：
【变】从发呆出神，变成注意到窗外的雨丝
雨忽然密了，我盯着看了几秒，手指还停在书页边。
"""

    def _normalize_recent_role_prefix(self, text: str) -> str:
        normalized = text
        normalized = re.sub(r"^\s*我的回复\s*[:：]\s*", "我的上一轮回复：", normalized)
        normalized = re.sub(r"^\s*助手\s*[:：]\s*", "我的上一轮回复：", normalized)
        normalized = re.sub(r"^\s*用户\s*[:：]\s*", "当前对话对象消息：", normalized)
        normalized = re.sub(r"^\s*当前对象消息\s*[:：]\s*", "当前对话对象消息：", normalized)
        return normalized

    def _sanitize_recent_messages(self, messages: list[str], counterpart_info: Optional[dict] = None) -> list[str]:
        sanitized: list[str] = []
        counterpart_id = str((counterpart_info or {}).get("sender_id") or "").strip()
        for msg in messages:
            text = (msg or "").strip()
            if not text:
                continue
            if any(token in text for token in self.FUTURE_TIME_PATTERNS):
                continue
            text = self._normalize_recent_role_prefix(text)
            if counterpart_id:
                text = text.replace(f"(ID:{counterpart_id})", "")
            text = re.sub(r"\(ID:[^)]+\)", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            sanitized.append(text)
        return sanitized

    def _safe_non_negative_int(self, value, default: int = 2) -> int:
        try:
            return max(int(value), 0)
        except Exception:
            return default

    async def _call_llm(self, prompt: str, persona_name: str | None = None, tools=None) -> tuple[Optional[str], Any]:
        """调用 LLM，并输出更明确的失败分类日志。返回 (completion_text, raw_response)。"""
        provider_id = self._persona_value(persona_name, "thinking_provider_id", "")
        try:
            if not provider_id:
                provider_id = await self._get_default_provider_id()
            if not provider_id:
                logger.error("[ReflectionGenerator] 无可用思考模型提供商")
                return None, None
            kwargs = {
                "chat_provider_id": provider_id,
                "prompt": prompt,
            }
            if tools is not None:
                kwargs["tools"] = tools
            response = await self.context.llm_generate(**kwargs)
            if response is None:
                logger.error(f"[ReflectionGenerator] provider={provider_id} 返回空响应")
                return None, None
            completion_text = getattr(response, "completion_text", None)
            if completion_text and completion_text.strip():
                return completion_text.strip(), response
            # 修复：当传入 tools 且 LLM 选择调用 Tool 时，completion_text 常为空，这是预期行为而非错误。
            # 若此时打 ERROR 日志会污染日志且误导排查方向，降级为 INFO。
            tools_call_names = getattr(response, "tools_call_name", None) or []
            if tools is not None and tools_call_names:
                logger.info(
                    f"[ReflectionGenerator] 命中 Tool 调用（completion_text 为空属预期）: provider={provider_id}, tools={tools_call_names}"
                )
                return None, response
            logger.error(f"[ReflectionGenerator] provider={provider_id} completion_text 为空")
            return None, response
        except Exception as e:
            err_text = str(e)
            if "no choices" in err_text.lower():
                logger.error(f"[ReflectionGenerator] provider={provider_id} 无 choices: {e}")
            else:
                logger.error(f"[ReflectionGenerator] provider={provider_id} 调用异常: {e}")
            return None, None

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