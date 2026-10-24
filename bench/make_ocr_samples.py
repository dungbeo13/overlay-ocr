"""Generate synthetic, labelled OCR test images (EN + JA) with Pillow and Windows fonts.

Output: bench/data/ocr/synthetic/<case>/<lang>_<n>.png plus
  labels.json  {"<case>/<lang>_<n>.png": ["line", ...]}   ground truth in reading order
  meta.json    {"<case>/<lang>_<n>.png": {"case", "lang", "size", "font", "lines": [{"text", "box"}]}}
All sentences are original. Output is deterministic for a given --seed.

    python -m bench.make_ocr_samples            # 8 images per case and language
    python -m bench.make_ocr_samples --quick    # 2 per case, into synthetic_quick/ (full set untouched)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.ocr.base import TextBox
from app.ocr.textnorm import reading_order
from bench.common import DATA_DIR, print_table, save_results

OUT_DIR = DATA_DIR / "ocr" / "synthetic"
CASES = ("plain_ui", "game_dialog", "subtitle", "document", "small_text", "vertical_ja")

# ----------------------------------------------------------------------------- fonts

_FONT_DIRS = [
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
]
# Preferred families first (rotated per image), then fallbacks.
_FONT_CANDIDATES = {
    ("en", "regular"): ["segoeui.ttf", "arial.ttf", "calibri.ttf", "tahoma.ttf", "verdana.ttf"],
    ("en", "bold"): ["segoeuib.ttf", "arialbd.ttf", "calibrib.ttf", "tahomabd.ttf", "verdanab.ttf"],
    ("ja", "regular"): ["YuGothM.ttc", "meiryo.ttc", "msgothic.ttc", "YuGothR.ttc", "BIZ-UDGothicR.ttc"],
    ("ja", "bold"): ["YuGothB.ttc", "meiryob.ttc", "msgothic.ttc", "BIZ-UDGothicB.ttc"],
}
_PREFERRED = {"en": 2, "ja": 3}  # how many leading candidates are "preferred" families


def _find(name: str) -> Path | None:
    for d in _FONT_DIRS:
        p = d / name
        if p.is_file():
            return p
    return None


def available_fonts(lang: str, weight: str) -> list[Path]:
    found = [p for n in _FONT_CANDIDATES[(lang, weight)] if (p := _find(n))]
    preferred = [p for p in found if p.name in _FONT_CANDIDATES[(lang, weight)][: _PREFERRED[lang]]]
    return preferred or found


@lru_cache(maxsize=256)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


@dataclass
class FontPick:
    regular: Path
    bold: Path

    def get(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return load_font(str(self.bold if bold else self.regular), size)


def pick_fonts(lang: str, idx: int) -> FontPick | None:
    reg, bold = available_fonts(lang, "regular"), available_fonts(lang, "bold")
    if not reg:
        return None
    r = reg[idx % len(reg)]
    b = bold[idx % len(bold)] if bold else r
    return FontPick(r, b)


# ----------------------------------------------------------------------------- text pools (original)

EN_UI = [
    "File", "Edit", "View", "Settings", "Save changes", "Cancel", "Apply", "Search files and folders",
    "Recent documents", "Download complete", "Sign out", "Account settings", "Notifications",
    "Display brightness", "Check for updates", "Open in new window", "Privacy and security",
    "Sound output device", "Export as PDF", "Keyboard shortcuts", "Show hidden items",
    "Sort by date modified", "Battery saver is on", "Connected to network", "Storage almost full",
    "Restart required to finish installing", "Automatic backups every 6 hours", "Language and region",
    "Remember this device for 30 days", "Clear browsing data", "Default printer", "Night light schedule",
]
EN_TITLES = ["Control Panel", "Media Library", "Project Settings", "Downloads", "Task Manager", "Preferences"]
EN_BUTTONS = ["OK", "Cancel", "Apply", "Next", "Back", "Retry", "Close", "Install", "Skip"]

EN_SPEAKERS = ["Captain Mira", "Old Tomas", "Elara", "Merchant Vex", "Gate Guard", "Professor Lin"]
EN_DIALOG = [
    "The old bridge fell apart last winter, so we have to cross the river at dawn.",
    "Bring me three silver feathers and I will tell you where the map is hidden.",
    "Careful, traveler. The forest paths shift whenever the moon is full.",
    "I have waited years for someone brave enough to open that door.",
    "Your blade is chipped. Leave it with me tonight and it will shine again.",
    "We are running low on supplies. Could you check the storage room?",
    "The lighthouse keeper has not been seen since the storm three days ago.",
    "If you help me fix the mill, the whole village will owe you a favor.",
    "Do not trust the man in the blue coat. He sold my cart for a handful of coins.",
    "The north tower is locked, but I hear the key was lost near the well.",
    "Rest here for a while. The road ahead is long and the nights are cold.",
    "Only one of these potions is safe to drink. Choose wisely.",
]
EN_HUD = [("Lv. 12", "Gold 3,450"), ("HP 120/150", "Day 4"), ("Quest: Find the key", "08:45"), ("Stage 3-2", "Score 18,200")]

EN_SUBS = [
    "I told you we should have taken the earlier train.",
    "Wait, did you hear that noise coming from the basement?",
    "It was never about the money.",
    "Let's meet at the cafe on the corner at seven.",
    "If we leave right now, we can still beat the storm.",
    "You kept the letter all these years?",
    "Nobody else knows the code, so it has to be one of us.",
    "Turn left at the bakery, then keep going until you see the river.",
    "I am not angry. I just need some time to think.",
    "The last bus already left. We are walking home tonight.",
    "Promise me you will call when you land.",
    "Something about this place feels different today.",
]

EN_DOC = [
    "Community gardens have become a common sight in many cities over the past decade.",
    "They give residents a place to grow vegetables, meet neighbors, and learn about local plants.",
    "Most gardens are managed by volunteers who share tools, seeds, and watering duties.",
    "A small plot can produce a surprising amount of food when it is planned carefully.",
    "Beans and squash grow well together because they use space and nutrients differently.",
    "Rainwater barrels reduce the cost of watering and ease the load on public supply lines.",
    "The new release improves startup time and reduces memory use on older laptops.",
    "Several settings were moved to a single page so that they are easier to find.",
    "Files saved in the previous format can still be opened, but they are converted on save.",
    "Users who rely on keyboard navigation will notice clearer focus outlines in every dialog.",
    "Public transit ridership usually rises when service is frequent and easy to understand.",
    "Clear maps, reliable timetables, and simple fares matter more than shiny new vehicles.",
    "Many riders choose a route based on how long they expect to wait at the stop.",
    "Short transfers between lines can make a network feel much smaller than it really is.",
    "Libraries now lend far more than books, including tools, cameras, and musical instruments.",
    "Workshops on budgeting and resume writing are often fully booked weeks in advance.",
    "Quiet study rooms remain the most requested space during the exam season.",
    "The museum will extend its opening hours on Fridays throughout the summer.",
    "Visitors are encouraged to reserve tickets online to avoid waiting at the entrance.",
    "A guided tour of the new wing starts every hour from the main hall.",
]

EN_SMALL = EN_UI + [
    "Last synced 5 minutes ago", "Ping 34 ms", "Press F to interact", "Autosave enabled",
    "3 new messages", "Volume 65%", "Inventory full", "Checkpoint reached", "Version 2.4.1 (build 1180)",
]

JA_UI = [
    "ファイル", "編集", "表示", "設定", "変更を保存", "キャンセル", "適用", "ファイルとフォルダーを検索",
    "最近使ったドキュメント", "ダウンロードが完了しました", "サインアウト", "アカウント設定", "通知",
    "画面の明るさ", "更新を確認", "新しいウィンドウで開く", "プライバシーとセキュリティ", "音声出力デバイス",
    "PDFとしてエクスポート", "キーボードショートカット", "隠しファイルを表示", "更新日時で並べ替え",
    "バッテリー節約機能がオンです", "ネットワークに接続済み", "ストレージの空き容量が少なくなっています",
    "インストールを完了するには再起動が必要です", "六時間ごとに自動バックアップ", "言語と地域",
    "このデバイスを三十日間記憶する", "閲覧データを削除", "既定のプリンター", "夜間モードのスケジュール",
]
JA_TITLES = ["コントロールパネル", "メディアライブラリ", "プロジェクト設定", "ダウンロード", "タスクマネージャー", "環境設定"]
JA_BUTTONS = ["OK", "キャンセル", "適用", "次へ", "戻る", "再試行", "閉じる", "インストール", "スキップ"]

JA_SPEAKERS = ["ミラ隊長", "トマス爺さん", "エラーラ", "商人ヴェクス", "門番", "リン教授"]
JA_DIALOG = [
    "古い橋は去年の冬に崩れたから、夜明けに川を渡るしかない。",
    "銀の羽を三枚持ってきたら、地図の隠し場所を教えてあげよう。",
    "気をつけて、旅の人。満月の夜には森の道が変わるんだ。",
    "あの扉を開ける勇気のある人を、何年も待っていたよ。",
    "剣が欠けているね。今夜預けてくれれば、元通りに磨いておくよ。",
    "物資が足りなくなってきた。倉庫を確認してくれないか？",
    "嵐の日から三日間、灯台守の姿を誰も見ていないそうだ。",
    "水車の修理を手伝ってくれたら、村のみんなが恩に着るよ。",
    "青いコートの男を信用するな。あいつは私の荷車をはした金で売ったんだ。",
    "北の塔には鍵がかかっているが、鍵は井戸の近くでなくしたらしい。",
    "ここで少し休んでいきなさい。この先の道は長く、夜は冷えるからね。",
    "この中で安全に飲める薬は一つだけ。よく考えて選びなさい。",
]
JA_HUD = [("Lv.12", "所持金 3,450"), ("HP 120/150", "四日目"), ("クエスト：鍵を探せ", "08:45"), ("ステージ 3-2", "スコア 18,200")]

JA_SUBS = [
    "だから早い電車に乗ろうって言ったのに。",
    "待って、地下室から何か聞こえなかった？",
    "最初からお金の問題じゃなかったんだ。",
    "七時に角のカフェで会いましょう。",
    "今すぐ出発すれば、嵐の前に間に合うはずだ。",
    "あの手紙をずっと持っていたの？",
    "暗証番号を知っているのは私たちだけだ。",
    "パン屋の角を左に曲がって、川が見えるまでまっすぐ進んで。",
    "怒ってないよ。少し考える時間がほしいだけ。",
    "最終バスはもう出ちゃった。今夜は歩いて帰ろう。",
    "着いたら必ず電話するって約束して。",
    "今日はこの場所の空気がいつもと違う気がする。",
]

JA_DOC = [
    "ここ十年ほどで、多くの都市に市民農園が見られるようになった。",
    "住民は野菜を育て、近所の人と交流し、地域の植物について学ぶことができる。",
    "ほとんどの農園はボランティアが運営し、道具や種、水やりの当番を分け合っている。",
    "小さな区画でも、計画を立てて育てれば驚くほど多くの収穫がある。",
    "豆とカボチャは空間と養分の使い方が違うため、一緒に育てると相性が良い。",
    "雨水をためるタンクを使えば、水道代を抑えられ、給水設備の負担も軽くなる。",
    "今回の更新では起動時間が短くなり、古いノートパソコンでのメモリ使用量も減った。",
    "いくつかの設定が一つの画面にまとめられ、見つけやすくなった。",
    "以前の形式で保存したファイルも開けるが、保存するときに新しい形式に変換される。",
    "キーボードで操作する利用者は、すべてのダイアログで選択枠が見やすくなったことに気づくだろう。",
    "公共交通の利用者は、本数が多く分かりやすい路線ほど増える傾向がある。",
    "新しい車両よりも、見やすい路線図と正確な時刻表、簡単な運賃のほうが大切だ。",
    "多くの利用者は、停留所での待ち時間を考えて路線を選んでいる。",
    "乗り換えが短いと、路線網は実際よりもずっと小さく感じられる。",
    "最近の図書館では、本だけでなく工具やカメラ、楽器まで貸し出している。",
    "家計の管理や履歴書の書き方の講座は、数週間前から満席になることが多い。",
    "試験の時期には、静かな自習室がいちばん人気のある場所になる。",
    "博物館は夏の間、金曜日の開館時間を延長する予定だ。",
    "入口での待ち時間を避けるため、事前にインターネットで予約することをおすすめする。",
    "新しい展示棟の案内ツアーは、毎時ちょうどに正面ホールから出発する。",
]

JA_SMALL = JA_UI + [
    "五分前に同期しました", "Ping 34 ms", "Fキーで調べる", "自動保存が有効です", "新着メッセージ三件",
    "音量 65%", "持ち物がいっぱいです", "チェックポイントに到達", "バージョン 2.4.1",
]

# Short manga lines; no long-vowel marks or brackets (they need rotated glyphs in vertical text).
JA_VERTICAL = [
    "まさか本当に来るとは", "思わなかったよ", "ここは任せて", "先に行って！", "約束したでしょう",
    "絶対に諦めない", "もう少しだけ待って", "今の音は何だ？", "早く逃げろ！", "君の番だよ",
    "信じていいの？", "明日また会おう", "風が変わった", "あの日の約束を", "忘れていないよ",
    "道を開けて！", "一人で行くな", "雨が止んだら", "出発しよう", "静かにして",
    "誰かいるの？", "大丈夫、心配ない", "これで最後だ", "まだ終わってない",
]

POOLS = {
    "en": dict(ui=EN_UI, titles=EN_TITLES, buttons=EN_BUTTONS, speakers=EN_SPEAKERS, dialog=EN_DIALOG,
               hud=EN_HUD, subs=EN_SUBS, doc=EN_DOC, small=EN_SMALL),
    "ja": dict(ui=JA_UI, titles=JA_TITLES, buttons=JA_BUTTONS, speakers=JA_SPEAKERS, dialog=JA_DIALOG,
               hud=JA_HUD, subs=JA_SUBS, doc=JA_DOC, small=JA_SMALL),
}

# ----------------------------------------------------------------------------- drawing helpers

# Characters that must not start a line in Japanese (simple kinsoku shori).
_NO_LINE_START = set("、。，．・：；？！ゃゅょっぁぃぅぇぉャュョッァィゥェォ」』）】ー")


@dataclass
class Canvas:
    img: Image.Image
    lines: list[TextBox] = field(default_factory=list)

    @property
    def draw(self) -> ImageDraw.ImageDraw:
        return ImageDraw.Draw(self.img)

    def text(self, xy, text: str, font, fill, stroke: int = 0, stroke_fill=None, anchor: str = "la") -> tuple:
        d = self.draw
        d.text(xy, text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill, anchor=anchor)
        l, t, r, b = d.textbbox(xy, text, font=font, stroke_width=stroke, anchor=anchor)
        self.lines.append(TextBox(text, (int(l), int(t), int(r - l), int(b - t))))
        return l, t, r, b


def wrap(text: str, font, max_w: float, lang: str) -> list[str]:
    """Greedy wrap by measured width: words for EN, characters (with kinsoku) for JA."""
    if lang == "en":
        out, cur = [], ""
        for word in text.split():
            cand = f"{cur} {word}".strip()
            if cur and font.getlength(cand) > max_w:
                out.append(cur)
                cur = word
            else:
                cur = cand
        return out + ([cur] if cur else [])
    out, cur = [], ""
    for ch in text:
        if cur and font.getlength(cur + ch) > max_w and ch not in _NO_LINE_START:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    return out + ([cur] if cur else [])


def colourful_background(rng: np.random.Generator, w: int, h: int) -> Image.Image:
    """Busy, saturated game-like scene: smooth colour field + shapes + fine noise."""
    base = rng.integers(0, 256, (4, 6, 3), dtype=np.uint8)
    img = Image.fromarray(base).resize((w, h), Image.BICUBIC)
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(int(rng.integers(25, 45))):
        x, y = int(rng.integers(-100, w)), int(rng.integers(-100, h))
        s = int(rng.integers(30, 260))
        col = tuple(int(c) for c in rng.integers(0, 256, 3)) + (int(rng.integers(90, 220)),)
        if rng.random() < 0.5:
            d.ellipse((x, y, x + s, y + int(s * rng.uniform(0.5, 1.5))), fill=col)
        else:
            d.polygon([(x + int(rng.integers(0, s)), y + int(rng.integers(0, s))) for _ in range(3)], fill=col)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    arr = np.asarray(img).astype(np.int16) + rng.normal(0, 9, (h, w, 1)).astype(np.int16)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def photo_background(rng: np.random.Generator, w: int, h: int) -> Image.Image:
    """Photo-like busy strip: multi-scale colour noise, bright highlights, sensor noise."""
    acc = np.zeros((h, w, 3), np.float32)
    for gh, gw, wt in ((2, 8, 0.5), (6, 24, 0.3), (20, 96, 0.2)):
        layer = rng.random((gh, gw, 3)).astype(np.float32)
        acc += wt * np.asarray(Image.fromarray((layer * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32)
    img = Image.fromarray(np.clip(acc, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(int(rng.integers(8, 16))):  # bright windows / lamps behind subtitles
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        s = int(rng.integers(10, 80))
        v = int(rng.integers(200, 256))
        d.ellipse((x, y, x + s, y + s), fill=(v, v, int(rng.integers(150, 256)), 200))
    img = img.filter(ImageFilter.GaussianBlur(2.0))
    arr = np.asarray(img).astype(np.int16) + rng.normal(0, 6, (h, w, 1)).astype(np.int16)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# ----------------------------------------------------------------------------- cases


def make_plain_ui(lang: str, i: int, rng: np.random.Generator, rnd: random.Random, fonts: FontPick) -> Canvas:
    dark = i % 2 == 0
    w, h = rnd.choice([(1024, 640), (1280, 720), (960, 600)])
    bg, panel, fg, sub = ((32, 32, 36), (45, 45, 52), (235, 235, 240), (170, 170, 180)) if dark else \
        ((243, 243, 243), (255, 255, 255), (25, 25, 25), (95, 95, 100))
    c = Canvas(Image.new("RGB", (w, h), bg))
    p = POOLS[lang]
    d = c.draw
    d.rectangle((0, 0, w, 44), fill=panel)
    c.text((16, 22), rnd.choice(p["titles"]), fonts.get(rnd.choice([15, 16, 18])), fg, anchor="lm")
    size = rnd.choice([16, 18, 20, 22])
    y = 76
    c.text((32, y), rnd.choice(p["ui"]), fonts.get(size + 8, bold=True), fg)
    y += size + 30
    for label in rnd.sample(p["ui"], 6):
        if y > h - 110:
            break
        d.rounded_rectangle((24, y - 8, w - 24, y + size + 12), radius=6, fill=panel)
        c.text((40, y), label, fonts.get(size), fg if rnd.random() < 0.7 else sub)
        y += size + 30
    bx, by = w - 24, h - 60
    for label in rnd.sample(p["buttons"], 3)[::-1]:
        f = fonts.get(size)
        bw = int(f.getlength(label)) + 40
        accent = rnd.random() < 0.4
        d.rounded_rectangle((bx - bw, by, bx, by + size + 20), radius=5,
                            fill=(0, 103, 192) if accent else panel, outline=sub)
        c.text((bx - bw / 2, by + (size + 20) / 2), label, f, (255, 255, 255) if accent else fg, anchor="mm")
        bx -= bw + 12
    return c


def make_game_dialog(lang: str, i: int, rng: np.random.Generator, rnd: random.Random, fonts: FontPick) -> Canvas:
    w, h = 1280, 720
    c = Canvas(colourful_background(rng, w, h))
    p = POOLS[lang]
    hud = rnd.choice(p["hud"])
    hf = fonts.get(22, bold=True)
    c.text((24, 20), hud[0], hf, (255, 255, 255), stroke=2, stroke_fill=(0, 0, 0))
    c.text((w - 24, 20), hud[1], hf, (255, 230, 120), stroke=2, stroke_fill=(0, 0, 0), anchor="ra")
    # semi-transparent dialog box
    box = (60, h - 250, w - 60, h - 30)
    over = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(over).rounded_rectangle(box, radius=14, fill=(10, 12, 28, rnd.choice([150, 175, 200])),
                                           outline=(220, 220, 255, 200), width=3)
    c.img = Image.alpha_composite(c.img.convert("RGBA"), over).convert("RGB")
    nf = fonts.get(26, bold=True)
    c.text((box[0] + 30, box[1] + 18), rnd.choice(p["speakers"]), nf, (255, 210, 90))
    size = rnd.choice([26, 28, 30])
    f = fonts.get(size)
    text = rnd.choice(p["dialog"])
    if rnd.random() < 0.5:
        text += (" " if lang == "en" else "") + rnd.choice(p["dialog"])
    y = box[1] + 18 + 26 + 18
    for line in wrap(text, f, box[2] - box[0] - 60, lang)[:4]:
        if y + size > box[3] - 8:
            break
        c.text((box[0] + 30, y), line, f, (250, 250, 250))
        y += int(size * 1.45)
    return c


def make_subtitle(lang: str, i: int, rng: np.random.Generator, rnd: random.Random, fonts: FontPick) -> Canvas:
    w, h = 1920, 200
    c = Canvas(photo_background(rng, w, h))
    p = POOLS[lang]
    size = rnd.choice([42, 46, 50])
    f = fonts.get(size, bold=rnd.random() < 0.5)
    n = 2 if i % 3 != 0 else 1
    texts = rnd.sample(p["subs"], n)
    lh = int(size * 1.3)
    y0 = h - 20 - n * lh
    for k, t in enumerate(texts):
        c.text((w / 2, y0 + k * lh), t, f, (255, 255, 255), stroke=3, stroke_fill=(0, 0, 0), anchor="ma")
    return c


def make_document(lang: str, i: int, rng: np.random.Generator, rnd: random.Random, fonts: FontPick) -> Canvas:
    w, h = 1920, 1080
    paper = rnd.choice([(255, 255, 255), (250, 248, 240), (245, 245, 245)])
    c = Canvas(Image.new("RGB", (w, h), paper))
    p = POOLS[lang]
    margin = 120
    size = rnd.choice([18, 20, 22])
    f = fonts.get(size)
    lh = int(size * 1.6)
    y = 70
    title = p["titles"][i % len(p["titles"])]
    c.text((margin, y), title, fonts.get(size + 14, bold=True), (20, 20, 20))
    y += size + 14 + 30
    sep = " " if lang == "en" else ""
    while y + lh < h - 60:
        para = sep.join(rnd.sample(p["doc"], rnd.randint(3, 5)))
        for line in wrap(para, f, w - 2 * margin, lang):
            if y + lh >= h - 60:
                break
            c.text((margin, y), line, f, (30, 30, 30))
            y += lh
        y += int(lh * 0.6)
    return c


def make_small_text(lang: str, i: int, rng: np.random.Generator, rnd: random.Random, fonts: FontPick) -> Canvas:
    w, h = 640, 400
    dark = i % 2 == 1
    bg, fg = ((24, 26, 30), (220, 222, 226)) if dark else ((250, 250, 250), (35, 35, 35))
    c = Canvas(Image.new("RGB", (w, h), bg))
    size = 11 + i % 3  # 11..13 px
    f = fonts.get(size)
    lh = size + rnd.choice([5, 6, 7])
    y = 12
    for t in rnd.sample(POOLS[lang]["small"], len(POOLS[lang]["small"])):
        if y + lh > h - 10:
            break
        c.text((12 + rnd.choice([0, 0, 12]), y), t, f, fg)
        y += lh
    return c


def _draw_vertical(c: Canvas, cx: float, top: float, text: str, font, fill) -> None:
    """Draw one vertical column, top-to-bottom, characters centred on cx."""
    d = c.draw
    size = font.size
    step = size * 1.08
    y = top
    for ch in text:
        if ch in "。、":  # vertical typesetting puts these in the upper-right of the cell
            d.text((cx - size * 0.5 + size * 0.6, y - size * 0.55), ch, font=font, fill=fill)
        else:
            d.text((cx - font.getlength(ch) / 2, y), ch, font=font, fill=fill)
        y += step
    l, t, r, b = int(cx - size / 2), int(top), int(cx + size / 2), int(top + step * len(text))
    c.lines.append(TextBox(text, (l, t, r - l, b - t)))


def make_vertical_ja(lang: str, i: int, rng: np.random.Generator, rnd: random.Random, fonts: FontPick) -> Canvas:
    # One wide panel (a manga tier): bubbles sit in separate x bands, so the right-to-left
    # column order is unambiguous for scoring.
    w, h = 960, 720
    c = Canvas(Image.new("RGB", (w, h), (255, 255, 255)))
    d = c.draw
    x0, y0, x1, y1 = 20, 20, w - 20, h - 20
    d.rectangle((x0, y0, x1, y1), outline=(0, 0, 0), width=4)
    for _ in range(60):  # sketchy strokes as "art"
        xa, ya = int(rng.integers(x0, x1)), int(rng.integers(y0, y1))
        xb, yb = xa + int(rng.integers(-120, 120)), ya + int(rng.integers(-120, 120))
        g = int(rng.integers(60, 200))
        d.line((xa, ya, xb, yb), fill=(g, g, g), width=int(rng.integers(1, 4)))
    size = rnd.choice([28, 32, 36])
    f = fonts.get(size, bold=rnd.random() < 0.3)
    step = size * 1.08
    bands = [(x1 - 300, x1 - 10), (x0 + (x1 - x0) // 2 - 150, x0 + (x1 - x0) // 2 + 150), (x0 + 10, x0 + 300)]
    for (bx0, bx1) in bands[: rnd.choice([2, 3, 3])]:
        ncol = rnd.choice([1, 2, 2, 3])
        cols = rnd.sample(JA_VERTICAL, ncol)
        col_h = max(len(t) for t in cols) * step
        bw, bh = ncol * size * 1.5 + 60, col_h + 70
        cx = (bx0 + bx1) / 2
        top = rnd.uniform(y0 + 20, max(y0 + 21, y1 - bh - 20))
        d.rounded_rectangle((cx - bw / 2, top, cx + bw / 2, top + bh), radius=int(min(bw, bh) / 2.5),
                            fill=(255, 255, 255), outline=(0, 0, 0), width=3)
        first = cx + (ncol - 1) * size * 0.75  # columns run right-to-left inside the bubble
        for k, t in enumerate(cols):
            _draw_vertical(c, first - k * size * 1.5, top + 35, t, f, (0, 0, 0))
    return c


MAKERS = {
    "plain_ui": make_plain_ui,
    "game_dialog": make_game_dialog,
    "subtitle": make_subtitle,
    "document": make_document,
    "small_text": make_small_text,
    "vertical_ja": make_vertical_ja,
}
LANGS_FOR = {case: (("ja",) if case == "vertical_ja" else ("en", "ja")) for case in CASES}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-case", type=int, default=8, help="images per case and language (default 8)")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--cases", default=",".join(CASES), help="comma-separated subset of " + ",".join(CASES))
    ap.add_argument("--quick", action="store_true",
                    help="2 images per case (smoke run); writes to synthetic_quick/ unless --out is given")
    ap.add_argument("--out", type=Path, help=f"output dir (default {OUT_DIR})")
    args = ap.parse_args()
    per_case = 2 if args.quick else args.per_case
    # A smoke run must not replace the full benchmark set that bench_ocr reads.
    args.out = args.out or (OUT_DIR.with_name("synthetic_quick") if args.quick else OUT_DIR)
    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    unknown = set(cases) - set(CASES)
    if unknown:
        ap.error(f"unknown cases: {sorted(unknown)}")

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    labels_path, meta_path = out / "labels.json", out / "meta.json"
    labels: dict[str, list[str]] = json.loads(labels_path.read_text("utf-8")) if labels_path.exists() else {}
    meta: dict[str, dict] = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}

    rows, t0 = [], time.perf_counter()
    for ci, case in enumerate(CASES):
        if case not in cases:
            continue
        case_dir = out / case
        if case_dir.exists():
            shutil.rmtree(case_dir)  # regenerate the whole case so labels and files stay in sync
        case_dir.mkdir(parents=True)
        for key in [k for k in labels if k.startswith(case + "/")]:
            labels.pop(key, None)
            meta.pop(key, None)
        for lang in LANGS_FOR[case]:
            made, skipped, nbytes, font_names = 0, "", 0, set()
            for i in range(per_case):
                fonts = pick_fonts(lang, i)
                if fonts is None:
                    skipped = f"no {lang} font found in {[str(d) for d in _FONT_DIRS]}"
                    break
                seed = args.seed + ci * 10_000 + (0 if lang == "en" else 5_000) + i
                canvas = MAKERS[case](lang, i, np.random.default_rng(seed), random.Random(seed), fonts)
                ordered = reading_order(canvas.lines, vertical=(case == "vertical_ja"))
                rel = f"{case}/{lang}_{i}.png"
                path = out / rel
                canvas.img.save(path, optimize=False)
                nbytes += path.stat().st_size
                labels[rel] = [b.text for b in ordered]
                meta[rel] = {
                    "case": case, "lang": lang, "size": list(canvas.img.size),
                    "font": fonts.regular.name, "bold_font": fonts.bold.name,
                    "lines": [{"text": b.text, "box": list(b.box)} for b in ordered],
                }
                font_names.add(fonts.regular.name)
                made += 1
            rows.append({"case": case, "lang": lang, "images": made, "MB": round(nbytes / 1e6, 2),
                         "fonts": ",".join(sorted(font_names)), "note": skipped})

    labels = dict(sorted(labels.items()))
    meta = dict(sorted(meta.items()))
    labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    elapsed = time.perf_counter() - t0
    print_table(rows, ["case", "lang", "images", "MB", "fonts", "note"])
    print(f"\n{sum(r['images'] for r in rows)} images in {elapsed:.1f}s -> {out}")
    res = save_results("make_ocr_samples_quick" if args.quick else "make_ocr_samples", {"seed": args.seed, "per_case": per_case, "out": str(out), "rows": rows})
    print(f"results: {res}")


if __name__ == "__main__":
    main()
