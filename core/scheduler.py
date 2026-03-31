""" 调度器模块 负责周期性思考和日记生成的调度 """

import asyncio
import datetime
import json
import random
import re
from typing import Optional, Any
from pathlib import Path
from astrbot.api import logger
from astrbot.api.event import MessageChain

from .reflection import ReflectionGenerator
from .diary import DiaryGenerator
from .dependency import DependencyManager
from .message_cache import MessageCache
from .silent_hours import SilentHoursChecker
from .mood import MoodManager


class AwarenessScheduler:
    """自我感知调度器（按人格分桶）"""

    RUNTIME_CONFIG_KEYS = {
        "reflection_retention_days",
        "diary_retention_days",
        "webui_default_window_days",
        "webui_default_theme",
        "webui_default_mode",
    }

    def __init__(
        self,
        context,
        config: dict,
        data_dir: str,
        reflection_generator: ReflectionGenerator,
        diary_generator: DiaryGenerator,
        dependency_manager: DependencyManager,
        message_cache: MessageCache,
        silent_hours: SilentHoursChecker,
        session_persona_map: dict[str, str] | None = None,
        mood_manager: MoodManager | None = None,
    ):
        self.context = context
        self.config = config
        self.data_dir = data_dir
        self.reflection_generator = reflection_generator
        self.diary_generator = diary_generator
        self.dependency_manager = dependency_manager
        self.message_cache = message_cache
        self.silent_hours = silent_hours
        self.session_persona_map = session_persona_map if session_persona_map is not None else {}
        self.mood_manager = mood_manager

        self.runtime_config: dict[str, Any] = {
            "reflection_retention_days": self._safe_retention_days(config.get("reflection_retention_days", 3), 3),
            "diary_retention_days": self._safe_retention_days(config.get("diary_retention_days", -1), -1),
            "webui_default_window_days": self._safe_window_days(config.get("webui_default_window_days", 3), 3),
            "webui_default_theme": str(config.get("webui_default_theme", "galaxy") or "galaxy"),
            "webui_default_mode": str(config.get("webui_default_mode", "overview") or "overview"),
        }

        self.is_running = False
        self.scheduler_task: Optional[asyncio.Task] = None
        self.persona_states: dict[str, dict[str, Any]] = {}
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3
        self.last_reflection_error_code: Optional[str] = None
        self.last_reflection_error_message: Optional[str] = None
        self.last_reflection_error_time: Optional[str] = None
        self.last_diary_error_code: Optional[str] = None
        self.last_diary_error_message: Optional[str] = None
        self.last_diary_error_time: Optional[str] = None
        self.last_dedupe_hit: bool = False
        self.last_dedupe_mode: str = "none"
        self.last_dedupe_source: Optional[str] = None
        self.last_selected_session_id: Optional[str] = None
        self.last_selected_session_source: str = "none"

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.scheduler_task = asyncio.create_task(self._run_scheduler())
        logger.info(
            f"[Scheduler] 调度器已启动，思考间隔：{self.config.get('thinking_interval_minutes', 30)}分钟，"
            f"抖动：±{self._safe_non_negative_int(self.config.get('thinking_interval_jitter_seconds', 0), 0)}秒"
        )

    async def stop(self):
        self.is_running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
        logger.info("[Scheduler] 调度器已停止")

    def _config_get(self, key: str, default=None):
        if key in self.RUNTIME_CONFIG_KEYS:
            return self.runtime_config.get(key, default)
        return self.config.get(key, default)

    def _config_set(self, key: str, value):
        if key in self.RUNTIME_CONFIG_KEYS:
            self.runtime_config[key] = value
        else:
            self.config[key] = value

    def load_runtime_config(self, runtime_config: dict[str, Any] | None):
        runtime_config = runtime_config or {}
        if "reflection_retention_days" in runtime_config:
            self.runtime_config["reflection_retention_days"] = self._safe_retention_days(runtime_config.get("reflection_retention_days"), 3)
        if "diary_retention_days" in runtime_config:
            self.runtime_config["diary_retention_days"] = self._safe_retention_days(runtime_config.get("diary_retention_days"), -1)
        if "webui_default_window_days" in runtime_config:
            self.runtime_config["webui_default_window_days"] = self._safe_window_days(runtime_config.get("webui_default_window_days"), 3)
        if "webui_default_theme" in runtime_config:
            self.runtime_config["webui_default_theme"] = str(runtime_config.get("webui_default_theme") or "galaxy").strip() or "galaxy"
        if "webui_default_mode" in runtime_config:
            self.runtime_config["webui_default_mode"] = str(runtime_config.get("webui_default_mode") or "overview").strip() or "overview"

    def get_runtime_config(self) -> dict[str, Any]:
        return {
            "reflection_retention_days": self._safe_retention_days(self.runtime_config.get("reflection_retention_days", 3), 3),
            "diary_retention_days": self._safe_retention_days(self.runtime_config.get("diary_retention_days", -1), -1),
            "webui_default_window_days": self._safe_window_days(self.runtime_config.get("webui_default_window_days", 3), 3),
            "webui_default_theme": str(self.runtime_config.get("webui_default_theme", "galaxy") or "galaxy"),
            "webui_default_mode": str(self.runtime_config.get("webui_default_mode", "overview") or "overview"),
        }

    async def update_runtime_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        changed: dict[str, Any] = {}
        if "reflection_retention_days" in updates:
            value = self._safe_retention_days(updates.get("reflection_retention_days"), 3)
            self._config_set("reflection_retention_days", value)
            changed["reflection_retention_days"] = value
        if "diary_retention_days" in updates:
            value = self._safe_retention_days(updates.get("diary_retention_days"), -1)
            self._config_set("diary_retention_days", value)
            changed["diary_retention_days"] = value
        if "webui_default_window_days" in updates:
            value = self._safe_window_days(updates.get("webui_default_window_days"), 3)
            self._config_set("webui_default_window_days", value)
            changed["webui_default_window_days"] = value
        if "webui_default_theme" in updates:
            value = str(updates.get("webui_default_theme") or "galaxy").strip() or "galaxy"
            self._config_set("webui_default_theme", value)
            changed["webui_default_theme"] = value
        if "webui_default_mode" in updates:
            value = str(updates.get("webui_default_mode") or "overview").strip() or "overview"
            self._config_set("webui_default_mode", value)
            changed["webui_default_mode"] = value

        if "reflection_retention_days" in changed:
            await self._apply_reflection_retention()
        if "diary_retention_days" in changed:
            await self._apply_diary_retention()
        return self.get_runtime_config()

    def _normalize_persona_name(self, persona_name: str | None) -> str | None:
        if persona_name is None:
            return None
        value = str(persona_name).strip()
        return value or None

    def _enabled_personas(self) -> list[str]:
        raw = self.config.get("enabled_personas", [])
        if isinstance(raw, str):
            parts = re.split(r"[,\n\r]+", raw)
            return [x.strip() for x in parts if x and x.strip()]
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return []

    def is_persona_enabled(self, persona_name: str | None) -> bool:
        normalized = self._normalize_persona_name(persona_name)
        if not normalized:
            return False
        enabled = self._enabled_personas()
        if not enabled:
            return False
        return normalized in enabled

    def _ensure_persona_state(self, persona_name: str) -> dict[str, Any]:
        persona_name = self._normalize_persona_name(persona_name) or "未命名人格"
        if persona_name not in self.persona_states:
            self.persona_states[persona_name] = {
                "current_awareness_text": "",
                "today_reflections": [],
                "last_reflection_time": None,
                "last_diary_date": "",
                "last_diary_check_minute": -1,
                "diary_generated_today": False,
                "diary_memory_version_counter": {},
                # 心情系统字段
                "current_mood": None,
                "today_moods": [],
            }
        return self.persona_states[persona_name]

    def export_persona_states(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for persona_name, state in self.persona_states.items():
            payload[persona_name] = {
                "current_awareness_text": state.get("current_awareness_text", ""),
                "today_reflections": list(state.get("today_reflections", []) or []),
                "last_reflection_time": state.get("last_reflection_time").isoformat() if state.get("last_reflection_time") else None,
                "last_diary_date": state.get("last_diary_date", ""),
                "last_diary_check_minute": int(state.get("last_diary_check_minute", -1) or -1),
                "diary_generated_today": bool(state.get("diary_generated_today", False)),
                "diary_memory_version_counter": dict(state.get("diary_memory_version_counter", {}) or {}),
                # 心情系统导出
                "current_mood": state.get("current_mood"),
                "today_moods": list(state.get("today_moods", []) or []),
            }
        return payload

    def restore_persona_states(self, saved: dict[str, Any]):
        self.persona_states = {}
        for persona_name, state in (saved or {}).items():
            if not isinstance(state, dict):
                continue
            item = self._ensure_persona_state(str(persona_name))
            item["current_awareness_text"] = str(state.get("current_awareness_text") or "")
            item["today_reflections"] = [str(x) for x in (state.get("today_reflections") or []) if str(x).strip()]
            last_reflection_time = state.get("last_reflection_time")
            try:
                item["last_reflection_time"] = datetime.datetime.fromisoformat(last_reflection_time) if last_reflection_time else None
            except Exception:
                item["last_reflection_time"] = None
            item["last_diary_date"] = str(state.get("last_diary_date") or "")
            item["last_diary_check_minute"] = self._safe_int(state.get("last_diary_check_minute"), -1)
            item["diary_generated_today"] = bool(state.get("diary_generated_today", False))
            item["diary_memory_version_counter"] = dict(state.get("diary_memory_version_counter") or {})
            # 心情系统恢复
            item["current_mood"] = state.get("current_mood") or None
            item["today_moods"] = list(state.get("today_moods", []) or [])

    def import_legacy_single_state(self, reflections: list[str], current_text: str, diary_generated_today: bool, last_diary_date: str):
        enabled = self._enabled_personas()
        if len(enabled) != 1:
            return
        state = self._ensure_persona_state(enabled[0])
        state["today_reflections"] = [str(x) for x in (reflections or []) if str(x).strip()]
        state["current_awareness_text"] = str(current_text or "")
        state["diary_generated_today"] = bool(diary_generated_today)
        state["last_diary_date"] = str(last_diary_date or "")

    async def get_current_awareness_for_session(self, session_id: str | None) -> str:
        if not session_id:
            return ""
        persona_name = self.session_persona_map.get(session_id)
        if not persona_name:
            return ""
        state = self.persona_states.get(persona_name) or {}
        return str(state.get("current_awareness_text") or "")

    def get_current_mood_for_session(self, session_id: str | None) -> dict | None:
        """获取指定会话的当前心情状态"""
        if not session_id:
            return None
        persona_name = self.session_persona_map.get(session_id)
        if not persona_name:
            return None
        return self.get_current_mood_for_persona(persona_name)

    def get_current_mood_for_persona(self, persona_name: str | None) -> dict | None:
        """获取指定人格的当前心情状态"""
        normalized = self._normalize_persona_name(persona_name)
        if not normalized:
            return None
        state = self.persona_states.get(normalized)
        if not state:
            return None
        return state.get("current_mood")

    def get_today_moods_for_persona(self, persona_name: str | None, limit: int = 10) -> list[dict]:
        """获取指定人格今日的心情历史"""
        normalized = self._normalize_persona_name(persona_name)
        if not normalized:
            return []
        state = self.persona_states.get(normalized)
        if not state:
            return []
        moods = list(state.get("today_moods", []) or [])
        if limit > 0:
            return moods[-limit:]
        return moods

    def get_mood_context(self, persona_name: str | None = None, session_id: str | None = None) -> dict | None:
        """
        获取心情上下文（供外部插件如 DayFlow 调用）
        优先使用 session_id 查找，其次使用 persona_name
        """
        mood = None
        if session_id:
            mood = self.get_current_mood_for_session(session_id)
        if not mood and persona_name:
            mood = self.get_current_mood_for_persona(persona_name)
        return mood

    def _run_today_reset_for_persona(self, persona_name: str, today_str: str):
        state = self._ensure_persona_state(persona_name)
        state["today_reflections"] = []
        state["current_awareness_text"] = ""
        state["last_diary_date"] = today_str
        state["last_diary_check_minute"] = -1
        state["diary_generated_today"] = False
        # 心情系统重置
        state["current_mood"] = None
        state["today_moods"] = []

    async def reset_today_reflections(self, persona_name: str | None = None) -> dict[str, Any]:
        now = datetime.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        target_persona = self._normalize_persona_name(persona_name)
        if not target_persona:
            enabled = self._enabled_personas()
            target_persona = enabled[0] if enabled else None
        if not target_persona:
            return {
                "date": today_str,
                "persona_name": "（未识别）",
                "removed_local_file": False,
                "today_reflections_count": 0,
                "current_awareness_text": "",
            }
        reflections_file = self._reflection_day_path(today_str, target_persona)
        removed_local_file = False
        if reflections_file.exists():
            reflections_file.unlink(missing_ok=True)
            removed_local_file = True
        state = self._ensure_persona_state(target_persona)
        state["today_reflections"] = []
        state["current_awareness_text"] = ""
        state["last_reflection_time"] = None
        state["current_mood"] = None
        state["today_moods"] = []
        self.last_dedupe_hit = False
        self.last_dedupe_mode = "none"
        self.last_dedupe_source = None
        self.consecutive_failures = 0
        self._clear_reflection_error()
        return {
            "date": today_str,
            "persona_name": target_persona,
            "removed_local_file": removed_local_file,
            "today_reflections_count": 0,
            "current_awareness_text": "",
        }

    def _sanitize_persona_path(self, persona_name: str) -> str:
        return re.sub(r'[\\/:*?"<>|]+', '_', persona_name).strip() or '未命名人格'

    def _diaries_dir(self, persona_name: str | None = None) -> Path:
        base = Path(self.data_dir) / "diaries"
        if not persona_name:
            return base
        return base / self._sanitize_persona_path(persona_name)

    def _reflections_dir(self, persona_name: str | None = None) -> Path:
        base = Path(self.data_dir) / "reflections"
        if not persona_name:
            return base
        return base / self._sanitize_persona_path(persona_name)

    def _diary_text_path(self, date_str: str, persona_name: str) -> Path:
        return self._diaries_dir(persona_name) / f"{date_str}.txt"

    def _diary_meta_path(self, date_str: str, persona_name: str) -> Path:
        return self._diaries_dir(persona_name) / f"{date_str}.json"

    def _reflection_day_path(self, date_str: str, persona_name: str) -> Path:
        return self._reflections_dir(persona_name) / f"{date_str}.json"

    def _load_json_file(self, file_path: Path, default):
        if not file_path.exists():
            return default
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json_file(self, file_path: Path, payload):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_default_diary_meta(self, date_str: str, persona_name: str) -> dict[str, Any]:
        return {
            "date": date_str,
            "persona_name": persona_name,
            "memory_status": "unknown",
            "starred": False,
            "note": "",
            "updated_at": datetime.datetime.now().isoformat(),
        }

    def _build_default_reflection_day_meta(self, date_str: str, persona_name: str) -> dict[str, Any]:
        return {
            "date": date_str,
            "persona_name": persona_name,
            "starred": False,
            "note": "",
            "updated_at": datetime.datetime.now().isoformat(),
        }

    def _load_diary_meta(self, date_str: str, persona_name: str) -> dict[str, Any]:
        data = self._load_json_file(self._diary_meta_path(date_str, persona_name), {})
        if not isinstance(data, dict):
            data = {}
        base = self._build_default_diary_meta(date_str, persona_name)
        base.update(data)
        base["starred"] = bool(base.get("starred", False))
        base["note"] = str(base.get("note") or "")
        return base

    def _save_diary_meta_sync(self, date_str: str, persona_name: str, payload: dict[str, Any]):
        final_payload = self._build_default_diary_meta(date_str, persona_name)
        final_payload.update(payload or {})
        final_payload["updated_at"] = datetime.datetime.now().isoformat()
        self._write_json_file(self._diary_meta_path(date_str, persona_name), final_payload)

    def _load_reflection_day_rows(self, date_str: str, persona_name: str) -> list[dict[str, Any]]:
        rows = self._load_json_file(self._reflection_day_path(date_str, persona_name), [])
        if not isinstance(rows, list):
            return []
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                normalized.append(row)
            elif isinstance(row, str):
                normalized.append({"time": "", "content": row, "created_at": ""})
        return normalized

    def _extract_reflection_day_meta(self, date_str: str, persona_name: str, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = rows if rows is not None else self._load_reflection_day_rows(date_str, persona_name)
        meta = self._build_default_reflection_day_meta(date_str, persona_name)
        if rows:
            last = rows[-1]
            if isinstance(last, dict):
                if "day_meta" in last and isinstance(last.get("day_meta"), dict):
                    meta.update(last["day_meta"])
                elif "starred" in last or "note" in last:
                    meta["starred"] = bool(last.get("starred", False))
                    meta["note"] = str(last.get("note") or "")
        meta["starred"] = bool(meta.get("starred", False))
        meta["note"] = str(meta.get("note") or "")
        return meta

    def _save_reflection_day_rows_with_meta(self, date_str: str, persona_name: str, rows: list[dict[str, Any]], meta: dict[str, Any] | None = None):
        final_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item.pop("starred", None)
            item.pop("note", None)
            item.pop("day_meta", None)
            final_rows.append(item)
        final_meta = self._build_default_reflection_day_meta(date_str, persona_name)
        if meta:
            final_meta.update(meta)
        final_meta["updated_at"] = datetime.datetime.now().isoformat()
        if final_rows:
            final_rows[-1]["day_meta"] = {
                "starred": bool(final_meta.get("starred", False)),
                "note": str(final_meta.get("note") or ""),
                "updated_at": final_meta.get("updated_at"),
                "persona_name": persona_name,
            }
        self._write_json_file(self._reflection_day_path(date_str, persona_name), final_rows)

    def _scan_persona_dirs(self, root: Path) -> list[tuple[str, Path]]:
        if not root.exists():
            return []
        pairs: list[tuple[str, Path]] = []
        for item in root.iterdir():
            if item.is_dir():
                pairs.append((item.name, item))
        return pairs

    def get_diary_item(self, date_str: str, persona_name: str | None = None) -> dict[str, Any] | None:
        if persona_name:
            txt_file = self._diary_text_path(date_str, persona_name)
            if not txt_file.exists():
                return None
            content = txt_file.read_text(encoding="utf-8").strip()
            stat = txt_file.stat()
            meta = self._load_diary_meta(date_str, persona_name)
            return {
                "date": date_str,
                "persona_name": persona_name,
                "title": self._extract_title(content, date_str),
                "content": content,
                "updated_at": int(stat.st_mtime),
                "memory_status": meta.get("memory_status", "unknown"),
                "starred": bool(meta.get("starred", False)),
                "note": str(meta.get("note") or ""),
            }
        for dir_name, _ in self._scan_persona_dirs(self._diaries_dir()):
            item = self.get_diary_item(date_str, dir_name)
            if item:
                return item
        return None

    def list_diaries(self, days: int | None = None, starred_only: bool = False) -> list[dict[str, Any]]:
        root = self._diaries_dir()
        if not root.exists():
            return []
        window_days = self._safe_window_days(days if days is not None else self._config_get("webui_default_window_days", 3), 3)
        items: list[dict[str, Any]] = []
        for persona_name, persona_dir in self._scan_persona_dirs(root):
            for txt_file in persona_dir.glob("*.txt"):
                date_str = txt_file.stem.strip()
                if not self._date_in_window(date_str, window_days):
                    continue
                try:
                    content = txt_file.read_text(encoding="utf-8").strip()
                except Exception:
                    content = ""
                meta = self._load_diary_meta(date_str, persona_name)
                if starred_only and not meta.get("starred", False):
                    continue
                stat = txt_file.stat()
                items.append({
                    "date": date_str,
                    "persona_name": persona_name,
                    "title": self._extract_title(content, date_str),
                    "preview": self._build_preview(content, limit=120),
                    "length": len(content),
                    "updated_at": int(stat.st_mtime),
                    "memory_status": meta.get("memory_status", "unknown"),
                    "starred": bool(meta.get("starred", False)),
                    "note": str(meta.get("note") or ""),
                })
        items.sort(key=lambda x: (x["date"], x.get("persona_name", "")), reverse=True)
        return items

    def get_reflection_day_item(self, date_str: str, persona_name: str | None = None) -> dict[str, Any] | None:
        if persona_name:
            fp = self._reflection_day_path(date_str, persona_name)
            if not fp.exists():
                return None
            rows = self._load_reflection_day_rows(date_str, persona_name)
            meta = self._extract_reflection_day_meta(date_str, persona_name, rows)
            return {
                "date": date_str,
                "persona_name": persona_name,
                "count": len(rows),
                "items": rows,
                "starred": bool(meta.get("starred", False)),
                "note": str(meta.get("note") or ""),
            }
        for dir_name, _ in self._scan_persona_dirs(self._reflections_dir()):
            item = self.get_reflection_day_item(date_str, dir_name)
            if item:
                return item
        return None

    def list_reflection_days(self, days: int | None = None, starred_only: bool = False) -> list[dict[str, Any]]:
        root = self._reflections_dir()
        if not root.exists():
            return []
        window_days = self._safe_window_days(days if days is not None else self._config_get("webui_default_window_days", 3), 3)
        items: list[dict[str, Any]] = []
        for persona_name, persona_dir in self._scan_persona_dirs(root):
            for fp in persona_dir.glob("*.json"):
                date_str = fp.stem.strip()
                if not self._date_in_window(date_str, window_days):
                    continue
                rows = self._load_reflection_day_rows(date_str, persona_name)
                meta = self._extract_reflection_day_meta(date_str, persona_name, rows)
                if starred_only and not meta.get("starred", False):
                    continue
                preview = rows[-1].get("content", "") if rows else ""
                items.append({
                    "date": date_str,
                    "persona_name": persona_name,
                    "count": len(rows),
                    "preview": self._build_preview(preview, limit=90),
                    "first_time": rows[0].get("time", "") if rows else "",
                    "last_time": rows[-1].get("time", "") if rows else "",
                    "starred": bool(meta.get("starred", False)),
                    "note": str(meta.get("note") or ""),
                })
        items.sort(key=lambda x: (x["date"], x.get("persona_name", "")), reverse=True)
        return items

    async def set_diary_starred(self, date_str: str, starred: bool, persona_name: str | None = None) -> dict[str, Any] | None:
        item = self.get_diary_item(date_str, persona_name)
        if not item:
            return None
        persona_name = item.get("persona_name")
        meta = self._load_diary_meta(date_str, persona_name)
        meta["starred"] = bool(starred)
        self._save_diary_meta_sync(date_str, persona_name, meta)
        return self.get_diary_item(date_str, persona_name)

    async def set_diary_note(self, date_str: str, note: str, persona_name: str | None = None) -> dict[str, Any] | None:
        item = self.get_diary_item(date_str, persona_name)
        if not item:
            return None
        persona_name = item.get("persona_name")
        meta = self._load_diary_meta(date_str, persona_name)
        meta["note"] = str(note or "")
        self._save_diary_meta_sync(date_str, persona_name, meta)
        return self.get_diary_item(date_str, persona_name)

    async def set_reflection_day_starred(self, date_str: str, starred: bool, persona_name: str | None = None) -> dict[str, Any] | None:
        item = self.get_reflection_day_item(date_str, persona_name)
        if not item:
            return None
        persona_name = item.get("persona_name")
        rows = self._load_reflection_day_rows(date_str, persona_name)
        meta = self._extract_reflection_day_meta(date_str, persona_name, rows)
        meta["starred"] = bool(starred)
        self._save_reflection_day_rows_with_meta(date_str, persona_name, rows, meta)
        return self.get_reflection_day_item(date_str, persona_name)

    async def set_reflection_day_note(self, date_str: str, note: str, persona_name: str | None = None) -> dict[str, Any] | None:
        item = self.get_reflection_day_item(date_str, persona_name)
        if not item:
            return None
        persona_name = item.get("persona_name")
        rows = self._load_reflection_day_rows(date_str, persona_name)
        meta = self._extract_reflection_day_meta(date_str, persona_name, rows)
        meta["note"] = str(note or "")
        self._save_reflection_day_rows_with_meta(date_str, persona_name, rows, meta)
        return self.get_reflection_day_item(date_str, persona_name)

    def _safe_window_days(self, value, default: int) -> int:
        try:
            parsed = int(value)
            if parsed == -1:
                return -1
            return max(parsed, 1)
        except Exception:
            return default

    def _safe_int(self, value, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _date_in_window(self, date_str: str, days: int) -> bool:
        if days == -1:
            return True
        try:
            d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            delta = (datetime.date.today() - d).days
            return 0 <= delta < days
        except Exception:
            return False

    def _extract_title(self, content: str, fallback: str) -> str:
        if not content:
            return fallback
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        return first_line or fallback

    def _build_preview(self, content: str, limit: int = 120) -> str:
        compact = " ".join(line.strip() for line in str(content).splitlines() if line.strip())
        if len(compact) <= limit:
            return compact
        return compact[:limit].rstrip() + "……"

    async def _run_scheduler(self):
        diary_time_str = self.config.get("diary_time", "23:58")
        try:
            diary_hour, diary_minute = map(int, diary_time_str.split(":"))
        except (ValueError, AttributeError):
            diary_hour, diary_minute = 23, 58
            logger.warning("[Scheduler] 日记时间格式错误，使用默认值 23:58")
        logger.info(f"[Scheduler] 日记生成时间设置为：{diary_hour:02d}:{diary_minute:02d}")

        while self.is_running:
            try:
                now = datetime.datetime.now()
                today_str = now.strftime("%Y-%m-%d")
                enabled_personas = self._enabled_personas()

                for persona_name in enabled_personas:
                    state = self._ensure_persona_state(persona_name)
                    if state.get("last_diary_date") != today_str:
                        self._run_today_reset_for_persona(persona_name, today_str)

                auto_diary_enabled = self.config.get("enable_auto_diary", True)
                current_total_minutes = now.hour * 60 + now.minute

                if auto_diary_enabled and now.hour == diary_hour and now.minute == diary_minute:
                    for persona_name in enabled_personas:
                        state = self._ensure_persona_state(persona_name)
                        if state.get("last_diary_check_minute") == current_total_minutes:
                            continue
                        state["last_diary_check_minute"] = current_total_minutes
                        if state.get("diary_generated_today") and not self.config.get("allow_overwrite_today_diary", False):
                            logger.info(f"[Scheduler] 跳过日记生成：{today_str} persona={persona_name} 今日日记已生成")
                            continue
                        await self._generate_and_push_diary(today_str, persona_name)

                auto_reflection_enabled = self.config.get("enable_auto_reflection", True)
                if auto_reflection_enabled:
                    if self.silent_hours.is_silent():
                        logger.debug("[Scheduler] 当前处于静默时段，跳过思考")
                    else:
                        for persona_name in enabled_personas:
                            await self._do_reflection(persona_name)
                else:
                    logger.debug("[Scheduler] 自动思考已关闭，跳过本轮思考")

                base_seconds = max(float(self.config.get("thinking_interval_minutes", 30) or 30) * 60.0, 1.0)
                jitter = float(self._safe_non_negative_int(self.config.get("thinking_interval_jitter_seconds", 0), 0))
                sleep_seconds = base_seconds + (random.uniform(-jitter, jitter) if jitter > 0 else 0.0)
                sleep_seconds = max(sleep_seconds, 1.0)
                await asyncio.sleep(sleep_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Scheduler] 调度异常: {e}", exc_info=True)
                await asyncio.sleep(60)

    def _normalize_text_for_dedupe(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"^\s*\d{1,2}:\d{2}[，,：:]?\s*", "", text)
        text = text.lower()
        text = re.sub(r"[，。！？；：、,.!?;:\-—（）()\[\]{}\"'“”‘’…·]", " ", text)
        text = re.sub(r"\s+", "", text)
        return text

    def _extract_dedupe_tokens(self, text: str) -> set[str]:
        normalized = self._normalize_text_for_dedupe(text)
        if not normalized:
            return set()
        stop_tokens = {
            "现在", "此刻", "这会", "这会儿", "感觉", "有点", "一些", "正在", "就是", "还是",
            "似乎", "自己", "今天", "刚刚", "目前", "然后", "因为", "所以", "已经", "有些", "一种",
        }
        tokens: set[str] = set()
        for i in range(len(normalized) - 1):
            bg = normalized[i:i + 2]
            if bg and bg not in stop_tokens:
                tokens.add(bg)
        for i in range(len(normalized) - 2):
            tg = normalized[i:i + 3]
            if tg and tg not in stop_tokens:
                tokens.add(tg)
        return tokens

    def _calc_similarity(self, text_a: str, text_b: str) -> float:
        tokens_a = self._extract_dedupe_tokens(text_a)
        tokens_b = self._extract_dedupe_tokens(text_b)
        if not tokens_a or not tokens_b:
            return 0.0
        inter = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        if union == 0:
            return 0.0
        return inter / union

    def _get_similarity_threshold(self) -> float | None:
        mode = self.config.get("reflection_dedupe_mode", "普通")
        if mode == "严格":
            return 0.62
        if mode == "普通":
            return 0.72
        if mode == "无限制":
            return None
        return 0.72

    def _mark_dedupe(self, hit: bool, mode: str = "none", source: Optional[str] = None):
        self.last_dedupe_hit = hit
        self.last_dedupe_mode = mode
        self.last_dedupe_source = source

    def _record_reflection_error(self, code: str, message: str):
        self.last_reflection_error_code = code
        self.last_reflection_error_message = message
        self.last_reflection_error_time = datetime.datetime.now().strftime("%H:%M:%S")

    def _clear_reflection_error(self):
        self.last_reflection_error_code = None
        self.last_reflection_error_message = None
        self.last_reflection_error_time = None

    def _record_diary_error(self, code: str, message: str):
        self.last_diary_error_code = code
        self.last_diary_error_message = message
        self.last_diary_error_time = datetime.datetime.now().strftime("%H:%M:%S")

    def _clear_diary_error(self):
        self.last_diary_error_code = None
        self.last_diary_error_message = None
        self.last_diary_error_time = None

    async def select_reflection_session(self, persona_name: str) -> str | None:
        recent_session_ids = await self.message_cache.get_recent_session_ids()
        for session_id in recent_session_ids:
            if self.session_persona_map.get(session_id) == persona_name:
                self.last_selected_session_id = session_id
                self.last_selected_session_source = "recent_message"
                return session_id
        session_ids = await self.message_cache.get_all_session_ids()
        for session_id in session_ids:
            if self.session_persona_map.get(session_id) == persona_name:
                self.last_selected_session_id = session_id
                self.last_selected_session_source = "message_cache"
                return session_id
        self.last_selected_session_id = None
        self.last_selected_session_source = "none"
        return None

    def _is_duplicate_reflection(self, persona_name: str, new_text: str) -> bool:
        state = self._ensure_persona_state(persona_name)
        if not new_text:
            self._mark_dedupe(False)
            return False
        normalized_new = self._normalize_text_for_dedupe(new_text)
        threshold = self._get_similarity_threshold()
        exact_prefix_guard = self.config.get("reflection_exact_prefix_guard", True)
        current_awareness_text = str(state.get("current_awareness_text") or "")
        today_reflections = list(state.get("today_reflections") or [])

        if current_awareness_text:
            current_normalized = self._normalize_text_for_dedupe(current_awareness_text)
            if current_normalized == normalized_new:
                self._mark_dedupe(True, "exact", "current_awareness")
                return True
            if threshold is not None:
                similarity = self._calc_similarity(current_awareness_text, new_text)
                if similarity >= threshold:
                    self._mark_dedupe(True, "similar", "current_awareness")
                    return True
            if exact_prefix_guard:
                current_tail = current_normalized[:24]
                new_tail = normalized_new[:24]
                if current_tail and new_tail and current_tail == new_tail:
                    self._mark_dedupe(True, "prefix", "current_awareness")
                    return True

        if today_reflections:
            reference_count = self._safe_non_negative_int(self.config.get("reflection_reference_count", 2), default=2)
            tail_size = max(reference_count, 2)
            recent_tail = today_reflections[-tail_size:]
            for index, old in enumerate(recent_tail, start=1):
                old_normalized = self._normalize_text_for_dedupe(old)
                if old_normalized == normalized_new:
                    self._mark_dedupe(True, "exact", f"recent_{index}")
                    return True
                if threshold is not None:
                    similarity = self._calc_similarity(old, new_text)
                    if similarity >= threshold:
                        self._mark_dedupe(True, "similar", f"recent_{index}")
                        return True
                if exact_prefix_guard:
                    old_tail = old_normalized[:24]
                    new_tail = normalized_new[:24]
                    if old_tail and new_tail and old_tail == new_tail:
                        self._mark_dedupe(True, "prefix", f"recent_{index}")
                        return True

        self._mark_dedupe(False)
        return False

    def _build_recent_reflections_text(self, persona_name: str) -> str:
        reference_count = self._safe_non_negative_int(self.config.get("reflection_reference_count", 2), default=2)
        if reference_count <= 0:
            return "（不参考最近思考）"
        recent = self._ensure_persona_state(persona_name).get("today_reflections", [])[-reference_count:]
        if not recent:
            return "（暂无最近思考）"
        return "\n".join([f"- {x}" for x in recent])

    def _safe_non_negative_int(self, value, default: int = 2) -> int:
        try:
            return max(int(value), 0)
        except Exception:
            return default

    def _safe_retention_days(self, value, default: int) -> int:
        try:
            parsed = int(value)
            if parsed == -1:
                return -1
            return max(parsed, 0)
        except Exception:
            return default

    def _trim_today_moods(self, persona_name: str):
        """限制今日心情记录数量"""
        max_history = self.mood_manager.get_mood_max_history() if self.mood_manager else 24
        state = self._ensure_persona_state(persona_name)
        moods = list(state.get("today_moods", []) or [])
        if len(moods) > max_history:
            state["today_moods"] = moods[-max_history:]

    async def run_manual_reflection(self, session_id: str, persona_name: str | None, persona_desc: str | None = None) -> dict[str, Any]:
        persona_name = self._normalize_persona_name(persona_name)
        if not persona_name or not self.is_persona_enabled(persona_name):
            return {"status": "skipped", "message": "当前人格未启用 DayMind"}
        self.session_persona_map[session_id] = persona_name
        return await self._do_reflection(persona_name, session_id=session_id, persona_desc=persona_desc, manual=True)

    async def _do_reflection(self, persona_name: str, session_id: str | None = None, persona_desc: str | None = None, manual: bool = False) -> dict[str, Any]:
        try:
            if not self.is_persona_enabled(persona_name):
                return {"status": "skipped", "message": f"人格未启用: {persona_name}"}

            now = datetime.datetime.now()
            current_time_str = now.strftime("%H:%M")
            state = self._ensure_persona_state(persona_name)

            selected_session_id = session_id or await self.select_reflection_session(persona_name)
            if not selected_session_id:
                logger.debug(f"[Scheduler] 跳过思考：persona={persona_name} 暂无会话")
                return {"status": "skipped", "message": f"人格 {persona_name} 暂无可用会话"}

            logger.debug(f"[Scheduler] 开始思考... persona={persona_name}, session={selected_session_id}, time={current_time_str}")

            persona_ctx = await self.dependency_manager.resolve_persona_context(selected_session_id)
            resolved_desc = persona_desc or (persona_ctx.get("persona_desc") if persona_ctx else None)
            self.session_persona_map[selected_session_id] = persona_name

            result = await self.reflection_generator.generate(
                current_time_str,
                selected_session_id,
                self._build_recent_reflections_text(persona_name),
                persona_name,
                resolved_desc,
            )

            if result:
                if self._is_duplicate_reflection(persona_name, result):
                    state["last_reflection_time"] = now
                    logger.info(
                        f"[Scheduler] 思考结果命中去重，跳过更新: persona={persona_name}, mode={self.last_dedupe_mode}, source={self.last_dedupe_source}"
                    )
                    self.consecutive_failures = 0
                    self._clear_reflection_error()
                    return {"status": "duplicate", "text": result}

                state["current_awareness_text"] = result
                state.setdefault("today_reflections", []).append(result)
                state["last_reflection_time"] = now
                self.consecutive_failures = 0
                self._clear_reflection_error()

                await self._append_reflection_history(now.strftime("%Y-%m-%d"), persona_name, result)
                await self._apply_reflection_retention()

                # 生成心情
                mood_result = None
                if self.mood_manager and self.mood_manager.is_mood_enabled():
                    try:
                        schedule_data = await self.dependency_manager.get_schedule_data()
                        recent_reflections = list(state.get("today_reflections", []) or [])
                        mood_result = await self.mood_manager.generate_mood(
                            reflection_text=result,
                            schedule_data=schedule_data,
                            recent_reflections=recent_reflections,
                            persona_name=persona_name,
                            persona_desc=resolved_desc,
                        )
                        mood_result = self.mood_manager.validate_mood(mood_result)
                        state["current_mood"] = mood_result
                        state.setdefault("today_moods", []).append(mood_result)
                        self._trim_today_moods(persona_name)
                        logger.info(f"[Scheduler] 心情生成完成: persona={persona_name}, mood={mood_result.get('label')}")
                    except Exception as e:
                        logger.warning(f"[Scheduler] 心情生成失败: {e}")

                logger.info(f"[Scheduler] 思考完成: persona={persona_name}, result={result}")
                return {"status": "success", "text": result, "mood": mood_result}

            self.consecutive_failures += 1
            self._record_reflection_error("reflection_empty", "思考结果为空，请检查模型提供商配置")
            logger.warning(f"[Scheduler] 思考失败（连续失败：{self.consecutive_failures}次） persona={persona_name}")
            if self.consecutive_failures >= self.max_consecutive_failures:
                logger.warning(f"[Scheduler] 连续{self.consecutive_failures}次思考失败，请检查模型提供商配置！")
            return {"status": "failed", "message": "思考结果为空，请检查模型提供商配置"}

        except Exception as e:
            logger.error(f"[Scheduler] 思考过程出错: {e}", exc_info=True)
            self.consecutive_failures += 1
            self._record_reflection_error("reflection_exception", str(e))
            return {"status": "failed", "message": str(e)}

    async def run_manual_diary(self, session_id: str, persona_name: str | None, persona_desc: str | None = None) -> dict[str, Any]:
        persona_name = self._normalize_persona_name(persona_name)
        if not persona_name or not self.is_persona_enabled(persona_name):
            return {"status": "skipped", "message": "当前人格未启用 DayMind"}
        self.session_persona_map[session_id] = persona_name
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        state = self._ensure_persona_state(persona_name)
        if state.get("diary_generated_today") and not self.config.get("allow_overwrite_today_diary", False):
            return {"status": "exists"}
        return await self._generate_and_push_diary(today_str, persona_name, primary_target=session_id, persona_desc=persona_desc, manual=True)

    async def _generate_and_push_diary(self, date_str: str, persona_name: str, primary_target: str | None = None, persona_desc: str | None = None, manual: bool = False) -> dict[str, Any]:
        memory_status = "skipped"
        state = self._ensure_persona_state(persona_name)
        try:
            if state.get("diary_generated_today") and not self.config.get("allow_overwrite_today_diary", False):
                logger.info(f"[Scheduler] 跳过日记生成：{date_str} persona={persona_name} 今日日记已生成")
                return {"status": "exists"}

            target = primary_target or await self.select_reflection_session(persona_name)
            primary_persona_desc = persona_desc
            if target:
                persona_ctx = await self.dependency_manager.resolve_persona_context(target)
                if persona_ctx and not primary_persona_desc:
                    primary_persona_desc = persona_ctx.get("persona_desc")
                self.session_persona_map[target] = persona_name

            diary_content = await self.diary_generator.generate(
                date_str,
                list(state.get("today_reflections", []) or []),
                session_id=target,
                persona_name=persona_name,
                persona_desc=primary_persona_desc,
            )

            if not diary_content:
                self._record_diary_error("diary_empty", "日记生成结果为空，请检查模型提供商配置")
                logger.warning(f"[Scheduler] 日记生成失败 persona={persona_name}")
                await self._save_diary_meta(date_str, persona_name, memory_status="failed")
                return {"status": "failed", "message": "日记生成结果为空，请检查模型提供商配置"}

            overwrite = bool(self.config.get("allow_overwrite_today_diary", False))
            regeneration_info = {"matched": 0, "updated": 0, "ids": []}
            if overwrite and self.config.get("store_diary_to_memory", True) and self.dependency_manager.has_livingmemory and target:
                regeneration_info = await self.dependency_manager.mark_daymind_diary_memories_deleted(date_str=date_str, session_id=target)

            await self._save_diary_local(date_str, persona_name, diary_content)

            if self.config.get("store_diary_to_memory", True) and self.dependency_manager.has_livingmemory:
                memory_metadata = self._build_diary_memory_metadata(date_str, persona_name)
                memory_metadata["replaces_memory_ids"] = regeneration_info.get("ids", [])
                stored = await self.dependency_manager.store_to_memory(
                    date_str=date_str,
                    content=diary_content,
                    session_id=target,
                    persona_id=persona_name,
                    metadata=memory_metadata,
                )
                if not stored:
                    memory_status = "failed"
                    self._record_diary_error("memory_store_failed", "日记写入记忆系统失败")
                    logger.warning(f"[Scheduler] 日记存入记忆系统失败 persona={persona_name}")
                    await self._save_diary_meta(date_str, persona_name, memory_status=memory_status)
                    return {"status": "failed", "message": "日记写入记忆系统失败"}
                memory_status = "stored"
            else:
                memory_status = "skipped"

            await self._save_diary_meta(date_str, persona_name, memory_status=memory_status)
            await self._apply_diary_retention()

            if not manual:
                await self._push_diary_to_targets(diary_content)

            state["diary_generated_today"] = True
            state["today_reflections"] = []
            state["current_awareness_text"] = ""
            state["last_diary_date"] = date_str
            self._clear_diary_error()
            return {"status": "success", "content": diary_content, "marked_deleted": int(regeneration_info.get("updated", 0) or 0)}

        except Exception as e:
            logger.error(f"[Scheduler] 日记生成流程出错: {e}", exc_info=True)
            self._record_diary_error("diary_exception", str(e))
            await self._save_diary_meta(date_str, persona_name, memory_status="failed")
            return {"status": "failed", "message": str(e)}

    def _build_diary_memory_metadata(self, date_str: str, persona_name: str) -> dict:
        state = self._ensure_persona_state(persona_name)
        overwrite = self.config.get("allow_overwrite_today_diary", False)
        counter = dict(state.get("diary_memory_version_counter", {}) or {})
        version = int(counter.get(date_str, 0) or 0) + 1
        counter[date_str] = version
        state["diary_memory_version_counter"] = counter
        return {
            "type": "diary",
            "source": "daymind",
            "date": date_str,
            "persona_name": persona_name,
            "version": version,
            "is_regenerated": overwrite and version > 1,
            "overwrite_of_date": date_str if overwrite and version > 1 else "",
            "status": "active",
        }

    def _get_primary_persona_id(self, persona_name: str | None) -> str | None:
        return self._normalize_persona_name(persona_name)

    async def _save_diary_local(self, date_str: str, persona_name: str, content: str):
        try:
            diary_file = self._diary_text_path(date_str, persona_name)
            diary_file.parent.mkdir(parents=True, exist_ok=True)
            with open(diary_file, 'w', encoding='utf-8') as f:
                f.write(content)
            if not self._diary_meta_path(date_str, persona_name).exists():
                self._save_diary_meta_sync(date_str, persona_name, self._build_default_diary_meta(date_str, persona_name))
            logger.info(f"[Scheduler] 日记已保存到本地: persona={persona_name}, path={diary_file}")
        except Exception as e:
            logger.error(f"[Scheduler] 保存日记到本地失败: {e}")
            self._record_diary_error("local_save_failed", str(e))

    async def _save_diary_meta(self, date_str: str, persona_name: str, memory_status: str = "unknown"):
        try:
            current = self._load_diary_meta(date_str, persona_name)
            current["memory_status"] = memory_status
            self._save_diary_meta_sync(date_str, persona_name, current)
        except Exception as e:
            logger.debug(f"[Scheduler] 保存日记元信息失败: {e}")

    async def _append_reflection_history(self, date_str: str, persona_name: str, content: str):
        try:
            history_dir = self._reflections_dir(persona_name)
            history_dir.mkdir(parents=True, exist_ok=True)
            items = self._load_reflection_day_rows(date_str, persona_name)
            day_meta = self._extract_reflection_day_meta(date_str, persona_name, items)
            items.append({
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "content": content,
                "created_at": datetime.datetime.now().isoformat(),
                "persona_name": persona_name,
            })
            self._save_reflection_day_rows_with_meta(date_str, persona_name, items, day_meta)
        except Exception as e:
            logger.debug(f"[Scheduler] 保存思考流失败: {e}")

    async def _apply_reflection_retention(self):
        try:
            keep_days = self._safe_retention_days(self._config_get("reflection_retention_days", 3), default=3)
            if keep_days == -1:
                return
            root = self._reflections_dir()
            if not root.exists():
                return
            cutoff = datetime.date.today() - datetime.timedelta(days=keep_days - 1) if keep_days > 0 else datetime.date.today() + datetime.timedelta(days=1)
            for persona_name, persona_dir in self._scan_persona_dirs(root):
                for fp in persona_dir.glob("*.json"):
                    try:
                        file_date = datetime.datetime.strptime(fp.stem, "%Y-%m-%d").date()
                    except Exception:
                        continue
                    if file_date >= cutoff:
                        continue
                    meta = self._extract_reflection_day_meta(fp.stem, persona_name, self._load_reflection_day_rows(fp.stem, persona_name))
                    if bool(meta.get("starred", False)):
                        continue
                    fp.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"[Scheduler] 应用思考流轮换失败: {e}")

    async def _apply_diary_retention(self):
        try:
            keep_days = self._safe_retention_days(self._config_get("diary_retention_days", -1), default=-1)
            if keep_days == -1:
                return
            root = self._diaries_dir()
            if not root.exists():
                return
            cutoff = datetime.date.today() - datetime.timedelta(days=keep_days - 1) if keep_days > 0 else datetime.date.today() + datetime.timedelta(days=1)
            for persona_name, persona_dir in self._scan_persona_dirs(root):
                for txt_fp in persona_dir.glob("*.txt"):
                    date_str = txt_fp.stem
                    try:
                        file_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    except Exception:
                        continue
                    if file_date >= cutoff:
                        continue
                    meta = self._load_diary_meta(date_str, persona_name)
                    if bool(meta.get("starred", False)):
                        continue
                    txt_fp.unlink(missing_ok=True)
                    self._diary_meta_path(date_str, persona_name).unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"[Scheduler] 应用日记轮换失败: {e}")

    async def _push_diary_to_targets(self, content: str):
        targets = self.config.get("diary_push_targets", [])
        if not targets:
            logger.debug("[Scheduler] 未配置推送目标")
            return
        for target in targets:
            max_retries = int(self.config.get("push_retry_times", 3))
            retry_delay = float(self.config.get("push_retry_delay_seconds", 2))
            success = False
            last_error = None
            for attempt in range(1, max_retries + 1):
                try:
                    await self._send_message_to_target(target, content)
                    success = True
                    break
                except Exception as e:
                    last_error = e
                    logger.warning(f"[Scheduler] 推送失败，第{attempt}/{max_retries}次: target={target}, error={e}")
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay)
            if not success:
                self._record_diary_error("push_failed", f"target={target}, error={last_error}")
                logger.error(f"[Scheduler] 推送日记到 {target} 最终失败: {last_error}", exc_info=True)

    async def _send_message_to_target(self, target: str, content: str):
        parts = target.split(":")
        if len(parts) != 3:
            logger.warning(f"[Scheduler] 无效的推送目标格式: {target}")
            return
        message_chain = MessageChain().message(content)
        await self.context.send_message(target, message_chain)
        logger.info(f"[Scheduler] 日记已推送到: {target}")

    def get_status(self, persona_name: str | None = None) -> dict:
        silent_status = self.silent_hours.get_status()
        reference_count = self._safe_non_negative_int(self.config.get("reflection_reference_count", 2), default=2)
        runtime_config = self.get_runtime_config()
        normalized = self._normalize_persona_name(persona_name)
        state = self._ensure_persona_state(normalized) if normalized and self.is_persona_enabled(normalized) else None

        # 获取当前心情信息
        current_mood = None
        today_moods_count = 0
        if state:
            current_mood = state.get("current_mood")
            today_moods_count = len(state.get("today_moods", []) or [])

        return {
            "is_running": self.is_running,
            "enable_auto_reflection": self.config.get("enable_auto_reflection", True),
            "enable_auto_diary": self.config.get("enable_auto_diary", True),
            "enabled_personas": self._enabled_personas(),
            "reflection_reference_count": reference_count,
            "current_awareness_text": state.get("current_awareness_text") if state else "",
            "today_reflections_count": len(state.get("today_reflections", [])) if state else 0,
            "last_reflection_time": state.get("last_reflection_time").strftime("%H:%M") if state and state.get("last_reflection_time") else None,
            "consecutive_failures": self.consecutive_failures,
            "silent_hours": silent_status,
            "diary_generated_today": bool(state.get("diary_generated_today", False)) if state else False,
            "last_diary_date": state.get("last_diary_date") if state else "",
            "primary_memory_target": self.last_selected_session_id,
            "primary_persona_id": self._get_primary_persona_id(normalized),
            "allow_overwrite_today_diary": self.config.get("allow_overwrite_today_diary", False),
            "recent_reflections_preview": (state.get("today_reflections", [])[-max(reference_count, 2):] if state else []),
            "next_reflection_in_minutes": self.config.get("thinking_interval_minutes", 30),
            "thinking_interval_jitter_seconds": self._safe_non_negative_int(self.config.get("thinking_interval_jitter_seconds", 0), 0),
            "reflection_dedupe_mode": self.config.get("reflection_dedupe_mode", "普通") or "普通",
            "reflection_dedupe_similarity_threshold": self._get_similarity_threshold(),
            "last_reflection_error_code": self.last_reflection_error_code,
            "last_reflection_error_message": self.last_reflection_error_message,
            "last_reflection_error_time": self.last_reflection_error_time,
            "last_diary_error_code": self.last_diary_error_code,
            "last_diary_error_message": self.last_diary_error_message,
            "last_diary_error_time": self.last_diary_error_time,
            "last_dedupe_hit": self.last_dedupe_hit,
            "last_dedupe_mode": self.last_dedupe_mode,
            "last_dedupe_source": self.last_dedupe_source,
            "last_selected_session_id": self.last_selected_session_id,
            "last_selected_session_source": self.last_selected_session_source,
            "diary_memory_version": 0,
            "reflection_retention_days": runtime_config["reflection_retention_days"],
            "diary_retention_days": runtime_config["diary_retention_days"],
            "webui_default_window_days": runtime_config["webui_default_window_days"],
            "webui_default_theme": runtime_config["webui_default_theme"],
            "webui_default_mode": runtime_config["webui_default_mode"],
            # 心情系统状态
            "enable_mood_system": self.config.get("enable_mood_system", True),
            "inject_mood_into_reply": self.config.get("inject_mood_into_reply", True),
            "current_mood": current_mood,
            "today_moods_count": today_moods_count,
        }
