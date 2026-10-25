# Screen Translator

Ứng dụng overlay dịch màn hình thời gian thực (Anh/Nhật → Việt).
- Phase 1 (MVP chế độ Vùng) đã chạy được.
- Có thêm tầng **dịch bằng LLM local**: giữ tên riêng theo glossary và chọn xưng hô theo nhân vật.
- Cài từ đầu: xem mục *Cài đặt từ đầu* ngay dưới đây. Phần Phase 0 ở cuối là các bài **đo trên máy thật** (máy của tác giả) để chốt engine.

## Cài đặt từ đầu

**Cần có:**

- Windows 11 24H2 trở lên.
- Python 3.12 bản 64-bit, có trình chạy `py` (bộ cài ở python.org có sẵn).
- GPU NVIDIA: không bắt buộc cho OCR, nhưng bắt buộc nếu muốn dịch bằng LLM trên máy.
  - OCR trên GPU dùng CUDA 13 nên cần driver NVIDIA r580 trở lên. Máy không có GPU NVIDIA hoặc driver cũ hơn thì app tự chạy OCR bằng CPU (chậm hơn nhiều).
  - Script cài LLM tự chọn bản CUDA 13.4 hoặc 12.4 theo driver.

**1. Lấy mã nguồn** về một đường dẫn ngắn (shortcut không nhận đường dẫn quá 260 ký tự):

```powershell
cd $env:USERPROFILE
git clone https://github.com/dungbeo13/overlay-ocr
cd overlay-ocr
```

Không có git thì tải ZIP trên GitHub (*Code → Download ZIP*) rồi giải nén vào chỗ tương tự.

**2. Tạo venv và cài thư viện.** venv phải nằm ở đường dẫn ngắn: OpenCV và các DLL CUDA/cuDNN sẽ không load được khi đường dẫn vượt 260 ký tự.

```powershell
py -3.12 -m venv $env:USERPROFILE\.venvs\screen-translator
$py = "$env:USERPROFILE\.venvs\screen-translator\Scripts\python.exe"
& $py -m pip install -r requirements.txt
```

pip tải khoảng 1,5–2 GB, phần lớn là thư viện CUDA/cuDNN cho OCR; venv chiếm khoảng 2,5 GB. `requirements.lock.txt` ghi lại đúng phiên bản đã kiểm chứng. Mọi lệnh `& $py …` trong README đều chạy từ thư mục gốc dự án, với `$py` trỏ tới Python trong venv như trên (mở cửa sổ PowerShell mới thì đặt lại `$py`).

**3. (Tùy chọn) LLM dịch trên máy** (llama.cpp và model Hy-MT2, nằm trong `%USERPROFILE%\.screen-translator`):

```powershell
& $py -m tools.setup_llm --models light   # chỉ bản 1.8B: tải khoảng 2 GB
& $py -m tools.setup_llm                  # cả bản 1.8B và 7B: tải khoảng 6,7 GB
```

Không cài thì app dịch bằng Google (cần mạng). Có thể cài sau: hướng dẫn lần đầu và *Cài đặt* → *Dịch* đều hiện lệnh này. Chi tiết ở mục *Dịch bằng LLM local*.

**4. (Tùy chọn) SearXNG trên máy**, để phần tra cứu game mới tìm được trang Việt hóa và wiki trên web: `& $py -m tools.setup_searxng` (xem mục *SearXNG trên máy*).

**5. Tạo shortcut hoặc chạy thẳng:**

```powershell
& $py -m tools.install_shortcut   # shortcut Desktop + Start Menu (hoặc bấm đúp "Tạo lại shortcut.cmd")
& $py -m app                       # hoặc chạy thẳng, có cửa sổ dòng lệnh
```

- Lần mở đầu tiên, RapidOCR tự tải các model OCR, nên cần mạng.
- `.\run.ps1` (mục dưới) là script PowerShell. Windows mặc định không cho chạy script PowerShell (.ps1); nếu PowerShell báo *running scripts is disabled on this system* thì chạy `powershell -ExecutionPolicy Bypass -File .\run.ps1` (tham số thêm phía sau như thường), hoặc dùng `& $py -m app`.
- llama.cpp, model, SearXNG và model OCR đều do bạn tải từ nguồn gốc khi cài; repo không phân phối lại thứ nào (xem mục *Dữ liệu bên thứ ba trong repo*).

## Chạy ứng dụng (MVP)

```powershell
.\run.ps1                                      # dịch vùng đã lưu; lần đầu nhấn Ctrl+Alt+R để chọn vùng
.\run.ps1 --lang en --region 0,1040,2560,400   # tham số dòng lệnh chỉ áp dụng cho lần chạy này
.\run.ps1 --help
```

| Phím tắt | Việc |
|---|---|
| Ctrl+Alt+R | Chọn vùng cần dịch (kéo chuột, Esc để huỷ). Vùng được lưu lại |
| Ctrl+Alt+T | Tạm dừng / tiếp tục |
| Ctrl+Alt+H | Ẩn / hiện bản dịch (để xem chữ gốc) |
| Ctrl+Alt+L | Đổi ngôn ngữ nguồn Nhật ↔ Anh |
| Ctrl+Alt+Q | Thoát |

Menu ở khay hệ thống có đủ các lệnh trên, cộng thêm **Chế độ anti-cheat** và **Mở thư mục dữ liệu**.

- **Game có anti-cheat:** app không inject và không đọc bộ nhớ process nào. Mỗi giây app chỉ xem tên file exe của cửa sổ đang active. Nếu exe nằm trong `blocked_processes` (file `data/config.json`), app xử lý theo chế độ:
  - *Tạm dừng* (mặc định): ngừng chụp màn hình, xoá overlay, tự chạy lại khi bạn rời game.
  - *Chỉ cảnh báo*: báo một lần, vẫn tiếp tục dịch.
  - *Tắt*: không kiểm tra gì.

  Muốn thêm game của bạn thì thêm tên exe vào danh sách đó.
- Ở chế độ tiếng Nhật, đoạn chữ chỉ có ký tự Latin (menu, phụ đề tiếng Anh) sẽ được dịch như tiếng Anh.
- Dữ liệu nằm trong `data/`:
  - `config.json`: cài đặt.
  - `cache.sqlite3`: cache bản dịch.
  - `logs/app.log`: log.
- Chụp màn hình dùng dxcam. Khi dxcam không trả về frame nào, ví dụ lúc màn hình đang tắt, app chuyển sang GDI (mss). Overlay vẫn vô hình với cả hai cách.
- Kiểm thử:
  - `& $py -m unittest discover -s tests -t .` (703 test, đếm ngày 2026-10-25).
  - End-to-end: `& $py -m tools.e2e_check --scenes ja|mixed`. Lệnh này mở một cửa sổ demo khoảng 50 giây và gọi Google thật. Kết quả được ghi vào `bench/results/e2e_mvp.json`.
  - Có thêm `--llm` để kiểm tra tầng LLM.

## Mở app không cần terminal, Cài đặt và hướng dẫn lần đầu

**Shortcut** (tên *Screen Translator*, ngoài Desktop và trong Start Menu; tạo bằng lệnh đầu tiên dưới đây; mặc định không khởi động cùng Windows):

```powershell
& $py -m tools.install_shortcut             # tạo / cập nhật shortcut Desktop + Start Menu
& $py -m tools.install_shortcut --startup   # thêm: khởi động cùng Windows (thư mục Startup)
& $py -m tools.install_shortcut --remove    # gỡ (thêm --startup để gỡ cả shortcut Startup)
& $py -m tools.install_shortcut --check     # xem các shortcut đang trỏ tới đâu
```

- Shortcut chạy `pythonw.exe run_app.pyw` của venv: không có cửa sổ dòng lệnh, app chỉ hiện biểu tượng 文 ở khay hệ thống (lần mở nào cũng có một thông báo nhỏ *Screen Translator đang chạy*). Icon `assets/app.ico` (có sẵn trong repo) giống icon khay; lệnh chỉ vẽ lại file này khi nó chưa có, hoặc khi thêm `--redraw-icon`. Các cửa sổ của app hiện trên thanh tác vụ với tên *Screen Translator* và icon 文 (không phải icon Python).
- **Shortcut trỏ tới đúng thư mục dự án hiện tại** (`run_app.pyw` trong thư mục này) và tới Python của venv đã dùng để chạy lệnh tạo shortcut. `Tạo lại shortcut.cmd` dùng venv `%USERPROFILE%\.venvs\screen-translator`, hoặc Python ở biến môi trường `SCREEN_TRANSLATOR_PYTHON` nếu có đặt (như `run.ps1`). Nếu chuyển thư mục dự án sang chỗ khác thì **bấm đúp `Tạo lại shortcut.cmd`** trong thư mục mới: shortcut Desktop, Start Menu (và Startup, nếu đang bật *Khởi động cùng Windows*) sẽ trỏ sang chỗ mới. Tạo lại venv thì cũng chạy tệp đó (hoặc lệnh đầu tiên ở trên). Chạy lại lúc nào cũng được: shortcut đã đúng thì không đổi gì.
- Mở app lần nữa khi nó đang chạy thì không có bản thứ hai: app đang chạy báo *App đang chạy ở khay hệ thống* và mở menu khay. Nếu bản đầu còn đang khởi động (máy vừa bật, ổ đĩa chậm), lần mở thứ hai chờ nó tới 45 giây rồi mới chuyển sang; chỉ khi app thật sự bị treo mới có thông báo *đang chạy nhưng không phản hồi*. Mỗi thư mục dữ liệu chỉ chạy được một bản.
- Bấm (chuột trái hoặc phải) vào biểu tượng 文 để mở menu; bấm đúp để chọn vùng dịch.
- Lỗi lúc khởi động (thiếu thư viện, shortcut có tham số sai…) hiện trong hộp thoại tiếng Việt, có nút *Sao chép chi tiết* và *Mở thư mục nhật ký*, và được ghi vào `data/logs/app.log`. **Các cửa sổ của app không hiện trong ảnh chụp màn hình** (để app không tự dịch lại chữ của mình): muốn hỏi ai về một lỗi thì bấm *Sao chép chi tiết* rồi dán. Lỗi xảy ra trước khi app kịp mở log nằm ở `data/logs/startup-error.log`; nếu app bị crash, stack nằm ở `data/logs/crash.log` (của lần chạy trước: `crash.log.1`).
- `.\run.ps1` và `python -m app` vẫn chạy như cũ (có terminal).

**Cài đặt** (khay → *Cài đặt…*): cửa sổ riêng, app vẫn dịch trong lúc mở; nút *Lưu*, *Hủy*, *Mặc định*. *Mặc định* hỏi lại trước, chỉ điền lại các ô (bấm *Lưu* mới áp dụng) và không đổi *Khởi động cùng Windows*.

| Tab | Gồm |
|---|---|
| Chung | Ngôn ngữ nguồn, *Khởi động cùng Windows*, *Hướng dẫn nhanh…* |
| Dịch | Chế độ LLM, mô hình, số câu thoại gần đây gửi kèm, tự thêm thuật ngữ LLM đề xuất, tắt LLM để giải phóng VRAM khi đang chơi game có anti-cheat. Khi chưa cài LLM: lệnh cài kèm nút *Sao chép lệnh* |
| Hiển thị | Phông chữ, cỡ chữ nhỏ nhất, độ che chữ gốc (0 % = trong suốt, 100 % = che kín), khung viền vùng dịch, kèm ô xem trước vẽ bản dịch lên một khung thoại giả đúng như overlay vẽ |
| Phím tắt | Bấm vào ô rồi nhấn tổ hợp mới (Esc: hủy, Backspace hoặc nút *Xóa phím*: bỏ phím). Báo phím trùng nhau, phím Windows dùng (Alt+F4, Win+L…) và phím thiếu Ctrl / Alt / Win |
| Anti-cheat | Chế độ và danh sách game: thêm từ cửa sổ đang mở, gõ tên tệp .exe, xóa, khôi phục danh sách mặc định |
| Nâng cao | Chu kỳ chụp, thiết bị OCR, cách chụp màn hình, mở thư mục dữ liệu |

- Áp dụng ngay khi bấm *Lưu*: phím tắt (đăng ký lại; phím đang bị app khác giữ thì phím cũ vẫn dùng và app báo lỗi, không bao giờ lưu một phím cho hai việc), hiển thị, LLM, anti-cheat, chu kỳ chụp, ngôn ngữ. Đổi thiết bị OCR hoặc cách chụp thì cửa sổ báo *Cần khởi động lại app để áp dụng* và hiện nút *Khởi động lại app*; đổi lại như cũ rồi *Lưu* thì thông báo và nút biến mất. *Khởi động lại app* dùng đúng cài đặt đã lưu (bỏ các tham số chỉ cho một lần chạy như `--device`, `--capture`, `--lang`), và hỏi trước nếu cửa sổ *Thuật ngữ & nhân vật* còn thay đổi chưa lưu.
- Cửa sổ có thể để mở trong lúc chơi: đổi ngôn ngữ bằng phím tắt, đổi chế độ LLM / anti-cheat ở khay hay hồ sơ tự chuyển thì cửa sổ cập nhật theo, *Lưu* không đặt lại giá trị cũ.
- Trong lúc một ô phím tắt chờ bạn nhấn, phím tắt toàn cục của app tạm tắt, để tổ hợp đó vào được ô (không mở chọn vùng).
- Mọi thứ vẫn lưu trong `data/config.json`; sửa tay khi app đã tắt vẫn được. Giá trị sửa tay mà app vẫn chạy được (ví dụ phím F9 không có Ctrl, chu kỳ 30 ms) được giữ nguyên: cửa sổ chỉ báo, không bắt sửa trước khi lưu thứ khác.
- Menu chuột phải của ô chữ và các nút chuẩn của Qt cũng hiện tiếng Việt (*Hoàn tác, Cắt, Sao chép, Dán…*).

**Hướng dẫn nhanh**: tự mở một lần ở lần chạy đầu tiên (không mở trong lần chạy có `--exit-after`). Có 6 bước ngắn, bước nào cũng bỏ qua được; Enter là *Tiếp*:

1. App làm gì, và lưu ý về anti-cheat.
2. Chọn ngôn ngữ nguồn.
3. Chọn hoặc tạo hồ sơ game (hồ sơ tự bật theo tên tệp .exe của game).
4. Chọn vùng dịch: mở game trước, để khung thoại hiện trên màn hình, rồi bấm *Chọn vùng ngay* (hoặc bỏ qua và chọn sau bằng Ctrl+Alt+R).
5. Bảng phím tắt.
6. Tình trạng LLM: đã cài / đang tải / chưa có, kèm lệnh cài `tools.setup_llm`.

Mở lại bằng khay → *Hướng dẫn nhanh…*, nút trong *Cài đặt* → *Chung*, hoặc `--first-run`. Đóng app khi hướng dẫn còn mở thì lần sau nó vẫn tự mở.

Kiểm thử: `& $py -m unittest tests.test_settings_ui -v`. Phần chạy trên nền Windows thật có một lần mở `run_app.pyw` bằng `pythonw.exe` với thư mục dữ liệu tạm: kiểm tra không có console, có icon khay, lần mở thứ hai chuyển sang bản đang chạy, thoát sạch. Ảnh xem trước từng tab và từng bước nằm ở `bench/results/preview/settings_*.png` và `first_run_*.png`.

## Dịch bằng LLM local (glossary + ngữ cảnh)

App chạy một model dịch trên GPU của bạn bằng llama-server, chỉ nghe trên 127.0.0.1. Model nhận cả màn hình cùng lúc, kèm glossary của hồ sơ, danh sách nhân vật và các câu thoại gần đây. Nhờ vậy tên riêng được giữ cố định, còn cách xưng hô (anh/em, tao/mày, bà/cháu…) đi theo người nói và người nghe.

- **Chế độ** (khay hệ thống → *Dịch bằng LLM*, hoặc `--llm-mode`):
  - *Google trước, LLM tinh chỉnh* (mặc định): bản Google hiện ngay, khoảng 0,5–1 s sau được thay tại chỗ bằng bản LLM, không bị nháy trống.
  - *Chỉ LLM*: chờ bản LLM; nếu LLM chưa sẵn sàng hoặc quá thời gian thì dùng Google.
  - *Tắt*.
- **Model** (khay → *Mô hình*, hoặc `--llm-model light|quality`):
  - *Tự động* (mặc định): dùng Hy-MT2-7B (chất lượng, cần thêm khoảng 5,3 GB VRAM) nếu còn trống ≥ 7,5 GB VRAM, nếu không thì dùng Hy-MT2-1.8B.
  - Bản 1.8B (khoảng 2 GB VRAM) chỉ tinh chỉnh câu thoại và phụ đề; phần UI để Google dịch.
- **Hồ sơ** (`data/profiles/*.json`, khay → *Hồ sơ* → *Sửa thuật ngữ / nhân vật…*). Mỗi game một hồ sơ, gồm:
  - Thuật ngữ cố định.
  - Nhân vật: tên tiếng Việt, giới tính, cách tự xưng, ghi chú quan hệ (ví dụ: *gọi Ren là "anh", xưng "em"*).
  - Văn phong.
  - Danh sách file exe để **tự chuyển hồ sơ** khi bạn mở game đó.

  Tên do LLM đề xuất được thêm vào glossary với nền vàng. Chúng chỉ là gợi ý cho LLM cho tới khi bạn bấm *Xác nhận*. Xoá một gợi ý sai thì các bản dịch đã cache với gợi ý đó cũng bị bỏ.
- **Vòng đời:**
  - Server tải model khoảng 2 s.
  - Server tự tắt (trả VRAM) sau 30 s khi game có anti-cheat ở foreground, và sau 10 phút không dùng. Lần sau cần thì tự bật lại.
  - Khi app thoát, không còn process llama-server nào sót lại.
  - Ngữ cảnh hội thoại tự reset sau 3 phút im lặng hoặc khi đổi vùng.
- **Cài runtime và model (lần đầu hoặc cài lại):** `& $py -m tools.setup_llm` tải những gì còn thiếu (thêm `--models light` để chỉ lấy bản 1.8B), `--verify` kiểm lại các file đã cài, `--force` tải lại hết. Bản dùng là llama.cpp b11669 CUDA 13.4 (driver r580+) hoặc 12.4, tự chọn theo driver, và 2 file GGUF đã kiểm SHA256, nằm trong `%USERPROFILE%\.screen-translator`.
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

### Chọn cách xưng hô (thẻ “Nhân vật”)

Không cần gõ ghi chú đúng mẫu *gọi Ren là 'anh', xưng 'em'* nữa. Mở *Sửa thuật ngữ / nhân vật…* → thẻ **Nhân vật**, chọn một nhân vật ở bảng trên (ô *Tìm nhân vật…* lọc theo tên và ghi chú, tiện với hồ sơ nhiều nhân vật như Blue Archive); khung **Xưng hô** bên dưới có:

- **Tự xưng**: cách nhân vật tự gọi mình (tôi, em, anh, tớ, ta…), chọn trong danh sách hoặc gõ.
- **Cách gọi từng người nghe**: mỗi dòng một người nghe, là một nhân vật khác trong hồ sơ hoặc một nhóm như *khách*, *người lạ*, *mọi người* (dùng cho mọi người nghe chưa có dòng riêng).
  - *Gọi là*: từ dùng để gọi người đó (anh, em, cậu, mày, Sensei…). Muốn ghi thêm thì ghi trong ngoặc: `anh (hoặc 'tiền bối')`, `anh (お兄ちゃん)`.
  - *Tự xưng riêng (gợi ý cho LLM)*: thường để trống; chỉ điền khi nhân vật tự xưng khác đi với riêng người này. App vẫn dùng *Tự xưng* ở trên cho mọi người nghe, từ ở cột này chỉ gửi cho LLM trong ghi chú. Ô giống hệt *Tự xưng* thì hiện trống.
  - Phím Insert thêm dòng, Delete xoá dòng đang chọn.
- **Ghi chú khác**: tuổi, vai trò, giọng nói (ô nhiều dòng).
- **Gợi ý**: điền Tự xưng còn trống (theo vai trò trong ghi chú, ví dụ *giáo viên* → cô / thầy) và các ô *Gọi là* còn trống khi đoán được: theo cách người nghe tự xưng (anh → anh, tao → mày, tớ → cậu), theo vai của người nghe (*thầy của Ren*, *sư phụ*, *bà nội* → thầy, bà…), hoặc theo giới tính khi người nghe chưa có Tự xưng. Không đoán được thì để trống (ví dụ một bạn cùng lớp xưng *tớ* với người tự xưng *tôi*), vì từ đã điền là thành cặp cố định ngay. *Thêm người nghe* cũng điền sẵn từ gợi ý như vậy.
- **Ghi chú gõ tay thiếu dấu nháy** (*gọi Ren là con, xưng ta*): app không đọc được dạng này. Khung xem trước báo bằng chữ đỏ và nút **Chuyển vào bảng** biến chúng thành dòng.
- **LLM sẽ được báo**: cặp xưng hô cố định app gửi kèm mỗi câu thoại, tính bằng đúng đoạn code lúc dịch, ví dụ *Sera → Ren: Sera xưng 'em', gọi 'anh'*. "Chưa cố định" nghĩa là LLM tự chọn theo ghi chú. Khung này cũng báo những gì app **không** dùng làm cặp cố định mà chỉ gửi như ghi chú:
  - tự xưng riêng khác Tự xưng chung (app dùng một Tự xưng cho mọi người nghe);
  - người nghe không phải nhân vật trong hồ sơ hay nhóm app hiểu (ví dụ *thầy Garo*: ghi *Garo*);
  - hai nhóm dùng hai từ khác nhau.
- Cột **Ghi chú** của bảng (một dòng mỗi nhân vật; di chuột lên ô để xem cả ghi chú) được viết từ các lựa chọn đó và vẫn sửa tay được (sửa tay thì khung Xưng hô đọc lại). Ghi chú có sẵn được giữ **nguyên văn** cho tới khi bạn đổi cách xưng hô của chính nhân vật đó; phần không phải xưng hô nằm ở *Ghi chú khác*. Khi viết lại, thứ tự các câu xưng hô được giữ ở những chỗ app đọc theo thứ tự (chữ *xưng* đầu tiên, câu *gọi X là* đầu tiên cho mỗi người).
- **Đổi tên nhân vật** (*Tên gốc* hoặc *Tên tiếng Việt*) thì các dòng xưng hô của nhân vật khác gọi tên cũ được đổi theo, và thanh trạng thái báo số nhân vật đã đổi.
- Không lưu được khi: một dòng có người nghe mà chưa có từ, một ô có hai từ (`anh hoặc chị`), hai dòng *Gọi là* cho cùng một người, hoặc một nhân vật chưa có **Tên gốc** (tên đúng như chữ trong game, ví dụ セラ: chỉ có *Tên tiếng Việt* thì app không nhận ra nhân vật). Dòng lỗi được tô đỏ. Tự xưng nhiều từ (`em/tớ`) của hồ sơ cũ chỉ phải sửa khi bạn sửa chính nhân vật đó.

### Hồ sơ Blue Archive

Hồ sơ mẫu **Blue Archive** nằm ở `profiles/templates/blue_archive.json`. Lần đầu bạn mở BlueArchive.exe, app đề nghị dùng hồ sơ này (hoặc chọn khay → *Tra cứu game*). Bấm dùng thì app chép nó vào `data/profiles/`. Hồ sơ được soạn từ dữ kiện công khai trên wiki (tên nhân vật, trường, CLB) và ghi chú riêng, không lấy từ tệp của game. Hồ sơ dựng theo mục *Hồ sơ Blue Archive (bản nháp)* trong `reports/Dịch game Nhật sang tiếng Việt.md`. Với BA, app chỉ chụp màn hình rồi OCR, không hook và không đọc bộ nhớ game, vì bản Steam có Nexon Game Security còn bản Yostar JP có XIGNCODE3.

- **Nội dung:**
  - 73 thuật ngữ: Kivotos, Schale, Hộp Shittim, Pyroxene, Gematria, MomoTalk, Total Assault…, tên trường (Trường Abydos, Học viện Gehenna…) và tên CLB (Ủy ban Đối sách, Problem Solver 68, CLB Phát triển Game…).
  - 107 nhân vật: Sensei, Arona, Plana, Black Suit và 103 học sinh. Mỗi người có trường, CLB và một câu ngắn về giọng nói. Ví dụ: Hoshino mở đầu bằng "Uhe~" và tự gọi mình là おじさん, dịch "ông chú này". Aris tự gọi mình là "Aris", nói như nhân vật game nhập vai, パンパカパーン thành "Tèn ten!". Shiroko nói ん thành "Ừm.". Izuna và Asuna gọi Sensei là "chủ nhân", Wakamo gọi あなた様 thành "Ngài", Kisaki tự xưng 妾 thành "ta". Yuzu, Haruka, Miyu, Tsukuyo nói lắp: せ、先生 thành "S-Sensei".
  - `src_lang` là `ja`. `processes` chỉ có `BlueArchive.exe` (bản Steam/Nexon).
- **Xưng hô:**
  - Học sinh xưng **em** và gọi **Sensei**, không dịch thành thầy/cô. Câu gốc dùng です/ます thì thêm "ạ". Nhân vật nói suồng sã (Hoshino, Shiroko, Serika, Momoi, Mika, Neru…) thì không thêm "ạ".
  - Game không nêu giới tính của Sensei, nên mặc định Sensei xưng **tôi**, gọi học sinh là **em** và gọi Black Suit là **ông**.
  - Giữa hai học sinh, hồ sơ không đặt cặp xưng hô cố định. Ghi chú chỉ nói thêm, ví dụ "đàn em của Hoshino, gọi Hoshino là Hoshino-senpai". Lý do: Sensei nói bằng nút lựa chọn và không có ô tên. Vì thế người nghe mà app đoán (người vừa nói trước) thường là một học sinh khác, dù câu đang nói với Sensei. Một cặp cố định giữa hai học sinh sẽ khiến câu nói với Sensei bị dịch như nói với học sinh kia.
  - Câu mở đầu bằng tên người được gọi (先生、… / ねえ先生、… / ホシノ先輩！) thì người đó là người nghe, bất kể ai vừa nói trước.
- **Đổi Sensei thành thầy/em:**
  1. Mở *Sửa thuật ngữ / nhân vật…* → thẻ **Nhân vật** → chọn 先生.
  2. Đặt Tự xưng là `thầy` và Giới tính là `nam`.
  3. Trong ghi chú, đổi `xưng 'tôi' (không xưng 'anh', 'thầy' hay 'cô')` thành `xưng 'thầy' (không xưng 'tôi' hay 'anh')`.

  Muốn Sensei là cô giáo thì dùng `cô` và `nữ`. Lưu ý: khi đó model 7B cũng hay để học sinh gọi Sensei là "thầy" (2 trên 3 câu thử).
- **Tên:** tên nhân vật và tên riêng viết theo cách La-tinh chính thức của bản Global. Ví dụ: Hoshino, Yuuka (giữ nguyên âm dài), Aris (bluearchive.wiki viết Arisu), Kivotos, Schale, Problem Solver 68, Pandemonium Society. Phần mô tả trong tên trường và tên CLB thì dịch ra tiếng Việt.
- **Mọi cách dịch tiếng Việt trong hồ sơ chỉ là đề xuất.** BA không có bản tiếng Việt chính thức, và không tìm thấy quy ước xưng hô hay glossary tiếng Việt nào được ghi lại. Chỉ "CLB Phát triển Game" là đã thấy dùng thật; các thuật ngữ khác có ghi chú "đề xuất". Bạn sửa theo ý mình trong *Sửa thuật ngữ / nhân vật…*, ví dụ "CLB Phụ đạo" thay cho "CLB Học bù", hoặc "dũng giả" thay cho "dũng sĩ".
- **Chưa kiểm chứng, cần bạn tự làm:**
  - Tên exe của client PC Yostar JP: lúc chơi, mở Task Manager xem tên exe rồi thêm vào hồ sơ. Trước khi thêm, chọn hồ sơ bằng tay.
  - Nếu chơi bản Global tiếng Anh thì xoá `src_lang`.
  - Bố cục hộp thoại chưa được đối chiếu với ảnh chụp thật. App đã hiểu nhãn CLB nằm cạnh tên, dù là một nhãn ("ホシノ 対策委員会") hay hai nhãn ("ユウカ" và "セミナー"). Nhưng nhãn dài hơn 10 ký tự (như "ナギサ ティーパーティー") thì app chưa nhận ra đó là nhãn tên.
  - Nên dùng model 7B. Bản 1.8B thường dịch 先生 thành "Thầy".
- **Kết quả thử** (model 7B, câu tự viết theo phong cách BA, không lấy thoại của game, 2026-10-25):
  - Đúng cả nghĩa lẫn xưng hô: 40/48 câu thoại trong bộ thử của người review (lần review trước: 36/48) và 27/30 câu trong bộ thử lúc dựng hồ sơ (trước: 26/30). Tên riêng đúng ở mọi câu.
  - Lỗi còn lại chủ yếu là "em" nằm sai vai trong câu nhờ, câu cấm hoặc câu hỏi ý người nghe. Ví dụ: 好きなものを買ってくださいね → "Em mua…", 気にしないで → "em đừng…", 知りたい？ → "em muốn biết…", ようこそ → "Chào mừng em…". Bộ kiểm tra xưng hô chưa bắt được các lỗi này. Ngoài ra, Sensei thỉnh thoảng xưng "anh" (1 trên 11 câu).
  - Hồ sơ làm prompt dài thêm khoảng 400 token. Với 12 dòng lịch sử của 12 người nói khác nhau, prompt khoảng 4000 trên 4096 token. Nếu prompt quá dài, app tự bớt lịch sử và gửi lại. Nếu log hay báo "prompt too long", đặt `llm_ctx` lên 6144.

### Kết quả tầng LLM (2026-10-24)

Mục này và các lần sửa bên dưới là nhật ký đo theo thời gian (số đo lúc đó, trên máy của tác giả); bảng nào cũng ghi rõ là của lần nào.

- **Unit test:** lúc đó đạt hết (sau lần 7 của phần xưng hô theo vai).
- **E2E `--llm` với model tự động (7B):** đạt 16/16 (lần 7).
  - 12/12 trang được LLM dịch, 5 lần thay tại chỗ, 0 khung trống.
  - Tên đúng 6/6, người nói đúng 14/14.
  - VRAM 1,1 → 7,5 → 1,2 GB.
- **Bộ cảnh thoại:**

  | | Sạch / 40 | Lỗi xưng hô (bộ kiểm tra tự động) | chrF so với Google (70,5) | Độ trễ p50 |
  |---|---|---|---|---|
  | 7B | 40 | 0 | 73,2 | 0,59 s |
  | 1.8B | 30/35 câu được trả lời | 5 | 66,2 | 0,11 s |

- **Xưng hô theo vai (cập nhật 2026-10-24):** cùng một từ đổi vai theo chiều nói (em gái xưng "em" gọi anh là "anh", anh trai thì ngược lại). Bộ kiểm tra `app/translate/pronoun_roles.py` giờ phân biệt *tự xưng* với *gọi người nghe*. Sau khi LLM trả lời, app tự sửa đại từ sai vai khi câu gốc nói rõ ai làm (xin lỗi, cảm ơn, tha lỗi, nhờ / giúp, "để em…", "em sẽ…"): 「ごめんね。一緒に探してくれる？」 của Sera ra "**Em** xin lỗi nhé. Anh giúp em tìm cùng được không?" thay vì "Anh xin lỗi nhé…". Lỗi app thấy mà không sửa chắc chắn được (gọi "Em ơi" sai, "mua kem cho chị" khi chị là người mua, "Anh đi đây" cho 行ってきます, "Ý em là sao?") thì hỏi lại LLM một lần với ghi chú chỉ đúng từ sai; câu trả lời mới chỉ được nhận khi giữ nguyên số đại từ và các hành động (xin lỗi, giúp…). Việc sửa tốn khoảng 0,3 ms với màn hình một câu; hỏi lại tốn thêm một request (7B khoảng 0,4–0,6 s) và chỉ xảy ra với câu như thế.
  - **Rà soát lại (2026-10-24):** bản sửa đầu đảo ngược cả câu đúng. Giờ app không sửa khi hành động chỉ được kể lại hay trích dẫn (「母さんが、お前に謝れってさ」, 「ケンジくんに謝れって言われた」, 『ごめん』って, "Mom told me to say sorry", "Did he say sorry"), khi câu có điều kiện ("Nếu anh giúp em…"), khi chủ ngữ là người khác ("nhân viên sẽ giúp anh", "Kenji đã xin lỗi anh"), khi câu gọi người ngoài cặp ("Chị ơi" với người lạ), và "bỏ qua" chỉ là "tha" khi có "cho". Mỗi câu nguồn chỉ áp cho câu dịch tương ứng, nên "Why should I apologize? You apologize!" được sửa đúng cả hai nửa. "sẽ" chỉ được báo, không sửa, không hỏi lại (từ lần 4 có một ngoại lệ: khi chủ ngữ đó cũng do một đại từ rõ của câu gốc chỉ ra thì được hỏi lại). Mỗi lần sửa được ghi vào `app.log`. Cache tầng 2 có revision mới (`llm-r2`), nên bản dịch đã lưu trước đó được dịch lại.
  - Trên 118 câu có nhãn của người rà soát (cố ý nhiều câu khó), bộ kiểm tra chỉ dựa vào câu gốc: độ chính xác 0,97, độ phủ 0,89 (trước 0,64 / 0,83). Đây là số trong mẫu: luật được sửa trên chính các câu này. Trên 116 câu trả lời thật của hai model cho các câu khó đó: trước có 11 câu (hoặc vế câu) đúng bị làm sai; giờ không có câu đúng nào bị đổi, 2–3 câu sai được sửa đúng. Các cảnh `roles_probe_*` trong bench (split "probe") chứa các câu này và được báo riêng, để số tune / test vẫn so được với trước.
  - **Đo độc lập (held-out):** 2 người soạn mới 120 câu có nhãn (49 câu sai vai), không xem code bộ kiểm tra.
    - Độ chính xác 0,90: 1 lần báo nhầm trên 71 câu đúng ("để cô xách giúp em" của cô giáo).
    - **Độ phủ chỉ 0,18** (9/49). Với tập câu xin lỗi / cảm ơn / nhờ vả thông thường là 0,28; với tập câu khó (ai làm gì, câu kể lại, câu nhiều vế) là 0,08.
    - Sửa tự động: 1 câu sai được sửa đúng ("thầy xin lỗi" → "em xin lỗi"), 0 câu đúng bị đổi.
    - Kết luận: app chỉ sửa khi chắc chắn và hầu như không làm hỏng câu đúng, nhưng phần lớn lỗi sai vai vẫn lọt qua. Các nhóm bị lọt nhiều nhất:
      - Câu hỏi thăm người nghe (大丈夫？/怪我してない？ → "em có sao không").
      - ただいま / 行ってきます (khi có chữ chen giữa "đi … đây").
      - Câu có đại từ rõ ràng trong câu gốc (I/you, お前を/私も).
      - Câu "ai làm gì" với động từ thường.
    - Hai bộ này (A, B) đã được đọc kỹ chỗ lọt nên giờ là dữ liệu phát triển: chuyển sang `bench/data/translate/roles_dev/` cùng 118 câu có nhãn của người rà soát (`roles_labelled.json`). Kết quả đo held-out cũ vẫn ở `bench/results/eval_roles_heldout.json`.
  - **Cập nhật lần 2 (2026-10-24): tăng độ phủ cho ba nhóm lỗi hay lọt.**
    - Câu hỏi thăm người nghe (大丈夫？, 怪我はない？, お怪我はございませんか, 元気？, 疲れてない？, 寒くない？, お腹すいてない？, どうしたの？, 何してるの？, "Are you okay / hurt?", "Did you eat?", "What are you doing?"): chủ ngữ của "có sao không / không sao chứ / có bị thương không / ổn không / mệt / đói / làm gì" trong câu hỏi phải là người nghe ("Anh có sao không?" của Sera là đúng, "Em có sao không?" là sai). Ngược lại, câu kể cảm giác của chính người nói (お腹すいた！, 怒ってないよ, 心配したんだよ, うれしいよ) cần từ tự xưng ("Con đói quá", "Mẹ không giận đâu"). Câu trả lời 大丈夫だよ ("Em không sao", hay "Anh sẽ ổn thôi") không bị bắt; câu hỏi về người thứ ba (ケンジは大丈夫？, あいつ、何してるんだ？), câu có 私 của người nói (私の顔、大丈夫？), câu trích dẫn cũng vậy.
    - Câu chào: ただいま / "I'm home", 行ってきます, お先に失礼します, いただきます là của người nói ("Cháu đi học đây ạ", có chữ chen giữa "đi … đây" cũng được; "Con về rồi đây"; "Con mời mẹ ăn cơm"); おかえり, 行ってらっしゃい là của người nghe ("Anh về rồi à", "Mừng cháu về nhà", "Anh đi cẩn thận nhé"); おやすみ: "Chúc anh ngủ ngon", "Em đi ngủ đây"; ごちそうさま là lời cảm ơn người nghe. 'Mẹ đi làm đây' của người mẹ cho 行ってくるね là đúng; 「ただいま」って (trích dẫn) không bị xét.
    - Đại từ rõ trong câu gốc (私/僕/俺/お前/あなた/君… + が/は/も/を/に/の, I/me/my/you/your, "we" khi câu có cả "you" và bản dịch không có "chúng/bọn…"): mỗi câu có bao nhiêu chủ ngữ / sở hữu ("của …") / tân ngữ là đại từ rõ thì bản dịch phải có đúng chừng ấy và đúng người: "I'll protect you" → "Anh sẽ bảo vệ em", không phải "em sẽ bảo vệ anh"; "お前はわしの自慢の弟子じゃ" → "Con là đệ tử … của ta". Từ chỉ vai mà người nói dùng để tự xưng (先生 của cô giáo, お母さん của mẹ, お姉ちゃん của chị) được hiểu là người nói. Không xét câu kể lại, trích dẫn, câu điều kiện, câu nhờ vả / "Can I…?", câu có 好き / 大事 / 分かる (hay bị đảo cấu trúc: "Anh là người em thích"), câu có người thứ ba ("anh ấy", "em gái tôi"), mệnh đề phụ đứng đầu (私がついてるから、…), và với tiếng Anh, câu mà bản dịch có nhiều đại từ hơn câu gốc ("Are you looking for…?" → "Em có thể giúp anh tìm…?").
    - Lần báo nhầm của bộ held-out cũ ("để cô xách giúp em cho" của cô giáo) đã hết: "em" ở đó là người được giúp, không phải chủ ngữ của "cho".
    - App làm gì: lỗi ở câu hỏi thăm, câu chào và ở sở hữu ("của …") thì **hỏi lại LLM** (không tự sửa); lỗi chủ ngữ / tân ngữ theo đại từ rõ thì **chỉ báo** (như "sẽ"), vì bản dịch hay đảo cấu trúc những câu đó (nhưng nếu câu đó đằng nào cũng bị hỏi lại thì chúng được ghi vào ghi chú, xem "Rà soát lần 3"). Trên dev, nếu tự sửa thì cả 34 chỗ đều sửa đúng, nhưng số câu còn ít nên chưa bật. *(Từ lần 4 các lỗi này được hỏi lại, xem "Hỏi lại lỗi chủ ngữ / tân ngữ theo đại từ rõ".)*
    - Số đo trên dev (238 câu, 85 câu sai vai; nhóm theo câu gốc, xem `bench/eval_roles_heldout.py`): đây là số **trong mẫu**, luật được viết khi xem các câu này.

      | Nhóm | Câu sai / đúng | Trước: bắt được, báo nhầm | Sau: bắt được, báo nhầm |
      |---|---|---|---|
      | Hỏi thăm người nghe | 4 / 2 | 0, 0 | 4, 0 |
      | Câu chào | 4 / 5 | 2, 0 | 4, 0 |
      | Đại từ rõ | 45 / 87 | 13, 2 | 32, 1 |
      | Còn lại | 32 / 59 | 26, 0 | 28, 0 |
      | Tổng | 85 / 153 | 41, 2 (P 0,95, R 0,48) | 68, 1 (P 0,99, R 0,80) |

      Lần báo nhầm còn lại là "Thứ Sáu anh sẽ lấy được máy" ("sẽ", đã biết, chỉ báo). Tự sửa: 24 câu sai, 22 sửa đúng hoàn toàn, 0 câu đúng bị đổi (như trước). Riêng từng bộ: A 0,28 → 0,84, B 0,08 → 0,50, bộ có nhãn 0,89 → 0,97 (độ phủ); độ chính xác 1,00 / 1,00 / 0,97.
    - Rà soát câu trả lời thật: 435 câu đã lưu (226 câu của hai model trong `eval_llm.json`, 85 câu Google, 109 câu tham chiếu, 15 trang e2e) thêm 1 câu bị báo, là lỗi thật (bà hỏi cháu 怪我はないかい？ mà bản 1.8B dịch "Bà không sao chứ?"), 0 câu bị sửa thêm. 168 câu mới của cả hai model trên 5 cảnh soạn mới cho ba nhóm này: thêm 1 câu bị báo, cũng là lỗi thật (cô giáo nói 先生、うれしかったよ mà bản 1.8B dịch "Cô ơi, em rất vui"). Trong lúc làm, bản đầu của phần "đại từ rõ" đã báo nhầm 4 câu đúng của model (câu đảo cấu trúc, "anh ấy", "em gái tôi", "anh nấu cho em"); các trường hợp đó giờ được bỏ qua và nằm trong test. Chạy lại cả hai model với sửa / hỏi lại bật: bản 7B không có câu nào sai vai, bản 1.8B chỉ bị hỏi lại ở các câu sai thật.
    - Đo lại: `& $py -m bench.eval_roles_heldout --dev --show` (dev). Bộ held-out mới (soạn độc lập, chưa ai xem) đặt vào `bench/data/translate/heldout/` rồi chạy `& $py -m bench.eval_roles_heldout --show`. **Không chỉnh luật dựa trên bộ held-out**: một khi đã chỉnh, nó không còn là held-out nữa, phải soạn bộ mới.
  - **Rà soát lần 3 (2026-10-24): sửa những gì hai người rà soát tìm thấy.** Một người soạn thêm 212 câu có nhãn (95 câu sai vai, 117 câu đúng, tập trung vào ba nhóm trên và các câu gần giống); người kia thử hơn 130 câu khó và chạy lại bench. Các lỗi đã sửa:
    - **Trích dẫn:** 『大丈夫か？』って聞いた, 『何してるの？』って (có trợ từ trước ngoặc đóng) từng bị coi là câu hỏi của chính người nói và bị hỏi lại; 『俺に任せろ』って言った từng bị **tự sửa sai** ("cứ để anh lo" → "cứ để em lo"). Giờ mọi tín hiệu nằm trong 「」/『』 mà câu còn tiếp sau dấu ngoặc (って, と言, も言わず…) đều không được xét. Ngoặc bao cả câu và nhãn tên (セラ：…, セラ「…」) không phải trích dẫn, nên 「お前は私の自慢の弟子じゃ」 giờ cũng được xét. Đại từ nằm trong ngoặc kép bên trong bản dịch không bao giờ bị tự sửa.
    - **Người ngoài cặp:** "Em có sao không? Để chị dán băng cá nhân cho em nhé." (Sera nói với một đứa bé) từng bị hỏi lại. Giờ dòng nào dùng một đại từ thân tộc ngoài cặp như đại từ ("chị" giữa em/anh) thì không xét gì, như với "Chị ơi".
    - **Chủ của một vật:** "Hộp cơm của em có ổn không?", "Máy em bị sao vậy?" không còn bị coi là hỏi người nghe; bộ phận cơ thể vẫn được xét ("Chân anh có sao không?", "Vết thương của anh…").
    - **Tiếng Anh:** "What's wrong with me / my phone?", "Are you okay with me coming?" (câu có I/me/my hay người thứ ba) không còn là câu hỏi thăm. 行ってくる？ / "I'm off?" là hỏi người nghe có đi không. "Anh đưa em đi chơi đây": người được đưa đi không phải người đi.
    - **Đại từ rõ:** không còn xét "của bạn anh" (bạn = bạn bè), mệnh đề quan hệ (お兄ちゃんが作ったカレー, 選んだかばん), "You and I", câu bị động có tác nhân (私は先生に褒められた → "Cô đã khen em", "saved by you") và "we" không kèm "you" (có thể gồm cả người nghe: "We'll be late!" → "Con sẽ muộn mất"; mất 1 câu bắt đúng trên dev). Thêm các từ chỉ thời gian trước chủ ngữ ("Tối qua anh…", "Lát nữa anh…").
    - **Tự sửa:** các tín hiệu mới chưa từng được thử tự sửa trên dev (ごちそうさま, 先生がやる / お母さんが作る, 任せなさい, 俺が何とかする) giờ chỉ **hỏi lại**, không tự sửa. Câu mà sửa một chỗ sẽ thành mâu thuẫn ("Con sai rồi. Con xin lỗi mẹ nhé" cho お母さんが悪かったわ。ごめんね。, trước bị sửa thành "Con sai rồi. Mẹ xin lỗi con nhé") giờ được hỏi lại cả câu, ghi chú nêu cả chủ ngữ sai. Khi hỏi lại, nếu model đặt "bạn" / "tôi" vào đúng chỗ ghi chú yêu cầu thì app thay bằng từ của cặp ("Bạn không sao chứ?" → "Cháu không sao chứ?", không bao giờ thay sang tao/mày); câu trả lời đưa vào một đại từ khác mà ghi chú không yêu cầu thì bị bỏ.
    - **Thêm độ phủ** (chỉ hỏi lại hoặc chỉ báo, không tự sửa): "Có sao không em?" (gọi ở cuối câu hỏi), うん、大丈夫 (trả lời về chính mình; "Ừ, anh sẽ ổn thôi" là trấn an nên không xét), 寒い… / 痛い / 怖い, 心配したんだからね, どこか痛い？, 熱ある？, ちゃんと寝てる？, "Did you sleep", "Trông em mệt quá", "Em ra cửa hàng" cho 行ってくる, お前は俺が守る / 家まで送る (chủ đề là tân ngữ), câu kết bằng tên gọi (…、トビ, ", son"), và "tay mẹ" cho "your hand" (sở hữu không có "của", chỉ báo).
    - Cache tầng 2 lên revision `llm-r3`, nên các câu đã lưu với bản kiểm tra cũ được dịch lại. check_line nhanh hơn với dòng dài (câu nguồn chỉ tách một lần). `bench/eval_roles_heldout.py` giờ báo thêm độ chính xác / độ phủ của những gì app **thật sự làm** (tự sửa hoặc hỏi lại), tách khỏi lỗi chỉ được báo.
    - Số đo, **cả hai đều trong mẫu** (luật được chỉnh khi xem chính các câu này; bộ 212 câu nay nằm ở `bench/data/translate/roles_dev/roles_labelled_r2.json`):

      | Bộ | Trước lần 2 | Sau lần 2 | Sau lần 3 |
      |---|---|---|---|
      | dev (238 câu, 85 sai): bắt được P / R | 0,95 / 0,48 | 0,99 / 0,80 | 0,99 / 0,79 |
      | dev: app làm gì đó P / R | 0,97 / 0,45 | 1,00 / 0,64 | 1,00 / 0,64 |
      | 212 câu của người rà soát (95 sai): P / R | 0,83 / 0,11 | 0,95 / 0,75 | 1,00 / 0,93 |
      | 212 câu: app làm gì đó P / R | 0,78 / 0,07 | 0,96 / 0,49 | 1,00 / 0,63 |
      | 212 câu: tự sửa trên câu sai (đúng hoàn toàn) / trên câu đúng | 3 (0) / 1 | 4 (0) / 1 | 1 (0) / 0 |

      Tự sửa trên dev vẫn là 24 câu sai (22 đúng hoàn toàn), 0 câu đúng. 435 câu trả lời thật đã lưu và 336 câu mới của hai model (bản trước có / không có sửa) cho kết quả giống hệt bản trước: không thêm câu bị báo, bị sửa hay bị hỏi lại.
    - `bench/eval_llm.py` (cả hai model): 7B giữ nguyên mọi chỉ số (sạch 119/120, lỗi vai 3/114, chrF 73,2). 1.8B: các chỉ số như lần đo 12:15 (sạch 94/115, lỗi xưng hô 21, lỗi vai 3/114), nhưng câu của bà 怪我はないかい？ giờ ra "**Cháu** không sao chứ?" sau một lần hỏi lại (trước là "Bà không sao chứ?", và ở bản lần 2 thành "Bạn không sao chứ?").
    - Kiểm tra e2e có LLM (`tools/e2e_check.py --llm`) lần đầu trượt mục "speaker attribution" (8/14): khi một trang lặp lại được lấy từ cache tầng 2 trong job LLM (không phải trong batch Google), kết quả mất tên người nói, nên bộ nhớ hội thoại ghi người nói rỗng. Đây là lỗi có sẵn, phụ thuộc thời điểm. Giờ job đó giữ người nói của nhãn như đường batch (`app/pipeline/pipeline.py`, có test); chạy lại: 16/16 PASS (14/14 người nói đúng), không còn llama-server, VRAM trở về như trước. e2e không LLM: PASS.
  - **Đo held-out C (2026-10-24, một lần, sau khi mọi thay đổi đã xong).** 2 người soạn mới 120 câu (50 câu sai vai), không xem code và không xem các bộ cũ:
    - C1: 60 câu nhắm vào ba nhóm trên.
    - C2: 60 câu thoại thông thường, không nhắm vào luật nào.
    - Người viết luật không được đọc bộ này. Bộ nằm ở `bench/data/translate/heldout/` (sau lần đo này đã chuyển sang `roles_dev/`).

    | | Trước lần 2 | Bây giờ |
    |---|---|---|
    | Cả bộ: báo đúng (P) / bắt được (R) | 1,00 / 0,08 (4/50) | **1,00 / 0,56** (28/50) |
    | C1 (ba nhóm) / C2 (thông thường): R | 0,04 / 0,12 | 0,68 / 0,44 |
    | App thật sự làm (tự sửa hoặc hỏi lại): P / R | — | 1,00 / 0,40 |
    | Câu đúng bị báo nhầm / bị tự sửa | 0 / 0 trên 70 | **0 / 0 trên 70** |

    Theo nhóm (bắt được / app làm), tính bằng R:
    - Hỏi thăm người nghe: 0,89 / 0,78.
    - Câu chào: 1,00 / 0,90.
    - Đại từ rõ: 0,43 / 0,14. Chủ ngữ / tân ngữ ở nhóm này mới chỉ được báo, chưa hỏi lại (lần 4 đã bật hỏi lại, xem mục dưới).
    - Còn lại (ai làm gì với động từ thường, nhờ 〜てくれない, câu nhiều vế): 0,10 / 0,10.

    Không có lần tự sửa nào trên bộ này, vì các luật mới chỉ hỏi lại. Bộ C giờ đã bị xem, nên lần sau phải soạn bộ held-out mới. Kết quả đo C được giữ ở `bench/results/eval_roles_heldout_C.json`.
  - **Lần 4 (2026-10-24): hỏi lại lỗi chủ ngữ / tân ngữ theo đại từ rõ.** Trên bộ C, nhóm "đại từ rõ" (私が / お前を / I / you…) được bộ kiểm tra bắt 0,43 nhưng app chỉ làm gì đó với 0,14, vì chủ ngữ, tân ngữ và sở hữu không có "của" ("tay mẹ" cho "your hand") theo đại từ rõ chỉ được báo. Giờ các lỗi đó được **hỏi lại** LLM một lần, không bao giờ tự sửa:
    - Phần kiểm tra đại từ rõ không đổi và vẫn bỏ qua những câu nó không chắc: câu kể lại, trích dẫn, câu điều kiện, bị động có tác nhân, mệnh đề quan hệ, 好き / 大事 / 分かる, người thứ ba, số nhiều, "you and I", "we" không kèm "you", mệnh đề phụ đứng đầu, và câu tiếng Anh mà bản dịch có nhiều đại từ hơn câu gốc. Mỗi trường hợp có test là không hỏi lại.
    - "sẽ" vẫn chỉ được báo. Ngoại lệ duy nhất: chủ ngữ của "sẽ" cũng là chủ ngữ mà một đại từ rõ chỉ ra. Ví dụ 私がやる → "Anh sẽ làm" của Sera được ghi theo đại từ và được hỏi lại, còn 任せて！ → "Anh sẽ lo hết!" vẫn chỉ được báo.
    - Ghi chú hỏi lại nêu đúng từ sai và người nó chỉ, ví dụ `I'll in the source is Ren, the subject: "em" there means Ren, write "anh"`.
    - Câu trả lời mới chỉ được nhận khi đủ cả bốn điều kiện:
      - ít lỗi vai hơn, tính cả lỗi được hỏi lẫn mọi loại lỗi;
      - giữ số đại từ và các hành động (xin lỗi, giúp…);
      - không đưa vào đại từ nào ngoài các từ ghi chú yêu cầu;
      - không làm mất thuật ngữ glossary.
    - Giới hạn: mỗi màn hình hỏi lại tối đa 3 câu, gộp trong một request, mỗi câu một lần. Câu hỏi thăm, câu chào và lời gọi sai được ưu tiên trước các câu chỉ có lỗi theo đại từ rõ. Vì vậy một màn hình xấu chỉ thêm nhiều nhất một request.
    - Cache tầng 2 lên revision `llm-r4`. `bench/eval_llm.py` giờ ghi lại câu nào bị hỏi lại (trường `reask` của từng dòng và `role_reask_lines` trong tóm tắt), và in các câu đó ra để đọc.
    - Số đo trên dev (570 câu, 230 câu sai). Đây là số **trong mẫu**, gồm cả bộ C đã xem:

      | | Lần 3 | Lần 4 |
      |---|---|---|
      | App làm gì đó (sửa / hỏi lại): P / R | 1,00 / 0,58 | 1,00 / 0,79 |
      | Nhóm đại từ rõ: R | 0,30 | 0,69 |
      | Bộ C (120 câu): R | 0,40 | 0,56 |
      | Bộ C, nhóm đại từ rõ: R | 0,14 | 0,43 |
      | Câu đúng bị hỏi lại | 0 / 340 | 0 / 340 |

    - **Hỏi lại với model thật** (`& $py -m bench.eval_roles_reask --dev --derive-refs`): bản dịch có nhãn được đưa vào làm câu trả lời đầu của model, rồi đường hỏi lại thật của app chạy trên đó. Câu tham chiếu được suy ra từ nhãn. Dòng "trần" là khi mọi câu sai đều được hỏi lại với bộ kiểm tra hoàn hảo.

      | Trên 230 câu sai | 7B | 1.8B |
      |---|---|---|
      | Được hỏi lại | 159 | 159 |
      | Sửa đúng hết / tốt hơn / như cũ / tệ hơn | 121 / 0 / 1 / 0 | 57 / 1 / 0 / 0 |
      | Bị chặn: đổi nhiều hơn vai / không ít lỗi hơn | 35 / 2 | 99 / 2 |
      | Nhóm đại từ rõ (111 câu sai): hỏi lại / sửa đúng | 72 / 55 | 72 / 31 |
      | Trần: sửa đúng (hỏi lại cả 230) | 131 | 68 |
      | Câu đúng bị hỏi lại (trên 340) | 0 | 0 |
      | Độ trễ một lần hỏi lại p50 / p95 | 0,51 / 0,65 s | 0,10 / 0,14 s |

      - Câu "như cũ" của 7B: お母さん、僕のお弁当入れ忘れたでしょ, "Mẹ ơi, mẹ quên bỏ hộp cơm của mẹ…" thành "Mẹ ơi, con quên mang hộp cơm của con…". Sở hữu giờ đúng, nhưng người quên thành người con. Chủ ngữ của 忘れた không có đại từ rõ nên app không thấy.
      - Phần lớn câu bị chặn ở 7B là câu trả lời đúng nhưng thêm hoặc bớt một đại từ, ví dụ "Xin lỗi anh, em ăn mất…", hoặc "Em, có phải em đang sốt không?". Ở 1.8B, điều kiện này chủ yếu chặn câu trả lời sai, ví dụ "Ông ấy tin tưởng em", hay "Bà ơi, mẹ đã sai rồi".
    - **Dịch mới từ đầu** (`& $py -m bench.eval_roles_e2e --dev --derive-refs --scenes all`): 684 câu, gồm 570 câu dev và 114 câu cảnh bench có `ref` do người viết. Mỗi câu được dịch hai lượt, một lượt tắt phần sửa vai và một lượt bật. Lượt bật còn ghi lại câu trả lời đầu của chính nó, nên có thể so tắt / bật trên cùng một câu trả lời.

      | | 7B | 1.8B |
      |---|---|---|
      | Câu sai vai, tắt → bật (cùng câu trả lời đầu) | 25 → 14 (3,7% → 2,0%) | 9 → 9 (1,3%) |
      | Câu tốt hơn / tệ hơn | 12 / 0 | 0 / 0 |
      | Câu được tự sửa | 8 | 0 |
      | Câu bị hỏi lại (tỉ lệ) | 10 (1,5%) | 5 (0,7%) |
      | Hỏi lại: nhận / bị chặn / model trả lời y như cũ | 4 / 5 / 1 | 0 / 1 / 4 |
      | Độ trễ thêm: trung bình mỗi câu / p50 câu bị hỏi lại | +7 ms / +0,46 s | ~0 / +0,09 s |

      - Phần lớn chỗ tốt hơn ở 7B đến từ các loại cũ: tự sửa câu xin lỗi, hỏi lại câu chào và câu hỏi thăm.
      - Loại mới của lần 4 chỉ được hỏi lại ở 1 câu (7B) và 5 câu (1.8B). Ở mọi câu đó, model hoặc trả lời y như cũ, hoặc bị chặn vì thêm "anh ấy". Nghĩa là trên câu dịch mới, lỗi loại này hiếm (7B: 6 trên 339 câu nhóm đại từ rõ), và khi có thì model thường giữ nguyên câu dù có ghi chú. Bước hỏi lại ở trên sửa được nhiều là vì câu trả lời đầu ở đó không phải của chính model.
      - 5 câu bị chặn ở 7B đều có câu trả lời mới đúng vai nhưng làm mất một đại từ. Ví dụ "Nè, chị đang làm gì vậy?" bỏ lời gọi "Chị ơi", và "Em đi trước nhé!" bỏ câu thừa "Em sẽ theo sau ngay". Điều kiện "giữ số đại từ" vì vậy làm mất một số lần sửa đúng. Có thể nới điều kiện này sau (ví dụ không tính lời gọi), nhưng phải đo lại trước khi đổi.
      - llama-server không cho kết quả giống hệt nhau giữa hai lượt chạy, vì prompt lúc lấy từ cache, lúc được tính lại. Câu trả lời đầu khác nhau ở 1 câu (7B) và 38 câu (1.8B). Vì vậy bảng trên so trên cùng câu trả lời đầu. So hai lượt chạy riêng cũng cho kết quả như vậy: 25 → 14 và 9 → 9.
      - Lúc đo, 1.8B dịch đúng "Grandma, the lady next door asked me to tell you thanks." mà app vẫn hỏi lại, vì bộ kiểm tra coi "thanks" là lời cảm ơn của người nói. Lỗi này đã được sửa: "tell you thanks" / "told you thanks" là lời cảm ơn được nhắn lại. Phần sửa có test và không đổi số đo dev. Cột 1.8B ở trên đã được chấm lại bằng bộ kiểm tra mới, trên chính các câu đã dịch. Sau khi sửa, không còn câu đúng nào bị hỏi lại ở cả hai model.
    - `bench/eval_llm.py`, cả hai model: mọi chỉ số và **mọi câu trả lời** giống hệt lần đo 15:20.
      - 7B: sạch 119/120, lỗi vai 3/114, chrF 73,2.
      - 1.8B: sạch 94/115, lỗi vai 3/114, chrF 66,2.
      - Số request như cũ (157 / 169). Mỗi model vẫn có đúng 1 câu bị hỏi lại, cùng câu như trước, và cả hai câu đều sai rồi được sửa đúng: 怪我はないかい？ → "Cháu không sao chứ?" và 行ってきます！ → "Em đi đây!".
      - Độ trễ p50 121 → 124 ms (1.8B) và 628 → 653 ms (7B) với cùng số request. Đây là dao động giữa các lần chạy: lúc đo có một game chạy nền.
    - Rà soát câu trả lời thật đã lưu, chấm lại bằng luật mới:
      - 320 cặp (câu, bản dịch) khác nhau của hai model, trong `eval_llm.json` và các bản lưu cũ;
      - 417 câu của `eval_llm`, Google, câu tham chiếu và các trang e2e (`bench/results/e2e/*/dump/states.jsonl`).

      Không có câu nào bị hỏi lại thêm.
    - E2E có LLM (`tools/e2e_check.py --llm`): 16/16 PASS. Câu của Sera vẫn là "Em xin lỗi nhé. Anh giúp em tìm cùng được không?" (1 lần tự sửa, 0 lần hỏi lại). Không còn llama-server sau khi thoát; VRAM 1,26 → 7,93 → 1,27 GB.
    - Bộ held-out D (soạn mới, có `ref`) sẽ được đo một lần sau khi mọi thay đổi xong, bằng `bench.eval_roles_heldout`, `bench.eval_roles_reask` và `bench.eval_roles_e2e`. Mặc định cả ba đọc `bench/data/translate/heldout/`.
  - **Lần 5 (2026-10-24): sửa những gì vòng rà soát thứ tư tìm thấy.** Một người rà soát soạn 80 câu khó mới (37 sai, 43 đúng, mỗi câu có `ref` do người viết). Trên bộ này, bản lần 4 hỏi lại **11/43 câu đúng**, và ghi chú hỏi lại bảo model đảo đúng hai đại từ đang đúng. Model nào làm theo ghi chú thì câu trả lời sai vẫn qua hết các điều kiện nhận, vì các điều kiện đó dùng chính bộ kiểm tra đã báo nhầm. 7B đã làm theo một lần: "Tao thua mày tâm phục khẩu phục" (You beat me fair and square) thành "Mày đánh tao một cách công bằng thôi". Người còn lại tìm thêm các lỗi kỹ thuật. Đã sửa:
    - **Bộ kiểm tra, câu đúng bị đọc sai** (giờ không xét):
      - 俺が trong mệnh đề thời gian: お前は俺が戻るまでここにいろ → "Em cứ ở yên đây cho đến khi anh quay lại". Trước đây お前は bị coi là tân ngữ như お前は俺が守る.
      - Câu tiếng Việt đổi chủ ngữ của câu gốc thành tác nhân, nguyên nhân hoặc vế kia của động từ đối ứng: "Em được anh cứu", "Em bị anh đánh", "Nhờ anh mà…", "Em hết hồn vì anh", "Tao thua mày". Nếu chính câu gốc cũng có nguyên nhân (僕のせいで, "because of you", "for you") thì câu vẫn được xét: "Tại chị mà em bị mắng" cho 僕のせいで… vẫn bị bắt.
      - Chủ đề với "thì" ("Em thì để anh đưa về"), đại từ sau "để" ở giữa câu ("Em sẽ để anh đưa em đi"; trừ "để tôi phải đợi"), và chủ ngữ sau "khi / lúc / nếu / rằng".
      - Tiếng Anh: "yourself", "your own" (đại từ phản thân, "nghĩ cho mình"), và các câu "you" chung chung ("You never know", "You win some, you lose some").
    - **Bộ kiểm tra, lỗi thật bị lọt** (giờ bắt được): "before me" và "after class" là giới từ, không phải mệnh đề bị đảo lên đầu câu; "một mình"; ", cô Mori" ở cuối câu (lời gọi); "Ms." không còn bị coi là hết câu; "Me too" / "Same to you" (chủ ngữ là người nói / người nghe).
    - **Điều kiện nhận câu hỏi lại:**
      - Lỗi chủ ngữ / tân ngữ theo đại từ rõ chỉ được coi là đã sửa khi bộ kiểm tra **vẫn xét được** các vị trí đó trong câu mới và thấy đúng. Câu mới chỉ "ít lỗi hơn" vì bộ kiểm tra không còn xét được thì không được nhận. Ví dụ 1.8B trả lời "Em sẽ là anh gửi cho" (vô nghĩa) cho "Em là người sẽ đưa anh đi": trước đây được nhận, giờ bị chặn ("slots not judged").
      - Lời gọi bằng từ của người nghe ("Bà ơi, …", "Chị ơi" bị bỏ) được thêm hoặc bỏ thì không tính là đổi số đại từ.
    - **Hỏi lại trong giới hạn thời gian của job:**
      - Pipeline đưa thời gian chờ của job cho translator (`time_budget_s`).
      - Không hỏi lại khi đã dùng quá 60% thời gian đó. Lần hỏi lại chạy quá giờ thì bị cắt ("no time"), và câu trả lời đầu vẫn được giữ, không mất cả job.
      - Câu còn sai vai mà chưa được hỏi lại ("over the cap", "no time", "failed") không được ghi vào cache tầng 2, để lần sau gặp lại có cơ hội được hỏi.
    - **Người nghe:** phần sửa sau (fix_pronouns, sửa vai) và phần hỏi lại giờ đoán người nghe như prompt và khoá cache, tức là tính cả các dòng chỉ làm ngữ cảnh. Trước đây một dòng ngữ cảnh của người thứ ba trên màn hình làm câu của Ren nói với Kenji bị sửa theo cặp của Sera.
    - Cache tầng 2 lên revision `llm-r5`.
    - Lỗi test chập chờn khi xoay log của llama-server: file log cũ còn bị khoá ngay sau khi dừng server thì giờ được thử lại vài lần rồi chép ra, không còn bị mất.
    - **Công cụ đo:**
      - File ghi `src_lang: auto` được đọc như app.
      - Trọng tài bỏ những kiểm tra chỉ dựa vào câu gốc mà báo nhầm ngay trên câu tham chiếu của chính câu đó.
      - Có thêm cột chấm **chỉ bằng câu tham chiếu**.
      - `--scenes` không còn bỏ mất bộ held-out.
      - File `eval_roles_reask_files.json` cũ (một lần chạy dở từ trước) đã được bỏ khỏi `bench/results`.
    - **Không làm**, kèm lý do:
      - Chặn câu trả lời viết lại quá nhiều chữ (so độ trùng chữ với câu đầu). Trên các câu đã lưu, nhiều câu sửa đúng cũng viết lại hẳn ("Có sao không em?" → "Anh ổn chứ?", "Mẹ luôn là người ủng hộ con mà."), nên ngưỡng nào cũng làm mất nhiều câu sửa đúng hơn số câu tệ chặn được. Các câu tệ đều đến từ lần hỏi lại câu đúng, và giờ những câu đó không còn bị hỏi lại.
      - Chặn câu chỉ đảo hai đại từ khi câu đầu có "được / bị / vì / thì…". Những câu đó giờ không bị hỏi lại ngay từ đầu, nên chặn thêm là thừa.
      - Ghi chú yêu cầu "chỉ đổi đúng các từ được nêu". Cách này đổi prompt, và chưa đo.
      - Báo các câu biến người nói / người nghe thành ngôi thứ ba ("bà ấy" cho chính bà). Chỉ báo thì app cũng không làm gì với nó.
    - Số đo trên dev (570 câu) và C (120 câu) **không đổi** so với lần 4: app làm gì đó P 1,00 / R 0,79, nhóm đại từ rõ R 0,69; C R 0,56, nhóm đại từ rõ 0,43; 0/340 câu đúng bị hỏi lại. 212 câu của vòng 2 (r2): P 1,00 / R 0,92. Trên 80 câu của vòng này (giờ đã là dữ liệu trong mẫu): trước P 0,72 / R 0,76 với 11 câu đúng bị hỏi lại; sau P 1,00 / R 0,92 với 0 câu.
    - Hỏi lại với câu trả lời đầu được đưa vào (`bench.eval_roles_reask`). Số đo trên dev là trong mẫu, câu tham chiếu suy ra từ nhãn; bộ 80 câu có câu tham chiếu do người viết.

      | | 7B lần 4 → lần 5 | 1.8B lần 4 → lần 5 |
      |---|---|---|
      | Dev: câu sai được sửa đúng hết (trên 159 câu hỏi lại) | 121 → 117 | 57 → 60 |
      | Dev: bị chặn vì "đổi nhiều hơn vai" | 35 → 32 | 99 → 94 |
      | Dev: bị chặn vì "slots not judged" (điều kiện mới) | 7 | 1 |
      | Dev: nhóm đại từ rõ, sửa đúng (trên 72 câu hỏi lại) | 55 → 51 | 31 → 31 |
      | Dev: trần (hỏi lại cả 230 câu với bộ kiểm tra hoàn hảo) | 131 → 140 | 68 → 74 |
      | 80 câu: câu sai được hỏi lại / sửa đúng | 28 / 22 → 34 / 26 | 28 / 16 → 34 / 18 |
      | 80 câu: câu đúng bị hỏi lại / bị đổi | 11 / 5 → **0 / 0** | 11 / 5 → **0 / 0** |
      | 80 câu: chrF của câu được nhận so với câu tham chiếu | −19,4 → −4,0 | −26,2 → −6,3 |
      | Câu đúng trên dev bị hỏi lại | 0 / 340 | 0 / 340 |

      Điều kiện "slots not judged" làm mất 7 câu sửa đúng của 7B trên dev, vì câu mới viết lại hẳn, ví dụ "Em ổn mà. Còn anh thì sao?". Đổi lại, nó chặn được câu vô nghĩa của 1.8B. Trên câu do chính model dịch ra thì điều kiện này chưa làm mất câu sửa nào (xem dưới).
    - Dịch mới từ đầu trên 80 câu (`bench.eval_roles_e2e`):
      - 7B hỏi lại 1 câu (trước 5, cả 5 đều là câu đúng). Câu đó là "You can't trust anyone these days" ("you" chung chung) và model giữ nguyên câu.
      - 1.8B hỏi lại 3 câu, cả 3 đều sai thật. Có 1 câu model trả lời y như cũ, 1 câu bị chặn vì không ít lỗi hơn, và câu vô nghĩa "Em sẽ là anh gửi cho" giờ bị chặn ("slots not judged").
      - Câu đúng bị đổi: 0. Câu sai vai, tắt → bật: 4 → 4 với cả hai model. Trong 4 "lỗi" của 7B, trọng tài chấm nhầm 2 câu đúng nhưng bị đảo cấu trúc, ví dụ "Ta sẽ trực tiếp dạy dỗ con" cho câu tham chiếu bị động; 2 câu còn lại là "you" chung chung, đúng hay sai còn tuỳ cách hiểu.
    - Dịch mới từ đầu trên dev (`& $py -m bench.eval_roles_e2e --dev --derive-refs --scenes all`). Có 684 câu, gồm 570 câu dev và 114 câu cảnh bench có `ref` do người viết. Bảng so trên cùng câu trả lời đầu:

      | | 7B lần 4 → lần 5 | 1.8B lần 4 → lần 5 |
      |---|---|---|
      | Câu sai vai, tắt → bật (trọng tài) | 25 → 14 ⇒ **25 → 12** | 9 → 9 ⇒ 9 → 9 |
      | Chỉ chấm bằng câu tham chiếu, tắt → bật | 13 → 8 ⇒ 13 → 8 | 4 → 4 ⇒ 4 → 4 |
      | Câu tốt hơn / tệ hơn | 12 / 0 ⇒ 14 / 0 | 0 / 0 ⇒ 0 / 0 |
      | Câu bị hỏi lại | 10 ⇒ 10 (cả 10 đều sai thật) | 5 ⇒ 5 (cả 5 đều sai thật) |
      | Hỏi lại: nhận / bị chặn / trả lời y như cũ | 4 / 5 / 1 ⇒ 6 / 3 / 1 | 0 / 1 / 4 ⇒ 0 / 1 / 4 |
      | Độ trễ thêm: trung bình mỗi câu / p50 câu bị hỏi lại | +7 ms / +0,46 s ⇒ +17 ms / +0,45 s | ~0 / +0,09 s ⇒ +1 ms / +0,10 s |

      - Hai câu được nhận thêm ở 7B là nhờ không tính lời gọi bị bỏ: "Chị ơi, em đang làm gì vậy?" → "Nè, chị đang làm gì vậy?".
      - Loại lỗi chủ ngữ / tân ngữ theo đại từ rõ vẫn **chưa sửa được câu nào** trên câu do chính model dịch ra. Loại này được hỏi lại ở 1 câu (7B) và 5 câu (1.8B), đều là câu sai thật. Model trả lời y như cũ, hoặc câu trả lời bị chặn vì thêm "anh ấy".
      - `bench/results/eval_roles_reask_dev.json` và `eval_roles_e2e_dev.json` giờ là kết quả của lần 5. Các bảng lần 4 ở trên giữ lại để so sánh.
    - Kiểm tra hồi quy:
      - `bench/eval_llm.py` với cả hai model: cả 824 câu trả lời (cảnh và đoạn) giống hệt lần đo trước. Số request như cũ (157 / 169), các chỉ số cũng vậy: 7B sạch 119/120, lỗi vai 3/114, chrF 73,2; 1.8B sạch 94/115, lỗi vai 3/114, chrF 66,2. Độ trễ p50 649 ms (7B) và 123 ms (1.8B). Mỗi model vẫn có 1 câu bị hỏi lại, cùng câu như trước, và đều được sửa đúng.
      - `tools/e2e_check.py --llm`: 16/16 PASS trong 85 s. Câu của Sera vẫn là "Em xin lỗi nhé. Anh giúp em tìm cùng được không?" (1 lần tự sửa, 0 lần hỏi lại). VRAM 1,13 → 7,53 → 1,21 GB. Không còn llama-server sau khi thoát.
      - Không có `--llm` (cảnh mixed): PASS, 10/10.
      - Unit test: lúc đó đạt hết.
    - **Đo held-out D** (khi bộ D đã nằm trong `bench/data/translate/heldout/`; mỗi lệnh đo đúng một lần, không chỉnh luật theo kết quả):
      - `& $py -m bench.eval_roles_heldout --show`
      - `& $py -m bench.eval_roles_reask --models quality,light`
      - `& $py -m bench.eval_roles_e2e --models quality,light`

      Không thêm `--dev`, `--derive-refs` hay `--scenes`, để kết quả là số của riêng bộ D. Kết quả lưu ở `bench/results/eval_roles_heldout.json`, `eval_roles_reask.json` và `eval_roles_e2e.json`.
    - **Kết quả held-out D (2026-10-24, đo một lần, sau mọi thay đổi):**
      - Bộ D gồm 120 câu, 49 câu sai vai, mỗi câu có bản dịch đúng ghi vai từng đại từ. 2 người soạn mà không xem code hay các bộ cũ.
        - D1: 60 câu tập trung vào đại từ rõ.
        - D2: 60 câu thoại thông thường.
      - Log của lần đo: `bench/results/heldout_d_*.log`.

      **1. Bộ kiểm tra trên câu có nhãn:**
      - Độ chính xác 1,00: 0 câu đúng bị báo, 0 câu bị tự sửa trên 71 câu. Độ phủ 0,43.
      - App thật sự làm gì đó: R **0,41**. Nếu tắt phần hỏi lại mới thì chỉ còn **0,16**. Có 12 câu sai chỉ được bắt nhờ phần mới, câu nào cũng là lỗi thật ("Ta mong chờ ngày **ta** vượt qua con", "**Con** tự hào về bố lắm").
      - Nhóm đại từ rõ: app làm R 0,51 (trên bộ C trước đây là 0,14). Nhóm còn lại: 0,14.

      **2. Hỏi lại trên câu sai có sẵn** (đưa câu sai của bộ D làm câu trả lời đầu, rồi chạy đường hỏi lại thật):

      | | 7B | 1.8B |
      |---|---|---|
      | Câu sai bị hỏi lại | 20 | 20 |
      | Sửa đúng / tệ hơn | **16 / 0** | **9 / 0** |
      | Câu trả lời mới bị loại | 4 | 11 (chủ yếu đổi nhiều hơn phần vai) |
      | Câu đúng bị hỏi lại | 0 | 0 |
      | Thời gian thêm mỗi lần hỏi lại (p50) | 0,57 s | 0,11 s |

      Câu bản 1.8B trả lời lại thường đổi cả câu chữ (chrF so với bản đúng −12).

      **3. Dịch mới từ đầu bằng model thật** (phần sửa xưng hô bật và tắt, cùng câu trả lời đầu):
      - Với cặp xưng hô cho sẵn, model **ít khi sai vai**: 7B sai 4/120 câu, 1.8B sai 2/120 theo bộ chấm. Đọc lại thì 1 câu mỗi model là bộ chấm nhầm: "Hôm qua chị đã thay em làm…" là đúng. Vậy thực tế 7B sai 3, 1.8B sai 1.
      - Bật hay tắt cho kết quả như nhau: 4 → 4 và 2 → 2, không câu nào tệ hơn.
      - Chỉ có 1 lần hỏi lại (7B), model trả lại y câu cũ. Thời gian thêm trung bình khoảng 5 ms mỗi câu.
      - Các lỗi còn lại đều là kiểu **"ai làm ơn cho ai"**:
        - 作ってくれたのか → "**Anh** làm bữa trưa cho em à?" (đúng là Sera làm).
        - 入れてくれる？ → "**con** có thể cho thêm…".
        - 見なかった？ → "**con** có thấy mèo…".
        - 好き (1.8B): "**Anh** thích điều đó của em".
      - Bộ kiểm tra chưa nhận ra 〜てくれた quá khứ, câu hỏi "did you see" và câu cảm xúc (cố ý bỏ qua). Riêng câu 〜てくれる？ được hỏi lại nhưng model không đổi.
      - Kết luận: phần hỏi lại mới an toàn (0 lần đụng vào câu đúng) và sửa được phần lớn câu sai khi model thật sự sai theo kiểu đó. Nhưng trên bản dịch thật của model, kiểu lỗi này hiếm, nên hiệu quả nhìn thấy được là nhỏ. Lỗi còn lại chủ yếu nằm ở động từ "làm ơn" (〜てくれた/〜てくれる).
  - **Lần 6 (2026-10-24): bắt và hỏi lại lỗi "ai làm ơn cho ai".** Trên bộ D, lỗi vai còn lại của chính model đều thuộc kiểu này: 弁当作ってくれたのか (anh trai nói với Sera) ra "**Anh** làm bữa trưa cho em à?" (người làm là Sera), còn 唐揚げ入れてくれる？ ra "**con** có thể cho thêm…" (có bị hỏi lại, nhưng model trả lại y câu cũ).
    - **Dữ liệu:**
      - Bộ D đã bị xem nên chuyển sang `bench/data/translate/roles_dev/`. Thư mục `heldout/` giờ trống, chờ bộ E. Kết quả đo bộ D được giữ ở `bench/results/eval_roles_heldout_D.json`, `eval_roles_reask_D.json` và `eval_roles_e2e_D.json`, vì lần đo bộ E sẽ ghi đè các file không có hậu tố.
      - Thêm vào dev 80 câu của vòng rà soát 4 (`roles_labelled_r3.json`) và 58 câu "ai làm ơn cho ai" (`roles_benefactive_dev.json`). Bộ 58 câu do người viết luật soạn trước khi viết luật: có câu đúng, câu sai, cả hai chiều, người thứ ba làm, câu hỏi, câu nhờ, quá khứ và tương lai, cùng các câu gần giống dễ báo nhầm.
      - Dev giờ có 828 câu, trong đó 340 câu sai.
    - **Câu gốc** (`pronoun_roles._ja_favours`, `_en_favours`):
      - 〜てくれる / 〜てくださる: chủ ngữ làm việc đó cho người nói; không nêu ai thì là người nghe.
      - 〜てもらう / 〜ていただく: người nói được người ở vế に làm cho; không nêu ai thì là người nghe.
      - 〜てあげる / 〜てやる / 〜て差し上げる: người nói làm cho người nghe, kể cả khi người nói tự xưng bằng vai (お母さんが作ってあげる).
      - Cũng nhận お〜いただく / お〜くださる (お越しいただき) và động từ chính くれる / やる (お前がくれたお守り, この花、お前にやるよ).
      - Chỉ xét khi chắc chắn:
        - trước tín hiệu không có tên, người thứ ba, từ chỉ vai ngoài cặp, hay danh từ khác làm chủ ngữ (母さんが作ってくれた nói với bạn, 彼ね…作ってくれるの, ケンジくんが…);
        - câu không phải điều kiện, trích dẫn hay kể lại (〜てくれたら, 〜てくれるって, 『買ってやる』って);
        - nếu không nêu người làm, câu phải cho thấy đó là người nghe: câu hỏi (作ってくれたのか), lời cảm ơn, xin lỗi hay "may quá" (〜てくれてありがとう, 助かった, もらって悪いな), hoặc gọi người nghe ở đầu câu (お兄ちゃん、迎えに来てくれたの？).
      - Không xét もらう trong câu hỏi (手伝ってもらった？: người nghe mới là người được giúp), あげる trong câu hỏi không phải lời mời (作ってあげたの？), và 〜てあげて.
      - Tiếng Anh: "thanks for …ing", "you made / did you … / can you … for me", "I … / let me … for you". Không xét câu có người thứ ba, "we", trích dẫn, kể lại hay "if".
      - Sửa luôn một lỗi cũ: ようこそお越しくださいました (câu kính ngữ kể việc người nghe đã đến) từng bị coi là lời nhờ.
    - **Bản dịch** (`_favour_slots`):
      - Người làm là chủ ngữ đứng đầu mệnh đề có "cho / giúp / giùm / dùm / hộ / thay / tặng"; người được làm cho đứng ngay sau các từ đó: "Em làm cơm hộp cho anh à?", "Hôm qua chị đã làm thêm giờ thay em". Dạng đảo "Em lúc nào cũng được cô giúp đỡ" cũng được xét.
      - Không coi là dấu hiệu: "cho phép / cho nên / cho đến / cho dù", "làm cho anh vui" (nghĩa là gây ra), "thay đổi / thay vì", "ủng hộ / hộ chiếu".
      - Không lấy chủ ngữ khi ở giữa có người khác, mệnh đề quan hệ ("Em sẽ nấu món cà ri mà chị thích cho chị"), lời kể, điều kiện, bị động, "và / với" hay "cảm ơn" ("Tôi cảm ơn anh đã làm thay ca cho tôi").
      - Không xét câu dịch có hai hành động ngược chiều nhau, câu gốc có nhiều câu mà bản dịch gộp lại, câu có trích dẫn hay "tự".
      - Lời nhờ 〜てくれる？ cũng được xét theo cách này, nên "Chị nấu cơm cho em được không?" (sai) giờ bị bắt cả chữ "Chị", không chỉ chữ "em".
    - **App làm gì:**
      - Lỗi loại này chỉ được **hỏi lại**, không bao giờ tự sửa. Câu có lỗi loại này cũng không bị tự sửa một nửa ở chỗ khác; cả câu được hỏi lại.
      - Ghi chú hỏi lại nói rõ ai làm cho ai và cấu trúc cần viết: `In "弁当作ってくれた" Sera does it for Kaito: Sera is the one who does it ("em"), Kaito the one it is done for ("anh"); write "em … cho anh" (or "em … giúp anh"), not "anh … cho em". "Anh" there means Sera, write "Em"; …`. Với lời nhờ, ghi chú viết `Mom is asked to do it for Yuta …`.
      - Điều kiện nhận câu trả lời mới vẫn như cũ: ít lỗi hơn, giữ số đại từ và các hành động, không thêm từ ngoài ghi chú, trong giới hạn thời gian. Có hai điểm mới:
        - Vì ghi chú bảo viết "mẹ … cho con", câu trả lời được thêm đại từ, nhưng chỉ đúng bằng số vị trí "ai làm cho ai" mới mà bộ kiểm tra xét được. Ví dụ "con có thể cho thêm gà chiên vào" → "mẹ có thể cho con gói thêm gà chiên" được nhận.
        - Phải còn ít nhất một vị trí "ai làm cho ai" được xét, vì người làm và người được làm cho thuộc cùng một hành động. Câu trả lời mà bộ kiểm tra không còn thấy hành động đó thì bị chặn ("slots not judged").
      - Cache tầng 2 lên revision `llm-r6`.
    - **Số đo trên dev** (828 câu, 340 sai). Đây là số **trong mẫu**, đo bằng `& $py -m bench.eval_roles_heldout --dev --derive-refs`:

      | | Trước lần 6 | Sau lần 6 |
      |---|---|---|
      | Bộ kiểm tra bắt được: P / R | 1,00 / 0,72 (246 câu) | 1,00 / 0,78 (266 câu) |
      | App làm gì đó (sửa hoặc hỏi lại): P / R | 1,00 / 0,72 (244 câu) | 1,00 / 0,78 (264 câu) |
      | Câu "ai làm ơn cho ai" (60 sai, 99 đúng): app làm gì đó | 30 | 50 (R 0,83) |
      | Câu đúng bị báo / bị tự sửa (trên 488) | 1 ("sẽ", đã biết) / 0 | 1 / 0 |
      | Tự sửa trên câu sai (đúng hoàn toàn) | 25 (22) | 25 (22) |

      Độ phủ "app làm gì đó" theo từng bộ:
      - 570 câu cũ: 0,79 → 0,81.
      - Bộ D: 0,41 → 0,45.
      - 80 câu vòng 4: 0,92 → 0,92.
      - 58 câu tự soạn: 0,33 → 0,92. Số này cao vì người soạn câu cũng là người viết luật.
    - **Rà soát câu trả lời thật đã lưu:** 3.030 cặp (câu, bản dịch) khác nhau, lấy từ mọi lần chạy trong `eval_llm.json`, `eval_roles_e2e*.json`, `eval_roles_reask*.json` và các trang e2e.
      - Luật mới hỏi lại thêm 33 câu, **cả 33 đều sai thật**. Trong đó có 2 câu do chính model dịch ra: câu của bộ D, và "anh có thể chụp ảnh cho em" (Ren nhờ người lạ chụp ảnh). Số còn lại là câu sai có nhãn và câu trả lời sai lúc hỏi lại.
      - Không có câu đúng nào bị hỏi lại.
      - Bản đầu của luật từng báo nhầm câu đúng "Em sẽ nấu món cà ri mà chị thích cho chị" (mệnh đề quan hệ). Lỗi này đã được sửa và có test.
    - **Hỏi lại với câu trả lời đầu được đưa vào** (`& $py -m bench.eval_roles_reask --dev --derive-refs`). Câu tham chiếu suy ra từ nhãn với 570 câu cũ, do người viết với các câu còn lại:

      | | 7B | 1.8B |
      |---|---|---|
      | Câu sai được hỏi lại / sửa đúng hết | 241 / 176 | 241 / 96 |
      | Bị chặn | 64 | 143 |
      | Câu "ai làm ơn cho ai" (60 sai): hỏi lại / sửa đúng | 44 / 27 | 44 / 18 |
      | Riêng 570 câu cũ: sửa đúng, lần 5 → lần 6 | 117 → 120 | 60 → 61 |
      | Câu đúng bị hỏi lại | 0 / 488 | 0 / 488 |
      | Độ trễ một lần hỏi lại p50 | 0,53 s | 0,10 s |

      Ở 7B, phần lớn câu "ai làm ơn cho ai" bị chặn là câu trả lời đúng vai nhưng thêm hoặc bỏ một đại từ ở vế khác. Ví dụ "Con sẽ làm cho mẹ, đợi một chút nhé." được trả lời "Mẹ sẽ làm cho con đây, **con** đợi một chút nhé.". Điều kiện này vẫn giữ nguyên, để ưu tiên không làm hỏng câu.
    - **Dịch mới từ đầu** (`& $py -m bench.eval_roles_e2e --dev --derive-refs --scenes all`): 942 câu, gồm 828 câu dev và 114 câu cảnh bench có `ref`. So trên cùng câu trả lời đầu:

      | | 7B | 1.8B |
      |---|---|---|
      | Câu sai vai, tắt → bật (trọng tài) | 34 → 19 | 17 → 17 |
      | Chỉ chấm bằng câu tham chiếu, tắt → bật | 19 → 13 | 6 → 6 |
      | Câu "ai làm ơn cho ai" (178 câu), tắt → bật | 11 → 4 | 4 → 4 |
      | Câu tốt hơn / tệ hơn | 16 / 0 | 0 / 0 |
      | Câu bị hỏi lại (tỉ lệ) | 14 (1,5%) | 11 (1,2%) |
      | Hỏi lại: nhận / bị chặn / trả lời y như cũ | 8 / 4 / 2 | 0 / 4 / 7 |
      | Độ trễ thêm: trung bình mỗi câu / p50 câu bị hỏi lại | +6 ms / +0,47 s | +7 ms / +0,10 s |

      - **Hai lỗi "ai làm ơn cho ai" của 7B trên bộ D giờ được sửa ngay trên bản dịch của chính nó**, nhờ ghi chú mới:
        - "Anh làm bữa trưa cho em à? Cảm ơn em nhiều nhé, Sera." → "**Em** đã làm bữa trưa cho **anh** à? Cảm ơn em nha, Sera."
        - "…ngày mai con có thể cho thêm gà chiên vào được không ạ?" → "…ngày mai, **mẹ** có thể cho **con** gói thêm gà chiên không?". Với ghi chú cũ, model trả lại y câu cũ.
      - Trên 120 câu của bộ D, lỗi của 7B giảm từ 4 xuống 2. Hai câu còn lại là お母さん、僕の猫見なかった？ ("con có thấy mèo…", câu này không có 〜てくれる), và một câu đúng mà trọng tài chấm nhầm.
      - 1.8B chỉ bị hỏi lại 2 câu loại này ("Tối nay con sẽ nấu món con yêu thích cho mẹ" cho "I'll cook your favourite for you tonight"), và cả hai lần model trả lại y câu cũ.
      - Riêng trên 684 câu của lần 5: 7B 26 → 13 (lần 5 đo được 25 → 12), 1.8B 10 → 10 (lần 5: 9 → 9). Không câu nào tệ hơn khi so trên cùng câu trả lời đầu.
    - **Kiểm tra hồi quy:**
      - `bench/eval_llm.py` với cả hai model: **mọi câu trả lời** (cảnh và đoạn) và mọi chỉ số giống hệt lần đo của lần 5. Số request vẫn là 157 / 169, và mỗi model vẫn có đúng 1 câu bị hỏi lại như trước. Độ trễ p50 630 ms (7B) và 122 ms (1.8B).
      - `tools/e2e_check.py --llm`: 16/16 PASS trong 80 s. Câu của Sera vẫn là "Em xin lỗi nhé. Anh giúp em tìm cùng được không?" (1 lần tự sửa, 0 lần hỏi lại). VRAM 1,06 → 7,48 → 1,04 GB. Không còn llama-server sau khi thoát.
      - Unit test: lúc đó đạt hết.
    - **Chưa làm được:**
      - Câu dịch không có "cho / giúp / thay…" thì không xét được, ví dụ "Em đến đón anh à?" cho 迎えに来てくれたの？, hay "Hả, em bao anh à?" cho おごってくれるの？.
      - Câu hỏi về việc người nghe đã làm mà câu gốc không có 〜てくれる (お母さん、僕の猫見なかった？ → "con có thấy mèo…", lỗi thứ ba của bộ D) vẫn chưa được xét.
      - Câu có người thứ ba được nêu tên (ケンジくんが…てくれた), và câu kể không có câu hỏi hay lời cảm ơn (来てくれたね), cố ý bị bỏ qua để tránh báo nhầm.
    - **Đo held-out E:** xem lệnh ở cuối lần 7 bên dưới.
  - **Lần 7 (2026-10-25): sửa theo rà soát lần 6.** Người rà soát soạn bộ 125 câu "ai làm ơn cho ai" (`roles_labelled_r4.json`, 50 sai, 75 đúng, có `ref`) và tìm ra luật lần 6 làm hỏng câu đúng:
    - Trên bộ r4, 12 câu đúng bị hỏi lại và **3 câu đúng bị tự sửa thành sai nghĩa**. Ví dụ 手伝ってくれて助かったよ、今度は俺が手伝ってやる。: "Cảm ơn em đã giúp anh, …" bị sửa thành "Cảm ơn **anh** đã giúp **em**, …", vì mọi chữ "giúp" trong câu bị xét theo lời hứa giúp lại ở vế sau.
    - Với bản dịch mới của 1.8B, 4 câu bị tự sửa sai kiểu đó ("Cảm ơn bạn đã giúp đỡ…" thành "Cảm ơn **anh** đã giúp đỡ…", tức Kaito tự cảm ơn mình).
    - Còn có câu điều kiện ("Anh giúp em một tay, em sẽ mời anh uống nước" cho 手伝ってくれたら、ジュースおごってあげる bị sửa thành "Em giúp anh…"), người sở hữu ("mẹ ủi áo sơ mi **của con** giúp con" bị coi "con" là người làm), で của 自分でやる và に của 一緒にやろう bị đọc là 〜てやる, lời cảm ơn rồi việc của chính người nói (迎えに来てくれてありがとう、荷物持つね), "cho kịp" và "mượn".
    - **Bộ kiểm tra (`pronoun_roles.py`):**
      - Câu gốc có ơn đi **hai chiều** (〜てくれて…〜てあげる, 〜てくれたら…〜てあげる, "thanks for … I'll …"): không vị trí giúp / cho / "ai làm cho ai" nào bị xét theo cả câu nữa.
      - Câu gốc có **nhiều vế có hành động** (nối bằng 、): mỗi vế câu dịch được xét theo đúng vế ở cùng chỗ trong câu gốc. Điều kiện: hai bên có cùng số vế (không tính tên, tiếng gọi, chủ đề, thán từ, lời cảm ơn hay "may quá" đứng riêng), lời cảm ơn nằm ở cùng chỗ, và không vế dịch nào sau vế đầu mở bằng "vì / nếu / khi…" (dấu hiệu đã đảo vế). Không khớp thì không xét. Mệnh đề quan hệ trên chủ đề (君が貸してくれたノート、…) và nguyên nhân với おかげで được tính chung với vế sau. Lỗi tìm theo cách này chỉ được **hỏi lại**, không tự sửa. Nhờ vậy câu sai hai chiều vẫn bị bắt đúng vế: "Cảm ơn anh đã giúp em, lần sau **anh** sẽ giúp **em** nhé".
      - Điều kiện: không xét phần điều kiện của vế dịch (sau "nếu / hễ / giá mà / giả sử / lỡ mà", trước "thì", "Anh **mà** giúp em **là**…"). "khi / lúc" không tính là điều kiện ("Lúc về em sẽ mua kem cho chị" vẫn được xét).
      - Người sở hữu không phải chủ ngữ ("áo sơ mi của con giúp con", "em trai chị giúp chị").
      - で chỉ là thể て sau ん / い (読んで, 急いで), nên 自分でやる / 後でやる không còn là 〜てやる. やる / あげる sau に chỉ tính khi người nhận là người nghe (お前にやる, không phải 一緒にやろう).
      - Không coi là "cho người được làm": "cho kịp / cho xong / cho vui…", "cho" trước một vật ("cho hộp cơm") nếu không ở cuối vế, chủ ngữ của động từ ngược chiều (mượn, xin, vay, nhận).
      - Không có khung "làm ơn": 〜てやるな (cấm), 〜てくれてありがとう、って伝えて (cảm ơn hộ người khác), 助けてくれたのはケンジくん, 作ってくれた人, "I've been waiting for you", "too old for you", "ask for me".
      - Bắt thêm (chỉ hỏi lại): これ、あげる; この前あげたマフラー、使ってる？; tên gọi ở đầu câu hỏi hay lời cảm ơn là người nghe (先輩、…ありがとうございます, たくみくん、…くれたの？); "nhận … từ anh" (người nhận là chủ ngữ); "Hộp cơm anh làm cho em ngon lắm" (mệnh đề quan hệ, không phải "làm cho em ngon").
      - Ghi chú hỏi lại trích đúng từ trọn vẹn: 送ってくれた thay vì ちゃんが送ってくれた, おごってあげる thay vì ュースおごってあげる. Câu tiếng Anh trích cả vế: "Can you cover for me tomorrow" thay vì "Can you".
      - Kiểm tra một câu 600 từ không dấu câu mất khoảng 60 ms (trước đây đo được 52 ms); câu thường khoảng 0,2 ms.
    - **Điều kiện nhận câu trả lời khi hỏi lại "ai làm cho ai"** (`llm_local._reask_roles`, `same_shape`): "giúp" được đổi thành "cho" (ghi chú đưa cả hai cấu trúc), và được bỏ từ sai mà ghi chú đã nêu ("Thưa cô, em cho cô mượn…" → "Cô ơi, cho em mượn…"). Khi số đại từ giảm, câu mới phải còn ít nhất một vị trí "ai làm cho ai" được xét ("Mẹ ơi, gà chiên nhé?" vẫn bị chặn).
    - **Bench:**
      - `eval_roles_reask` in thêm số câu đúng **bị tự sửa** mà không hỏi lại. Việc đổi tôi / bạn sang từ của cặp không tính là tự sửa.
      - Trọng tài không tính vị trí "ai làm cho ai" mà chính câu tham chiếu bị xét sai. Trước đây vị trí đó có thể bị một kiểm tra khác che mất.
      - `eval_roles_e2e --show-favour` in mọi câu "ai làm ơn cho ai" (tắt / bật / ref) để người đọc lại, vì trọng tài đếm thiếu lỗi khi câu dịch đổi cách nói.
      - Bộ r4 đã được đọc khi sửa, nên chuyển vào `roles_dev/`. Dev giờ có 953 câu, 390 câu sai.
    - Cache tầng 2 lên revision `llm-r7`.
    - **Không làm, và lý do:**
      - Chặn câu trả lời đổi nội dung bằng độ trùng từ (bỏ đại từ). Trên chính lần chạy của người rà soát, các câu sửa đúng có độ trùng chỉ 0,25–0,46 ("Để anh mang giúp em."), còn các câu trôi nghĩa mà họ nêu lại ở 0,67–0,86 ("Vâng, quà đây", "gấp quần áo"). Một ngưỡng như vậy sẽ chủ yếu bỏ đi câu sửa đúng.
      - Coi chủ ngữ của câu dịch không có "cho / giúp" là người làm cho 〜てくれた ("Bà ơi, cháu đã mang đồ giặt vào"). Câu dịch thường đổi chiều mà vẫn đúng ("Em có quà à?", "Em vui quá"), nên dễ báo nhầm.
    - **Bộ kiểm tra trên dev** (`& $py -m bench.eval_roles_heldout --dev --derive-refs`, trong mẫu):

      | | Lần 6 | Lần 7 |
      |---|---|---|
      | 828 câu dev cũ: app làm gì đó P / R | 1,00 / 0,78 (264) | 1,00 / 0,78 (265) |
      | Bộ r4 (125 câu): P / R | 0,77 / 0,78 (12 câu đúng bị hỏi lại, 3 bị tự sửa) | **1,00 / 0,90** (0 / 0) |
      | Cả 953 câu: P / R | – | 1,00 / 0,80 (310 / 390) |
      | Câu "ai làm ơn cho ai" (105 sai, 168 đúng): P / R | – | 1,00 / 0,87 |
      | Tự sửa trên câu sai (đúng hoàn toàn) / trên câu đúng | 25 (22) / 0 | 27 (23) / 0 |

      - Theo từng bộ (app làm gì đó): 570 câu cũ 0,81; D 0,45; r3 0,92; 58 câu tự soạn 0,92; r4 0,90.
      - 5 câu sai của r4 còn sót đều là loại cố ý bỏ qua: câu mỉa (余計なことしてくれたな, 朝から仕事増やしてくれるね, よくも…食べてくれたな) và câu điều kiện (〜てくれたら, 〜ていただけると).
      - Rà soát 3.741 cặp (câu, bản dịch, cặp xưng hô) của mọi lần chạy đã lưu. Có 18 câu app hỏi lại qua vị trí giúp / cho / "ai làm cho ai" mà không phải câu sai có nhãn, và **cả 18 đều sai thật**.
    - **Hỏi lại với câu trả lời đầu được đưa vào** (`& $py -m bench.eval_roles_reask --dev --derive-refs`):

      | | 7B | 1.8B |
      |---|---|---|
      | 828 câu cũ: câu sai sửa đúng, lần 6 → lần 7 | 176 → 181 | 96 → 104 |
      | 58 câu tự soạn: sửa đúng | 14 → 17 | 8 → 14 |
      | Bộ r4: hỏi lại / sửa đúng | 43 / 27 (lần 6 khi rà soát: 37 / 19) | 43 / 20 (37 / 12) |
      | Bộ r4: câu đúng bị hỏi lại / bị tự sửa | 0 / 0 (lần 6: 10 / 3) | 0 / 0 |
      | Cả 953 câu: câu sai hỏi lại / sửa đúng / tệ hơn | 285 / 208 / 0 | 285 / 124 / 0 |
      | Câu đúng bị hỏi lại / bị tự sửa (trên 563) | 0 / 0 | 0 / 0 |
      | Độ trễ một lần hỏi lại p50 | 0,49 s | 0,10 s |

      - Hai câu ơn hai chiều mà 7B dịch sai đã được sửa đúng từng vế, ví dụ "Cảm ơn anh đã giúp em, lần sau **anh** sẽ giúp **em** nhé" → "…lần sau **em** sẽ giúp **anh** nhé".
      - Câu trả lời của 7B vẫn có thể đổi nội dung mà bộ kiểm tra vai không thấy: "cà ri" thành "cá riêu", はい thành "Vâng".
      - Một câu nhận sai có từ lần 5, không thuộc loại "ai làm cho ai": HB31 "mẹ quên bỏ hộp cơm của mẹ" → "**con** quên mang hộp cơm của con" (sửa đúng người sở hữu nhưng đổi người quên).
    - **Dịch mới từ đầu** (`& $py -m bench.eval_roles_e2e --dev --derive-refs --scenes all --show-favour`): 1.067 câu, gồm 953 câu dev và 114 câu cảnh bench. So trên cùng câu trả lời đầu:

      | | 7B | 1.8B |
      |---|---|---|
      | Câu sai vai, tắt → bật (trọng tài) | 40 → 24 | 22 → 22 |
      | Riêng 941 câu đã đo ở lần 6, tắt → bật: lần 6 / lần 7 | 34 → 19 / 34 → 18 | 17 → 17 / 18 → 18 |
      | Câu "ai làm ơn cho ai" (292 câu), tắt → bật | 15 → 7 | 8 → 8 |
      | Câu tốt hơn / tệ hơn | 17 / 0 | 0 / 0 |
      | Câu bị hỏi lại: nhận / bị chặn / trả lời y như cũ | 16: 9 / 5 / 2 | 11: 0 / 4 / 7 |
      | Câu bị tự sửa (đều đúng) | 8 | 0 |
      | Độ trễ thêm: trung bình mỗi câu / p50 câu bị hỏi lại | +7 ms / +0,43 s | 0 ms / +0,08 s |

      - Cả 9 câu 7B được nhận đều đúng vai. Trong đó có hai lỗi "ai làm ơn cho ai" của bộ D (như lần 6) và lời nhờ người lạ chụp ảnh ("Xin lỗi em, anh có thể chụp ảnh cho em" → "Xin lỗi, em có thể chụp ảnh cho anh được không?").
      - Trên 125 câu r4, 7B được hỏi lại 2 câu (R4-68/69, "Em lúc nào cũng chỉ nhận sự giúp đỡ từ anh"). Câu trả lời mới vẫn sai chiều ("Em … chỉ biết nhờ anh giúp") nên bị chặn đúng. Không câu đúng nào bị hỏi lại hay bị sửa.
      - 1.8B có 3 câu "tệ hơn" giữa hai lượt chạy. Cả 3 đều do câu trả lời đầu của hai lượt khác nhau (77 câu khác nhau), không do phần sửa vai: trên cùng câu trả lời đầu thì 0 câu tệ hơn.
      - **Đọc lại bằng mắt** các câu "ai làm ơn cho ai" của r4 (`--show-favour`): 7B còn sai ở R4-32, R4-47/48, R4-71, R4-82, R4-85, R4-115. Không câu nào bị bắt, vì đều thuộc loại cố ý bỏ qua để tránh báo nhầm:
        - 〜てあげて cho người thứ ba (お父さんにも…残しておいてあげてね → "Mẹ nhớ để lại…");
        - câu dịch không có "cho / giúp" ("Bà ơi, cháu đã mang đồ giặt vào nhà rồi ạ");
        - câu hỏi với あげた;
        - もらった không nêu người làm ("Đây là thứ anh đã làm cho em");
        - câu mỉa;
        - người thứ ba trong câu tiếng Anh ("My brother fixed my bike for me" → "Anh đã sửa xe cho em").
    - **Kiểm tra hồi quy:**
      - `bench/eval_llm.py` với cả hai model: mọi câu trả lời (cảnh và đoạn) và mọi chỉ số giống hệt lần 6. Mỗi model vẫn có đúng 1 câu bị hỏi lại, cùng câu như trước. Độ trễ p50 584 ms (7B) và 114 ms (1.8B).
      - `tools/e2e_check.py --llm`: 16/16 PASS. Câu của Sera vẫn là "Em xin lỗi nhé. Anh giúp em tìm cùng được không?" (1 lần tự sửa, 0 lần hỏi lại). VRAM 1,05 → 7,33 → 1,05 GB. Không còn llama-server sau khi thoát.
      - Không có `--llm` (cảnh mixed): PASS, 10/10.
      - Unit test: lúc đó đạt hết.
    - **Đo held-out E** (khi bộ E đã nằm trong `bench/data/translate/heldout/`; mỗi lệnh chạy đúng một lần, không chỉnh luật theo kết quả):
      - `& $py -m bench.eval_roles_heldout --show`
      - `& $py -m bench.eval_roles_reask --models quality,light --show`
      - `& $py -m bench.eval_roles_e2e --models quality,light --show-favour`

      Không thêm `--dev`, `--derive-refs` hay `--scenes`. Người đọc lại các câu `favour` mà lệnh cuối in ra, vì trọng tài đếm thiếu lỗi trên câu dịch đổi cách nói. Kết quả lưu ở `bench/results/eval_roles_heldout.json`, `eval_roles_reask.json`, `eval_roles_e2e.json`.
    - **Kết quả held-out E (2026-10-25, đo một lần, sau mọi thay đổi của lần 7):**
      - Bộ E gồm 120 câu, 49 câu sai vai, 78 câu thuộc loại "làm ơn". 2 người soạn mà không xem code hay các bộ cũ.
        - E1: 60 câu "ai làm ơn cho ai".
        - E2: 60 câu thoại thông thường.
      - Log: `bench/results/heldout_e_*.log`.

      **1. Bộ kiểm tra:**
      - App xử lý (tự sửa hoặc hỏi lại): P **0,97**, R **0,69**. Nếu bỏ luật "làm ơn" thì R chỉ còn 0,43; có 13 câu sai chỉ được bắt nhờ luật mới.
      - Riêng nhóm "làm ơn": R 0,72, không báo nhầm câu nào.
      - Tự sửa: 2 câu sai, cả 2 sửa đúng; 0 câu đúng bị đổi.
      - **1 lần báo nhầm**, ở luật đại từ rõ của vòng trước (không phải luật "làm ơn"): 「部長には私から話しておくから」 → "tôi sẽ nói chuyện với trưởng phòng cho em". Câu này đúng, nhưng 私から ("từ tôi") bị hiểu là tân ngữ. Khi hỏi lại, câu trả lời mới bị loại, nên câu hiển thị không đổi. Lỗi này được sửa ở bước hồ sơ Blue Archive, có test.

      **2. Hỏi lại trên câu sai có sẵn:**

      | | 7B | 1.8B |
      |---|---|---|
      | Câu sai bị hỏi lại | 32 | 32 |
      | Sửa đúng / tệ hơn | **21 / 0** | 12 / 0 |
      | Riêng câu "làm ơn": hỏi / sửa đúng | 27 / 17 | 27 / 10 |
      | Câu đúng bị hỏi lại / bị đổi | 1 / 0 | 1 / 0 |
      | Thời gian thêm mỗi lần hỏi lại (p50) | 0,51 s | 0,11 s |

      **3. Dịch mới từ đầu:**
      - Với cặp xưng hô cho sẵn, **bản 7B không sai vai câu nào** trên 120 câu, kể cả khi tắt phần sửa xưng hô. Bản 1.8B sai 2 câu, và bật hay tắt cho kết quả như nhau.
      - Mỗi model chỉ có 1 lần hỏi lại, và câu trả lời mới đều bị loại. Không câu nào tệ hơn, thời gian thêm khoảng 4 ms mỗi câu.
      - Vậy trên bộ E, hiệu quả "dịch thật" không đo được, vì model gần như không mắc kiểu lỗi này. Trên dữ liệu phát triển (đo trong mẫu), bản 7B giảm từ 40 xuống 24 lỗi vai, riêng câu "làm ơn" giảm từ 15 xuống 7.
- **Hạn chế đã biết:**
  - Một câu đúng mà bộ kiểm tra đọc sai vẫn có thể bị hỏi lại. Nếu model làm theo ghi chú (đảo đúng hai đại từ được nêu), câu trả lời sai vẫn qua các điều kiện nhận, vì chúng dùng chính bộ kiểm tra đó. Cách phòng duy nhất là bộ kiểm tra không báo nhầm. Lần 5 đã bỏ các dạng báo nhầm đã biết, nhưng dạng mới chỉ lộ ra trên dữ liệu mới.
  - "You" chung chung ngoài vài thành ngữ ("You can't trust anyone these days" → "…em không thể tin ai…") vẫn bị coi là người nghe và bị hỏi lại; model thường giữ nguyên câu.
  - Lỗi sai vai ở động từ thường mà câu gốc không nói rõ ai làm (ai mua, ai làm vỡ, ai cho vay) vẫn còn và app không tự thấy: trên tập test của bộ cảnh thoại là 1/43 câu với mỗi model, cả trước và sau khi sửa.
  - Câu kể lại (「ケンジくんに謝れって言われた」) không được kiểm tra từ câu gốc, nên nếu model đảo ai xin lỗi ai thì app không sửa; bench chỉ thấy lỗi đó nhờ câu tham chiếu.
  - Câu dịch đổi cấu trúc quanh "sẽ" ("I'll have it ready by Friday" → "Thứ Sáu anh sẽ lấy được máy", đúng) vẫn bị bộ kiểm tra báo nhầm; app không sửa hay hỏi lại vì "sẽ".
  - Với 1.8B, hỏi lại hầu như không đổi được câu sai do chính model dịch ra: model thường trả lại đúng câu cũ (7/11 lần hỏi lại trên câu dịch mới ở lần 6), kể cả với ghi chú "ai làm cho ai" mới. 7B thì được nhận 8/14 lần, gồm cả hai lỗi "ai làm ơn cho ai" của bộ D. Câu trả lời sửa đúng vai nhưng thêm hoặc bỏ một đại từ ở vế khác vẫn bị chặn.
  - Lỗi "ai làm ơn cho ai" chỉ được xét khi bản dịch có "cho / giúp / thay / tặng…" hoặc "được": "Em đến đón anh à?" của Sera cho 迎えに来てくれたの？ (người đến đón là người nghe, phải là "Anh đến đón em à?") không bị bắt.
  - Câu gốc có hành động ở nhiều vế chỉ được xét khi câu dịch có cùng số vế (lần 7). Nếu câu dịch gộp vế, câu đó không được xét. Một trạng ngữ đứng riêng trước dấu 、 (今日だけ、…) vẫn bị đếm như một vế.
  - Trên bản dịch mới của 7B (bộ r4), các lỗi "ai làm ơn cho ai" còn lại đều thuộc loại cố ý bỏ qua để tránh báo nhầm: 〜てあげて cho người thứ ba, câu dịch không có "cho / giúp", câu hỏi với あげた, もらった không nêu người làm, câu mỉa, người thứ ba trong câu tiếng Anh.
  - Câu trả lời khi hỏi lại có thể đổi nội dung mà bộ kiểm tra vai không thấy ("cà ri" thành "cá riêu"). Độ trùng từ không tách được loại này khỏi câu sửa đúng, nên không dùng để chặn.
  - Tên kanji hiếm bị dịch kiểu Hán-Việt (小鳥遊 → Tiểu Điểu Du). Cách sửa là thêm tên đó vào glossary.
  - Cùng một câu thoại nhưng đổi người nói ở trang sau thì chưa được dịch lại.
  - Nhân vật chưa có trong hồ sơ có thể bị xưng "tao" theo 俺.
  - Cỡ chữ đổi giữa các trang (theo chiều cao box OCR).
  - Bản 1.8B vẫn có lỗi nghĩa mà bộ kiểm tra không bắt được.
- **Chưa kiểm chứng:**
  - Game thật có anti-cheat (chỉ test bằng server giả).
  - Bấm menu khay thật.
  - dxcam lúc màn hình bật (cả buổi test màn hình đều tắt nên dùng GDI).
  - Chất lượng dịch qua đánh giá của người thật.

### Kết quả Phase 1 (2026-10-23)

Kết quả lúc đó, giữ lại làm lịch sử (đo trên máy của tác giả).

- **Unit test:** lúc đó đạt hết.
- **End-to-end:** chạy trên cửa sổ demo gồm hộp thoại JA, phụ đề EN, menu JA, đoạn văn EN và chữ JA hiện từng ký tự. Lần chạy cuối:
  - Độ phủ 10/10 cảnh.
  - Độ trễ từ lúc chữ xuất hiện tới lúc hiện bản dịch: p50 0,47–0,52 s, max 0,75 s.
  - Không có lần nào phải dịch lại.
  - Không OCR nhầm overlay của chính mình.
  - Thoát sạch với exit code 0.
  - CPU trung bình 8–10% của một nhân, RAM khoảng 1,3 GB, VRAM khoảng 1 GB.
- **Khi đang ở trong game có anti-cheat:** không chụp, không OCR, không có cửa sổ overlay nào. Engine OCR được giải phóng sau 30 giây.
- **Hạn chế đã biết:**
  - Dịch máy không có ngữ cảnh. Ví dụ tên riêng セラ bị dịch thành "huyết thanh".
  - Ô che chữ gốc là một màu phẳng, nên lộ thành mảng trên nền gradient.
  - Nhãn dài có thể lấn qua viền khung UI.
  - Sau khi đổi cảnh, bản dịch cũ còn hiện khoảng 0,3 giây.
  - Câu có số thay đổi liên tục (đồng hồ đếm ngược) sẽ bị dịch lại mỗi lần số đổi.
- **Chưa kiểm chứng:**
  - Đường dxcam trong e2e (màn hình đang tắt trong suốt lúc test, nên app dùng GDI).
  - Bấm phím và kéo chuột thật.
  - Game thật có anti-cheat.
  - Nhiều màn hình hoặc scale khác 100% (chỉ mô phỏng bằng `QT_SCALE_FACTOR`).

## Tra cứu dữ liệu game mới

Khi bạn chọn một game chưa có hồ sơ, app có thể tra dữ liệu cộng đồng **một lần** để dựng sẵn hồ sơ thuật ngữ theo kiểu hồ sơ Blue Archive: tên nhân vật, giới tính, họ / tên gọi tắt, địa danh, tổ chức. Không tìm thấy gì thì app chạy như trước.

- **Hỏi trước, mỗi game một lần.** Trước khi gửi gì lên mạng, app hỏi bạn. Câu trả lời được nhớ trong `data/lookup_state.json`. Thứ được gửi đi là **tên game** (đúng tên hiện trong cửa sổ hỏi; nếu bạn sửa tên thì chỉ gửi tên bạn sửa) và, khi game nằm trong thư viện Steam, **mã Steam** đọc từ tệp Steam của game. Không gửi đường dẫn, tên thư mục cá nhân, tên người dùng hay tên máy. Không muốn được hỏi nữa thì đặt `"lookup_ask_new_games": false`.
- **Nhận ra game chỉ bằng tệp trên đĩa**, không đụng tới process của game:
  - tên sản phẩm trong tệp .exe;
  - `steamapps/appmanifest_*.acf` và `libraryfolders.vdf` của Steam (cho mã Steam và tên chính thức);
  - tên thư mục của game (thư mục chứa exe, hoặc thư mục trên `bin` / `Binaries\Win64`…);
  - các tệp đánh dấu engine nằm cạnh exe (Unity, Unreal, Ren'Py, RPG Maker, KiriKiri, NScripter…).

  Tên exe chung chung như `Game.exe` và exe của engine (`SiglusEngine.exe`, `BGI.exe`, `AdvHD.exe`…) không được dùng làm tên game. Tên thư mục cũng không được dùng khi đó là thư mục chung (`Games`, `Desktop`, `ゲーム`…), thư mục nằm ngay trong thư mục người dùng, hay thư mục đồng bộ (`OneDrive - …`, `Dropbox`…), vì những tên đó có thể chứa tên bạn hay tên công ty.
- **Hồ sơ mẫu được xét trước.** `profiles/templates/*.json` khớp theo tên exe, mã Steam hoặc tên game (Blue Archive, ブルーアーカイブ, ブルアカ…). Khớp thì app dùng luôn hồ sơ mẫu, không lên mạng.
- **Nguồn dữ liệu** (`lookup_sources`, mặc định bật cả 5):

  | Nguồn | Lấy gì | Giấy phép |
  |---|---|---|
  | Steam (storesearch, appdetails) | Tên chính thức, tên tiếng Nhật, danh sách ngôn ngữ (để báo game có tiếng Việt chính thức không) | Dữ liệu của Valve, chỉ dùng cho cá nhân |
  | Wikidata | Đúng game (đối chiếu mã Steam / VNDB; một mục tìm theo tên chỉ được nhận khi nó là game), tên ja / en / vi, ngôn ngữ của game (báo tiếng Việt chính thức khi game không có trên Steam), danh sách nhân vật kèm giới tính, mã wiki Fandom | CC0 |
  | VNDB (Kana API) | Visual novel: nhân vật (tên La-tinh, tên gốc, tên khác, giới tính, vai trò) và các bản tiếng Việt. Nhân vật nào VNDB đánh dấu spoiler thì bỏ, và nhân vật hay tên khác trùng với họ từ Wikidata / wiki cũng bị bỏ (hai nguồn đó không có cờ spoiler). Khi VNDB đã có danh sách nhân vật, Wikidata và wiki chỉ bổ sung chi tiết, không thêm người | ODbL 1.0 |
  | Wiki của game (MediaWiki: Fandom, wiki.gg) | Tên tiếng Nhật / romaji / giới tính trong infobox, địa danh, tổ chức. Chỉ lấy tên. Chỉ dùng wiki có tên khớp với game hay series của nó; với wiki của cả series thì chỉ lấy mục nhân vật của đúng game đó | CC BY-SA |
  | Web (SearXNG, xem mục dưới) | Trang Việt hóa (“<tên game> Việt hóa”), tối đa 4 link; tìm wiki khi các nguồn trên không chỉ ra. Chỉ giữ trang có tiêu đề nói về bản Việt hóa của đúng game này; bỏ Wikipedia, trang anime / vietsub, phần khác của game (STEINS;GATE 0, Vol. 4…), trang tải lậu, 18+, apk, font | — |

- **Lịch sự với các trang:**
  - User-Agent ghi tên app và `github.com/dungbeo13/overlay-ocr`.
  - Mỗi nguồn chỉ gửi vài yêu cầu (Steins;Gate: 16 yêu cầu cho cả 4 nguồn).
  - Timeout 10 s mỗi yêu cầu; cả lượt tối đa khoảng 30 s. Bấm *Hủy*, đóng cửa sổ hay thoát app thì lượt tra dừng ngay, không gửi thêm yêu cầu nào.
  - Không giữ cookie. Kết quả được cache 14 ngày trong `data/lookup_cache/` (câu trả lời lỗi thì không cache).
  - Nguồn nào lỗi hay quá giờ thì app vẫn dùng kết quả của các nguồn còn lại.
- **Chắc đúng game mới dựng hồ sơ.** Khi hai nguồn tự tìm ra cùng một game và trỏ tới nhau (mã Steam / VNDB trên Wikidata), hoặc tên khớp hẳn, app mới lấy nhân vật, tìm web và wiki. Khi chưa chắc (ví dụ gõ “Persona”), cửa sổ chỉ hiện các game gần đúng; bạn chọn đúng game rồi bấm *Tra cứu game này*.
- **Hồ sơ nháp:**
  - Tên nhân vật viết đúng như chữ trong game (牧瀬紅莉栖). Tên tiếng Việt giữ romaji, họ trước tên như thứ tự gốc, nguyên âm dài viết “ou / uu” (Makise Kurisu, Okabe Rintarou), không kèm -san. Từ thông dụng dùng làm tên (少年, 謎の男) được dịch (Cậu bé, Người đàn ông bí ẩn). Game có tiếng Việt chính thức thì dùng tên tiếng Việt trên Wikidata (旅人 → Nhà Lữ Hành).
  - Giới tính lấy theo nguồn. Ô *Tự xưng* để trống để bạn tự chọn trong thẻ *Nhân vật*. Ghi chú chỉ ghi vai trò (nhân vật chính / phụ).
  - Họ và tên riêng (牧瀬, 紅莉栖) thành thuật ngữ loại `person`, để chữ trong game chỉ ghi một phần tên vẫn dịch đúng. Phần tên chỉ có 1 chữ (至) và từ thông dụng thì không thêm; phần tên chỉ có 2 chữ kana (るか, nằm trong するか) chỉ là gợi ý. `lookup.aliases` nối mỗi phần tên với nhân vật của nó, nên khi ô tên trong game chỉ ghi 紅莉栖, app vẫn biết đó là 牧瀬紅莉栖 và dùng giới tính, cách xưng hô của cô.
  - Biệt danh từ VNDB **mặc định không thêm**, vì VNDB không đánh dấu spoiler cho biệt danh (tên thật, thân phận ẩn). Bật ô *Thêm biệt danh (có thể lộ nội dung truyện)* thì app chỉ thêm biệt danh ngắn kiểu オカリン, ダル (kana ngắn hoặc có ～ちゃん…), thuộc về đúng một nhân vật và không trùng tên người khác; chúng là **gợi ý** nền vàng, bấm *Xác nhận* thì mới cố định. Tên đầy đủ kiểu ジョン・タイター không bao giờ được thêm.
  - Địa danh, tổ chức và tên game chỉ được thêm khi có nguồn ghi.
- **Gộp vào hồ sơ đã có:**
  - Mục bạn đã có giữ nguyên.
  - Thuật ngữ bạn đã xoá không được thêm lại.
  - Văn phong chỉ được điền khi còn trống; ngôn ngữ chỉ được điền cho hồ sơ mới (hồ sơ đã có để trống nghĩa là “theo cài đặt của app”).
  - Không bao giờ gộp vào hồ sơ *Mặc định*, vì mọi game chưa có hồ sơ đều dùng nó.
- **Khối `lookup` trong tệp hồ sơ** ghi lại:
  - mã game trên các trang, nguồn và giấy phép;
  - đường link Việt hóa;
  - bảng nối phần tên với nhân vật (`aliases`);
  - mục nào được nhập từ nguồn nào (`review`).

  Cửa sổ sửa hồ sơ lưu lại vẫn giữ nguyên khối này.
- **Bản Việt hóa: app chỉ hiện đường link**, không tải hay cài gì. Link từ VNDB là bản đã có người ghi nhận. Link từ tìm kiếm web nằm trong nhóm riêng *Kết quả tìm web, chưa kiểm chứng* (thu gọn, xếp sau link VNDB). Game đã có tiếng Việt chính thức thì app không tìm trang Việt hóa trên web.
- **Tạo yêu cầu tra cứu / Nhập hồ sơ:**
  - App tạo sẵn một đoạn yêu cầu (Markdown) để dán cho Claude hay trợ lý khác. Đoạn này có đúng định dạng JSON của app, các quy tắc trên và những gì app đã tìm được cho đúng game đó (không kèm hồ sơ của game khác, không kèm các gợi ý chưa xác nhận).
  - Câu trả lời nhập lại bằng *Nhập hồ sơ từ file* hoặc từ clipboard. App nhận JSON thuần, khối ```json dán từ khung chat (có chữ xung quanh cũng được), hoặc một tệp hồ sơ. Khối `lookup` lạ (kiểu dữ liệu sai) bị bỏ qua phần không hợp lệ.
  - Nhập từ cửa sổ tra cứu của một game thì hồ sơ được gắn với game đó (cửa sổ ghi rõ “Gắn với game …”). Nhập từ khay hay Cài đặt thì chỉ tạo hồ sơ, không gắn với game nào.
- **Dòng lệnh** (chạy thử giống hệt app, in từng nguồn tìm được gì và mất bao lâu):

  ```powershell
  & $py -m tools.lookup_game "Steins;Gate"
  & $py -m tools.lookup_game --exe "D:\SteamLibrary\steamapps\common\...\Game.exe"   # chỉ đọc tệp
  & $py -m tools.lookup_game "Steins;Gate" --profile-out data\sg.json   # ghi hồ sơ nháp (data\ không lên repo)
  & $py -m tools.lookup_game "Steins;Gate" --request                # in đoạn yêu cầu cho Claude
  & $py -m tools.lookup_game "Steins;Gate" --aliases                # thêm biệt danh (có thể lộ nội dung truyện)
  & $py -m tools.lookup_game "Persona 5 Royal" --managed            # bật SearXNG trên máy cho nguồn web, tắt khi xong
  ```

- **Kết quả thử thật** (2026-10-25, bản đã sửa, cả 5 nguồn, `--managed`: SearXNG trên máy bật cho nguồn web rồi tắt). Mỗi lượt mất khoảng 2–10 s khi cache trống, kể cả 1,5 s khởi động SearXNG:

  | Game | Nhận ra | Hồ sơ nháp |
  |---|---|---|
  | Steins;Gate | Steam + Wikidata + VNDB + Fandom | 15 nhân vật, 22 thuật ngữ (1 gợi ý: るか); 2 link Việt hóa từ VNDB và 4 kết quả tìm web |
  | Summer Pockets | Steam + Wikidata + VNDB + Fandom | 14 nhân vật, không còn nhân vật / tên thật spoiler (七海, 鷹原羽未…); 1 link VNDB và 2 kết quả web (Hoshi) |
  | Persona | chưa chắc: gần nhất là Persona 6 | không dựng hồ sơ; gợi ý Persona 6, Persona 5 Royal, Persona 3 Reload, Persona 4 Golden |
  | Persona 5 Royal | Steam + Wikidata | chỉ có tên game (wiki Megami Tensei của cả series bị loại); 4 kết quả web (voz, Tổ Mèo Cam, viethoagame…) |
  | Ever17 | Steam + Wikidata + VNDB | hồ sơ tên “Ever17”; 8 nhân vật (少年 → Cậu bé) |
  | Genshin Impact | Wikidata + Fandom (không có trên Steam) | báo có tiếng Việt chính thức; 50 nhân vật (旅人 → Nhà Lữ Hành); không tìm trang Việt hóa |
  | NEKOPARA Vol. 1 | Steam + Wikidata + VNDB | 10 nhân vật; không còn trang 18+ / tải lậu trong link |
  | Fate/stay night | Wikidata + VNDB (bản REMASTERED trên Steam là bản khác: chỉ nằm trong danh sách game gần giống) | 24 nhân vật, 24 thuật ngữ; không thêm tên thật của Servant |
  | Blue Archive | hồ sơ mẫu, không lên mạng | 107 nhân vật, 73 thuật ngữ |

- **Hạn chế đã biết:**
  - Cách VNDB phiên âm tên khác có thể khác bản chính thức (フェイリス: VNDB ghi “Feyris”, bản tiếng Anh ghi “Faris”). Vì vậy biệt danh chỉ là gợi ý.
  - Tên La-tinh lấy theo nguồn, có thể khác cách viết chính thức (VNDB: “Toosaka”, bản chính thức: “Tohsaka”).
  - Kết quả tìm web vẫn có thể lẫn trang tin tức hay bài viết về phần khác; vì vậy chúng nằm riêng, ghi *chưa kiểm chứng*.
  - Ngôn ngữ gốc đoán theo VNDB rồi Wikidata (nước sản xuất). Game không phải của Nhật hay Anh / Mỹ thì để trống (app giữ cài đặt ngôn ngữ của bạn).
- Kiểm thử: `& $py -m unittest tests.test_lookup -v`. Không dùng mạng: dữ liệu là câu trả lời thật đã thu gọn trong `tests/data/lookup/` (Steins;Gate, Doki Doki Literature Club, kết quả SearXNG cho Persona 5 Royal), ghi lại bằng `--record`. Blue Archive phải ra hồ sơ mẫu mà không gửi yêu cầu nào. Các tệp đó là dữ liệu bên thứ ba với giấy phép riêng (trích wiki Fandom theo CC BY-SA 3.0, VNDB theo ODbL…), xem mục *Dữ liệu bên thứ ba trong repo* và `tests/data/lookup/NOTICE.md`.

### Trong app: khay, cửa sổ tra cứu, Cài đặt

**Khi bạn mở một game mới.** Mỗi lần đổi cửa sổ, app xem tên tệp .exe như trước. Nếu không hồ sơ nào ghi tệp đó, app đọc **tệp** của game ở một luồng nền: tên sản phẩm trong .exe, tệp Steam, tên thư mục, tệp đánh dấu engine (Unity, Unreal, Ren'Py, RPG Maker, KiriKiri, NW.js, BGI…) và thư mục của các launcher (Steam, Epic Games, GOG, XboxGames…). App không đọc gì từ process của game.

- Nếu đó là game và app chưa từng hỏi về nó, app hiện **một** thông báo nhỏ ở khay: *Game mới: <tên>. Bấm để tra cứu dữ liệu cộng đồng (tên nhân vật, thuật ngữ, bản Việt hóa). Chưa gửi gì lên mạng.* App không mở cửa sổ đè lên game, kể cả game có anti-cheat.
- Menu khay có thêm mục **Tra cứu game** (khi có game mới, tiêu đề ghi số game, ví dụ *Tra cứu game (1 game mới)*), gồm:
  - *Game mới: <tên>…*;
  - *Tra cứu game…*: game đang chơi, hoặc tên bạn gõ;
  - *Tạo yêu cầu tra cứu cho Claude…*;
  - *Nhập hồ sơ từ file…*;
  - *Dán hồ sơ từ clipboard…*.
- App không hỏi về: Explorer, trình duyệt, Discord, IDE, launcher, Python, `claude.exe` và các tệp trong thư mục Windows. Game trong danh sách anti-cheat ở chế độ *Tạm dừng* cũng được bỏ qua, vì app không dịch khi game đó đang mở.
- Mỗi game chỉ hiện thông báo một lần (`data/lookup_state.json`), kể cả sau khi khởi động lại app. Windows thường ẩn thông báo khi một game toàn màn hình đang chạy, nên game nào bạn chưa mở câu hỏi thì sau khi khởi động lại app vẫn nằm trong mục *Tra cứu game* ở khay (không hiện thông báo lần nữa). Hai game cùng dùng `Game.exe` hay `SiglusEngine.exe` được tính là hai game khác nhau (theo thư mục).

**Cửa sổ hỏi trước khi gửi.** Bấm vào thông báo hoặc vào mục ở khay thì cửa sổ này mở ra. Nó ghi đúng **tên game sẽ được gửi** (sửa được nếu app đoán sai; sửa thì chỉ tên bạn sửa được gửi), mã Steam nếu có, và **gửi tới đâu**: store.steampowered.com, www.wikidata.org, api.vndb.org, wiki Fandom / wiki.gg, và SearXNG (đúng máy sẽ nhận: SearXNG trên máy này, hoặc địa chỉ bạn đặt trong Cài đặt). Có các nút:

- *Tra cứu*;
- *Để sau*: lần này không tra. Mục ở khay vẫn còn tới khi thoát app;
- *Không hỏi lại game này*;
- *Tạo yêu cầu cho Claude…*: không lên mạng.

Nếu game khớp một hồ sơ mẫu có sẵn (ví dụ Blue Archive), app bỏ qua bước hỏi và mở thẳng hồ sơ mẫu, không gửi gì lên mạng.

**Cửa sổ tra cứu.** Cửa sổ này không chặn app: app vẫn dịch trong lúc bạn tra. Việc tra chạy ở luồng nền.

- Mỗi nguồn có một dòng: đang tra / xong / không có gì / lỗi / quá giờ / bỏ qua, thời gian và kết quả. Nút *Hủy tra cứu*; đóng cửa sổ cũng hủy.
- Kết quả chia nhóm *Nhân vật*, *Thuật ngữ*, *Bản Việt hóa*, *Kết quả tìm web, chưa kiểm chứng*, *Nguồn*; nhân vật và thuật ngữ có ô đánh dấu. Mục hồ sơ đã có hiện màu xám, ghi *đã có trong hồ sơ, giữ nguyên*.
- Ô *Thêm biệt danh (có thể lộ nội dung truyện)* mặc định tắt (xem phần Hồ sơ nháp ở trên).
- Khi không chắc đúng game, cửa sổ không dựng hồ sơ nháp: nó hiện game gần đúng nhất và các game gần giống, kèm nút *Tra cứu game này*.
- Link Việt hóa chỉ mở trong trình duyệt khi bạn bấm vào. Cửa sổ nhắc rằng app không bao giờ tải hay cài gì, và link từ tìm kiếm web chưa ai kiểm chứng.
- Nút lưu:
  - *Tạo hồ sơ “<tên>”*; khi tên đó đã có, nút đổi thành *Thêm vào hồ sơ “<tên>”*;
  - *Thêm vào hồ sơ đang dùng “X”*, trừ khi hồ sơ đang dùng là *Mặc định*.

  Gộp vào hồ sơ đã có thì không ghi đè mục nào của bạn. Sau khi lưu, app chuyển sang hồ sơ đó và thêm tệp .exe của game vào danh sách tự chuyển hồ sơ. Exe dùng chung như `Game.exe` hay `SiglusEngine.exe` được ghi kèm thư mục game (`Summer Pockets/SiglusEngine.exe`), nên chỉ đúng game đó tự chuyển hồ sơ. Nút *Mở hồ sơ để xem lại…* mở cửa sổ *Thuật ngữ & nhân vật*.
- Không tìm thấy gì thì app ghi nhận và chạy như trước. Nếu mọi nguồn đều lỗi mạng, app hỏi lại sau 7 ngày.

**Tạo yêu cầu cho Claude / Nhập hồ sơ.** Mở được từ khay, từ cửa sổ tra cứu và từ Cài đặt.

- Cửa sổ yêu cầu có các nút *Sao chép*, *Lưu thành file…* và *Nhập câu trả lời…*.
- Cửa sổ nhập hồ sơ nhận cả câu trả lời dán từ khung chat, hoặc *Mở file…*. Nó hiện trước các mục sẽ được thêm, cảnh báo nhân vật không có tên chữ Nhật, rồi tạo hoặc gộp như trên.
- Hộp chọn tệp là hộp của Qt (tiếng Việt), không phải hộp của Windows, để nó cũng được ẩn khỏi ảnh chụp như mọi cửa sổ khác của app.

**Cài đặt → thẻ “Tra cứu game”.** Bấm *Lưu* thì áp dụng ngay.

- *Hỏi khi gặp game mới*.
- Các nguồn: Steam, Wikidata, VNDB, Wiki game, Tìm web qua SearXNG.
- SearXNG:
  - địa chỉ (để trống thì dùng bản cài trên máy; app tự điền sau khi cài);
  - trạng thái, nút *Kiểm tra* (chỉ hỏi `/healthz`, không tìm gì);
  - *Cài SearXNG trên máy…*: mở một cửa sổ chạy `tools.setup_searxng --no-search` ở nền. Cửa sổ hiện từng bước và toàn bộ dòng in ra, có nút *Dừng*; chỉ chạy khi bạn bấm *Bắt đầu cài*. Bước kiểm tra cuối chỉ bật SearXNG trên 127.0.0.1 rồi tắt, không tìm gì trên mạng. Xong thì app tự điền `searxng_url`;
  - *App tự bật / tắt SearXNG*;
  - *Dùng máy chủ SearXNG công cộng khi máy không có*: mặc định tắt, chỉ dùng khi máy chưa có SearXNG nào, kèm ghi chú: máy chủ công cộng thấy tên game và IP của bạn. Hiện ô này bị khóa vì chưa có máy chủ công cộng nào dùng được.
- Các nút *Tạo yêu cầu cho Claude…*, *Nhập hồ sơ từ file…*, *Dán từ clipboard*.
- *Quên các game đã hỏi* (hỏi lại trước khi xóa).

**Vòng đời và nhật ký.**

- App chỉ bật SearXNG trong lúc một lượt tra cứu cần tìm web. App tắt nó khi thoát, khi khởi động lại, hoặc khi bạn đổi cài đặt SearXNG; ngoài ra SearXNG tự tắt sau 10 phút không dùng.
- `data/logs/app.log` ghi tên game đã gửi, gửi tới nguồn nào, và mỗi nguồn trả lời ra sao (số yêu cầu, số lấy từ cache). Đường dẫn trong thư mục người dùng được ghi thành `~`. Khi thoát, mọi lượt tra đang chạy bị hủy ngay (không chờ máy chủ trả lời) và việc cài SearXNG bị dừng (không hỏi).
- Thử thật (2026-10-25, Persona 5 Royal, có SearXNG trên máy): xong trong khoảng 8 s, có 4 link Việt hóa từ web. Wiki Megami Tensei (của cả series) không còn được dùng. SearXNG khởi động trong 1,5 s và đã tắt khi xong; cổng 8888 đóng.

Kiểm thử: `& $py -m unittest tests.test_lookup_ui -v`, không dùng mạng. Phần chạy trên nền Windows thật kiểm tra mọi cửa sổ và cửa sổ con đều được ẩn khỏi ảnh chụp, rồi vẽ ảnh xem trước vào `bench/results/preview/`: `lookup_consent.png`, `lookup_running.png`, `lookup_result.png`, `lookup_unsure.png`, `lookup_template.png`, `lookup_empty.png`, `lookup_request.png`, `lookup_import.png`, `lookup_file_dialog.png`, `searxng_install.png`, `settings_lookup.png`. Ảnh xem trước không chứa tên người dùng (đường dẫn hiện là `%USERPROFILE%`, hộp chọn tệp mở ở `C:\Windows\Web`).

## SearXNG trên máy (tùy chọn)

Khi tra cứu một game, app có thể tìm thêm trên web: trang bản Việt hóa (“<tên game> Việt hóa”) hay wiki của game. Việc tìm này đi qua SearXNG, một máy tìm kiếm gộp mã nguồn mở chạy ngay trên máy bạn, không cần API key và không mất phí. Không có SearXNG thì app vẫn tra cứu bằng Steam, Wikidata, VNDB và wiki như thường, chỉ thiếu phần tìm trên web.

**Cài:**

```powershell
& $py -m tools.setup_searxng            # cài hoặc sửa, chạy thử, rồi điền searxng_url vào data/config.json
& $py -m tools.setup_searxng --verify   # kiểm tra lại: mã nguồn, khởi động, một lần tìm JSON, tắt
& $py -m tools.setup_searxng --status   # đã cài chưa, có đang trả lời không
& $py -m tools.setup_searxng --remove   # gỡ
```

- SearXNG nằm ở `%USERPROFILE%\.screen-translator\searxng`, trong một venv riêng (cùng bản Python 3.12 với app). Thư viện của SearXNG chỉ cài vào venv đó, venv của app không đổi gì.
- Bản dùng là commit `ef0ec03` (2026-10-24) của github.com/searxng/searxng. Mã nguồn tải về được so với mã băm đã ghim, khác là không cài.
- SearXNG viết cho Linux. Để chạy thẳng trên Windows chỉ cần 3 chỉnh sửa nhỏ, đều nằm trong thư mục cài: bỏ qua module `pwd` (Windows không có), ghi sẵn số phiên bản (nếu máy không có git), thêm gói `tzdata` (dữ liệu múi giờ). Server là server có sẵn của SearXNG (`python -m searx.webapp`): không cần Docker, Redis hay Valkey.
- `settings.yml` được tạo từ `deploy/searxng/settings.yml`: `secret_key` ngẫu nhiên, chỉ nghe trên 127.0.0.1, cổng 8888 (cổng bận thì lấy cổng trống khác), bật JSON, tắt limiter và image proxy.
- Trình cài tự thêm vào `data/config.json` hai khóa `"searxng_url": "http://127.0.0.1:8888"` và `"searxng_managed": true`. Các khóa khác giữ nguyên.

**App bật / tắt SearXNG:**

- Chỉ bật khi đang tra cứu một game (sau khi bạn đã đồng ý tra cứu game đó). Khởi động mất khoảng 1,5 giây (lần đầu tiên khoảng 4 giây), không hiện cửa sổ, log ghi vào `data/logs/searxng.log`. Dùng khoảng 90–105 MB RAM, không dùng GPU.
- Tự tắt sau 10 phút không dùng và khi app thoát. Giống llama-server, SearXNG chạy trong một Job Object của Windows: app có crash thì cũng không sót lại process nào.
- Chỉ nghe trên 127.0.0.1. App ép địa chỉ này qua biến môi trường (biến môi trường thắng `settings.yml`). Ngay sau khi bật, app kiểm tra các cổng SearXNG đang nghe; thấy địa chỉ nào khác 127.0.0.1 là tắt luôn.
- Nếu ở `searxng_url` đã có một SearXNG đang chạy (ví dụ bản Docker), app dùng luôn bản đó, không bật thêm bản thứ hai. Khi `searxng_url` trỏ sang máy khác, cửa sổ hỏi trước khi tra ghi rõ máy đó sẽ nhận tên game.
- Đặt `"searxng_managed": false` thì app không tự bật gì, chỉ dùng SearXNG ở `searxng_url` khi nó đang chạy.

**Instance công khai** (`searxng_public_fallback`, mặc định `false`; chỉ dùng khi không có SearXNG nào được đặt: không `searxng_url`, không bản cài trên máy): đêm 2026-10-25 đã thử 66 instance trên searx.space, mỗi instance một lần tìm `format=json`. Không instance nào trả JSON cho app:

- 50 instance báo lỗi 429 (bộ chặn bot);
- 7 instance báo 403 (tắt JSON, hoặc bị tường lửa chặn);
- 3 instance báo 418, 6 instance trả về trang HTML chặn bot.

App không giả làm trình duyệt để lách qua, nên danh sách `PUBLIC_INSTANCES` đang trống và phần dự phòng này hiện không làm gì. Muốn thử lại thì chạy `& $py -m tools.setup_searxng --test-public`.

Về riêng tư, nếu sau này dùng instance công khai: người chạy instance thấy tên game bạn tìm và địa chỉ IP của bạn. Với SearXNG trên máy, Google, Bing… thấy IP và câu tìm, nhưng không có cookie hay tài khoản nào.

**Docker** (cho sau này): `deploy/searxng/` có `docker-compose.yml` (cổng `127.0.0.1:8888:8080`, cùng commit với bản trên) và hướng dẫn trong `deploy/searxng/README.md`, gồm cả Docker Desktop lẫn docker compose trong WSL. `& $py -m tools.setup_searxng --docker` tạo sẵn thư mục chạy được, có `secret_key` thật, nằm ngoài repo.

Kiểm thử: `& $py -m unittest tests.test_searxng -v`. Không cần mạng: test dùng server giả và các câu trả lời thật đã thu gọn trong `tests/data/lookup/searxng_*.json`.

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
| `app/winutil.py` | DPI awareness, tắt EcoQoS (`set_power_throttling`) |
| `app/pipeline/` | Phát hiện thay đổi, gom dòng thành đoạn, theo dõi block, luồng OCR và luồng dịch → `OverlayState` |
| `app/overlay/` | Cửa sổ overlay, vẽ bản dịch (`render.py`), quy đổi toạ độ vật lý ↔ Qt, chọn vùng |
| `app/ui/` | Phím tắt toàn cục, icon khay hệ thống, cửa sổ Cài đặt / hồ sơ / tra cứu, bảo vệ anti-cheat (`game_guard.py`) |
| `app/main.py` | Nối mọi thành phần, CLI, log, debug dump |
| `assets/` | Icon của shortcut (`app.ico`) |
| `profiles/templates/` | Hồ sơ mẫu có sẵn (Blue Archive) |
| `deploy/searxng/` | `settings.yml` của SearXNG và bản Docker (tùy chọn) |
| `bench/` | Các script đo đạc; kết quả ghi vào `bench/results/` (không có trong repo) |
| `bench/data/` | Dữ liệu đo: ảnh OCR tổng hợp, câu mẫu, cảnh thoại và các bộ câu xưng hô có nhãn |
| `tools/` | Cài LLM / SearXNG, tạo shortcut, tra cứu game, cửa sổ demo, kiểm thử end-to-end, xem trước overlay |
| `tests/` | Unit test; `tests/data/lookup/` là câu trả lời API đã thu gọn (xem *Dữ liệu bên thứ ba trong repo*) |
| `research_notes/`, `reports/` | Ghi chú nghiên cứu và báo cáo về cách dịch game Nhật sang tiếng Việt |
| `data/` | Dữ liệu riêng của bạn (cài đặt, hồ sơ, cache, log); app tự tạo, không có trong repo |

## Các bài đo tự động

Mọi số đo trong README (Phase 0, Phase 1, tầng LLM, xưng hô, tra cứu…) là đo trên máy của tác giả (RTX 4080 SUPER 16 GB, i7-13700KF, màn 2560×1440, Windows 11). Kết quả chi tiết được ghi vào `bench/results/`; thư mục này nằm trong `.gitignore` nên không có trong repo. Muốn có số của máy bạn thì chạy lại các lệnh dưới đây, các lệnh `bench.eval_*` ở mục *Dịch bằng LLM local* và `tools.e2e_check`.

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
- Tuỳ chọn: thêm file `.txt` cùng tên, ghi chữ đúng từng dòng, để tính độ chính xác.

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

### 4. (Tuỳ chọn) Key cho engine dịch tốt hơn
Đặt biến môi trường rồi chạy lại `bench.eval_translate`:
- `AZURE_TRANSLATOR_KEY` và `AZURE_TRANSLATOR_REGION`: Azure Translator gói F0 miễn phí, 2 triệu ký tự/tháng.
- `GEMINI_API_KEY`: Gemini Flash-Lite, dịch có ngữ cảnh, giữ đúng xưng hô. Đổi model bằng `GEMINI_MODEL`.

## Kết quả Phase 0 (2026-10-22, RTX 4080 SUPER, i7-13700KF, màn 2560×1440)

Kết quả lúc đó, đo trên máy của tác giả. Số liệu chi tiết được ghi vào `bench/results/*.json` và `*.log` của máy đo (không có trong repo; chạy lại theo mục *Các bài đo tự động*).

**Ẩn overlay khỏi capture:** đạt 15/15 kịch bản. Overlay hoàn toàn vô hình với dxcam, WGC và mss, kể cả khi native window bị tạo lại.

**Chụp màn hình** (đo khi chụp mỗi 150 ms):

| Backend | Dải phụ đề 2560×200 | Hộp thoại 800×300 | Toàn màn hình | CPU |
|---|---|---|---|---|
| dxcam | 0,84 ms | 0,54 ms | 4,3 ms | ≤2% |
| WGC | 0,52 ms | 0,24 ms | 3,2 ms | 16–19% (copy mọi frame) |
| mss | 6,8 ms | 5,1 ms | 24,9 ms | 1–9% |

→ dùng **dxcam** làm backend chính. Khâu chụp không phải là nút thắt.

**OCR** (88 ảnh tổng hợp, p50; CER = tỉ lệ lỗi ký tự):

| Cấu hình | Hộp thoại game | Phụ đề | UI | Trang tài liệu | CER EN / JA | CPU |
|---|---|---|---|---|---|---|
| RapidOCR small, **CUDA** | 44–46 ms | 41 ms | 105 ms | 380–390 ms | 0,2% / 0,7% | ~1 nhân |
| RapidOCR small, CPU 8 luồng | 270–285 ms | 420 ms | 220–250 ms | 1,2–1,4 s | 0,2% / 0,7% | ~6 nhân |
| RapidOCR tiny, CPU (chỉ EN) | 130 ms | 116 ms | 94 ms | 880 ms | 0,3% / — | ~5 nhân |
| RapidOCR medium, CUDA | 70 ms | 78 ms | 130 ms | 520 ms | 0,1% / 1,5% | ~1 nhân |
| RapidOCR medium, CPU | 4,8–5,3 s | 5 s | 3,7–4 s | 34 s | — | không dùng được |
| Windows OCR | 19–26 ms | 13–16 ms | 8–17 ms | 77–204 ms | 5,8% / 11,3% (phụ đề viền ~21%) | thấp |

→ Đề xuất mặc định: **RapidOCR small chạy CUDA** cho mọi trường hợp. Nhanh gấp ~6 lần CPU và chỉ tốn ~1 nhân thay vì ~6. Cần xác nhận lại bằng bài đo FPS game (mục 3 ở trên). CPU small là phương án dự phòng. Windows OCR chỉ hợp với chữ UI tiếng Anh rõ nét.

**Dịch — Google gtx miễn phí:**
- Soak 30 phút, 1 lô/giây × 20 câu: **1801/1801 thành công**, 0 lần bị chặn (429), 0 lỗi lệch số câu.
- **Độ trễ mới là vấn đề:** p50 ≈ 1,4 s, p95 ≈ 2,4 s mỗi lô. Ngay cả 1 câu cũng mất p50 ≈ 1 s (thấp nhất ~0,1 s, phân bố hai đỉnh).
- → Với chữ mới, độ trễ đầu-cuối ≈ chụp 0,15 s + OCR 0,05 s + dịch 1–1,5 s. **Khâu dịch là nút thắt.** Hướng xử lý:
  - Cache mạnh cho câu lặp lại.
  - Thử Azure F0 (theo tài liệu là 150–300 ms).
  - Hiện chữ gốc hoặc trạng thái "đang dịch" trong lúc chờ.
- Chất lượng: dịch máy không có ngữ cảnh. Ví dụ "Inventory is full." → "Hàng tồn kho đã đầy."; câu chửi tiếng Nhật bị dịch lịch sự; xưng hô lẫn lộn (chị / tôi / bạn).

**Chưa làm trong Phase 0:**
- OCR manga chuyên dụng (bộ phát hiện bong bóng + manga-ocr, cần PyTorch ~3 GB).
- Model dịch offline (HY-MT2 qua llama.cpp).
- Thử trên nhiều màn hình hoặc màn có scale khác 100%.

## Ghi chú kỹ thuật đã kiểm chứng (2026-10-22, Win 11 build 26300)
- `SetWindowDisplayAffinity(0x11)` trên cửa sổ PySide6 trong suốt, click xuyên qua: **ẩn khỏi cả DXGI, WGC và GDI**. Qt 6.12 giữ nguyên HWND khi `setWindowFlags`. Khi native window bị tạo lại, HWND mới quay về mặc định, nên overlay luôn phải dùng `apply_to_widget()`.
- Process chạy nền bị Windows EcoQoS hạ xung, khiến OCR chậm khoảng 5 lần. App phải gọi `set_power_throttling(False)` khi khởi động.
- OpenCV mặc định dùng 24 luồng, làm CPU tăng vọt. Nên gọi `cv2.setNumThreads(1)`.
- PP-OCRv6 `tiny` không hỗ trợ tiếng Nhật; `small`/`medium` thì có.
- Windows OCR chỉ đọc được ngôn ngữ đã cài trong Windows (máy đo có `en-US` và `ja`).
- Endpoint Google gtx: chỉ `POST /translate_a/t` với tham số trên query và `q` trong body là được chấp nhận; `GET` bị 429.
- Bản dịch máy không giữ được ngữ cảnh. Ví dụ "Inventory" bị dịch thành "Hàng tồn kho", câu chửi tiếng Nhật bị dịch lịch sự. Đây là lý do cần tầng LLM có glossary.

## Giấy phép

Mã nguồn dự án theo giấy phép [MIT](LICENSE). Dữ liệu bên thứ ba trong repo, cùng phần mềm và model bạn tự tải khi cài, theo giấy phép riêng của chúng (mục dưới).

## Dữ liệu bên thứ ba trong repo

`tests/data/lookup/` chứa câu trả lời thật của các API, đã thu gọn, chỉ dùng làm dữ liệu cho test. Các tệp này **không** theo giấy phép MIT của mã nguồn dự án: mỗi tệp giữ giấy phép của nguồn (chi tiết trong `tests/data/lookup/NOTICE.md`).

- `fandom_*.json`: đoạn trích wikitext của steins-gate.fandom.com và doki-doki-literature-club.fandom.com, đã thu gọn và sửa đổi (chỉ giữ tham số infobox và mẫu `{{Nihongo}}`), theo giấy phép [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Tác giả là những người đóng góp có tên trong lịch sử sửa đổi của từng trang; địa chỉ nguồn ghi ở `request.url` của mỗi tệp.
- `vndb_*.json`: dữ liệu của vndb.org, theo giấy phép [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
- `wikidata_*.json`: dữ liệu Wikidata, [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
- `steam_*.json`: dữ liệu cửa hàng của Valve, chỉ giữ để chạy test.
- Kết quả SearXNG (`searxng_search_*.json`, `web_hits.json`): tiêu đề và địa chỉ của các trang web công khai; đoạn trích nội dung đã cắt còn 60 ký tự.

Tên game, tên nhân vật và nhãn hiệu thuộc về chủ sở hữu của chúng; dự án này không liên quan tới họ.

**Phần mềm và model bạn tự tải khi cài.** Repo không phân phối lại thứ nào trong số này:

- `tools.setup_llm` tải llama.cpp ([github.com/ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp), giấy phép MIT; bản Windows kèm thư viện CUDA runtime của NVIDIA, theo giấy phép của NVIDIA) và các file GGUF Hy-MT2 của Tencent trên Hugging Face ([Hy-MT2-1.8B-GGUF](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF), [Hy-MT2-7B-GGUF](https://huggingface.co/tencent/Hy-MT2-7B-GGUF)); giấy phép của model ghi trên model card.
- `tools.setup_searxng` tải SearXNG ([github.com/searxng/searxng](https://github.com/searxng/searxng), giấy phép AGPL-3.0) ở một commit đã ghim, kèm tệp LICENSE của nó, rồi chạy riêng trên máy bạn (chỉ nghe ở 127.0.0.1).
- Model OCR do gói `rapidocr` tự tải ở lần OCR đầu tiên; các thư viện Python do pip cài vào venv của bạn theo `requirements.txt`, gói nào theo giấy phép của gói đó.
