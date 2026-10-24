# SearXNG bằng Docker (tùy chọn)

App **không cần Docker**: `python -m tools.setup_searxng` cài SearXNG chạy thẳng trên Windows, và app tự bật / tắt nó (xem mục *SearXNG trên máy (tùy chọn)* trong `README.md` ở thư mục gốc). Thư mục này dành cho lúc bạn muốn chạy SearXNG bằng Docker thay cho bản đó.

- `docker-compose.yml`: một container SearXNG, cùng commit với bản cài thẳng (image `searxng/searxng:2026.10.24-ef0ec03da`). Cổng được mở là `127.0.0.1:8888:8080`, nên chỉ máy này vào được, máy khác trong mạng LAN không vào được. Không có Valkey/Redis, vì đã tắt limiter.
- `settings.yml`: cài đặt dùng chung cho cả bản Docker và bản cài thẳng: bật định dạng JSON, tắt limiter, tắt image proxy, `public_instance: false`.
- **Không chạy thẳng hai tệp trong thư mục này.** `secret_key` ở đây chỉ là chữ mẫu `ultrasecretkey`, và SearXNG sẽ không chịu chạy với chữ mẫu đó. Lệnh dưới đây tạo một bản sao có khóa ngẫu nhiên, nằm ngoài thư mục dự án, để khóa thật không bao giờ bị commit lên repo.

Chưa thử chạy bằng Docker.

## Docker Desktop (Windows)

1. Cài Docker Desktop rồi mở nó lên.
2. Trong thư mục dự án, chạy:

   ```powershell
   & $py -m tools.setup_searxng --docker
   ```

   Lệnh này ghi `docker-compose.yml` và `settings.yml` (đã có `secret_key` ngẫu nhiên) vào `%USERPROFILE%\.screen-translator\searxng\docker\`. Chạy lại lệnh thì khóa cũ được giữ; thêm `--reset-settings` nếu muốn tạo khóa mới.
3. Khởi động:

   ```powershell
   cd "$env:USERPROFILE\.screen-translator\searxng\docker"
   docker compose up -d
   ```

4. Kiểm tra: mở <http://127.0.0.1:8888>, hoặc chạy `& $py -m tools.setup_searxng --status`.

Trong *Cài đặt → Tra cứu game*, ô địa chỉ SearXNG để `http://127.0.0.1:8888`. Khi container đang chạy, app dùng luôn nó và không bật thêm bản cài thẳng. Khi container tắt mà vẫn để *app tự bật SearXNG*, app chạy bản cài thẳng trên cùng cổng đó (nếu đã cài).

Dừng: `docker compose down`. Cập nhật: sửa tag image trong `docker-compose.yml`, rồi chạy `docker compose pull` và `docker compose up -d`.

## docker compose trong WSL

1. Cài Docker Engine trong bản Linux của WSL 2 (ví dụ Ubuntu), theo hướng dẫn của Docker.
2. Chép thư mục `%USERPROFILE%\.screen-translator\searxng\docker` (đã tạo ở bước 2 phía trên) vào WSL, ví dụ:

   ```bash
   cp -r /mnt/c/Users/<tên bạn>/.screen-translator/searxng/docker ~/searxng
   ```

   Hoặc chép hai tệp trong thư mục này rồi tự thay `ultrasecretkey` bằng một khóa ngẫu nhiên:

   ```bash
   sed -i "s/ultrasecretkey/$(openssl rand -hex 32)/" settings.yml
   ```

3. Khởi động: `cd ~/searxng && docker compose up -d`.
4. Với WSL 2, cổng `127.0.0.1:8888` bên trong WSL được chuyển sang `127.0.0.1:8888` của Windows (cài đặt mặc định `localhostForwarding=true`; chế độ mạng `mirrored` cũng làm như vậy). Vì thế app vẫn dùng `http://127.0.0.1:8888`. Nếu Windows không vào được địa chỉ này, xem lại `%USERPROFILE%\.wslconfig`.

## Riêng tư

- Container chỉ mở cổng trên 127.0.0.1. Đừng đổi thành `8888:8080` hay `0.0.0.0:8888:8080`, vì như vậy cả mạng LAN dùng được máy tìm kiếm của bạn.
- App chỉ gửi tên game. SearXNG chuyển câu tìm kiếm đó tới Google, Bing, DuckDuckGo… Các trang đó thấy địa chỉ IP của bạn, nhưng không nhận cookie hay hồ sơ tài khoản nào.
