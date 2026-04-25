"""
日记图片渲染模块
将日记内容渲染为纸质手写风格的图片
"""

import datetime
import io
import os
import random
import re
import struct
from pathlib import Path
from typing import Optional

from astrbot.api import logger

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


class DiaryRenderer:
    """日记图片渲染器 — 纸质手写风"""

    FONT_DOWNLOAD_URL = "https://github.com/AkisAya/NotoSerifSC-Regular/raw/main/NotoSerifSC-Regular.ttf"
    FONT_FILENAME = "NotoSerifSC-Regular.ttf"

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.fonts_dir = data_dir / "fonts"
        self._title_font: Optional[ImageFont.FreeTypeFont] = None
        self._body_font: Optional[ImageFont.FreeTypeFont] = None
        self._date_font: Optional[ImageFont.FreeTypeFont] = None
        self._decor_font: Optional[ImageFont.FreeTypeFont] = None
        self._initialized = False

    def _ensure_fonts(self) -> bool:
        if not HAS_PILLOW:
            logger.error("[DiaryRenderer] Pillow 未安装，无法渲染日记图片")
            return False

        if self._initialized:
            return self._title_font is not None

        font_path = self._find_or_download_font()
        if not font_path:
            logger.error("[DiaryRenderer] 无法获取字体文件，日记图片渲染不可用")
            return False

        try:
            self._title_font = ImageFont.truetype(str(font_path), 36)
            self._body_font = ImageFont.truetype(str(font_path), 22)
            self._date_font = ImageFont.truetype(str(font_path), 18)
            self._decor_font = ImageFont.truetype(str(font_path), 14)
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"[DiaryRenderer] 加载字体失败: {e}")
            return False

    def _find_or_download_font(self) -> Optional[Path]:
        cached = self.fonts_dir / self.FONT_FILENAME
        if cached.exists() and cached.stat().st_size > 100_000:
            return cached

        system_font = self._find_system_font()
        if system_font:
            return system_font

        return self._download_font()

    def _find_system_font(self) -> Optional[Path]:
        candidates = []

        if os.name == "nt":
            windir = os.environ.get("WINDIR", r"C:\Windows")
            font_dir = Path(windir) / "Fonts"
            candidates = [
                font_dir / "msyh.ttc",
                font_dir / "msyhbd.ttc",
                font_dir / "simhei.ttf",
                font_dir / "simsun.ttc",
                font_dir / "simfang.ttf",
            ]
        else:
            candidates = [
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
                Path("/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc"),
                Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
                Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
                Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            ]

        for path in candidates:
            if path.exists() and path.stat().st_size > 100_000:
                logger.info(f"[DiaryRenderer] 使用系统字体: {path}")
                return path

        return None

    def _download_font(self) -> Optional[Path]:
        try:
            import urllib.request
            self.fonts_dir.mkdir(parents=True, exist_ok=True)
            target = self.fonts_dir / self.FONT_FILENAME
            tmp = target.with_suffix(".tmp")

            logger.info(f"[DiaryRenderer] 正在下载字体: {self.FONT_DOWNLOAD_URL}")
            urllib.request.urlretrieve(self.FONT_DOWNLOAD_URL, str(tmp))

            if tmp.exists() and tmp.stat().st_size > 100_000:
                tmp.replace(target)
                logger.info(f"[DiaryRenderer] 字体下载完成: {target}")
                return target
            else:
                tmp.unlink(missing_ok=True)
                logger.error("[DiaryRenderer] 下载的字体文件过小，可能不完整")
                return None
        except Exception as e:
            logger.error(f"[DiaryRenderer] 下载字体失败: {e}")
            return None

    def render(self, diary_text: str, date_str: str = "", persona_name: str = "") -> Optional[bytes]:
        if not self._ensure_fonts():
            return None

        try:
            diary_text = self._preprocess_text(diary_text)
            if not diary_text.strip():
                return None

            lines = self._wrap_text(diary_text)
            img_width = 680
            padding_x = 50
            padding_top = 60
            padding_bottom = 70
            line_height = 38
            title_area_height = 90

            content_height = len(lines) * line_height
            img_height = padding_top + title_area_height + content_height + padding_bottom
            img_height = max(img_height, 400)

            img = self._create_paper_background(img_width, img_height)
            draw = ImageDraw.Draw(img)

            y = padding_top
            y = self._draw_title(draw, date_str, persona_name, y, img_width)
            y += 20
            self._draw_separator(draw, y, padding_x, img_width - padding_x)
            y += 25
            y = self._draw_body(draw, lines, y, padding_x, line_height)
            self._draw_footer(draw, img_height, img_width, padding_x, date_str)

            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"[DiaryRenderer] 渲染日记图片失败: {e}", exc_info=True)
            return None

    def _preprocess_text(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _create_paper_background(self, width: int, height: int) -> Image.Image:
        bg_r, bg_g, bg_b = 252, 248, 240

        img = Image.new("RGB", (width, height), (bg_r, bg_g, bg_b))
        pixels = img.load()

        random.seed(42)
        for x in range(width):
            for y in range(height):
                noise = random.randint(-4, 4)
                r = max(0, min(255, bg_r + noise))
                g = max(0, min(255, bg_g + noise))
                b = max(0, min(255, bg_b + noise))
                pixels[x, y] = (r, g, b)

        draw = ImageDraw.Draw(img)
        border_color = (200, 185, 165)
        draw.rectangle([0, 0, width - 1, height - 1], outline=border_color, width=2)

        inner_border = (225, 215, 200)
        draw.rectangle([4, 4, width - 5, height - 5], outline=inner_border, width=1)

        return img

    def _draw_title(self, draw: ImageDraw.Draw, date_str: str, persona_name: str, y: int, img_width: int) -> int:
        title_text = self._format_date_title(date_str)
        bbox = draw.textbbox((0, 0), title_text, font=self._title_font)
        tw = bbox[2] - bbox[0]
        tx = (img_width - tw) // 2
        draw.text((tx, y), title_text, fill=(62, 50, 38), font=self._title_font)

        y += 50

        if persona_name:
            sub_text = f"— {persona_name}"
            bbox_s = draw.textbbox((0, 0), sub_text, font=self._date_font)
            sw = bbox_s[2] - bbox_s[0]
            sx = (img_width - sw) // 2
            draw.text((sx, y), sub_text, fill=(140, 125, 105), font=self._date_font)
            y += 28

        return y

    def _format_date_title(self, date_str: str) -> str:
        if not date_str:
            return datetime.datetime.now().strftime("%Y年%m月%d日")
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            weekday = weekdays[dt.weekday()]
            return f"{dt.year}年{dt.month}月{dt.day}日 {weekday}"
        except Exception:
            return date_str

    def _draw_separator(self, draw: ImageDraw.Draw, y: int, x_start: int, x_end: int):
        line_color = (195, 180, 160)
        draw.line([(x_start, y), (x_end, y)], fill=line_color, width=1)

        mid = (x_start + x_end) // 2
        decor = "✦"
        bbox = draw.textbbox((0, 0), decor, font=self._decor_font)
        dw = bbox[2] - bbox[0]
        draw.text((mid - dw // 2, y - 10), decor, fill=(175, 155, 130), font=self._decor_font)

    def _draw_body(self, draw: ImageDraw.Draw, lines: list[str], y: int, padding_x: int, line_height: int) -> int:
        text_color = (52, 42, 32)
        for line in lines:
            if line == "":
                y += line_height // 2
                continue
            draw.text((padding_x, y), line, fill=text_color, font=self._body_font)
            y += line_height
        return y

    def _draw_footer(self, draw: ImageDraw.Draw, img_height: int, img_width: int, padding_x: int, date_str: str):
        footer_y = img_height - 40
        footer_color = (180, 165, 145)
        time_str = datetime.datetime.now().strftime("%H:%M")
        footer_text = f"✎ {time_str}"
        draw.text((padding_x, footer_y), footer_text, fill=footer_color, font=self._decor_font)

    def _wrap_text(self, text: str) -> list[str]:
        max_width = 560
        result: list[str] = []

        for paragraph in text.split("\n"):
            if not paragraph.strip():
                result.append("")
                continue

            current_line = ""
            for char in paragraph:
                test_line = current_line + char
                try:
                    bbox = self._body_font.getbbox(test_line)
                    w = bbox[2] - bbox[0]
                except Exception:
                    w = len(test_line) * 22

                if w > max_width:
                    if current_line:
                        result.append(current_line)
                    current_line = char
                else:
                    current_line = test_line

            if current_line:
                result.append(current_line)

        return result
