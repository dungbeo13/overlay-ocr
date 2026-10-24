"""Render hand-translated OverlayStates onto synthetic OCR samples so a human can eyeball the overlay.

Uses the exact painting code of OverlayWindow (app.overlay.render.paint_overlay). Each output PNG
shows the original image on top and the image with the overlay composited below it.

Run: python -m tools.preview_overlay [--out bench/results/preview] [--cases game_dialog_en,...]
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

from app.capture.base import Region
from app.config import PROJECT_ROOT, Config
from app.ocr.textnorm import join_lines, union_rects
from app.overlay.render import LayoutCache, paint_overlay, pick_fg
from app.pipeline.types import BGR, Block, OverlayState

SYNTHETIC = PROJECT_ROOT / "bench" / "data" / "ocr" / "synthetic"
DEFAULT_OUT = PROJECT_ROOT / "bench" / "results" / "preview"


@dataclass
class BlockSpec:
    lines: list[int]  # indices into meta.json "lines"
    translation: str | None
    stale: str | None = None  # drawn dimmed while `translation` is None
    vertical: bool = False


@dataclass
class Case:
    name: str
    image: str  # relative to bench/data/ocr/synthetic
    blocks: list[BlockSpec]
    crop: tuple[int, int, int, int] | None = None  # x, y, w, h of the region inside the image
    status: str = "running"
    pending: int = 0
    message: str = ""
    config: dict = field(default_factory=dict)


CASES = [
    Case("game_dialog_en", "game_dialog/en_0.png", [
        BlockSpec([0], "Nhiệm vụ: Tìm chìa khóa"),
        BlockSpec([2], "Elara"),
        BlockSpec([3], "Người gác hải đăng không còn ai nhìn thấy kể từ trận bão ba ngày trước."),
    ]),
    Case("game_dialog_ja", "game_dialog/ja_0.png", [
        BlockSpec([0], "Nhiệm vụ: Tìm chìa khóa"),
        BlockSpec([2], "Đội trưởng Mira"),
        BlockSpec([3], "Tháp phía bắc bị khóa, nhưng hình như chìa khóa đã bị đánh rơi gần cái giếng."),
    ]),
    Case("game_dialog_ja_2lines", "game_dialog/ja_3.png", [
        BlockSpec([1], "Ngày thứ tư"),
        BlockSpec([2], "Elara"),
        BlockSpec([3, 4], "Thanh kiếm của cậu bị mẻ rồi. Nếu tối nay để nó lại cho ta, ta sẽ mài lại như mới. "
                          "Ta đã đợi suốt bao năm một người đủ can đảm để mở cánh cửa đó."),
    ], pending=1),
    Case("subtitle_en", "subtitle/en_0.png", [
        BlockSpec([0], "Anh đã giữ lá thư ấy suốt ngần ấy năm sao?"),
    ]),
    Case("subtitle_ja", "subtitle/ja_0.png", [
        BlockSpec([0], "Nếu khởi hành ngay bây giờ, chúng ta sẽ kịp đến trước cơn bão."),
    ]),
    Case("document_en", "document/en_1.png", [
        BlockSpec([0], "Thư viện đa phương tiện"),
        BlockSpec([1, 2], "Các tệp được lưu ở định dạng cũ vẫn mở được, nhưng sẽ được chuyển đổi khi lưu. Các buổi "
                          "hướng dẫn lập ngân sách và viết sơ yếu lý lịch thường kín chỗ trước nhiều tuần. Chúng cho "
                          "cư dân một nơi để trồng rau, gặp gỡ hàng xóm và tìm hiểu về cây cối địa phương. Một số "
                          "cài đặt đã được gom về một trang duy nhất để dễ tìm hơn."),
        BlockSpec([3, 4], "Vườn cộng đồng đã trở thành cảnh quen thuộc ở nhiều thành phố trong thập kỷ qua. Một số "
                          "cài đặt đã được gom về một trang duy nhất để dễ tìm hơn. Thùng chứa nước mưa giúp giảm "
                          "chi phí tưới cây và giảm tải cho hệ thống cấp nước công cộng. Du khách nên đặt vé trực "
                          "tuyến để khỏi phải xếp hàng ở lối vào."),
        BlockSpec([5, 6], "Thùng chứa nước mưa giúp giảm chi phí tưới cây và giảm tải cho hệ thống cấp nước công "
                          "cộng. Các tệp được lưu ở định dạng cũ vẫn mở được, nhưng sẽ được chuyển đổi khi lưu. Vườn "
                          "cộng đồng đã trở thành cảnh quen thuộc ở nhiều thành phố trong thập kỷ qua. Du khách nên "
                          "đặt vé trực tuyến để khỏi phải xếp hàng ở lối vào."),
        # stale: the previous (shorter) translation is shown dimmed while the new one is pending
        BlockSpec([7, 8], None, stale="Một mảnh đất nhỏ có thể cho lượng lương thực đáng ngạc nhiên nếu được "
                                      "quy hoạch cẩn thận. Người dùng điều hướng bằng bàn phím sẽ thấy viền tiêu "
                                      "điểm rõ hơn trong mọi hộp thoại."),
        BlockSpec([9, 10], None),  # pending, nothing drawn: the original stays readable
    ], crop=(0, 40, 1920, 500), pending=2),
    Case("vertical_ja", "vertical_ja/ja_0.png", [
        BlockSpec([0, 1], "Vẫn chưa kết thúc đâu. Tin được không?", vertical=True),
        BlockSpec([2, 3], "Đợi thêm chút nữa thôi. Không ngờ lại đến thật.", vertical=True),
        BlockSpec([4, 5], "Tiếng gì vừa rồi vậy? Lời hứa ngày hôm ấy…", vertical=True),
    ]),
    Case("vertical_ja_1", "vertical_ja/ja_1.png", [
        BlockSpec([0, 1, 2], "Gió đã đổi chiều. Khi mưa tạnh, hãy đợi thêm chút nữa.", vertical=True),
        BlockSpec([3, 4, 5], "Đừng đi một mình. Cứ để chỗ này cho tôi. Đợi thêm chút nữa thôi.", vertical=True),
        BlockSpec([6, 7], "Tránh đường ra! Tôi chưa quên đâu.", vertical=True),
    ]),
    # Stress: a translation far longer than the box -> grows down within the region, then elides.
    Case("stress_grow", "game_dialog/en_0.png", [
        BlockSpec([0], "Nhiệm vụ chính: Tìm chiếc chìa khóa cũ đã bị thất lạc trong ngọn hải đăng bỏ hoang"),
        BlockSpec([3], "Người gác ngọn hải đăng ở mũi đá phía bắc đã không còn được ai nhìn thấy kể từ trận bão "
                       "khủng khiếp ba ngày trước; dân làng đồn rằng ông đã ra khơi tìm con thuyền mất tích, "
                       "nhưng không ai dám chắc điều gì."),
    ], crop=(0, 0, 1280, 620)),
    Case("stress_elide", "subtitle/en_3.png", [
        BlockSpec([0], "Chuyến xe buýt cuối cùng đã rời bến từ lâu rồi, vì vậy tối nay chúng ta sẽ phải đi bộ về "
                       "nhà qua con đường tối tăm dọc bờ sông, băng qua cây cầu cũ, ngang qua khu chợ đêm đã đóng "
                       "cửa và cả dãy phố vắng tanh không một bóng người, mãi đến tận nửa đêm mới tới nơi. "
                       "Nếu trời đổ mưa thì chúng ta sẽ trú tạm dưới mái hiên của tiệm sách cũ ở góc đường, "
                       "đợi cho mưa ngớt rồi mới đi tiếp, vì đôi giày này đã ướt sũng từ chiều nay rồi. "
                       "Ngày mai nhất định phải dậy thật sớm để kịp chuyến tàu đầu tiên ra ga trung tâm."),
    ], crop=(300, 115, 1320, 75), message="Lỗi dịch: hết thời gian chờ", config={"min_font_px": 16}),
]


def load_meta() -> dict:
    import json
    return json.loads((SYNTHETIC / "meta.json").read_text(encoding="utf-8"))


def sample_bg(image: np.ndarray, rects: list[tuple[int, int, int, int]], gap: int = 3, ring: int = 3) -> BGR:
    """Median colour of a thin ring of pixels just outside each line box (same idea as the pipeline)."""
    h, w = image.shape[:2]
    mask = np.zeros((h, w), dtype=bool)
    for x, y, bw, bh in rects:
        x0, y0 = max(0, x - gap - ring), max(0, y - gap - ring)
        x1, y1 = min(w, x + bw + gap + ring), min(h, y + bh + gap + ring)
        mask[y0:y1, x0:x1] = True
        ix0, iy0 = max(0, x - gap), max(0, y - gap)
        ix1, iy1 = min(w, x + bw + gap), min(h, y + bh + gap)
        mask[iy0:iy1, ix0:ix1] = False
    pixels = image[mask]
    if len(pixels) == 0:
        return (0, 0, 0)
    med = np.median(pixels, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def build_state(case: Case, meta: dict, image: np.ndarray, region: Region) -> OverlayState:
    info = meta[case.image]
    lang = info["lang"]
    blocks = []
    for i, spec in enumerate(case.blocks, start=1):
        lines = [info["lines"][k] for k in spec.lines]
        rects = [tuple(ln["box"]) for ln in lines]
        rel = [(x - region.left, y - region.top, w, h) for x, y, w, h in rects]
        bg = sample_bg(image, rects)
        sizes = [r[2] for r in rects] if spec.vertical else [r[3] for r in rects]
        blocks.append(Block(
            id=i, box=union_rects(rel), lines=tuple(rel),
            source=join_lines([ln["text"] for ln in lines], lang), vertical=spec.vertical,
            bg=bg, fg=pick_fg(bg), line_height=int(np.median(sizes)),
            translation=spec.translation, stale_translation=spec.stale if spec.translation is None else None,
        ))
    return OverlayState(seq=1, region=region, blocks=tuple(blocks), status=case.status, message=case.message,
                        src_lang=lang, tgt_lang="vi", pending=case.pending, timestamp=time.time())


def to_qimage(bgr: np.ndarray) -> QImage:
    h, w = bgr.shape[:2]
    rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def render_case(case: Case, meta: dict, out_dir: Path) -> Path:
    full = cv2.imread(str(SYNTHETIC / case.image), cv2.IMREAD_COLOR)
    if full is None:
        raise FileNotFoundError(SYNTHETIC / case.image)
    x, y, w, h = case.crop or (0, 0, full.shape[1], full.shape[0])
    region = Region(x, y, w, h)
    state = build_state(case, meta, full, region)
    crop = full[y:y + h, x:x + w]
    config = Config(**case.config)

    base = to_qimage(crop)
    # Like the real window: paint into a transparent layer (grayscale text AA), then composite.
    layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(QColor(0, 0, 0, 0))
    cache = LayoutCache()
    p = QPainter(layer)
    paint_overlay(p, state, config, (w, h), cache=cache)
    p.end()
    composed = base.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(composed)
    p.drawImage(0, 0, layer)
    p.end()

    sep = 6
    sheet = QImage(w, 2 * h + sep, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor(255, 0, 255))
    p = QPainter(sheet)
    p.drawImage(0, 0, base)
    p.drawImage(0, h + sep, composed)
    p.end()
    out = out_dir / f"{case.name}.png"
    sheet.save(str(out))

    for b in state.blocks:
        lay = cache.get(b, config, (w, h))
        if lay is None:
            print(f"  block {b.id}: no translation drawn (pending)")
            continue
        f = lay.fit
        print(f"  block {b.id}: font {f.font_px}px (max {max(config.min_font_px, int(0.9 * b.line_height))}), "
              f"{len(f.lines)} line(s), box {b.box} -> rect {QRect(f.rect.toAlignedRect()).getRect()}"
              f"{' GROWN' if f.grown else ''}{' ELIDED' if f.elided else ''}{' (stale)' if lay.dimmed else ''}"
              f" bg={b.bg} fg={b.fg}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cases", default="", help="comma-separated case names (default: all)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841 (fonts need it)
    args.out.mkdir(parents=True, exist_ok=True)
    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    meta = load_meta()
    for case in CASES:
        if wanted and case.name not in wanted:
            continue
        print(case.name)
        path = render_case(case, meta, args.out)
        print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
