# Hướng dẫn chi tiết

Trang này là phần tham khảo đầy đủ cho những gì [README](../README.md) chỉ tóm tắt: shortcut và cách mở app, cửa sổ *Cài đặt*, *Hướng dẫn nhanh*, chụp màn hình và game có anti-cheat, dịch bằng LLM trên máy, thẻ *Nhân vật* (chọn cách xưng hô) và hồ sơ mẫu Blue Archive. Tra cứu game mới và SearXNG nằm ở [tra-cuu-game.md](tra-cuu-game.md).

Trong các lệnh, `$py` là Python của venv; lệnh chạy trong PowerShell, từ thư mục app. Mở cửa sổ PowerShell mới thì dán trước 2 dòng ở mục [*Mỗi lần mở cửa sổ PowerShell mới*](../README.md#mỗi-lần-mở-cửa-sổ-powershell-mới) của README.

- [Mở app không cần cửa sổ dòng lệnh, cửa sổ Cài đặt và Hướng dẫn nhanh](#mở-app-không-cần-cửa-sổ-dòng-lệnh-cửa-sổ-cài-đặt-và-hướng-dẫn-nhanh)
- [Chụp màn hình và game có anti-cheat](#chụp-màn-hình-và-game-có-anti-cheat)
- [Dịch bằng LLM local (glossary + ngữ cảnh)](#dịch-bằng-llm-local-glossary--ngữ-cảnh)
  - [Chọn cách xưng hô (thẻ “Nhân vật”)](#chọn-cách-xưng-hô-thẻ-nhân-vật)
  - [Hồ sơ Blue Archive](#hồ-sơ-blue-archive)

## Mở app không cần cửa sổ dòng lệnh, cửa sổ Cài đặt và Hướng dẫn nhanh

**Shortcut** (tên *Screen Translator*, ngoài Desktop và trong Start Menu; tạo bằng lệnh đầu tiên dưới đây; mặc định không khởi động cùng Windows):

Đây là danh sách lệnh, **không dán cả khối**: chỉ chạy dòng bạn cần.

```powershell
& $py -m tools.install_shortcut             # tạo / cập nhật shortcut Desktop + Start Menu
& $py -m tools.install_shortcut --startup   # thêm: khởi động cùng Windows (thư mục Startup)
& $py -m tools.install_shortcut --remove    # gỡ (thêm --startup để gỡ cả shortcut Startup)
& $py -m tools.install_shortcut --check     # xem các shortcut đang trỏ tới đâu
```

- Shortcut chạy `pythonw.exe run_app.pyw` của venv: không có cửa sổ dòng lệnh, app chỉ hiện biểu tượng 文 ở khay hệ thống. Lần mở đầu tiên, cửa sổ *Hướng dẫn nhanh* tự mở; những lần sau có một thông báo nhỏ *Screen Translator đang chạy* (hoặc, khi chưa có vùng dịch nào được lưu, một thông báo nhắc cách chọn vùng). Icon `assets/app.ico` (có sẵn trong repo) giống icon khay; lệnh chỉ vẽ lại file này khi nó chưa có, hoặc khi thêm `--redraw-icon`. Các cửa sổ của app hiện trên thanh tác vụ với tên *Screen Translator* và icon 文 (không phải icon Python).
- **Shortcut trỏ tới đúng thư mục dự án hiện tại** (`run_app.pyw` trong thư mục này) và tới Python của venv đã dùng để chạy lệnh tạo shortcut. `Tạo lại shortcut.cmd` dùng venv `%USERPROFILE%\.venvs\screen-translator`, hoặc Python ở biến môi trường `SCREEN_TRANSLATOR_PYTHON` nếu có đặt (như `run.ps1`). Nếu chuyển thư mục dự án sang chỗ khác thì **bấm đúp `Tạo lại shortcut.cmd`** trong thư mục mới: shortcut Desktop, Start Menu (và Startup, nếu đang bật *Khởi động cùng Windows*) sẽ trỏ sang chỗ mới. Tạo lại venv thì cũng chạy tệp đó (hoặc lệnh đầu tiên ở trên). Chạy lại lúc nào cũng được: shortcut đã đúng thì không đổi gì.
- Mở app lần nữa khi nó đang chạy thì không có bản thứ hai: app đang chạy báo *App đang chạy ở khay hệ thống* và mở menu khay. Nếu bản đầu còn đang khởi động (máy vừa bật, ổ đĩa chậm), lần mở thứ hai chờ nó tới 45 giây rồi mới chuyển sang; chỉ khi app thật sự bị treo mới có thông báo *đang chạy nhưng không phản hồi*. Mỗi thư mục dữ liệu chỉ chạy được một bản.
- Bấm (chuột trái hoặc phải) vào biểu tượng 文 để mở menu; bấm đúp để chọn vùng dịch.
- Lỗi lúc khởi động (thiếu thư viện, shortcut có tham số sai…) hiện trong hộp thoại tiếng Việt, có nút *Sao chép chi tiết* và *Mở thư mục nhật ký*, và được ghi vào `data/logs/app.log`. **Các cửa sổ của app không hiện trong ảnh chụp màn hình** (để app không tự dịch lại chữ của mình): muốn hỏi ai về một lỗi thì bấm *Sao chép chi tiết* rồi dán. Lỗi xảy ra trước khi app kịp mở log nằm ở `data/logs/startup-error.log`; nếu app bị crash, stack nằm ở `data/logs/crash.log` (của lần chạy trước: `crash.log.1`).
- `.\run.ps1` và `python -m app` vẫn chạy như cũ (có cửa sổ dòng lệnh).

**Cài đặt** (khay → *Cài đặt…*): cửa sổ riêng, app vẫn dịch trong lúc mở; nút *Lưu*, *Hủy*, *Mặc định*. *Mặc định* hỏi lại trước, chỉ điền lại các ô (bấm *Lưu* mới áp dụng) và không đổi *Khởi động cùng Windows*.

| Tab | Gồm |
|---|---|
| Chung | Ngôn ngữ nguồn, *Khởi động cùng Windows*, *Hướng dẫn nhanh…* |
| Dịch | Chế độ LLM, mô hình, số câu thoại gần đây gửi kèm, tự thêm thuật ngữ LLM đề xuất, tắt LLM để giải phóng VRAM khi đang chơi game có anti-cheat. Khi chưa cài LLM: lệnh cài kèm nút *Sao chép lệnh* |
| Hiển thị | Phông chữ, cỡ chữ nhỏ nhất, độ che chữ gốc (0 % = trong suốt, 100 % = che kín), khung viền vùng dịch, kèm ô xem trước vẽ bản dịch lên một khung thoại giả đúng như overlay vẽ |
| Phím tắt | Bấm vào ô rồi nhấn tổ hợp mới (Esc: hủy, Backspace hoặc nút *Xóa phím*: bỏ phím). Báo phím trùng nhau, phím Windows dùng (Alt+F4, Win+L…) và phím thiếu Ctrl / Alt / Win |
| Anti-cheat | Chế độ và danh sách game: thêm từ cửa sổ đang mở, gõ tên tệp .exe, xóa, khôi phục danh sách mặc định |
| Nâng cao | Chu kỳ chụp, thiết bị OCR, cách chụp màn hình, mở thư mục dữ liệu |
| Tra cứu game | Xem [*Cài đặt → thẻ “Tra cứu game”*](tra-cuu-game.md#trong-app-khay-cửa-sổ-tra-cứu-cài-đặt) |

Tab *Chung* còn ghi phiên bản app đang chạy (ví dụ *Phiên bản 1.0.0*).

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

Kiểm thử phần này: xem [phat-trien.md](phat-trien.md#cài-đặt-hướng-dẫn-lần-đầu-và-trình-mở-app).

## Chụp màn hình và game có anti-cheat

- Chụp màn hình dùng dxcam. Khi dxcam không trả về frame nào, ví dụ lúc màn hình đang tắt, app chuyển sang GDI (mss). Overlay vẫn vô hình với cả hai cách.
- **Game có anti-cheat:** app không inject và không đọc bộ nhớ process nào. Mỗi giây app chỉ xem tên file exe của cửa sổ đang active. Nếu exe nằm trong `blocked_processes` (file `data/config.json`), app xử lý theo chế độ:
  - *Tạm dừng* (mặc định): ngừng chụp màn hình, xóa overlay, tự chạy lại khi bạn rời game.
  - *Chỉ cảnh báo*: báo một lần, vẫn tiếp tục dịch.
  - *Tắt*: không kiểm tra gì.

  Muốn thêm game của bạn thì thêm tên exe vào danh sách đó.
- Danh sách và chế độ sửa được trong *Cài đặt* → *Anti-cheat* (cũng là `blocked_processes` và `guard_mode` trong `data/config.json`). Danh sách mặc định không có `BlueArchive.exe`.

## Dịch bằng LLM local (glossary + ngữ cảnh)

App chạy một model dịch trên GPU của bạn bằng llama-server, chỉ nghe trên 127.0.0.1. Model nhận cả màn hình cùng lúc, kèm glossary của hồ sơ, danh sách nhân vật và các câu thoại gần đây. Nhờ vậy tên riêng được giữ cố định, còn cách xưng hô (anh/em, tao/mày, bà/cháu…) đi theo người nói và người nghe.

- **Chế độ** (khay hệ thống → *Dịch bằng LLM*, hoặc `--llm-mode`):
  - *Google trước, LLM tinh chỉnh* (mặc định): bản Google hiện ngay, khoảng 0,5–1 s sau được thay tại chỗ bằng bản LLM, không bị nháy trống.
  - *Chỉ LLM*: chờ bản LLM; nếu LLM chưa sẵn sàng hoặc quá thời gian thì dùng Google.
  - *Tắt*.
- **Mô hình** (khay → *Dịch bằng LLM* → các mục *Mô hình: …*, hoặc *Cài đặt* → *Dịch* → *Mô hình*, hoặc `--llm-model light|quality`):
  - *Tự động* (mặc định): dùng *Chất lượng 7B* (Hy-MT2-7B, cần thêm khoảng 5,3 GB VRAM) khi còn trống từ 7,5 GB VRAM, nếu không thì dùng *Nhẹ 1.8B* (Hy-MT2-1.8B).
  - *Nhẹ 1.8B* (khoảng 2 GB VRAM) chỉ tinh chỉnh câu thoại và phụ đề; chữ trên menu, nút bấm của game vẫn do Google dịch.
- **Hồ sơ** (`data/profiles/*.json`, khay → *Hồ sơ* → *Sửa thuật ngữ / nhân vật…*). Mỗi game một hồ sơ, gồm:
  - Thuật ngữ cố định.
  - Nhân vật: tên tiếng Việt, giới tính, cách tự xưng, ghi chú quan hệ (ví dụ: *gọi Ren là "anh", xưng "em"*).
  - Văn phong.
  - Danh sách file exe để **tự chuyển hồ sơ** khi bạn mở game đó (thẻ *Văn phong & game*, nút *Thêm cửa sổ đang mở…*).

  Tên do LLM đề xuất được thêm vào glossary với nền vàng. Chúng chỉ là gợi ý cho LLM cho tới khi bạn bấm *Xác nhận*. Xóa một gợi ý sai thì các bản dịch đã cache với gợi ý đó cũng bị bỏ.
- **Vòng đời:**
  - Server tải model khoảng 2 s.
  - Server tự tắt (trả VRAM) sau 30 s khi game có anti-cheat ở foreground, và sau 10 phút không dùng. Lần sau cần thì tự bật lại.
  - Khi app thoát, không còn process llama-server nào sót lại.
  - Ngữ cảnh hội thoại tự reset sau 3 phút im lặng hoặc khi đổi vùng.
- **Cài runtime và model (lần đầu hoặc cài lại):** `& $py -m tools.setup_llm` tải những gì còn thiếu (thêm `--models light` để chỉ lấy bản 1.8B), `--verify` kiểm lại các file đã cài, `--force` tải lại hết. Bản dùng là llama.cpp b11669 CUDA 13.4 (driver r580+) hoặc 12.4, tự chọn theo driver, và 2 file GGUF đã kiểm SHA256, nằm trong `%USERPROFILE%\.screen-translator`.

Đo chất lượng tầng LLM (các lệnh `bench.eval_*` và `tools.e2e_check --llm`): xem [phat-trien.md](phat-trien.md#đo-chất-lượng-tầng-llm). Kết quả đo theo thời gian: [ket-qua-do.md](ket-qua-do.md#kết-quả-tầng-llm-2026-10-24).

### Chọn cách xưng hô (thẻ “Nhân vật”)

Không cần gõ ghi chú đúng mẫu *gọi Ren là 'anh', xưng 'em'* nữa. Mở *Sửa thuật ngữ / nhân vật…* → thẻ **Nhân vật**, chọn một nhân vật ở bảng trên (ô *Tìm nhân vật…* lọc theo tên và ghi chú, tiện với hồ sơ nhiều nhân vật như Blue Archive); khung **Xưng hô** bên dưới có:

- **Tự xưng**: cách nhân vật tự gọi mình (tôi, em, anh, tớ, ta…), chọn trong danh sách hoặc gõ.
- **Cách gọi từng người nghe**: mỗi dòng một người nghe, là một nhân vật khác trong hồ sơ hoặc một nhóm như *khách*, *người lạ*, *mọi người* (dùng cho mọi người nghe chưa có dòng riêng).
  - *Gọi là*: từ dùng để gọi người đó (anh, em, cậu, mày, Sensei…). Muốn ghi thêm thì ghi trong ngoặc: `anh (hoặc 'tiền bối')`, `anh (お兄ちゃん)`.
  - *Tự xưng riêng (gợi ý cho LLM)*: thường để trống; chỉ điền khi nhân vật tự xưng khác đi với riêng người này. App vẫn dùng *Tự xưng* ở trên cho mọi người nghe, từ ở cột này chỉ gửi cho LLM trong ghi chú. Ô giống hệt *Tự xưng* thì hiện trống.
  - Phím Insert thêm dòng, Delete xóa dòng đang chọn.
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

Hồ sơ mẫu **Blue Archive** nằm ở [`profiles/templates/blue_archive.json`](../profiles/templates/blue_archive.json). Lần đầu bạn mở BlueArchive.exe, app đề nghị dùng hồ sơ này (hoặc chọn khay → *Tra cứu game*). Bấm dùng thì app chép nó vào `data/profiles/`. Hồ sơ được soạn từ dữ kiện công khai trên wiki (tên nhân vật, trường, CLB) và ghi chú riêng, không lấy từ tệp của game. Hồ sơ dựng theo mục *Hồ sơ Blue Archive (bản nháp)* trong [`reports/Dịch game Nhật sang tiếng Việt.md`](../reports/Dịch%20game%20Nhật%20sang%20tiếng%20Việt.md). Với BA, app chỉ chụp màn hình rồi OCR, không hook và không đọc bộ nhớ game, vì bản Steam có Nexon Game Security còn bản Yostar JP có XIGNCODE3.

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
  1. Khay → *Hồ sơ* → *Sửa thuật ngữ / nhân vật…* → thẻ **Nhân vật** → chọn dòng 先生 (Sensei).
  2. Ở bảng trên, đặt cột *Giới tính* là *Nam*. Trong khung **Xưng hô**, đặt *Tự xưng* là `thầy`.
  3. Ở bảng *Cách gọi từng người nghe*, dòng *học sinh*: sửa ô *Tự xưng riêng (gợi ý cho LLM)* từ `tôi (không xưng 'anh', 'thầy' hay 'cô')` thành `thầy (không xưng 'tôi' hay 'anh')`. Ghi chú lưu vào hồ sơ khi đó có `xưng 'thầy' (không xưng 'tôi' hay 'anh')` thay cho `xưng 'tôi' (không xưng 'anh', 'thầy' hay 'cô')`.
  4. Bấm *Lưu*.

  Muốn Sensei là cô giáo thì làm như trên với *Nữ* và `cô` (thay cho *Nam* và `thầy`). Lưu ý: khi đó model 7B cũng hay để học sinh gọi Sensei là "thầy" (2 trên 3 câu thử).
- **Tên:** tên nhân vật và tên riêng viết theo cách La-tinh chính thức của bản Global. Ví dụ: Hoshino, Yuuka (giữ nguyên âm dài), Aris (bluearchive.wiki viết Arisu), Kivotos, Schale, Problem Solver 68, Pandemonium Society. Phần mô tả trong tên trường và tên CLB thì dịch ra tiếng Việt.
- **Mọi cách dịch tiếng Việt trong hồ sơ chỉ là đề xuất.** BA không có bản tiếng Việt chính thức, và không tìm thấy quy ước xưng hô hay glossary tiếng Việt nào được ghi lại. Chỉ "CLB Phát triển Game" là đã thấy dùng thật; các thuật ngữ khác có ghi chú "đề xuất". Bạn sửa theo ý mình trong *Sửa thuật ngữ / nhân vật…*, ví dụ "CLB Phụ đạo" thay cho "CLB Học bù", hoặc "dũng giả" thay cho "dũng sĩ".
- **Chưa kiểm chứng, cần bạn tự làm:**
  - Tên tệp .exe của client PC Yostar JP: chọn hồ sơ bằng tay trước, rồi lúc đang chơi, mở thẻ *Văn phong & game* của hồ sơ, bấm *Thêm cửa sổ đang mở…* và chọn cửa sổ game.
  - Nếu chơi bản Global tiếng Anh: trong thẻ *Văn phong & game*, đặt *Ngôn ngữ nguồn* là *Tiếng Anh* (trong tệp hồ sơ là `src_lang`).
  - Bố cục hộp thoại chưa được đối chiếu với ảnh chụp thật. App đã hiểu nhãn CLB nằm cạnh tên, dù là một nhãn ("ホシノ 対策委員会") hay hai nhãn ("ユウカ" và "セミナー"). Nhưng nhãn dài hơn 10 ký tự (như "ナギサ ティーパーティー") thì app chưa nhận ra đó là nhãn tên.
  - Nên dùng model 7B. Bản 1.8B thường dịch 先生 thành "Thầy".
- **Kết quả thử** (model 7B, câu tự viết theo phong cách BA, không lấy thoại của game, 2026-10-25):
  - Đúng cả nghĩa lẫn xưng hô: 40/48 câu thoại trong bộ thử của người review (lần review trước: 36/48) và 27/30 câu trong bộ thử lúc dựng hồ sơ (trước: 26/30). Tên riêng đúng ở mọi câu.
  - Lỗi còn lại chủ yếu là "em" nằm sai vai trong câu nhờ, câu cấm hoặc câu hỏi ý người nghe. Ví dụ: 好きなものを買ってくださいね → "Em mua…", 気にしないで → "em đừng…", 知りたい？ → "em muốn biết…", ようこそ → "Chào mừng em…". Bộ kiểm tra xưng hô chưa bắt được các lỗi này. Ngoài ra, Sensei thỉnh thoảng xưng "anh" (1 trên 11 câu).
  - Hồ sơ làm prompt dài thêm khoảng 400 token. Với 12 dòng lịch sử của 12 người nói khác nhau, prompt khoảng 4000 trên 4096 token. Nếu prompt quá dài, app tự bớt lịch sử và gửi lại. Nếu log hay báo "prompt too long", đặt `llm_ctx` lên 6144.

Ghi chú nghiên cứu dùng để soạn hồ sơ này: [`research_notes/Dịch game Nhật sang tiếng Việt/blue_archive.md`](../research_notes/Dịch%20game%20Nhật%20sang%20tiếng%20Việt/blue_archive.md).
