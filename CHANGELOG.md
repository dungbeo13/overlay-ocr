# Nhật ký thay đổi

Mọi thay đổi đáng chú ý của Screen Translator được ghi ở đây. Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/), số phiên bản theo [Semantic Versioning](https://semver.org/lang/vi/). Số phiên bản đang chạy hiện ở *Cài đặt* → *Chung*, khi di chuột lên biểu tượng 文 ở khay, và qua lệnh `.\run.ps1 --version`.

## [Chưa phát hành]

Chưa có thay đổi nào.

## [1.0.0] — 2026-10-25

Bản phát hành đầu tiên. Cách cài và cách dùng: [README](README.md).

### Thêm mới

**Dịch màn hình**

- Dịch liên tục một vùng màn hình do bạn chọn, tiếng Nhật hoặc tiếng Anh sang tiếng Việt, và vẽ bản dịch đè lên đúng chỗ chữ gốc. Ở chế độ tiếng Nhật, chữ chỉ có ký tự Latin vẫn được dịch như tiếng Anh.
- Chụp màn hình bằng dxcam, tự chuyển sang GDI khi dxcam không trả về hình; có thêm Windows Graphics Capture. OCR bằng RapidOCR trên GPU (CUDA), tự chạy bằng CPU khi máy không có GPU NVIDIA hoặc driver cũ.
- Bản dịch và mọi cửa sổ của app (menu, hộp thoại, cửa sổ *Cài đặt*) được ẩn khỏi ảnh chụp màn hình, nên app không bao giờ đọc lại chữ của chính mình.
- Phím tắt toàn cục: chọn vùng, tạm dừng, ẩn / hiện bản dịch, đổi ngôn ngữ nguồn, thoát. Vùng đã chọn được lưu cho lần sau.

**Dịch bằng LLM trên máy (tùy chọn)**

- Chế độ *Google trước, LLM tinh chỉnh*: bản Google hiện ngay, rồi được thay tại chỗ bằng bản của model Hy-MT2 (7B hoặc 1.8B, chạy bằng llama.cpp, chỉ nghe trên 127.0.0.1). Có thêm chế độ *Chỉ LLM* và *Tắt*.
- Tự chọn model theo VRAM còn trống; tự tắt server để trả VRAM khi bạn chơi game có anti-cheat hoặc sau 10 phút không dùng.
- `tools.setup_llm` tải llama.cpp và model, kiểm tra mã băm, tự chọn bản CUDA theo driver.

**Hồ sơ game và xưng hô**

- Hồ sơ cho từng game: thuật ngữ cố định, nhân vật (tên tiếng Việt, giới tính, cách tự xưng, quan hệ), văn phong, và danh sách tệp .exe để tự chuyển hồ sơ khi bạn mở game. Tên riêng LLM đề xuất được thêm dưới dạng gợi ý chờ bạn xác nhận.
- Kiểm tra xưng hô theo vai: khi bản dịch đặt đại từ sai người nói / người nghe, app tự sửa hoặc hỏi lại LLM một lần, chỉ khi chắc chắn. Trong lần thử gần nhất, bộ kiểm tra không làm hỏng câu nào vốn đã đúng.
- Thẻ *Nhân vật* trong cửa sổ *Thuật ngữ & nhân vật* (khay → *Hồ sơ* → *Sửa thuật ngữ / nhân vật…*): chọn cách tự xưng và cách gọi từng người nghe bằng bảng, có nút *Gợi ý*, và xem trước đúng những gì LLM sẽ được báo.
- Hồ sơ mẫu Blue Archive: 73 thuật ngữ và 107 nhân vật, soạn từ dữ kiện công khai; app đề nghị dùng khi bạn mở BlueArchive.exe.

**Tra cứu game mới**

- Khi bạn mở một game chưa có hồ sơ, app hỏi một lần (thông báo nhỏ ở khay, không đè lên game) rồi mới tra Steam, Wikidata, VNDB và wiki của game (Fandom, wiki.gg) để dựng hồ sơ nháp: nhân vật, giới tính, họ / tên gọi, địa danh, tổ chức. Chỉ tên game (và mã Steam nếu có) được gửi đi.
- Tìm trang Việt hóa và wiki qua SearXNG chạy trên máy bạn (`tools.setup_searxng`, chỉ nghe trên 127.0.0.1, app tự bật / tắt). Link Việt hóa chỉ được hiện, không bao giờ được tải.
- *Tạo yêu cầu tra cứu cho Claude…* và *Nhập hồ sơ* (từ file hoặc clipboard) để nhờ trợ lý AI soạn hồ sơ rồi nhập lại.

**Game có anti-cheat**

- App không inject, không đọc bộ nhớ hay process của game; mỗi giây chỉ xem tên tệp .exe của cửa sổ bạn đang dùng. Với game trong danh sách, chế độ mặc định *Tạm dừng* ngừng chụp màn hình, xóa bản dịch và tự chạy lại khi bạn rời game. Có thêm chế độ *Chỉ cảnh báo* và *Tắt*.

**Dùng hằng ngày**

- Shortcut Desktop / Start Menu mở app không kèm cửa sổ dòng lệnh, chỉ hiện biểu tượng 文 ở khay hệ thống; tùy chọn khởi động cùng Windows; `Tạo lại shortcut.cmd` khi chuyển thư mục app.
- Chỉ chạy một bản: mở lần nữa thì app đang chạy mở menu khay.
- Cửa sổ *Cài đặt* (Chung, Dịch, Hiển thị, Phím tắt, Anti-cheat, Nâng cao, Tra cứu game), phần lớn thay đổi áp dụng ngay khi bấm *Lưu*.
- *Hướng dẫn nhanh* 6 bước ở lần chạy đầu tiên.
- Lỗi lúc khởi động hiện trong hộp thoại tiếng Việt, có nút *Sao chép chi tiết* và *Mở thư mục nhật ký*.
- Số phiên bản: lệnh `--version`, tab *Chung* của *Cài đặt*, tooltip của biểu tượng khay.
- Bộ dọn rác của Python chỉ chạy trên luồng giao diện, để luồng phụ (tra cứu game, OCR, dịch) không hủy cửa sổ Qt đã đóng. Trước đó app có thể crash, hiếm gặp, trong lúc tra cứu game.

### Giới hạn đã biết

- Chỉ chạy trên Windows 11 24H2 trở lên; dịch bằng LLM trên máy cần GPU NVIDIA.
- Phần tự dừng khi gặp game có anti-cheat mới được kiểm thử tự động (giả lập), chưa thử với game thật.
- Bộ kiểm tra xưng hô chỉ can thiệp khi chắc chắn, nên vẫn lọt một phần lỗi (trong lần thử gần nhất, app bắt được khoảng 7/10 câu bị đặt xưng hô sai người). Bản 1.8B vẫn có lỗi nghĩa mà bộ kiểm tra không bắt được.
- Hồ sơ Blue Archive: tên exe của bản Yostar JP và bố cục hộp thoại thật chưa được kiểm chứng.
- Máy chủ SearXNG công cộng hiện không dùng được (không máy chủ nào trả JSON cho app), nên phần dự phòng này đang trống. Bản SearXNG bằng Docker (`deploy/searxng/`) chưa được thử.
- Chưa có OCR chuyên cho manga.

Danh sách đầy đủ: mục *Giới hạn hiện tại* trong [README](README.md#giới-hạn-hiện-tại) và [docs/ket-qua-do.md](docs/ket-qua-do.md).
