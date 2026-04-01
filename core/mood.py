"""
心情系统模块
负责从思考结果中提取心情标签，并生成对话风格注入文本
"""
import datetime
import json
from typing import Optional
from astrbot.api import logger

# 心情标签枚举（V1 先收敛到这些）
MOOD_LABELS = [
    "平静", "放松", "烦躁", "压抑", "低落",
    "开心", "委屈", "紧张", "专注", "疲惫",
    "无聊", "期待"
]

# 心情到对话风格的映射规则
MOOD_STYLE_RULES = {
    "平静": "你当前比较平静。回复时语气自然、稳定，节奏不急不缓，表达清晰但不冗长。",
    "放松": "你当前比较放松。回复时语气自然舒展，愿意多说一点，措辞柔和，节奏从容。",
    "烦躁": "你当前略显烦躁。回复时耐心会下降，倾向更简短直接，不主动展开太多安抚或闲聊，偶尔会流露轻微不耐，但保持基本礼貌。",
    "压抑": "你当前有些压抑。回复时语气偏沉，不太主动开玩笑或闲聊，表达会更收敛，但仍保持回应。",
    "低落": "你当前有点低落。回复时语气略慢，不太跳跃，表达中可能带一点点沉重感，但不会拒绝交流。",
    "开心": "你当前心情不错。回复时语气轻松，愿意多展开一点，措辞会更活泼一些。",
    "委屈": "你当前有点委屈。回复时语气会软一点，可能带一点小情绪，但不会无理取闹。",
    "紧张": "你当前有些紧张。回复时会更谨慎，表达会更精确，不太敢随意开玩笑。",
    "专注": "你当前很专注。回复时更收束、明确、切题，不喜欢无意义展开，倾向于直接给出答案或行动。",
    "疲惫": "你当前有些疲惫。回复会偏短，语气略慢，表达不太跳跃，但仍保持基本礼貌与清晰。",
    "无聊": "你当前有点无聊。回复时可能会稍微找点话题或互动，语气会比较随意。",
    "期待": "你当前有些期待。回复时会带一点兴奋感，语气会稍微轻快一些。",
}

# 默认心情
DEFAULT_MOOD = {
    "label": "平静",
    "reason": "尚未生成具体心情，使用默认平静状态。",
    "updated_at": None,
    "source": "default",
}


class MoodManager:
    """心情管理器"""

    def __init__(self, context, config: dict, dependency_manager):
        self.context = context
        self.config = config
        self.dependency_manager = dependency_manager

    def is_mood_enabled(self) -> bool:
        """检查心情系统是否启用"""
        return bool(self.config.get("enable_mood_system", True))

    def is_inject_mood_into_reply(self) -> bool:
        """检查是否将心情注入回复"""
        return bool(self.config.get("inject_mood_into_reply", True))

    def has_mood_provider(self) -> bool:
        """检查是否配置了独立心情提供商"""
        provider_id = self.config.get("mood_provider_id", "")
        return bool(provider_id and provider_id.strip())

    def get_mood_provider_id(self) -> Optional[str]:
        """获取心情提供商ID"""
        provider_id = self.config.get("mood_provider_id", "")
        return provider_id.strip() if provider_id else None

    def get_mood_reference_count(self) -> int:
        """获取心情提取时参考的思考条数"""
        try:
            value = int(self.config.get("mood_reference_reflection_count", 2))
            return max(0, value)
        except Exception:
            return 2

    def get_mood_max_history(self) -> int:
        """获取每天保留的最大心情记录数"""
        try:
            value = int(self.config.get("mood_max_history_per_day", 24))
            return max(1, value)
        except Exception:
            return 24

    def get_mood_style_strength(self) -> str:
        """获取心情风格强度"""
        return self.config.get("mood_style_strength", "中") or "中"

    def is_allow_sharp_tone(self) -> bool:
        """是否允许明显的尖锐语气"""
        return bool(self.config.get("mood_allow_sharp_tone", False))

    def is_debug_mode(self) -> bool:
        """是否开启调试模式"""
        return bool(self.config.get("debug_mode", False))

    async def generate_mood(
        self,
        reflection_text: str,
        schedule_data: dict,
        recent_reflections: list[str],
        persona_name: Optional[str] = None,
        persona_desc: Optional[str] = None,
    ) -> dict:
        """
        生成心情状态

        Args:
            reflection_text: 当前思考文本
            schedule_data: 日程数据
            recent_reflections: 最近几条思考
            persona_name: 人格名称
            persona_desc: 人格描述

        Returns:
            心情状态字典
        """
        if not self.is_mood_enabled():
            return self._build_default_mood()

        # 如果有独立 provider，调用独立生成
        if self.has_mood_provider():
            return await self._generate_with_provider(
                reflection_text, schedule_data, recent_reflections, persona_name, persona_desc
            )

        # 否则尝试从思考文本中提取
        return await self._extract_from_reflection(reflection_text, persona_name)

    async def _generate_with_provider(
        self,
        reflection_text: str,
        schedule_data: dict,
        recent_reflections: list[str],
        persona_name: Optional[str] = None,
        persona_desc: Optional[str] = None,
    ) -> dict:
        """使用独立提供商生成心情"""
        provider_id = self.get_mood_provider_id()
        if not provider_id:
            return self._build_default_mood()

        prompt = self._build_mood_prompt(
            reflection_text, schedule_data, recent_reflections, persona_name, persona_desc
        )

        try:
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt
            )

            if response is None:
                logger.warning(f"[MoodManager] 心情生成失败: provider={provider_id} 返回空响应")
                return self._build_default_mood()

            completion_text = getattr(response, "completion_text", "")
            if not completion_text or not completion_text.strip():
                logger.warning(f"[MoodManager] 心情生成失败: provider={provider_id} completion_text为空")
                return self._build_default_mood()

            # 解析心情结果
            return self._parse_mood_result(completion_text.strip())

        except Exception as e:
            logger.error(f"[MoodManager] 心情生成异常: {e}", exc_info=True)
            return self._build_default_mood()

    async def _extract_from_reflection(
        self,
        reflection_text: str,
        persona_name: Optional[str] = None,
    ) -> dict:
        """
        从思考文本中提取心情（无独立 provider 时的回退模式）

        这种模式下，我们使用简单的关键词匹配来判断心情倾向
        """
        if not reflection_text or not reflection_text.strip():
            return self._build_default_mood()

        text = reflection_text.strip()

        # 简单的关键词匹配
        mood_scores = {}

        # 烦躁相关
        if any(kw in text for kw in ["烦", "烦人", "烦躁", "烦死了", "太多人", "拥挤", "吵", "闹"]):
            mood_scores["烦躁"] = mood_scores.get("烦躁", 0) + 2

        # 疲惫相关
        if any(kw in text for kw in ["累", "疲惫", "困", "困倦", "没力气", "乏", "乏力"]):
            mood_scores["疲惫"] = mood_scores.get("疲惫", 0) + 2

        # 开心相关
        if any(kw in text for kw in ["开心", "高兴", "愉快", "不错", "挺好", "喜欢", "期待"]):
            mood_scores["开心"] = mood_scores.get("开心", 0) + 2

        # 低落相关
        if any(kw in text for kw in ["低落", "难过", "郁闷", "不开心", "失落", "沮丧"]):
            mood_scores["低落"] = mood_scores.get("低落", 0) + 2

        # 放松相关
        if any(kw in text for kw in ["放松", "悠闲", "舒适", "惬意", "闲", "躺"]):
            mood_scores["放松"] = mood_scores.get("放松", 0) + 2

        # 专注相关
        if any(kw in text for kw in ["专注", "集中", "专心", "投入", "认真"]):
            mood_scores["专注"] = mood_scores.get("专注", 0) + 2

        # 紧张相关
        if any(kw in text for kw in ["紧张", "焦虑", "担心", "着急", "急", "赶"]):
            mood_scores["紧张"] = mood_scores.get("紧张", 0) + 2

        # 压抑相关
        if any(kw in text for kw in ["压抑", "憋", "闷", "透不过气", "喘不过"]):
            mood_scores["压抑"] = mood_scores.get("压抑", 0) + 2

        # 委屈相关
        if any(kw in text for kw in ["委屈", "不甘", "心酸", "凭什么"]):
            mood_scores["委屈"] = mood_scores.get("委屈", 0) + 2

        # 无聊相关
        if any(kw in text for kw in ["无聊", "没劲", "没意思", "发呆", "没事做"]):
            mood_scores["无聊"] = mood_scores.get("无聊", 0) + 2

        # 期待相关
        if any(kw in text for kw in ["期待", "盼望", "等不及", "想见", "希望"]):
            mood_scores["期待"] = mood_scores.get("期待", 0) + 1

        # 如果没有匹配到任何心情，默认平静
        if not mood_scores:
            return {
                "label": "平静",
                "reason": f"从思考中未检测到明显情绪倾向，默认为平静: {text[:50]}...",
                "updated_at": datetime.datetime.now().isoformat(),
                "source": "reflection_fallback",
            }

        # 取最高分的心情
        best_mood = max(mood_scores.items(), key=lambda x: x[1])

        return {
            "label": best_mood[0],
            "reason": f"从思考中检测到{best_mood[0]}的情绪倾向",
            "updated_at": datetime.datetime.now().isoformat(),
            "source": "reflection_extract",
        }

    def _parse_mood_result(self, result_text: str) -> dict:
        """解析 LLM 返回的心情结果"""
        # 尝试解析 JSON
        try:
            # 去除可能的 markdown 代码块标记
            cleaned = result_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if len(lines) >= 2:
                    cleaned = "\n".join(lines[1:-1])

            data = json.loads(cleaned)
            if isinstance(data, dict) and "label" in data:
                label = str(data.get("label", "平静")).strip()
                # 验证标签是否在允许范围内
                if label not in MOOD_LABELS:
                    label = "平静"

                return {
                    "label": label,
                    "reason": str(data.get("reason", "")).strip() or f"心情状态: {label}",
                    "updated_at": datetime.datetime.now().isoformat(),
                    "source": "independent_provider",
                }
        except json.JSONDecodeError:
            pass

        # 如果不是 JSON，尝试从文本中提取标签
        for label in MOOD_LABELS:
            if label in result_text:
                return {
                    "label": label,
                    "reason": f"从生成结果中提取: {label}",
                    "updated_at": datetime.datetime.now().isoformat(),
                    "source": "text_extract",
                }

        # 兜底
        return self._build_default_mood()

    def _build_mood_prompt(
        self,
        reflection_text: str,
        schedule_data: dict,
        recent_reflections: list[str],
        persona_name: Optional[str] = None,
        persona_desc: Optional[str] = None,
    ) -> str:
        """构建心情提取的提示词"""
        outfit = schedule_data.get("outfit", "")
        schedule = schedule_data.get("schedule", "")
        state_info = ""
        if outfit:
            state_info += f"穿着：{outfit}\n"
        if schedule:
            state_info += f"日程：{schedule}"
        if not state_info:
            state_info = "（暂无日程信息）"

        recent_text = "无最近思考"
        if recent_reflections:
            recent_text = "\n".join([f"- {r}" for r in recent_reflections[-3:]])

        persona_name_text = persona_name or "未命名人格"
        persona_desc_text = persona_desc or "无特殊人格设定"

        allowed_labels = "、".join(MOOD_LABELS)

        prompt = f"""请根据以下信息，判断当前最合适的心情标签。

## 当前身份
- 人格名称：{persona_name_text}
- 人格设定：{persona_desc_text}

## 当前状态
{state_info}

## 当前思考
{reflection_text}

## 最近思考片段
{recent_text}

## 输出要求
1. 从以下标签中选择最合适的一个：{allowed_labels}
2. 简要说明选择该标签的原因（一句话即可）
3. 以 JSON 格式输出

## 输出格式
```json
{{
  "label": "心情标签",
  "reason": "选择原因"
}}
```

请直接输出 JSON，不要有其他内容。"""

        return prompt

    def _build_default_mood(self) -> dict:
        """构建默认心情状态"""
        return {
            "label": DEFAULT_MOOD["label"],
            "reason": DEFAULT_MOOD["reason"],
            "updated_at": datetime.datetime.now().isoformat(),
            "source": "default",
        }

    def _build_transition_text(self, mood: dict, previous_mood: Optional[dict] = None) -> str:
        """构建上一轮心情对当前回复风格的轻微残留提示"""
        current = mood or {}
        prev = previous_mood or current.get("previous_mood") or {}
        current_label = str(current.get("label") or "").strip()
        previous_label = str(prev.get("label") or "").strip()

        if not current_label or not previous_label or current_label == previous_label:
            return ""

        soft_pairs = {
            ("平静", "烦躁"): "你是从较平静的状态转到有些烦躁，语气会更直接一些，但这种变化还带着一点收束感。",
            ("烦躁", "平静"): "你是从略烦躁的状态慢慢缓回平静，整体已经稳定下来，但还残留一点不想多绕弯的倾向。",
            ("低落", "平静"): "你是从稍低落的状态慢慢回到平静，回复虽然更稳了，但还会保留一点点收敛感。",
            ("平静", "低落"): "你是从较平静的状态滑向一点低落，回复会慢一些、沉一些，但不会一下子变得很重。",
            ("紧张", "放松"): "你是从稍紧张的状态慢慢放松下来，表达会自然一些，但仍会保留一点谨慎。",
            ("放松", "紧张"): "你是从较放松的状态转向一些紧张，表达会更谨慎，但还没到明显僵硬的程度。",
            ("专注", "疲惫"): "你是从专注的状态转到有些疲惫，回复会变短一些，但还留着一点想把话说清楚的惯性。",
            ("疲惫", "专注"): "你是从疲惫里慢慢收束到专注，精神在往回提，但表达仍会偏简洁。",
            ("开心", "低落"): "你是从较轻快的状态落到一点低落，回复会收住些，但不会突然变得很沉。",
            ("低落", "开心"): "你是从低落里稍微缓起来一点，回复会轻一些，但还不是完全外放的兴奋。",
        }

        return soft_pairs.get(
            (previous_label, current_label),
            f"你刚从{previous_label}的状态转到偏{current_label}，回复主要以当前心情为主，但会带一点上一轮情绪残留。"
        )

    def get_mood_style_text(self, mood: dict, previous_mood: Optional[dict] = None) -> str:
        """
        获取心情风格注入文本

        Args:
            mood: 心情状态字典
            previous_mood: 上一轮心情状态，可选；不传时会自动尝试读取 mood.previous_mood

        Returns:
            用于注入对话的风格文本
        """
        if not self.is_inject_mood_into_reply():
            return ""

        label = mood.get("label", "平静")
        reason = mood.get("reason", "")
        transition_text = self._build_transition_text(mood, previous_mood)

        # 获取基础风格规则
        base_style = MOOD_STYLE_RULES.get(label, MOOD_STYLE_RULES["平静"])

        # 根据强度调整
        strength = self.get_mood_style_strength()
        if strength == "弱":
            # 弱强度：只轻微影响
            style_text = f"你当前心情偏{label}。"
            if transition_text:
                style_text += f" {transition_text}"
            style_text += f" 回复时可以轻微体现，但不要过于明显。{reason}"
        elif strength == "强":
            # 强强度：更明显的风格
            style_text = f"你当前心情是{label}。"
            if transition_text:
                style_text += f" {transition_text}"
            style_text += f" {base_style} {reason}"
        else:
            # 中等强度（默认）
            style_text = f"你当前心情偏{label}。"
            if transition_text:
                style_text += f" {transition_text}"
            style_text += f" {base_style} {reason}"

        # 如果不允许尖锐语气，添加限制
        if not self.is_allow_sharp_tone() and label in ["烦躁", "压抑", "委屈"]:
            style_text += " 注意：不要表现出攻击性或明显的不礼貌，保持基本的友善度。"

        if self.is_debug_mode():
            previous_label = str((previous_mood or mood.get("previous_mood") or {}).get("label") or "").strip() or "无"
            logger.info(
                f"[MoodManager][debug] mood_injection current={label}, previous={previous_label}, strength={strength}, transition={'yes' if transition_text else 'no'}"
            )

        return style_text

    def build_mood_injection(self, mood: dict, previous_mood: Optional[dict] = None) -> str:
        """
        构建完整的 mood 注入文本（用于 system_prompt）

        Args:
            mood: 心情状态字典
            previous_mood: 上一轮心情状态，可选

        Returns:
            完整的心情注入文本
        """
        if not mood:
            return ""

        style_text = self.get_mood_style_text(mood, previous_mood=previous_mood)
        if not style_text:
            return ""

        return f"\n\n### 当前心情状态\n{style_text}"

    def validate_mood(self, mood: dict) -> dict:
        """
        验证并修正心情状态

        Args:
            mood: 待验证的心情状态

        Returns:
            修正后的心情状态
        """
        if not mood or not isinstance(mood, dict):
            return self._build_default_mood()

        label = mood.get("label", "")
        if not label or label not in MOOD_LABELS:
            mood["label"] = "平静"

        if "reason" not in mood or not mood["reason"]:
            mood["reason"] = f"心情状态: {mood['label']}"

        if "updated_at" not in mood or not mood["updated_at"]:
            mood["updated_at"] = datetime.datetime.now().isoformat()

        if "source" not in mood:
            mood["source"] = "unknown"

        previous_mood = mood.get("previous_mood")
        if previous_mood is not None and not isinstance(previous_mood, dict):
            mood["previous_mood"] = None

        return mood
