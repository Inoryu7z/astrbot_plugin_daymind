[![DayMind Counter](https://count.getloli.com/get/@Inoryu7z.daymind?theme=miku)](https://github.com/Inoryu7z/astrbot_plugin_daymind)

# 🌙 DayMind · 心智手记

给 Bot 一条完整的当日心路，让陪伴不止于单次对话。

**DayMind** 是一个心智链路插件，专注于让 Bot 在一天之中持续知道：
**自己此刻在想什么、正处于什么状态、这一天又留下了什么。**

它会把零散的当下感受积累成连续的心路轨迹，让 Bot 的回应不再像每次都从空白开始，而像是真的正在经历今天。

更新日志：[CHANGELOG.md](https://github.com/Inoryu7z/astrbot_plugin_daymind/blob/main/CHANGELOG.md)

---

## ✨ 核心特性

### 🧠 自动思考

按设定间隔生成当下思考，并把"本日状态"注入后续对话。思考会综合参考：当前时间、今日日程、最近对话、当前人格设定、当前人格最近思考。

这样产生的不是一段孤立文本，而是一种持续更新的"今天的状态"：现在的心情、正在关注什么、刚刚经历的事留下了什么影响。Bot 的每一次回复，都更像带着此刻的心境在说话。

**DayFlow 细分骨架联动**：若 DayFlow 启用了 `enable_subdivision` 生成了 sub_events，思考提示词会自动适配——从"复述日程大骨架"转向"写出当前时段细分活动中的具体经历"。例如日程骨架是"下午出门逛街"，思考会基于 sub_events 中的"在咖啡厅点了一杯手冲"等具体片段展开，而不是泛泛地"下午逛街去了"。

### 💜 心情系统

每次思考后会生成心情标签，并影响下次思考前的对话风格。心情持续到下次思考，形成连续的心理状态。

**双模式运行**：
- **独立 Provider 模式**（推荐）：配置独立的心情模型提供商，生成更精准的心情标签
- **内联提取模式**：无独立提供商时，从思考文本中自动提取心情倾向

心情对对话的影响举例：`平静`语气自然稳定、`烦躁`回复更简短直接、`疲惫`回复偏短略慢、`开心`语气轻松愿意多展开、`专注`回复更切题不喜欢闲聊。

### 🌙 梦境构建

在睡眠时段生成 1-2 个象征性、情感驱动的梦境片段，让 Bot 的内心世界在夜晚也不空白。

- 梦境基于入睡前心情、最后一条思考和对话余温生成，不受日程约束
- 入睡后 15 分钟开始做梦，两次梦间隔至少 30 分钟
- **梦境余韵**：以极低强度影响醒来后的心情状态
- **梦境记忆**：醒来后首次对话时注入 system_prompt，Bot 可选择自然提及梦境

梦境与清醒思考的关键差异：清醒思考以日程为准、追求逻辑连贯；梦境可脱离现实、允许碎片化和场景跳跃、只留下若有若无的余韵。

### 📓 自动日记

在指定时间自动生成今日日记。日记综合今日日程、当前人格的当日思考流、人格设定、最近历史日记、今日心情变化轨迹，还原一天落幕时的真实心境。

最终沉淀下来的不只是今天发生了什么，更是：今天最重要的情绪是什么、哪些念头值得被留下来、这个人格会怎样理解今天。

### 🖼️ 日记图片渲染

支持将日记渲染为纸质手写风格的图片发送，而非纯文字。

- 暖色米白纸张纹理 + 手写体正文 + 装饰线
- 自动下载中文字体，支持国内镜像回退
- 渲染失败时自动回退到纯文字发送

通过人格级配置 `enable_diary_image` 开启。

### 🗣️ 思考后主动对话

思考时 LLM 可选择调用 `daymind_want_to_chat` 工具，若被调用，DayMind 会使用对话模型生成一条自然消息主动发送给配置的目标。发送的消息会写入对话历史，确保上下文连贯。

三档控制：
- **关闭**：不提供此工具，LLM 无法触发主动对话
- **低频**：仅在思考中产生强烈交流倾向时才调用
- **普通**：LLM 自行判断是否需要主动对话

同一推送目标两次主动对话之间有冷却时间（默认 90 分钟），防止频繁打扰。

### 🔗 自动补全链路

思考或日记生成前会自动检测今日日程是否存在，若缺失可自动调用 DayFlow 补生成今日日程，再继续生成思考或日记，避免因日程空了导致思考没素材、日记生成失败。可通过 `reflection_auto_ensure_today_schedule` 开关控制。

### 🧩 多人格隔离

思考流、日记、心情状态、本地存储全部按人格分桶，不同人格不会共用同一份当日状态与历史轨迹。同一个 Bot 下的不同人格，可以各自拥有独立连续的"今天"。

配合**人格白名单机制**：只有命中白名单的人格才会启用 DayMind，未命中直接跳过；思考、心情与日记共用同一套白名单。

### ⏱️ 思考调度随机抖动

在固定思考周期基础上加入随机抖动，每轮重新计算，用于错开并发高峰，适合多实例 / 多人格并行场景。

### 🧷 内容管理

- 日记与当日思考流星标功能，重要内容永久留存
- 支持为单篇日记、单日思考流添加专属备注
- 可自定义内容保留策略，灵活管理历史文件
- 支持一键清空当前人格的今日思考流，重置当日状态

---

## 🧩 推荐搭配插件

| 插件 | 作用 |
|------|------|
| `astrbot_plugin_dayflow_life_scheduler` | 提供天气、日程、穿着等现实轨迹，让思考与日记更贴近生活；可读取 DayMind 心情状态调整日程生成 |
| `astrbot_plugin_livingmemory` | 让日记进入长期记忆系统，支持后续召回与追踪 |

### 🗂️ LivingMemory 联动

启用日记写入 LivingMemory 后，生成的日记可进入长期记忆。当重复生成今日日记时，旧记录不会被物理删除，旧 diary memory 会被标记为 `deleted`，保留追踪痕迹。

### 🌊 DayFlow 协同

DayFlow 可以读取 DayMind 的心情状态，用于调整日程生成的风格和节奏、根据心情推荐不同类型的活动，让日程更贴合当前心理状态。

---

## 🎮 可用指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/daymind_status` | 管理员 | 查看当前状态、当前人格、白名单人格、今日思考次数、当前心情等信息 |
| `/查看心情` | 所有人 | 查看当前人格的心情状态和风格影响 |
| `/今日心情` | 所有人 | 查看当前人格今日的心情变化历史 |
| `/今日日记` | 所有人 | 查看当前人格今天的日记内容；若今天还没日记会直接提示 |
| `/昨日日记` | 所有人 | 查看当前人格昨天的日记内容；若昨天没有日记会直接提示 |
| `/手动思考` | 所有人 | 对当前人格立即手动触发一次思考 |
| `/生成日记` | 管理员 | 对当前人格立即手动生成今日日记 |
| `/清除今日思考` | 管理员 | 清空当前人格今日思考流、心情记录、本地文件与当前状态 |

---

## ⚙️ 主要配置项

### 基础开关
- `enable_auto_reflection`：是否启用自动思考
- `enable_auto_diary`：是否启用自动日记
- `enable_webui`：是否启用 DayMind 自带 WebUI
- `webui_theme`：WebUI 主题，可选 `galaxy`（星系，默认）或 `journal`（手账/Journal）。`journal` 主题还分桌面模式与笔记本模式，适合喜欢纸质感与手账风的用户
- `debug_mode`：是否启用调试日志
- `enabled_personas`：启用的人格白名单

### 心情系统
- `enable_mood_system`：是否启用心情系统（启用后自动注入对话风格）
- `mood_provider_id`：心情模型提供商（留空则从思考中提取）
- `mood_reference_reflection_count`：心情提取时参考最近几条思考
- `mood_max_history_per_day`：每日保留心情记录数
- `mood_style_strength`：心情风格强度（弱 / 中 / 强）
- `mood_allow_sharp_tone`：是否允许明显的尖锐语气

### 思考相关
- `thinking_interval_minutes`：自动思考间隔
- `thinking_interval_jitter_seconds`：每轮随机抖动秒数
- `reflection_reference_count`：生成新思考时参考最近几条思考
- `context_rounds`：思考时参考最近多少轮对话
- `thinking_mode`：思考长度模式（简洁 / 适量 / 丰富）
- `thinking_provider_id`：思考使用的模型提供商
- `thinking_prompt_template`：思考提示词模板
- `reflection_dedupe_mode`：本地近似去重强度（不调用 LLM）
- `reflection_auto_ensure_today_schedule`：思考前是否自动确保今日日程存在
- `reflection_generation_retry_count`：思考生成失败时重试次数
- `reflection_generation_retry_delay_seconds`：思考生成重试间隔

### 日记相关
- `diary_time`：自动日记生成时间
- `diary_mode`：日记长度模式（简洁 / 适量 / 丰富）
- `diary_reference_count`：参考历史日记篇数
- `diary_provider_id`：日记使用的模型提供商
- `diary_prompt_template`：日记提示词模板
- `enable_diary_image`：是否启用日记图片渲染（人格级配置，默认关闭）
- `store_diary_to_memory`：是否写入 LivingMemory
- `diary_push_targets`：日记主动推送目标列表
- `allow_overwrite_today_diary`：是否允许重复生成今日日记
- `diary_generation_retry_count`：日记生成失败时重试次数
- `diary_generation_retry_delay_seconds`：日记生成重试间隔

### 静默时段
- `silent_hours_enabled`：是否启用静默（睡眠）时段
- `silent_hours_start`：静默开始时间（如 `"23:00"`）
- `silent_hours_end`：静默结束时间（如 `"07:00"`）
- `smart_silent_hours`：智能静默时段，默认关闭。开启后自动从 DayFlow 当日日程 timeline 提取睡眠时段的结束时间，覆盖 `silent_hours_end`——例如 DayFlow 日程中 Bot 是 7:30 起床，那 DayMind 静默也会自动延到 7:30 结束，避免"日程已经醒了但思考还在静默"的错位。若 DayFlow 未就绪或当日无日程，自动回退到 `silent_hours_end`。

### 梦境系统
- `enable_dream`：是否启用梦境（默认开启）
- `dream_count_range`：每夜梦境数量范围（如 "1-2"）
- `dream_provider_id`：梦境模型提供商（留空则复用思考提供商）

### 主动对话
- `proactive_chat_mode`：思考后主动对话模式（关闭 / 低频 / 普通，默认关闭）
- `proactive_chat_push_target`：主动对话推送目标（UMO 格式，如 `default:FriendMessage:123456`）
- `proactive_chat_cooldown_minutes`：主动对话冷却时间（分钟，默认 90，高级设置）

---

## 📝 使用说明

1. 若未配置 `thinking_provider_id`，自动思考无法执行。
2. 若未配置 `diary_provider_id`，自动日记无法执行。
3. 只有命中 `enabled_personas` 白名单的人格才会生成思考、心情与日记。
4. 关闭自动思考 / 自动日记后，手动指令仍然可用，但仍会遵循人格白名单限制。
5. 心情系统默认启用，可通过 `enable_mood_system` 关闭。
6. 若启用了 `reflection_auto_ensure_today_schedule`，请确保 DayFlow 插件已正确配置并启用对应人格。
