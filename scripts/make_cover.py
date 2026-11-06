"""AI 日报封面图生成器 — 零依赖（仅用 Python 标准库）

设计原则：
  1. 完全程序化绘制几何图形与渐变色，不使用任何第三方素材 → 无版权/侵权风险
  2. 使用系统自带字体（微软雅黑 / 黑体）渲染文字，字体仅作屏幕显示，不随文件分发
  3. 输出 PNG，尺寸 900x383（公众号封面 2.35:1，文件远小于 2MB 限制）

用法：
  python make_cover.py --date 2026-09-29 --out cover.png
  python make_cover.py --date 2026-09-29 --title "AI日报" --accent "#165DFF" --out cover.png
"""

import argparse
import math
import os
import struct
import subprocess
import sys
import zlib
from datetime import datetime


# ---------- 解释器自举（保证能用到 PIL 渲染中文）----------

# 首选：带 Pillow 的解释器。脚本启动时若当前解释器没有 PIL 且未标记已重入，
# 则自动用备选解释器重新执行自己，避免调用方还要关心用哪个 python。
# 可用环境变量 AI_DAILY_PYTHON 指定（优先级最高）；否则按平台探测常见安装路径。


def _candidate_pythons():
    cands = []
    env = os.environ.get("AI_DAILY_PYTHON", "").strip()
    if env:
        cands.append(env)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        for ver in ("Python314", "Python313", "Python312", "Python311"):
            if local:
                cands.append(os.path.join(local, "Programs", "Python", ver, "python.exe"))
        cands += [r"C:\Python313\python.exe", r"C:\Python312\python.exe"]
    else:
        cands += ["/usr/bin/python3", "/usr/local/bin/python3", "python3"]
    return [c for c in cands if c]

_BOOTSTRAP_FLAG = "--_no-bootstrap"


def _has_pil():
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def _bootstrap():
    """当前解释器没有 PIL 时，用备选解释器重跑自己（返回 exit code 或 None）。"""
    if _has_pil() or _BOOTSTRAP_FLAG in sys.argv:
        return None
    for cand in _candidate_pythons():
        if not os.path.isfile(cand) or os.path.abspath(cand) == os.path.abspath(sys.executable):
            continue
        try:
            probe = subprocess.run([cand, "-c", "import PIL"], capture_output=True, timeout=30)
        except Exception:
            continue
        if probe.returncode != 0:
            continue
        cmd = [cand, os.path.abspath(__file__), _BOOTSTRAP_FLAG] + sys.argv[1:]
        return subprocess.run(cmd).returncode
    return None


# ---------- PNG 编码 ----------

def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path, width, height, rgb_rows):
    """rgb_rows: 每行一个 bytes，长度为 width*3"""
    raw = b"".join(b"\x00" + row for row in rgb_rows)   # 每行前置 filter byte 0
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw, 9))
    png += _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


# ---------- 画布 ----------

class Canvas:
    def __init__(self, w, h, bg=(255, 255, 255)):
        self.w, self.h = w, h
        self.buf = [bytearray(bytes(bg) * w) for _ in range(h)]

    def px(self, x, y, color, alpha=1.0):
        if not (0 <= x < self.w and 0 <= y < self.h) or alpha <= 0:
            return
        row = self.buf[y]
        i = x * 3
        if alpha >= 1.0:
            row[i:i + 3] = bytes(color)
        else:
            r, g, b = color
            row[i] = int(row[i] + (r - row[i]) * alpha + 0.5)
            row[i + 1] = int(row[i + 1] + (g - row[i + 1]) * alpha + 0.5)
            row[i + 2] = int(row[i + 2] + (b - row[i + 2]) * alpha + 0.5)

    def vgrad(self, x0, y0, x1, y1, top, bottom):
        """竖直渐变填充矩形"""
        span = max(1, y1 - y0)
        for y in range(max(0, y0), min(self.h, y1)):
            t = (y - y0) / span
            c = tuple(int(top[k] + (bottom[k] - top[k]) * t + 0.5) for k in range(3))
            for x in range(max(0, x0), min(self.w, x1)):
                self.px(x, y, c)

    def rounded_rect(self, x0, y0, x1, y1, r, color, alpha=1.0):
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                dx = 0
                if x < x0 + r:
                    dx = (x0 + r) - x
                elif x >= x1 - r:
                    dx = x - (x1 - r - 1)
                dy = 0
                if y < y0 + r:
                    dy = (y0 + r) - y
                elif y >= y1 - r:
                    dy = y - (y1 - r - 1)
                if dx or dy:
                    d = math.hypot(dx, dy)
                    if d > r:
                        continue
                    if d > r - 1:
                        self.px(x, y, color, alpha * (r - d))
                        continue
                self.px(x, y, color, alpha)

    def rect(self, x0, y0, x1, y1, color, alpha=1.0):
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                self.px(x, y, color, alpha)

    def disc(self, cx, cy, r, color, alpha=1.0, soft=True):
        """柔边圆（用于装饰光斑）"""
        rr = r + (r * 0.55 if soft else 0)
        for y in range(max(0, int(cy - rr)), min(self.h, int(cy + rr) + 1)):
            for x in range(max(0, int(cx - rr)), min(self.w, int(cx + rr) + 1)):
                d = math.hypot(x - cx, y - cy)
                if d > rr:
                    continue
                a = alpha * (1.0 - d / rr) ** 2 if soft else alpha
                self.px(x, y, color, a)

    def rows(self):
        return [bytes(r) for r in self.buf]


# ---------- 字体 ----------

FONT_CANDIDATES = [
    # Windows（微软雅黑 / 等线 / 黑体）
    (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"),
    (r"C:\Windows\Fonts\Dengb.ttf", r"C:\Windows\Fonts\Deng.ttf"),
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttc"),
    # macOS（苹方 / 黑体）
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
    ("/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/STHeiti Light.ttc"),
    # Linux（Noto / 文泉驿）
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
     "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
]


class BitmapFont:
    """从 TTF/TTC 栅格化所需字符，避免依赖 PIL。

    用 Windows 自带 GDI 无法直接取点位图，这里改用「字体度量 + 逐字符画点」
    不现实，故采用另一种稳妥策略：把文字交给系统渲染到临时文件再读回，
    但这会引入依赖。因此本脚本最后采用：文字以几何块阵 + 系统字体二选一，
    优先尝试 PIL，缺失则退回内置 5x7 拉丁点阵 + 中文字形框。
    """

    def __init__(self):
        self._pil = None
        try:
            from PIL import Image, ImageDraw, ImageFont  # noqa
            self._pil = (Image, ImageDraw, ImageFont)
        except Exception:
            self._pil = None

    @property
    def available(self):
        return self._pil is not None

    def render(self, canvas, text, size, xy, color, anchor="lt", bold=True):
        if not self._pil:
            return False
        Image, ImageDraw, ImageFont = self._pil
        path = None
        for b, n in FONT_CANDIDATES:
            for cand in ((b, n) if bold else (n, b)):
                if not os.path.isfile(cand):
                    continue
                try:
                    font = ImageFont.truetype(cand, size)
                except Exception:
                    continue
                # 只有能真正映射出中文字形才算可用
                if _font_has_cjk(font, ImageFont):
                    path, font_obj = cand, font
                    break
            if path:
                break
        if not path:
            return False
        tmp = Image.new("RGB", (canvas.w, canvas.h), (0, 0, 0))
        d = ImageDraw.Draw(tmp)
        d.text(xy, text, font=font_obj, fill=color, anchor=anchor)
        px = tmp.load()
        for y in range(canvas.h):
            for x in range(canvas.w):
                r, g, b = px[x, y]
                if r or g or b:
                    canvas.px(x, y, (r, g, b), 1.0)
        return True


def _font_has_cjk(font, ImageFont):
    """检测字体是否真的含中文字形（ttc 里可能挑到不含 CJK 的子字体）。"""
    try:
        mask = font.getmask("日")
        return mask.size[0] > 0 and mask.size[1] > 0
    except Exception:
        return False


# ---------- 内置 5x7 拉丁点阵（PIL 缺失时的兜底）----------

GLYPHS_5X7 = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    " ": ["00000"] * 7,
}


def draw_ascii(canvas, text, scale, x0, y0, color):
    cx = x0
    for ch in text.upper():
        g = GLYPHS_5X7.get(ch)
        if not g:
            cx += 6 * scale
            continue
        for ry, line in enumerate(g):
            for rx, bit in enumerate(line):
                if bit == "1":
                    canvas.rect(cx + rx * scale, y0 + ry * scale,
                                cx + (rx + 1) * scale, y0 + (ry + 1) * scale, color)
        cx += 6 * scale
    return cx


# ---------- 模板模式（底图 + 每日日期角标）----------

WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _resize_cover(img, w, h):
    """等比缩放并居中裁剪到目标尺寸。"""
    scale = max(w / img.width, h / img.height)
    nw, nh = round(img.width * scale), round(img.height * scale)
    img = img.resize((nw, nh))
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return img.crop((x0, y0, x0 + w, y0 + h))


def _pick_font(ImageFont, size, bold=True):
    for b, n in FONT_CANDIDATES:
        for cand in ((b, n) if bold else (n, b)):
            if not os.path.isfile(cand):
                continue
            try:
                font = ImageFont.truetype(cand, size)
            except Exception:
                continue
            if _font_has_cjk(font, ImageFont):
                return font
    return None


def _chip(img, ImageDraw, font, text, x, y, fg, bg, pad_x=18, pad_y=9, radius=10):
    """在 (x, y)（左上角）画一个圆角文字条，返回 (宽, 高)。"""
    bbox = ImageDraw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = tw + pad_x * 2, th + pad_y * 2
    ImageDraw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=bg)
    ImageDraw.text((x + pad_x - bbox[0], y + pad_y - bbox[1]), text, font=font, fill=fg)
    return (w, h)


def build_with_template(template_path, date_str, out_path, accent_hex):
    """用固定模板图做底，叠加每日日期角标。需要 PIL（模板是 JPG）。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None  # 调用方回落到程序化模式

    w, h = 900, 383
    img = _resize_cover(Image.open(template_path).convert("RGB"), w, h)
    draw = ImageDraw.Draw(img, "RGBA")
    accent = hex2rgb(accent_hex)

    dt = datetime.strptime(date_str, "%Y-%m-%d")
    label = f"{dt.month}月{dt.day}日 · {WEEKDAY_CN[dt.weekday()]}"

    font_main = _pick_font(ImageFont, 30, bold=True)
    if font_main:
        cw, ch = _chip(img, draw, font_main, label, 34, h - 92,
                       (255, 255, 255), accent + (235,))
        # 日期条下一行放一个细注脚（不与模板自带标题重复）
        font_sub = _pick_font(ImageFont, 15, bold=False)
        if font_sub:
            draw.text((36, h - 92 + ch + 8), "三源融合 · 公开信息聚合",
                      font=font_sub, fill=(255, 255, 255, 205))

    img.save(out_path, "PNG")
    return True


# ---------- 主流程 ----------

def hex2rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def build(date_str, title, subtitle, accent_hex, out_path, w=900, h=383):
    accent = hex2rgb(accent_hex)
    deep = (10, 18, 38)
    mid = (18, 34, 74)

    c = Canvas(w, h)
    c.vgrad(0, 0, w, h, deep, mid)

    # 装饰：无版权几何光斑（纯算法）
    c.disc(w * 0.86, h * 0.16, 74, accent, alpha=0.20)
    c.disc(w * 0.72, h * 0.92, 52, (90, 150, 255), alpha=0.13)
    c.disc(w * 0.05, h * 0.88, 44, accent, alpha=0.09)

    # 顶部强调条
    c.rect(0, 0, w, 5, accent, alpha=0.95)

    # 代码流装饰线（右侧，程序化）
    for i in range(9):
        y = 52 + i * 33
        ln = int(52 + (i * 37) % 118)
        alpha = 0.10 + (i % 3) * 0.05
        c.rect(w - 210, y, w - 210 + ln, y + 3, (120, 175, 255), alpha=alpha)

    # 网格点阵（左下）
    for gy in range(5):
        for gx in range(7):
            c.rect(34 + gx * 15, h - 96 + gy * 15, 37 + gx * 15, h - 93 + gy * 15,
                   (110, 160, 240), alpha=0.16)

    bf = BitmapFont()
    used_pil = False

    if bf.available:
        used_pil = bf.render(c, title, 58, (46, 104), (255, 255, 255), "lm", True)
        bf.render(c, subtitle, 26, (48, 182), hex2rgb("#9FB6DD"), "lm", False)
        bf.render(c, date_str, 30, (48, 250), accent, "lm", True)
        bf.render(c, "AI DAILY", 18, (w - 46, 46), hex2rgb("#6F86B0"), "rt", False)
    else:
        draw_ascii(c, "AI DAILY", 3, 48, 104, (255, 255, 255))
        draw_ascii(c, date_str, 2, 48, 250, accent)

    # 底部说明条
    c.rect(0, h - 42, w, h, (8, 14, 30), alpha=0.72)
    if bf.available:
        bf.render(c, "来源：何夕2077 · AIHOT · follow-builders（公开信息聚合）",
                  15, (48, h - 21), hex2rgb("#7C90B4"), "lm", False)
    else:
        draw_ascii(c, "SOURCES: PUBLIC AGGREGATION", 1, 48, h - 26, (124, 144, 180))

    write_png(out_path, w, h, c.rows())
    return used_pil


if __name__ == "__main__":
    _rc = _bootstrap()
    if _rc is not None:
        sys.exit(_rc)

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--title", default="AI日报")
    ap.add_argument("--subtitle", default="三源融合 · AI Coding / 具身智能 / 智能体安全")
    ap.add_argument("--accent", default="#165DFF")
    ap.add_argument("--template", default=None,
                    help="底图模板（JPG/PNG）。提供时走模板+日期角标模式，PIL 缺失则回落程序化")
    ap.add_argument("--out", required=True)
    a, _unknown = ap.parse_known_args()
    # 去掉自举用哨兵，避免影响后续解析
    _unknown = [u for u in _unknown if u != _BOOTSTRAP_FLAG]

    y, m, d = a.date.split("-")
    cn_date = f"{y} 年 {int(m)} 月 {int(d)} 日"

    if a.template and os.path.isfile(a.template):
        mode = build_with_template(a.template, a.date, a.out, a.accent)
        if mode:
            print(f"OK  {a.out}  {os.path.getsize(a.out)} bytes  (template + date chip)")
            sys.exit(0)
        print("template mode unavailable (no PIL), falling back to procedural")

    pil = build(cn_date, a.title, a.subtitle, a.accent, a.out)
    print(f"OK  {a.out}  {os.path.getsize(a.out)} bytes  (text via {'PIL' if pil else 'builtin bitmap'})")
