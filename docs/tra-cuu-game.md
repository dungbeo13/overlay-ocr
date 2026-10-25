# Tra cứu game mới và SearXNG

Trang này là phần tham khảo đầy đủ về tra cứu dữ liệu game mới (dựng hồ sơ nháp từ Steam, Wikidata, VNDB, wiki của game và kết quả tìm web) và về SearXNG chạy trên máy. [README](../README.md#tra-cứu-game-mới) chỉ tóm tắt phần này.

Trong các lệnh, `$py` là Python của venv; lệnh chạy trong PowerShell, từ thư mục app. Mở cửa sổ PowerShell mới thì dán trước 2 dòng ở mục [*Mỗi lần mở cửa sổ PowerShell mới*](../README.md#mỗi-lần-mở-cửa-sổ-powershell-mới) của README.

- [Tra cứu dữ liệu game mới](#tra-cứu-dữ-liệu-game-mới)
  - [Trong app: khay, cửa sổ tra cứu, Cài đặt](#trong-app-khay-cửa-sổ-tra-cứu-cài-đặt)
- [SearXNG trên máy (tùy chọn)](#searxng-trên-máy-tùy-chọn)

## Tra cứu dữ liệu game mới

Khi bạn chọn một game chưa có hồ sơ, app có thể tra dữ liệu cộng đồng **một lần** để dựng sẵn hồ sơ thuật ngữ theo kiểu hồ sơ Blue Archive: tên nhân vật, giới tính, họ / tên gọi tắt, địa danh, tổ chức. Không tìm thấy gì thì app chạy như trước.

- **Hỏi trước, mỗi game một lần.** Trước khi gửi gì lên mạng, app hỏi bạn. Câu trả lời được nhớ trong `data/lookup_state.json`. Thứ được gửi đi là **tên game** (đúng tên hiện trong cửa sổ hỏi; nếu bạn sửa tên thì chỉ gửi tên bạn sửa) và, khi game nằm trong thư viện Steam, **mã Steam** đọc từ tệp Steam của game. Không gửi đường dẫn, tên thư mục cá nhân, tên người dùng hay tên máy. Không muốn được hỏi nữa thì đặt `"lookup_ask_new_games": false`.
- **Nhận ra game chỉ bằng tệp trên đĩa**, không đụng tới process của game:
  - tên sản phẩm trong tệp .exe;
  - `steamapps/appmanifest_*.acf` và `libraryfolders.vdf` của Steam (cho mã Steam và tên chính thức);
  - tên thư mục của game (thư mục chứa exe, hoặc thư mục trên `bin` / `Binaries\Win64`…);
  - các tệp đánh dấu engine nằm cạnh exe (Unity, Unreal, Ren'Py, RPG Maker, KiriKiri, NScripter…).

  Tên exe chung chung như `Game.exe` và exe của engine (`SiglusEngine.exe`, `BGI.exe`, `AdvHD.exe`…) không được dùng làm tên game. Tên thư mục cũng không được dùng khi đó là thư mục chung (`Games`, `Desktop`, `ゲーム`…), thư mục nằm ngay trong thư mục người dùng, hay thư mục đồng bộ (`OneDrive - …`, `Dropbox`…), vì những tên đó có thể chứa tên bạn hay tên công ty.
- **Hồ sơ mẫu được xét trước.** [`profiles/templates/*.json`](../profiles/templates/) khớp theo tên exe, mã Steam hoặc tên game (Blue Archive, ブルーアーカイブ, ブルアカ…). Khớp thì app dùng luôn hồ sơ mẫu, không lên mạng.
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
  - Thuật ngữ bạn đã xóa không được thêm lại.
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

  Mỗi dòng dưới đây là một ví dụ riêng, **không dán cả khối**: chỉ chạy dòng bạn cần.

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

Kiểm thử phần tra cứu: xem [phat-trien.md](phat-trien.md#tra-cứu-game).

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

## SearXNG trên máy (tùy chọn)

Khi tra cứu một game, app có thể tìm thêm trên web: trang bản Việt hóa (“<tên game> Việt hóa”) hay wiki của game. Việc tìm này đi qua SearXNG, một máy tìm kiếm gộp mã nguồn mở chạy ngay trên máy bạn, không cần API key và không mất phí. Không có SearXNG thì app vẫn tra cứu bằng Steam, Wikidata, VNDB và wiki như thường, chỉ thiếu phần tìm trên web.

**Cài:**

Đây là danh sách lệnh, **không dán cả khối**: chỉ chạy dòng bạn cần.

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

**Docker** (cho sau này): [`deploy/searxng/`](../deploy/searxng/) có `docker-compose.yml` (cổng `127.0.0.1:8888:8080`, cùng commit với bản trên) và hướng dẫn trong [`deploy/searxng/README.md`](../deploy/searxng/README.md), gồm cả Docker Desktop lẫn docker compose trong WSL. `& $py -m tools.setup_searxng --docker` tạo sẵn thư mục chạy được, có `secret_key` thật, nằm ngoài repo.

Kiểm thử SearXNG: xem [phat-trien.md](phat-trien.md#searxng).
