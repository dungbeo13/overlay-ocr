# Phát triển

Trang này dành cho người muốn đọc hay sửa mã nguồn, chạy kiểm thử hoặc tự đo lại trên máy mình: cấu trúc thư mục, cách chạy test, các bài đo và những phần đo phải làm bằng tay. Hướng dẫn dùng app nằm ở [README](../README.md); các số đo đã có nằm ở [ket-qua-do.md](ket-qua-do.md).

Mọi lệnh `& $py …` dưới đây chạy từ thư mục app, với `$py` là Python trong venv như ở mục [*Mỗi lần mở cửa sổ PowerShell mới*](../README.md#mỗi-lần-mở-cửa-sổ-powershell-mới) của README:

```powershell
$py = "$env:USERPROFILE\.venvs\screen-translator\Scripts\python.exe"
```

Số phiên bản chỉ nằm ở một chỗ: `__version__` trong `app/__init__.py`. `& $py -m app --version` in ra số đó. Thay đổi của mỗi phiên bản ghi trong [CHANGELOG.md](../CHANGELOG.md).

## Cấu trúc

| Đường dẫn | Nội dung |
|---|---|
| `app/capture/` | Backend chụp màn hình: dxcam (DXGI), Windows Graphics Capture (theo màn hình / theo cửa sổ), mss (GDI) |
| `app/overlay/affinity.py` | Ẩn overlay khỏi capture (`WDA_EXCLUDEFROMCAPTURE`), tự áp lại khi HWND đổi |
| `app/ocr/` | RapidOCR PP-OCRv6 (CPU/CUDA), Windows OCR |
| `app/translate/` | Google gtx (miễn phí), Azure, Gemini, tầng LLM local, bộ kiểm tra xưng hô, cache RAM + SQLite |
| `app/llm/` | Chạy và tắt llama-server, chọn model |
| `app/lookup/` | Tra cứu dữ liệu game mới (Steam, Wikidata, VNDB, wiki, SearXNG) → hồ sơ nháp |
| `app/profiles.py` | Hồ sơ theo game: thuật ngữ, nhân vật, văn phong, tên exe |
| `app/address_notes.py` | Ghi chú xưng hô của nhân vật ↔ các dòng của khung *Xưng hô* (thẻ *Nhân vật*) |
| `app/single_instance.py` | Chỉ chạy một bản: khóa theo thư mục dữ liệu, lần mở thứ hai chuyển sang bản đang chạy |
| `app/qt_gc.py` | Bộ dọn rác của Python chỉ chạy trên luồng giao diện, để luồng phụ (OCR, dịch, tra cứu) không hủy đối tượng Qt của cửa sổ đã đóng (trước đây làm app crash, khoảng 1/12 lần chạy test tra cứu) |
| `app/winutil.py` | DPI awareness, tắt EcoQoS (`set_power_throttling`) |
| `app/pipeline/` | Phát hiện thay đổi, gom dòng thành đoạn, theo dõi block, luồng OCR và luồng dịch → `OverlayState` |
| `app/overlay/` | Cửa sổ overlay, vẽ bản dịch (`render.py`), quy đổi tọa độ vật lý ↔ Qt, chọn vùng |
| `app/ui/` | Phím tắt toàn cục, icon khay hệ thống, cửa sổ Cài đặt / hồ sơ / tra cứu, bảo vệ anti-cheat (`game_guard.py`) |
| `app/main.py` | Nối mọi thành phần, CLI, log, debug dump |
| `app/__init__.py` | Số phiên bản (`__version__`): nguồn duy nhất cho `--version`, khay, *Cài đặt*, log và User-Agent |
| `assets/` | Icon của shortcut (`app.ico`) |
| `profiles/templates/` | Hồ sơ mẫu có sẵn (Blue Archive) |
| `deploy/searxng/` | `settings.yml` của SearXNG và bản Docker (tùy chọn) |
| `bench/` | Các script đo đạc; kết quả ghi vào `bench/results/` (không có trong repo) |
| `bench/data/` | Dữ liệu đo: ảnh OCR tổng hợp, câu mẫu, cảnh thoại và các bộ câu xưng hô có nhãn |
| `tools/` | Cài LLM / SearXNG, tạo shortcut, tra cứu game, cửa sổ demo, kiểm thử end-to-end, xem trước overlay |
| `tests/` | Unit test; `tests/data/lookup/` là câu trả lời API đã thu gọn (xem [*Dữ liệu bên thứ ba trong repo*](../README.md#dữ-liệu-bên-thứ-ba-trong-repo)) |
| `research_notes/`, `reports/` | Ghi chú nghiên cứu và báo cáo về cách dịch game Nhật sang tiếng Việt |
| `docs/` | Tài liệu: hướng dẫn chi tiết, tra cứu game, phát triển (trang này), kết quả đo; `docs/images/` là ảnh minh họa của README |
| `data/` | Dữ liệu riêng của bạn (cài đặt, hồ sơ, cache, log); app tự tạo, không có trong repo |
| `run_app.pyw`, `run.ps1`, `Tạo lại shortcut.cmd` | Mở app không có cửa sổ dòng lệnh (shortcut chạy tệp này), mở app có cửa sổ dòng lệnh, tạo lại shortcut |
| `requirements.txt`, `requirements.lock.txt` | Thư viện Python cần cài; đúng phiên bản đã kiểm chứng |
| `README.md`, `CHANGELOG.md` | Hướng dẫn dùng app; nhật ký thay đổi theo phiên bản |
| `LICENSE` | Giấy phép MIT của mã nguồn dự án |

## Kiểm thử

Unit test dùng `unittest` của thư viện chuẩn. Các phần cần nền Windows thật của Qt (đọc lại cờ ẩn khỏi ảnh chụp trên cửa sổ thật, vẽ ảnh xem trước bằng `QWidget.grab`, mở `run_app.pyw` thật) chạy trong một process con; đặt `SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1` để bỏ qua các process con đó.

- Kiểm thử:
  - `& $py -m unittest discover -s tests -t .` (719 test, đếm ngày 2026-10-25, bản 1.0.0).
  - End-to-end: `& $py -m tools.e2e_check --scenes ja|mixed`. Lệnh này mở một cửa sổ demo khoảng 50 giây và gọi Google thật. Kết quả được ghi vào `bench/results/e2e_mvp.json`.
  - Có thêm `--llm` để kiểm tra tầng LLM.

### Cài đặt, hướng dẫn lần đầu và trình mở app

Kiểm thử: `& $py -m unittest tests.test_settings_ui -v`. Phần chạy trên nền Windows thật có một lần mở `run_app.pyw` bằng `pythonw.exe` với thư mục dữ liệu tạm: kiểm tra không có console, có icon khay, lần mở thứ hai chuyển sang bản đang chạy, thoát sạch. Ảnh xem trước từng tab và từng bước nằm ở `bench/results/preview/settings_*.png` và `first_run_*.png`.

### Tra cứu game

- Kiểm thử: `& $py -m unittest tests.test_lookup -v`. Không dùng mạng: dữ liệu là câu trả lời thật đã thu gọn trong `tests/data/lookup/` (Steins;Gate, Doki Doki Literature Club, kết quả SearXNG cho Persona 5 Royal), ghi lại bằng `--record`. Blue Archive phải ra hồ sơ mẫu mà không gửi yêu cầu nào. Các tệp đó là dữ liệu bên thứ ba với giấy phép riêng (trích wiki Fandom theo CC BY-SA 3.0, VNDB theo ODbL…), xem mục [*Dữ liệu bên thứ ba trong repo*](../README.md#dữ-liệu-bên-thứ-ba-trong-repo) và [`tests/data/lookup/NOTICE.md`](../tests/data/lookup/NOTICE.md).

Kiểm thử: `& $py -m unittest tests.test_lookup_ui -v`, không dùng mạng. Phần chạy trên nền Windows thật kiểm tra mọi cửa sổ và cửa sổ con đều được ẩn khỏi ảnh chụp, rồi vẽ ảnh xem trước vào `bench/results/preview/`: `lookup_consent.png`, `lookup_running.png`, `lookup_result.png`, `lookup_unsure.png`, `lookup_template.png`, `lookup_empty.png`, `lookup_request.png`, `lookup_import.png`, `lookup_file_dialog.png`, `searxng_install.png`, `settings_lookup.png`. Ảnh xem trước không chứa tên người dùng (đường dẫn hiện là `%USERPROFILE%`, hộp chọn tệp mở ở `C:\Windows\Web`).

### SearXNG

Kiểm thử: `& $py -m unittest tests.test_searxng -v`. Không cần mạng: test dùng server giả và các câu trả lời thật đã thu gọn trong `tests/data/lookup/searxng_*.json`.

### Phiên bản

Kiểm thử: `& $py -m unittest tests.test_version -v`. Kiểm tra số phiên bản chỉ nằm ở `app/__init__.py`; `--version` qua `python -m app`, `run.ps1` và `run_app.pyw` (bản không có cửa sổ dòng lệnh hiện hộp thoại, không crash); phiên bản ở khay, ở tab *Chung* của *Cài đặt* và trong User-Agent (tra cứu, SearXNG, `tools.setup_llm`); `CHANGELOG.md` có mục cho phiên bản đó; mọi chỗ README và `docs/` ghi số phiên bản (*Phiên bản …*, *Screen Translator …*) đều đúng số trong `app/__init__.py`. Dòng log lúc khởi động (*Screen Translator … starting*) được kiểm tra trong lần mở `run_app.pyw` thật của `tests.test_settings_ui`.

Đổi phiên bản: sửa `__version__` trong `app/__init__.py`, thêm mục mới trong `CHANGELOG.md`, rồi sửa các chỗ README và `docs/` ghi số phiên bản (test báo chỗ nào còn số cũ).

## Đo chất lượng tầng LLM

Kết quả các lần đo nằm ở [ket-qua-do.md](ket-qua-do.md#kết-quả-tầng-llm-2026-10-24).

- **Đo chất lượng:**
  - `& $py -m bench.eval_llm` chạy các cảnh thoại có nhân vật và glossary. Kết quả nằm trong `bench/results/eval_llm.csv`, với các cột `*_score` để bạn tự chấm 1–5. Thêm `--role-repair 0 --role-reask 0` để chạy lại cách cũ (không sửa vai) mà so sánh.
  - `& $py -m tools.e2e_check --llm` là bài e2e với LLM thật.
  - Xưng hô theo vai, trên các bộ câu có nhãn (`bench/data/translate/heldout/` hoặc `--dev`):
    - `& $py -m bench.eval_roles_heldout` đo bộ kiểm tra (không cần model); câu có `ref` thì đo thêm "trọng tài" (bộ kiểm tra có câu tham chiếu).
    - `& $py -m bench.eval_roles_reask` lấy bản dịch có nhãn làm câu trả lời đầu của model rồi chạy đúng đường hỏi lại của app với model thật: sửa được / không đổi / tệ hơn / bị chặn.
    - `& $py -m bench.eval_roles_e2e` dịch lại câu gốc từ đầu bằng model thật, một lần tắt và một lần bật phần sửa vai, rồi so số câu sai vai.
    - Cả hai lệnh sau nhận `--models quality,light`; với dữ liệu dev chưa có `ref`, thêm `--derive-refs` (câu tham chiếu suy ra từ nhãn). `bench.eval_roles_e2e` nhận thêm `--scenes all` để đo **thêm** các câu cảnh bench có `ref` (cùng với bộ câu có nhãn), hoặc `--scenes-only` để chỉ đo các câu cảnh.
    - File thiếu `src_lang` hoặc ghi `auto`: câu có kana / kanji được coi là tiếng Nhật, còn lại là tiếng Anh, như app (app luôn có ja hoặc en).
    - "Trọng tài" (bộ kiểm tra có câu tham chiếu) không tính những kiểm tra chỉ dựa vào câu gốc mà báo nhầm ngay trên chính câu tham chiếu của câu đó. Cả ba lệnh còn in thêm số chấm **chỉ bằng câu tham chiếu** (`ref_only`, không dùng kiểm tra nào của app), là con số độc lập với chính bộ kiểm tra quyết định việc hỏi lại.
    - Cả ba lệnh in thêm dòng `favour`: các câu "ai làm ơn cho ai" (〜てくれる / 〜てもらう / 〜てあげる, "for me / for you", "thanks for …ing"), nhận ra bằng một danh sách tín hiệu riêng của bench, không dùng bộ kiểm tra. Các câu này vẫn nằm trong nhóm của chúng (ask / greet / pron / other), nên số của bốn nhóm vẫn so được với các lần trước.
    - Kết quả nằm trong `bench/results/eval_roles_heldout.json`, `eval_roles_reask.json` và `eval_roles_e2e.json`. Khi chạy với `--dev` thì tên file có thêm `_dev`; `--scenes` trên bộ held-out ghi vào `eval_roles_e2e_scenes.json`.

## Các bài đo tự động

Mọi số đo trong README và [ket-qua-do.md](ket-qua-do.md) (Phase 0, Phase 1, tầng LLM, xưng hô, tra cứu…) là đo trên máy của tác giả (RTX 4080 SUPER 16 GB, i7-13700KF, màn 2560×1440, Windows 11). Kết quả chi tiết được ghi vào `bench/results/`; thư mục này nằm trong `.gitignore` nên không có trong repo. Muốn có số của máy bạn thì chạy lại các lệnh dưới đây, các lệnh `bench.eval_*` ở mục [*Đo chất lượng tầng LLM*](#đo-chất-lượng-tầng-llm) và `tools.e2e_check`.

| Lệnh | Đo gì |
|---|---|
| `& $py -m bench.probe_affinity` | Overlay có thật sự vô hình với dxcam / WGC / mss không (15 kịch bản) |
| `& $py -m bench.bench_capture --cv2-threads 1` | Độ trễ chụp và CPU của từng backend |
| `& $py -m bench.make_ocr_samples` | Sinh 88 ảnh test tổng hợp có đáp án (UI, hộp thoại game, phụ đề, tài liệu, chữ nhỏ, chữ dọc JA) |
| `& $py -m bench.bench_ocr` | Độ trễ p50/p95 và tỉ lệ lỗi ký tự (CER) của từng cấu hình OCR |
| `& $py -m bench.eval_translate` | Dịch bộ câu mẫu bằng mọi engine đang có key, xuất CSV để chấm điểm |
| `& $py -m bench.bench_gtx_soak --duration 1800` | Gọi Google miễn phí liên tục 30 phút, xem có bị chặn không |

Thêm `--quick` để chạy thử nhanh (<1 phút).

## Phần bạn cần tự làm

### 1. Ảnh chụp thật
Đặt ảnh chụp màn hình thật vào `bench/data/ocr/real/<game|subtitle|manga|web>/`. Thư mục này nằm trong `.gitignore`: ảnh chụp game hay manga thuộc bản quyền của chủ sở hữu, không đưa lên repo.
- Đặt tên bắt đầu bằng `ja_` hoặc `en_` (ví dụ `ja_dialog1.png`) để chọn đúng ngôn ngữ.
- Tùy chọn: thêm file `.txt` cùng tên, ghi chữ đúng từng dòng, để tính độ chính xác.

```powershell
& $py -m bench.bench_ocr --cases real/game,real/subtitle --configs "rapid-small-*,winocr"
```

### 2. Chấm điểm bản dịch
Mở `bench/results/eval_translate.csv` (do `bench.eval_translate` ghi ra) bằng Excel hoặc Google Sheets.
- Điền cột `<engine>_score` từ 1 (sai/khó hiểu) đến 5 (tự nhiên, đúng xưng hô).
- Muốn thử câu từ game hoặc manga bạn hay đọc: chép `bench/data/translate/eval_segments.csv` (bộ câu mẫu) sang `bench/data/translate/local/segments.csv`, thêm câu vào đó rồi chạy `& $py -m bench.eval_translate --segments bench\data\translate\local\segments.csv`. Thư mục `local/` nằm trong `.gitignore`, để thoại của game không bị đưa lên repo.
- Hiện chưa có đánh giá công khai nào cho Nhật→Việt, nên điểm bạn chấm là thước đo chính.

### 3. Ảnh hưởng tới FPS game
1. Mở game ở chế độ **Borderless/Windowed** và bật bộ đếm FPS (NVIDIA overlay `Alt+R`, hoặc PresentMon).
2. Trong lúc chơi, chạy lần lượt từng lệnh dưới đây và so FPS với lúc không chạy gì:

```powershell
& $py -m bench.load_ocr --config rapid-small-small-cpu-t8 --region 0,1040,2560,400 --interval 0.15 --duration 60
& $py -m bench.load_ocr --config rapid-small-small-cuda --region 0,1040,2560,400 --interval 0.15 --duration 60
```

Trong hai lệnh trên, `--region` là vùng màn hình cần OCR (left,top,width,height, đơn vị pixel vật lý).

### 4. (Tùy chọn) Key cho engine dịch tốt hơn
Đặt biến môi trường rồi chạy lại `bench.eval_translate`:
- `AZURE_TRANSLATOR_KEY` và `AZURE_TRANSLATOR_REGION`: Azure Translator gói F0 miễn phí, 2 triệu ký tự/tháng.
- `GEMINI_API_KEY`: Gemini Flash-Lite, dịch có ngữ cảnh, giữ đúng xưng hô. Đổi model bằng `GEMINI_MODEL`.
