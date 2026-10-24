# Khóa xưng hô, dịch chức năng, tránh văn convert

Muốn bản dịch game Nhật sang tiếng Việt vừa "mềm" vừa "sát nghĩa", cần làm ba việc cùng lúc. Thứ nhất, dịch *chức năng* của từng dấu hiệu tiếng Nhật (kính ngữ, đại từ, trợ từ cuối câu, giọng vai), không dịch hình thức của nó. Thứ hai, chốt xưng hô tiếng Việt theo từng *cặp nhân vật* và giai đoạn quan hệ, rồi đưa thẳng vào mỗi yêu cầu dịch. Thứ ba, chặn "văn convert" bằng quy tắc thuần Việt và danh sách từ cấm. Ba nhóm nguồn độc lập cùng dẫn tới đây: dân localize chuyên nghiệp (dịch sát làm nháp, biên tập cho tự nhiên, cấm thêm thắt), cộng đồng dịch Việt (xưng hô là lỗi bị chê nhiều nhất), và nghiên cứu dịch máy (mô hình nhỏ hay bỏ qua ngữ cảnh khi chọn đại từ, nên thông tin quyết định phải ghi rõ trong prompt). Kiến trúc app hiện tại (glossary theo game, character sheet với cặp đại từ cố định, ngữ cảnh hội thoại, bộ kiểm tra vai đại từ có hỏi lại, chỉ OCR với game có anti-cheat) đã khớp với cách LunaTranslator, GalTransl và VNTL làm, nên phần lợi còn lại nằm ở tinh chỉnh. Đòn bẩy lớn nhất có số liệu là bỏ Q4_K_M cho bản 7B. Theo báo cáo của chính Tencent, Q4_K_M giữ nguyên điểm FLORES nhưng tụt từ **83.14 xuống 75.11 điểm IFMTBench**. IFMTBench đo đúng khả năng làm theo chỉ dẫn mà glossary và quy tắc xưng hô cần tới. Kế đến là bám mẫu prompt chính thức của Hy-MT2, chỉ chèn mục glossary khớp với dòng đang dịch, giữ 3–8 dòng ngữ cảnh kèm bản dịch đã duyệt, và mở rộng bộ kiểm tra theo kiểu GalTransl. Có hai khoảng trống phải nói thẳng. Chưa có benchmark JA→VI nào cho LLM; kết quả ja–vi duy nhất tìm được là BLEU 9.64 từ thời trước LLM. Cũng chưa có quy ước tiếng Việt nào được ghi lại cho Blue Archive. Vì vậy hồ sơ BA ở cuối báo cáo là đề xuất cần tự kiểm, và app cần một bộ test JA→VI riêng.

## Dân localize chuyên nghiệp dịch chức năng, không dịch hình thức

Trong giới JP→EN, "dịch nguyên văn không tồn tại" là điều mọi người đều công nhận. Derek Heemsbergen (biên tập Dragalia Lost, Sakuna) nói thẳng như vậy. John Ricciardi (8-4) và Jessica Chavez (cựu Xseed) đều coi trọng ý đồ và cảm xúc mà đội gốc muốn người chơi nhận được hơn là khớp từng chữ ([Game Informer 2021](https://gameinformer.com/2021/11/24/the-trials-and-tribulations-of-video-game-localization)). Quy trình của Sega/Atlus có hai lớp. Người dịch đưa bản nháp sát nghĩa, "chính xác nhưng khô và cục", rồi biên tập viên thêm tính cách nhân vật vào ([Game Developer 2018](https://www.gamedeveloper.com/design/q-a-translating-the-humor-tone-of-i-yakuza-i-games-for-the-west); [Kotaku 2023](https://kotaku.com/yakuza-like-a-dragon-ishin-majima-kiryu-localization-1850263200)).

Tranh cãi thực sự không nằm ở chuyện "sát hay thoáng" mà ở chỗ được thoáng tới đâu, và có hai kiểu thất bại khác nhau. Kiểu thứ nhất là **dịch thiếu**: chữ đúng mà câu cứng. Gần như ai cũng coi đây là lỗi chất lượng. Vụ Ys VIII khiến CEO NIS America phải xin lỗi chính thức ngày 10/10/2017 và phát hành bản vá viết lại gần hết văn bản ([Niche Gamer](https://nichegamer.com/2017/10/10/nis-america-apologies-for-localization/)); Persona 5 cũng bị chính các dịch giả chuyên nghiệp chê là "stilted and unnatural" ([Destructoid](https://www.destructoid.com/some-fans-are-pissed-about-persona-5s-localization/)). Kiểu thứ hai là **dịch thừa**: thêm meme, hoặc thay dấu hiệu xã hội Nhật bằng khuôn mẫu Anh–Mỹ. Kiểu này chủ yếu bị fan biết tiếng Nhật phản đối. Ví dụ là cáo buộc rằng Umamusume EN bỏ mệnh đề 真剣勝負やぞ của Tamamo Cross và thêm "Let her cook" cho Daitaku Helios ([Localizers Exposed 2026](https://localizers.ch/column/when-localization-stops-translating-what-cygames-cedec-2026-umamusume-presentation-actually-shows/)). Trang này có lập trường rõ ràng và không trích trực tiếp slide của Cygames, nên chỉ nên đọc như ý kiến của một phía.

Từ hai cực đó rút ra được một ranh giới "trung thành nhưng tự nhiên" dùng được cho LLM. Mỗi dòng phải mang đủ thông tin, ý định, cảm xúc, mức lịch sự và tính cách của câu gốc. Câu chữ được tự do thay đổi. Không thêm nội dung không có trong gốc, không bỏ mệnh đề, và tránh tiếng lóng mạng thời thượng; tiếng lóng kiểu "kek" của Futaba trong P5 từng bị chế giễu rộng rãi.

**Kính ngữ** là ngoại lệ lớn nhất của nguyên tắc "dịch chức năng". Phụ đề Anh của Yakuza 0 giữ nguyên -san/-sama/-han/-chan/-kun nhưng đảo tên theo thứ tự phương Tây. Nhóm nghiên cứu Santos & Humblé coi đây là dấu hiệu khán giả ngày càng chấp nhận yếu tố ngoại lai ([Santos & Humblé 2023](https://www.redalyc.org/journal/7319/731976434018/movil/)). Sega từng dịch *aniki* thành "sir", bị chê, rồi tới Yakuza 6 giữ "aniki" và giải thích một lần bằng tooltip ở màn hình tải ([Game Developer 2018](https://www.gamedeveloper.com/design/q-a-translating-the-humor-tone-of-i-yakuza-i-games-for-the-west)). Quy tắc kinh nghiệm phổ biến là giữ kính ngữ khi truyện lấy bối cảnh Nhật, bỏ khi bối cảnh fantasy hoặc ngoài Nhật ([ANN Answerman](https://www.animenewsnetwork.com/answerman/2018-09-28/.137444)). Dù chọn cách nào, đó phải là một quy tắc thống nhất cho cả dự án. Khi kính ngữ *đổi* (bỏ -san, chuyển sang gọi trống tên), đó là một tình tiết của câu chuyện và phải hiện ra trong bản dịch.

**Đại từ và giọng vai** là nơi tiếng Anh "làm phẳng" nhiều nhất. Clyde Mandelin đã lập danh mục: 俺 thô và nam tính, 僕 nam mềm (con gái dùng thì thành tomboy), わたくし kiểu cách, わし ông già, 拙者 samurai, わらわ quý tộc kẻ cả. Ông khuyên đưa trước cho người dịch thông tin người nói và đại từ đã định, để tránh bị làm phẳng và thiếu nhất quán ([Legends of Localization 2019](https://legendsoflocalization.com/articles/personal-pronouns-in-japanese/)). Đây chính là việc character sheet của app làm. Kinsui gọi hiện tượng này là 役割語 (yakuwarigo): kiểu nói hư cấu, gắn với tuổi, giới và tầng lớp, dựa vào đại từ và trợ từ cuối câu mà tiếng Anh không có ([Wikipedia: Yakuwarigo](https://en.wikipedia.org/wiki/Yakuwarigo)). Một luận văn ở Leiden kết luận rằng không dịch trọn được role language. Tuy vậy nhân vật vẫn có thể giữ được tính cách nhờ dấu câu, cách trình bày chữ và lựa chọn từ ([Heerink 2018](https://studenttheses.universiteitleiden.nl/access/item:2604481/view)). Câu cửa miệng kiểu だってばよ mang giọng nhân vật chứ không mang nghĩa, nên phải tái tạo chức năng; tic tự chế trong ngôn ngữ đích lại rất dễ bị dùng không đều ([kodack](https://www.kodack.net/en/famous-anime-catchphrases/)). Một kỹ thuật cụ thể đáng học: NIS America chỉnh mức trang trọng của bà Roselia bằng mật độ từ rút gọn (contraction), ít trong cảnh thường và không có trong cảnh nghi lễ ([Game Informer 2021](https://gameinformer.com/2021/11/24/the-trials-and-tribulations-of-video-game-localization)). Tiếng Việt không có contraction. Núm vặn tương đương, theo suy luận của người nghiên cứu, là các tiểu từ "ạ/dạ/vâng" cùng việc chọn cặp xưng hô.

**Trợ từ cuối câu, phương ngữ, chơi chữ và chú thích** đều theo cùng logic. ね xin sự đồng tình về điều cả hai cùng biết, よ khẳng định điều chỉ người nói biết ([Jun Xu, UW–Madison](https://asset.library.wisc.edu/1711.dl/LQDBBOVKMPZJJ8U/R/file-e85ed.pdf)). Ngữ khí của chúng thường hiện ra qua giọng câu chứ không thành một từ riêng ([Self-Taught Japanese 2017](https://selftaughtjapanese.com/2017/10/05/japanese-emotive-sentence-ending-particles-ne-na-naa-yo-%E3%81%AD%E3%80%81%E3%81%AA%E3%80%81%E3%81%AA%E3%81%82%E3%80%81%E3%82%88/)). Không tìm thấy style guide chuyên nghiệp nào quy định riêng về trợ từ. Với Kansai-ben, Sega chủ trương không gán cứng sang một phương ngữ thật: đội đã thất bại nếu người chơi nghe mọi nhân vật Kansai "nói giọng miền Nam nước Mỹ" ([Kotaku 2023](https://kotaku.com/yakuza-like-a-dragon-ishin-majima-kiryu-localization-1850263200)). Chơi chữ thì phải dựng lại từ đầu: 布団が吹っ飛んだ thành "Practice safe sects!" ([Game Developer 2018](https://www.gamedeveloper.com/design/q-a-translating-the-humor-tone-of-i-yakuza-i-games-for-the-west)). Sega cũng thích giải thích văn hóa *ngay trong game* hơn là chú thích. Chú thích của người dịch (TL note) là dấu hiệu đặc trưng của scanlation và hiếm thấy trong bản dịch chuyên nghiệp ([Fabbretti 2016](https://orca.cardiff.ac.uk/96398/)). Về độ dài, một cụm năm chữ kanji có thể nở thành cả một dòng tiếng Anh ([Game Informer 2021](https://gameinformer.com/2021/11/24/the-trials-and-tribulations-of-video-game-localization)). Nhóm re:Edit của Fuwanovel cho rằng phần lớn dấu "……" trong bản dịch VN nên bỏ, chỉ giữ khi nó báo hiệu một câu bỏ lửng; nguồn này chỉ đọc được qua đoạn trích ([Fuwanovel](https://forums.fuwanovel.moe/blogs/entry/1264-killing-the-ellipsis-%E2%80%9C%E2%80%9D-in-vn-translations/)).

Về tổ chức công việc, Atlus chốt "key terms" ngay từ đầu Persona 5 ([PlayStation Blog 2017](https://blog.playstation.com/archive/2017/04/04/how-atlus-has-kept-persona-5s-localisation-authentic-to-the-japanese-original/)). Scott Strichart làm lượt rà soát nhất quán cuối cùng và đã viết lại khoảng 80% văn bản của một biên tập viên trên Yakuza 6 để khớp giọng Kiryu. Ở Radiant Historia, hai biên tập viên từng cho cùng một nhân vật hai giọng khác nhau ([Game Developer 2018](https://www.gamedeveloper.com/design/q-a-translating-the-humor-tone-of-i-yakuza-i-games-for-the-west)). Với LLM, điều này có nghĩa là character sheet phải đi kèm *mọi* yêu cầu dịch, không thể trông vào việc mô hình tự nhớ từ ngữ cảnh trước.

| Dấu hiệu tiếng Nhật | Kỹ thuật đã ghi nhận (JP→EN) | Chuyển sang tiếng Việt (suy luận) |
|---|---|---|
| Kính ngữ -san/-chan/-senpai | Giữ khi bối cảnh Nhật, một chính sách toàn dự án | Công tắc giữ/Việt hóa, cấm chồng kính ngữ |
| Keigo | Bỏ desu/masu, thể hiện bằng cách diễn đạt, chỉnh mật độ contraction | Tiểu từ ạ/dạ/vâng, đổi cặp xưng hô khi rơi keigo |
| 俺/僕/わたくし/わし | "Bơm lại" bằng từ vựng và cú pháp | Cặp xưng hô theo quan hệ, cộng giọng điệu |
| Tic, catchphrase | Một thủ pháp cố định cho mỗi nhân vật, dùng với tần suất có kiểm soát | Ghi vào character sheet, ví dụ "nya", "Uhe~" |
| Kansai-ben, giọng quê | Không gán cứng sang phương ngữ thật | Vài tiểu từ địa phương, không dựng phương ngữ đầy đủ |
| Chơi chữ | Dựng lại câu đùa | Gắn cờ cho người dùng; LLM 7B khó làm tốt |
| …… / ―― / nói lắp | Bỏ phần lớn "……", nói lắp chỉ ở chữ đầu | "...", "—", "Đ-Đừng" |

## Xưng hô là lỗi bị chê nhiều nhất trong bản dịch tiếng Việt

Người dịch Việt đều coi xưng hô là lựa chọn quan trọng nhất và cũng hay hỏng nhất. Nó phụ thuộc vào quan hệ, chênh lệch tuổi, cảm xúc của câu thoại và giai đoạn của mối quan hệ, chứ không phụ thuộc vào đại từ tiếng Nhật. Một blog dịch giả năm 2012 đưa ra bảng phân loại rõ nhất tìm được. **Bạn–mình** dùng cho người mới quen hoặc nhân vật rụt rè. **Cậu–tớ** "an toàn" nhưng không phải lúc nào cũng đúng. **Anh/cô–tôi** dùng cho hai người giữ khoảng cách, thường sau này chuyển thành **anh–em** vào lúc tỏ tình. **Mày–tao** dành cho kẻ thù hoặc bạn trai thân. **Ngươi–ta** dành cho truyện đánh nhau. Tác giả nhấn mạnh rằng cặp xưng hô phải *tiến triển* (bạn–mình → cậu–tớ → anh–em), và người dịch phải tự chọn thời điểm vì tiếng Nhật thường không đánh dấu sự chuyển đổi ([feilunhai 2012](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/)). Bộ style guide JP→VI cho LLM của người dùng voz Thang_97 (MTLS, 2026) biến ý này thành bảng theo chương. Nam chính gọi nữ chính "cậu" ở chương 1–2 và tránh hẳn "em" khi chưa thân, rồi chuyển "em" từ chương 3. Nữ chính tsundere tránh "anh" thân mật quá sớm. Bộ này còn có các quy tắc cấm cụ thể: "bà" chỉ dùng cho người trên 60 tuổi, không dùng "chú/bác" cho người chưa già ([MTLS romcom](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json)). Đây là bộ quy tắc của một tác giả, chưa phải đồng thuận của cộng đồng, nhưng là tài liệu JP→VI chi tiết nhất hiện có.

Độc giả để ý xưng hô rất kỹ. Một bài điểm sách LN bản quyền năm 2020 chê Oregairu (Hikari) chọn sai mức thân mật "tôi–cậu"/"tớ–cậu", và chê Re:Zero (IPM) tập này gọi "anh Subaru", tập sau gọi trống "Subaru" ([truyenbanquyen 2020](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)). Với bản dịch AI, lời phàn nàn chính trên voz là xưng hô "lúc thì 'ta/ngươi', lúc thì 'anh/em' loạn cào cào". Công cụ trong thread đó quảng cáo một bảng quan hệ (đặt "A là cha của B" thì AI dùng "cha/con"), và người dùng nhận xét rằng "quan trọng là chỉnh lại xưng hô" ([voz MC-AI](https://voz.vn/t/mc-ai-novel-trans-cong-cu-dich-truyen-tranh-tieu-thuyet-ho-tro-ai.1190485/)). Hướng dẫn dịch bằng AI trên docln khuyên đưa 2–5 chương đã dịch cho mô hình học xưng hô; nguồn này chỉ đọc được qua đoạn trích ([docln](https://docln.net/thao-luan/3268-huong-dan-dich-truyen-bang-ai)). Một bẫy ngữ nghĩa cần nhớ: bạn trai có thể gọi bạn gái là お前 ([dekiru](https://dekiru.vn/blog/detail-20160612203134372.htm)), nên 「お前のことが好きだ」 phải thành "Anh thích em", không phải "Tao thích mày". Câu ví dụ này là suy luận của người nghiên cứu, nhưng nó cho thấy vì sao không được ánh xạ theo từng đại từ.

**Kính ngữ chia đôi cộng đồng, nhưng có một luật cứng.** Blog năm 2012 khuyên chuyển kính ngữ sang danh xưng Việt, và tuyệt đối không viết "Anh Ninomiya san ơi" ([feilunhai 2012](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/)). Bài điểm sách năm 2020 lại muốn giữ nguyên ("dịch truyện Nhật thì nên dịch theo văn phong Nhật"). Người viết chê Nhã Nam dịch お兄様 của Miyuki "thẳng thừng ra là 'Anh hai'" vì mất sắc thái tôn kính, chê các phép dịch sát kiểu "đàn chị Momo" là "gượng", và khen Kem Đá (Hyouka) giữ "Chi-chan" ([truyenbanquyen 2020](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)). MTLS chọn đường giữa: giữ -san/-kun/-chan/-sama, dịch -sensei thành "thầy/cô", và dịch -senpai tùy ngữ cảnh ("anh/chị khóa trên" hoặc giữ nguyên) ([MTLS base](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json)). Chữ "senpai" đã đủ phổ biến để xuất hiện thẳng trong tên truyện tiếng Việt ([docln](https://docln.net/truyen/5797-100-dieu-em-khong-biet-ve-senpai)).

**Tên riêng viết theo Hepburn, họ trước tên sau. Hán-Việt phải "định khẩu phần".** MTLS ghi ví dụ 白川賢人 → Shirakawa Kento và đặt tỉ lệ Hán-Việt khoảng 5–10% cho thoại học đường hiện đại, 15–25% cho văn kể chuyện, 30–40% cho văn trang trọng hoặc cổ trang. Bộ này cấm các từ như "tiểu thư, công tử, ngài, đại nhân, mỹ lệ, ái mộ" trong bối cảnh hiện đại ([MTLS base](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json)). Lối Việt hóa tên kiểu Doraemon 1992 (Xu-ka, Xe-ko, Chai-en) là cách làm của một thời kỳ đã qua ([Saigoneer](https://saigoneer.com/saigon-culture/12042-doraemon-and-25-years-of-fostering-friendship-in-vietnam)). Fan còn phản đối đến mức SAO bản IPM phải đổi "Bông hồng xanh" về "Aobara" ([truyenbanquyen 2020](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)). Bản Genshin tiếng Việt chính thức giữ "Inazuma" bằng chữ Latin ([Genshin VI wiki](https://genshin-impact.fandom.com/vi/wiki/Inazuma)).

**"Văn convert" là mẫu tiêu cực mà cả cộng đồng dùng để so.** Đó là bản dịch thô từ Quick Translator hoặc Google: câu "lộn xộn, ngược câu", dày Hán-Việt khó hiểu. Đọc nhiều thì "tiếng Việt của bạn sẽ bị lậm convert" ([osach](https://osach.net/blogs/truyen-convert-la-gi-145); [linqqmignon 2021](https://linqqmignon.wordpress.com/2021/06/05/raw-va-convert-trong-edit-truyen/)). Quy định của docln cấm đăng bản Google chưa biên tập và hạn chế ghi chú cá nhân ([docln quy định](https://docln.net/thao-luan/2745-quy-dinh-doi-voi-truyen-dich)). MTLS đưa ra các cặp thay thế cụ thể: "nhìn" thay "ngắm nhìn/chiêm ngưỡng", "đi" thay "sải bước", "nói" thay "phán". Nó viết lại "Nàng ta sải bước nhẹ nhàng, dáng dấp ưa nhã khiến hắn ta ái mộ" thành "Cô ấy đi rất nhẹ nhàng và thanh lịch, khiến anh thích thú". Nó cũng phân loại thán từ học trò theo cảm xúc ("Ơ!?", "Hả!?", "Đ-Đừng nói thế!", "Chết tiệt..."), cấm "thưa/dạ vâng/kính thưa" trong lời teen, và dịch 彼女は綺麗だね thành "Cô ấy đẹp nhỉ", tức ね → "nhỉ" ([MTLS romcom](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json)). Độc giả nhận ra cả lỗi tiểu từ: một tập LN bị thay "nhé" bằng "lẽ" hàng loạt, và nhà phát hành phải nhận là lỗi biên tập ([truyenbanquyen 2020](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)).

Bảng ánh xạ trợ từ dưới đây dựa trên giải thích của các trang dạy tiếng Nhật: ね → "nhỉ" khi xin đồng tình, "nhé" khi đề nghị; よ → "đấy", "đâu" khi đi với phủ định ([riki](https://riki.edu.vn/goc-chia-se/cach-dung-vi-to-tieng-nhat/); [japo](https://vn.japo.news/contents/tieng-nhat/124050.html)). Phần còn lại là tổng hợp của người nghiên cứu. Nguyên tắc chung: chọn theo hành vi lời nói, và bỏ trợ từ khi ngữ điệu tiếng Việt đã đủ.

| Tiếng Nhật | Chức năng | Lựa chọn tiếng Việt |
|---|---|---|
| ね / よね | xin đồng tình, làm mềm | nhỉ / nhé / ha / phải không |
| よ | khẳng định, báo tin | đấy / đó / mà / cơ / đâu (phủ định) |
| ぞ / ぜ | nhấn mạnh kiểu nam | đấy / đi / hoặc chỉ "!" |
| かな / かしら | tự hỏi | nhỉ / không biết... |
| じゃん / でしょ | "rõ quá còn gì" | chứ gì / còn gì / đúng không |
| の? / なの? | hỏi mềm | à / hả / á |
| だもん | nũng nịu, cãi | ...mà! / ...cơ! |

Có một điểm hai trường phái đối nghịch nhau. Người nghiên cứu phần tiếng Việt cho rằng dùng tiểu từ miền Nam (nha/hông/nè) riêng cho nhân vật Kansai là hợp lệ. Sega thì cảnh báo không nên gán phương ngữ thật. Cách dung hòa hợp lý là chọn một nền phương ngữ cho cả dự án, rồi chỉ dùng một vài tiểu từ khác làm "màu" cho từng nhân vật, không dựng phương ngữ đầy đủ. Với tiếng tượng thanh, có ba cách đã được ghi nhận: dịch thành từ tượng thanh Việt, giữ nguyên kèm chú thích, hoặc thay bằng mô tả ngắn ([idichthuat](https://idichthuat.com/kinh-nghiem-dich-thuat-truyen-manga/)). Trong overlay, dạng ngoặc đơn như ドキドキ → "(thình thịch)" là gọn nhất.

## Blue Archive chưa có quy ước tiếng Việt, hồ sơ phải tự đặt

Blue Archive không có tiếng Việt chính thức. Trang Steam ghi "Tiếng Việt | Không được hỗ trợ" ([Steam](https://store.steampowered.com/app/3557620/Blue_Archive/?l=vietnamese)), và bản Steam toàn cầu (2025-07-04) loại trừ Việt Nam khỏi vùng phát hành ([Wikipedia](https://en.wikipedia.org/wiki/Blue_Archive)). Người chơi Việt vì thế có lẽ chủ yếu chơi trên mobile, giả lập hoặc client PC Nhật (ra mắt 2026-01-20). Nhân vật chính là 先生, không lồng tiếng và không có giới tính trong game ([Wikipedia](https://en.wikipedia.org/wiki/Blue_Archive)). Thoại tiếng Nhật thường bỏ đại từ ngôi hai và gọi thẳng 先生. Bản tiếng Anh giữ "Sensei", ví dụ 「よく来たね、先生。」 → "Welcome, Sensei…" (Blue Archive © NEXON Games Co., Ltd.; lines via bluearchive.wiki: [Hoshino/audio](https://bluearchive.wiki/wiki/Hoshino/audio)). Fan Việt cũng giữ "Sensei": tiêu đề video "Cái Chết Của Sensei Blue Archive Vietsub" ([TikTok](https://www.tiktok.com/discover/c%C3%A1i-ch%E1%BA%BFt-c%E1%BB%A7a-sensei-blue-archive-vietsub)), và một truyện dịch trên Wattpad vừa viết 'Sensei' vừa dùng "anh" khi kể, tức mặc định Sensei là nam ([Wattpad](https://www.wattpad.com/story/392461954-blue-archive-n%C6%A1i-mo%CC%A3i-c%C4%83m-h%C3%A2%CC%A3n-b%C4%83%CC%81t-%C4%91%C3%A2%CC%80u-quy%C3%AA%CC%89n-1)). Ngoài những dấu vết này, không tìm thấy glossary, bài Facebook hay trang Discord công khai nào ghi lại quy ước xưng hô của BA bằng tiếng Việt.

Từ đó, đề xuất của người nghiên cứu (là suy luận, chưa phải quy ước) như sau. Giữ "Sensei" để không gán giới tính cho người chơi. Học sinh xưng **em**, gọi **Sensei**, thêm "ạ" khi câu gốc dùng です/ます. Nhân vật nói suồng sã (Hoshino, Mika, Momoi) thì bỏ "ạ". Người lớn như Kaiser hay 黒服 xưng tôi, gọi ngài hoặc Sensei. Phản diện dùng ta/ngươi. Chế độ "Sensei nam" (thầy/em) là tùy chọn, không phải mặc định. Chỗ khó nằm ở lời tự xưng của Sensei trong các lựa chọn hội thoại. "Tôi" trung tính nhưng lạnh: chính MTLS khuyên tránh "tôi" cho nhân vật người lớn ấm áp vì nghe xa cách ([MTLS romcom](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json)). Vì vậy app nên coi đây là công tắc, với ba lựa chọn tôi / thầy / cô.

Các tên riêng cốt lõi đã có nguồn xác nhận cho cặp JP/EN: キヴォトス/Kivotos, 連邦捜査部シャーレ/Schale, シッテムの箱/Shittim Chest, アロナ/Arona, プラナ/Plana, 連邦生徒会/General Student Council, cùng tên đầy đủ các trường như アビドス高等学校/Abydos High School và トリニティ総合学園/Trinity General School ([ja.wikipedia](https://ja.wikipedia.org/wiki/ブルーアーカイブ); [Wikipedia](https://en.wikipedia.org/wiki/Blue_Archive)). Danh sách 13 trường theo tiếng Anh lấy từ bluearchive.wiki ([Abydos](https://bluearchive.wiki/wiki/Abydos_High_School)). Bản tiếng Việt duy nhất có thật là "Câu Lạc Bộ Phát Triển Game" cho ゲーム開発部, trong tên một bản scanlation ([truyentranhmanhua](https://truyentranhmanhua.org/truyen-tranh/blue-archive-cuoc-phieu-luu-cua-cau-lac-bo-phat-trien-game)). Mọi bản dịch tiếng Việt khác cho tên CLB đều là đề xuất. Ngay cả vài cặp JP/EN quen thuộc như 風紀委員会 = Prefect Team hay 補習授業部 = Make-Up Work Club cũng chưa được đối chiếu với nguồn gốc. Tên tổ chức viết bằng kanji (万魔殿, 百花繚乱) có thể dùng Hán-Việt (Vạn Ma Điện, Bách Hoa Liêu Loạn) như một lựa chọn phong cách nhất quán. Lựa chọn này không mâu thuẫn với quy tắc thuần Việt cho thoại, vì nó chỉ áp dụng cho danh từ riêng.

Tên nhân vật hiển thị bằng tên riêng, có ghi chú trang phục: "Hoshino (Swimsuit)", "Shiroko＊Terror". Nguyên âm dài được viết ra (Yuuka, Kikyou) ([bluearchive.wiki Characters](https://bluearchive.wiki/wiki/Characters)). Riêng アリス thì các nguồn không thống nhất: bluearchive.wiki viết "Arisu", Fandom viết "Tendou Alice", hàng bán lẻ viết "Aris Tendou" ([Arisu/audio](https://bluearchive.wiki/wiki/Arisu/audio); [Fandom](https://bluearchive.fandom.com/wiki/Tendou_Alice); [otakumode](https://otakumode.com/shop/69e0c52507f6df002574b0c8)). App cần chọn một cách viết và khóa lại.

Dữ liệu thoại song ngữ cho thấy một điều quan trọng về nguồn dịch. Bản tiếng Anh *làm phẳng* các tic của nhân vật: うへ～ của Hoshino lúc thành "Uhe~", lúc "Uhee~", lúc "Ugh."; lời tự gọi mình おじさん bị đổi thành "an old-timer like me" hay "my age", tức mất hẳn cái tự xưng ([Hoshino/audio](https://bluearchive.wiki/wiki/Hoshino/audio)). Aris tự gọi mình ở ngôi thứ ba (アリスは…), nhưng bản Anh lúc dùng "I", lúc dùng "Arisu". Thoại kiểu RPG của cô (lên cấp, điểm kinh nghiệm, HP) thì được dịch sát sang thuật ngữ game ([Arisu/audio](https://bluearchive.wiki/wiki/Arisu/audio)). Tiếng Việt lại giữ được cả hai điều này một cách tự nhiên: "Aris vui lắm ạ!" (trẻ con Việt vẫn hay tự gọi tên mình) và "ông chú này" cho おじさん. Đó là lý do nên dịch từ nguồn JP và chỉ dùng EN làm nguồn trung gian (pivot) tùy chọn.

Glossary ban đầu dựng từ dữ kiện công khai trên wiki: tên nhân vật, trường và CLB trên bluearchive.wiki và Wikipedia ([Characters](https://bluearchive.wiki/wiki/Characters); [Wikipedia](https://en.wikipedia.org/wiki/Blue_Archive)), cộng với ghi chú của chính người làm hồ sơ. Trang /audio của bluearchive.wiki cho thấy bản tiếng Anh xử lý giọng từng nhân vật ra sao; ghi chú giọng nói trong character sheet được viết lại bằng lời của người làm hồ sơ, không chép thoại. Cộng đồng có những bản trích dữ liệu từ tệp game; dự án này không dùng chúng. Thoại thuộc bản quyền của NEXON Games/Yostar, nên hồ sơ chỉ giữ tên và thuật ngữ, không phân phối lại thoại.

Về kỹ thuật, bản Steam dùng **Nexon Game Security**, còn bản Yostar JP dùng **XIGNCODE3** ([PCGamingWiki](https://www.pcgamingwiki.com/wiki/Blue_Archive)). Có một hướng dẫn tiếng Nga hook BlueArchive.exe bằng LunaTranslator bản sửa, nhưng chỉ đọc được qua đoạn trích và không có gì chứng minh là an toàn ([Steam guide](https://steamcommunity.com/sharedfiles/filedetails/?id=3768501145)). Kết luận: với BA, chỉ dùng OCR chụp cửa sổ, và cách này chạy giống nhau trên Steam, client JP và giả lập. Cần nói rõ là cũng không nguồn nào xác nhận NGS hay XIGNCODE3 *bỏ qua* công cụ chụp màn hình hoặc cửa sổ overlay; đây là giả định hợp lý, chưa được kiểm chứng. Bố cục hộp thoại (nhãn CLB cạnh tên, furigana, font) cũng cần một ảnh chụp thật để xác nhận.

## Công cụ cộng đồng đã giải sẵn bài toán OCR, ngữ cảnh và glossary

LunaTranslator hiện là công cụ đọc thời gian thực đầy đủ nhất. Ý tưởng dùng lại được nhiều nhất của nó là prompt dạng template. Các placeholder `{contextOriginal[N]}`, `{contextTranslation[N]}` và `{contextBoth[N]}` đưa các dòng trước, gồm cả câu gốc lẫn bản dịch, vào prompt. Placeholder `{DictWithPrompt[...]}` chỉ chèn những mục glossary khớp với câu, và "nếu không có mục nào khớp, trường này bị xóa" ([LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)). Với máy dịch truyền thống hoặc LLM không có placeholder, tên riêng được thay bằng ký hiệu "ZX?Z" theo cách của VNR rồi khôi phục sau khi dịch. Glossary riêng cho từng game có công tắc "Follow Default/Inherit Default" ([transoptimi](https://docs.lunatranslator.org/en/transoptimi.html)). Chi tiết đáng chú ý cho app: LunaTranslator xếp Hy-MT2 vào nhóm mô hình dịch offline chuyên dụng có *prompt cố định của nhà phát hành*, không cho sửa prompt ([LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)). Điều đó cho thấy mô hình này được thiết kế để dùng với mẫu chuẩn của nó.

Hook và OCR là hai lựa chọn khác hẳn nhau. Với VN offline (KiriKiri, RealLive, Ren'Py, TyranoScript), hook cho văn bản sạch và được mô tả là "the most reliable" ([VN Paths](https://vnpaths.com/how-to-translate-a-visual-novel/)). Nhưng hook nghĩa là tiêm DLL vào tiến trình game, cùng họ kỹ thuật với cheat trong mô tả của tài liệu bảo mật ([Black Hat EU 2014](https://www.blackhat.com/docs/eu-14/materials/eu-14-StJohn-Next-Level-Cheating-And-Leveling-Up-Mitigations-wp.pdf)). Không nhà phát hành gacha nào lên tiếng cho phép. Thậm chí ở một game F2P khác, nhà phát triển còn cảnh báo rằng sửa cả file localization cũng có thể bị khóa tài khoản tự động ([Gems of War](https://community.gemsofwar.com/t/will-i-get-banned-for-modifying-language-files/55235/7)). Overlay chỉ chụp màn hình vì vậy là thiết kế duy nhất không giống cheat về cấu trúc. Translumo cũng yêu cầu chế độ borderless hoặc windowed để overlay hiển thị đúng ([Translumo](https://github.com/ramjke/Translumo)).

Phần OCR của LunaTranslator có một cơ chế kích hoạt ba ngưỡng. **Image Stability** so các khung hình liên tiếp. **Image Consistency** so với khung lúc OCR lần trước, và tài liệu gọi đây là "tham số quan trọng nhất". **Text Similarity** so khoảng cách chỉnh sửa (edit distance) với văn bản OCR trước, để không dịch lại khi OCR chỉ rung nhẹ vài ký tự. Tài liệu khuyên đặt độ ổn định bằng 0 cho cảnh tĩnh và tăng lên khi nền chuyển động. Chế độ nhiều vùng nhận dạng riêng từng hộp, chẳng hạn hộp tên và hộp thoại ([ocrparam](https://raw.githubusercontent.com/HIllya51/LunaTranslator/main/docs/en/ocrparam.md)). Bộ lọc văn bản có các mục nối dòng không khoảng trắng cho tiếng Nhật, chuẩn hóa Unicode, và xóa ruby dạng `{kanji/furigana}`. Mục xóa ruby chỉ có tác dụng với văn bản hook ([textprocess](https://docs.lunatranslator.org/en/textprocess.html)). Không nguồn nào mô tả thuật toán xóa furigana trong ảnh OCR. Về động cơ OCR, meikiocr (Apache-2.0, chạy ONNX) được thiết kế riêng cho chữ trong game Nhật, và tác giả tuyên bố nó vượt PaddleOCR và EasyOCR, nhưng chưa có số liệu công bố ([meikiocr](https://github.com/rtr46/meikiocr)).

Các công cụ dịch theo lô cho thấy một bài học về thứ tự công việc. LinguaGacha chạy **trích thuật ngữ → dịch toàn bộ → tự soát lại** ([LinguaGacha](https://github.com/neavo/LinguaGacha)). Quy trình Trung Quốc dùng MTool kết hợp KeywordGacha để dựng glossary trước khi dịch ([Bilibili](https://www.bilibili.com/opus/1038930345281454117)). XUnity.AutoTranslator lưu mọi bản dịch tự sinh vào `_AutoGeneratedTranslations.txt` với ưu tiên thấp nhất. File của người dùng đè lên được, ALT+R tải lại, và có "splitter regex" để tách các chuỗi theo mẫu ([XUnity](https://github.com/bbepis/XUnity.AutoTranslator)). Plugin LlmTranslators của nó dùng temperature 0.2 trong ví dụ, chỉ đưa vào prompt những mục glossary khớp, và khuyên ghi rõ thể loại, giọng văn, quy tắc tên riêng cùng câu "output only the translation" ([LlmTranslators](https://github.com/joshfreitas1984/XUnity.AutoTranslate.LlmTranslators)). Vụ romhack.ing gỡ bản vá dịch hoàn toàn bằng AI của Langrisser V vào tháng 6/2026 nhắc rằng cộng đồng không chấp nhận bản dịch máy không có vòng sửa của người ([Time Extension](https://www.timeextension.com/news/2026/06/romhack-ing-disables-ai-translated-patch-downloads-following-langrisser-v-upset)). Một cache mà người dùng sửa đè được chính là vòng sửa đó.

Về thực hành của người Việt, một blog hướng dẫn dùng LunaTranslator ở chế độ OCR với Gemini 2.0 Flash-Lite, tắt streaming, và đặt ngữ cảnh khoảng **3–10 dòng "để giữ cách xưng hô nhất quán"**. Tác giả nhận xét bản tiếng Việt "dịch hơi bị sai 1 chút, nhưng tổng thể vẫn ok". Google Lens OCR tốt nhất nhưng cần mạng và chậm hơn ([Yukihana Games](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/)). LunaTranslator có tài liệu tiếng Việt ([docs/vi](https://docs.lunatranslator.org/vi/)), và đã có một công cụ dịch màn hình game do người Việt làm, cho nhập thông tin game làm ngữ cảnh ([RSTGameTranslation](https://thanhkeke97.github.io/RSTGameTranslation/index_vi.html)).

## Không có benchmark JA→VI, còn Q4_K_M cắt 8 điểm tuân thủ chỉ dẫn

Bằng chứng về mô hình cho JA→VI rất mỏng. VNTL, bảng xếp hạng duy nhất dành riêng cho VN, chấm 256 dòng JA→EN đơn lẻ bằng độ tương đồng embedding, không dùng LLM làm giám khảo, và không thêm mô hình mới từ khoảng đầu 2025 ([VNTL](https://huggingface.co/datasets/lmg-anon/vntl-leaderboard); [benchmarklist](https://benchmarklist.com/benchmarks/vntl/)). Trên bảng đó, GPT-4o đạt 0.7516, mô hình local tốt nhất (Qwen2.5-72B Q5_K_M) đạt 0.7079, còn **Google Translate chỉ 0.5395**. Đây là số JA→EN và đo từng dòng, nhưng nó gợi ý rằng Google chỉ nên là bản tạm hiển thị trước. JP-TL-Bench (2025–26) so cặp với giám khảo gemini-2.5-flash. Qwen3-30B-A3B-Instruct-2507 đứng thứ ba với 84.33% thắng, sau Gemini 2.5 Pro và Flash ([jp-tl-bench](https://github.com/shisa-ai/jp-tl-bench)). Bài báo của bộ này nêu đúng loại lỗi mà điểm theo câu bỏ sót, là đại từ sai giới tính ([arXiv 2601.00223](https://arxiv.org/pdf/2601.00223)). Ở WMT25, hệ thống Hunyuan đứng đầu en-ja theo điểm tự động, nhưng Slator nhận xét hệ thống này dường như được huấn luyện để tối ưu thẳng cho chính các metric đó ([Slator](https://slator.com/wmt25-preliminary-results-gemini-2-5-pro-gpt-4-1-lead-ai-translation/)). Báo cáo Hy-MT2 (5/2026) không có điểm theo cặp ngôn ngữ nào cho ja, vi hay ja–vi ([Hy-MT2 report](https://arxiv.org/html/2605.22064v2)). Kết quả ja–vi duy nhất tìm được là BLEU 9.64 từ năm 2018 ([NLP-Vietnamese-progress](https://github.com/undertheseanlp/NLP-Vietnamese-progress/blob/master/tasks/machine_translation.md)). Một mô hình Vi–Ja năm 2025 đã bị rút bài vì kết quả không hợp lệ ([arXiv 2504.00339](https://arxiv.org/abs/2504.00339)).

Hy-MT2 vẫn là lựa chọn hợp lý: giấy phép Apache-2.0, có vi và ja, và theo bảng của chính Tencent đạt FLORES XX↔XX XCOMET **86.89 cho bản 7B**, gần Gemini 3.1 Pro (88.74) ([Hy-MT2 report](https://arxiv.org/html/2605.22064v2)). Đây là số do nhà cung cấp tự công bố. Riêng điểm WMT25 thì bản trích xuất bị lệch cột, nên không nên dựa vào từng ô. Điểm then chốt cho app nằm ở bảng lượng tử hóa:

| Bản | FLORES XX↔XX (XCOMET) | IFMTBench (tuân thủ chỉ dẫn khi dịch) |
|---|---|---|
| 7B BF16 | 87.06 | 83.14 |
| 7B FP8 | 86.92 | 82.38 |
| **7B Q4_K_M** | 86.90 | **75.11** |
| 1.8B BF16 | 79.21 | 69.36 |
| 1.8B Q4_K_M | 77.19 | 63.47 |
| 1.8B 2-bit | 76.31 | 58.99 |

Nguồn: [Hy-MT2 report](https://arxiv.org/html/2605.22064v2). Q4_K_M giữ gần như nguyên chất lượng dịch thuần, nhưng mất khoảng 8 điểm đúng ở phần làm theo chỉ dẫn: thuật ngữ, ngữ cảnh, phong cách. Mức 75.11 xấp xỉ Qwen3.6-35B-A3B (74.66), trong khi bản BF16 vượt Gemma4-26B-A4B (79.22). Nói cách khác, glossary và cặp xưng hô của app đang chạy trên phiên bản *kém nghe lời nhất* của mô hình. Model card cũng cho biết thêm vài điều. Thông số lấy mẫu khuyến nghị là temperature 0.7, top_p 0.6, top_k 20, repetition_penalty 1.05. Không có system prompt mặc định. GGUF 7B cần STQ kernel từ llama.cpp PR #22836. Có các mẫu chính thức cho thuật ngữ ("Reference the following translations:" kèm các dòng "X translates to Y"), cho ngữ cảnh (đặt bối cảnh trước câu nguồn, "taking the provided background information into consideration") và cho phong cách ("strictly conform to" một style) ([Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B)). OpenRouter ghi ngữ cảnh 8,192 token cho bản 1.8B; bản 7B không ghi ([OpenRouter](https://openrouter.ai/models/tencent/hy-mt2-1.8b)). Các lựa chọn khác đều có nhược điểm. TranslateGemma bắt buộc một lượt user duy nhất, ngữ cảnh 2K, và không chính thức hỗ trợ glossary hay ngữ cảnh ([TranslateGemma](https://huggingface.co/google/translategemma-12b-it)). SakuraLLM chỉ dịch sang tiếng Trung và cấm dùng thương mại ([SakuraLLM](https://github.com/SakuraLLM/SakuraLLM)). Qwen3-30B-A3B ở Q4 cần khoảng 18 GB, không vừa 16 GB khi đang chạy game; đây là ước tính, chưa đo.

Bằng chứng về **ngữ cảnh** khá nhất quán. Nghiên cứu dịch manga ở COLING 2025 cho thấy dịch theo trang tốt hơn dịch từng dòng (ChrF 32.7 → 36.0). Thêm ngữ cảnh vượt quá một trang thì không giúp gì, dịch cả tập trong một lần là tệ nhất, và tóm tắt cuộn (chain-of-density) là cách tốt nhất trong các phương pháp ngữ cảnh dài ([Lippmann et al. 2025](https://arxiv.org/html/2411.02589v2)). Một bài tổng quan ghi nhận cấu hình phổ biến là 3–5 cặp câu trước đó ([arXiv 2506.07583](https://arxiv.org/pdf/2506.07583)). DelTA thấy rằng với cửa sổ lớn, LLM "mất dấu thông tin chi tiết của từng câu" ([DelTA](https://arxiv.org/pdf/2410.08143)). Phát hiện quan trọng nhất cho tiếng Việt là kết quả thử chín LLM, kể cả loại chuyên dịch: lợi thế của chúng so với mô hình encoder-decoder "không thể hiện ở việc dịch đại từ", và mô hình càng nhỏ càng ít dùng ngữ cảnh ([Mohammed & Niculae](https://arxiv.org/abs/2410.14391)). Nói cách khác, không thể trông vào lịch sử hội thoại để mô hình 7B tự suy ra "em/Sensei". Cặp xưng hô phải được ghi rõ.

Các định dạng của cộng đồng đều đi theo hướng ghi rõ đó. VNTL có khối Metadata (`[character] Name | Gender | Aliases`) và dòng nguồn có tên người nói ([vntl-llama3-8b-v2](https://huggingface.co/lmg-anon/vntl-llama3-8b-v2-gguf)). GalTransl dùng JSON name/message, và nhà phát triển cho biết chất lượng tốt hơn "khi tên được trích đúng". Nó chỉ gửi mục glossary khi thuật ngữ xuất hiện, và có bộ kiểm tra tự động: từ lặp, tiếng Nhật còn sót, số dòng lệch, bản dịch dài bất thường, thuật ngữ không được dùng ([GalTransl](https://github.com/GalTransl/GalTransl)). Sakura v1.0 cho rằng glossary giúp tăng "đặc biệt là độ chính xác đại từ". Nhưng system prompt của nó *cấm thêm đại từ không có trong gốc* ([SakuraLLM](https://github.com/SakuraLLM/SakuraLLM)). Quy tắc này sai với tiếng Việt, vì câu Việt thường cần một danh xưng rõ ràng ở chỗ tiếng Nhật bỏ trống. Một người dịch sách bằng LLM đã giải quyết bằng cách cho LLM tạo bản tóm tắt quan hệ và xưng hô rồi gắn vào chỉ dẫn ([TranslateBooksWithLLMs](https://github.com/hydropix/TranslateBooksWithLLMs/discussions/238)).

Với **few-shot, nhiệt độ và định dạng**, bằng chứng nghiêng về sự đơn giản. Chất lượng ví dụ quan trọng hơn số lượng: đổi bộ 5-shot ngẫu nhiên làm BLEURT dao động hơn 1 điểm ([Vilar et al. 2023](https://aclanthology.org/2023.acl-long.859)), còn trong nghiên cứu manga, 1-shot ngang 5-shot ([Lippmann et al. 2025](https://arxiv.org/html/2411.02589v2)). Với ChatGPT, nhiệt độ thấp thường tốt hơn, còn chain-of-thought làm hại vì đẩy mô hình về dịch từng chữ ([Peng et al. 2023](https://arxiv.org/pdf/2303.13780)). Ép định dạng JSON làm giảm rõ khả năng suy luận, dù thí nghiệm này không thử với dịch thuật ([Tam et al. 2024](https://arxiv.org/pdf/2408.02442)). Các khuyến nghị nhiệt độ mâu thuẫn nhau theo từng mô hình: Sakura 0.1, VNTL 0, TranslateGemma greedy, còn Hy-MT2 0.7. Chưa có phép đo độc lập nào cho Hy-MT2, nên đây là việc phải A/B. Về tinh chỉnh hai lượt, TEaR cải thiện được bằng chính phản hồi của mô hình ([TEaR](https://www.arxiv.org/pdf/2402.16379)). TasTe cho thấy lợi lớn nhất nằm ở các bản nháp kém ([TasTe](https://arxiv.org/pdf/2406.08434)), và MT-Ladder cho thấy lợi giảm dần qua mỗi vòng ([MT-Ladder](https://arxiv.org/pdf/2406.15741)). Ba kết quả này ủng hộ đúng thiết kế "chỉ hỏi lại dòng bị gắn cờ" mà app đang có. Lỗi gốc rễ vẫn là đại từ zero (bị lược) của tiếng Nhật: thông tin cần thiết nằm ngoài câu, và tăng cường dữ liệu không giải quyết được ([Ri et al.](https://arxiv.org/pdf/2107.00318)). Chính vì vậy app phải tự theo dõi ai đang nói với ai.

## Mười tám khuyến nghị cho app, xếp theo đòn bẩy

Vì kiến trúc đã đúng hướng, các khuyến nghị dưới đây chủ yếu là tinh chỉnh. Cột "Loại" phân biệt **bằng chứng** (có nguồn đo hoặc tài liệu tác giả) với **suy luận** (tổng hợp của người nghiên cứu, cần app tự kiểm).

| # | Khuyến nghị | Cơ sở | Loại |
|---|---|---|---|
| 1 | Đổi Hy-MT2-7B từ Q4_K_M sang **Q8_0**, hoặc Q6_K nếu thiếu VRAM. Chỉ dùng 1.8B khi bắt buộc cần độ trễ thấp, và khi đó cũng tránh Q4. | IFMTBench 83.14→75.11 (7B), 69.36→63.47 (1.8B) ([report](https://arxiv.org/html/2605.22064v2)) | Bằng chứng (số của nhà cung cấp). Giả định Q8_0 ≈ FP8 và mức VRAM ~8.5/~6.5 GB là suy luận. |
| 2 | Mỗi yêu cầu dịch ghi rõ **người nói, người nghe, cặp xưng hô của cặp đó, mức lịch sự**. Tên người nói lấy từ vùng OCR riêng và tra glossary, không bao giờ đưa qua máy dịch. | VNTL metadata, GalTransl name/message, [Mohammed & Niculae](https://arxiv.org/abs/2410.14391), multi-region của LunaTranslator | Bằng chứng về định dạng và về điểm yếu của mô hình. Cách đoán người nghe là suy luận. |
| 3 | Bám **mẫu chính thức của Hy-MT2** (terminology + contextual + style), chỉ dẫn bằng tiếng Anh, kết bằng "without additional explanation", không dùng system prompt kiểu chat. | [Model card](https://huggingface.co/tencent/Hy-MT2-7B); LunaTranslator dùng prompt cố định cho Hy-MT2 | Từng mẫu có bằng chứng. Ghép ba mẫu là suy luận, cần A/B. |
| 4 | Glossary **chỉ chèn mục khớp**. Sau khi dịch, kiểm tra thuật ngữ đích có xuất hiện. Với Google MT, đổi tên riêng sang placeholder rồi khôi phục, để tên ở bản tạm khớp với bản LLM. | [GalTransl](https://github.com/GalTransl/GalTransl), [transoptimi](https://docs.lunatranslator.org/en/transoptimi.html) | Bằng chứng (thiết kế công cụ). Hiệu quả chưa đo. |
| 5 | Ngữ cảnh **4–6 dòng** (khoảng 3–8), gồm câu JP và bản VI *đã qua bộ kiểm tra*. Không đưa output của Google vào lịch sử. Reset khi đổi cảnh. | [Lippmann 2025](https://arxiv.org/html/2411.02589v2), [survey](https://arxiv.org/pdf/2506.07583), [Yukihana](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/) | Khoảng 3–8 có bằng chứng. Con số mặc định và quy tắc lọc là suy luận. |
| 6 | Mở rộng bộ kiểm tra vai đại từ thành bộ kiểu GalTransl: kana/kanji còn sót, độ dài bất thường, thuật ngữ bị bỏ, kính ngữ chồng ("anh X-san"), từ cấm, cặp xưng hô sai. **Hỏi lại tối đa một lần**, kèm mô tả lỗi cụ thể. | [GalTransl](https://github.com/GalTransl/GalTransl), [TasTe](https://arxiv.org/pdf/2406.08434), [MT-Ladder](https://arxiv.org/pdf/2406.15741) | Bằng chứng. Giới hạn "một lần" là suy luận. |
| 7 | **A/B nhiệt độ**: cấu hình nhà sản xuất 0.7/0.6/20/1.05 so với 0.2 (giữ nguyên các tham số khác). Chọn theo bộ test riêng. | [Model card](https://huggingface.co/tencent/Hy-MT2-7B) đối lập với [Peng 2023](https://arxiv.org/pdf/2303.13780), [LlmTranslators](https://github.com/joshfreitas1984/XUnity.AutoTranslate.LlmTranslators) | Bằng chứng mâu thuẫn, chưa có đo độc lập. |
| 8 | Dựng **bộ test JA→VI riêng**: 200–300 dòng từ BA và VN đang chơi, chấm cặp theo kiểu JP-TL-Bench, cộng checklist xưng hô. | [jp-tl-bench](https://github.com/shisa-ai/jp-tl-bench); chưa có benchmark JA→VI | Phương pháp có bằng chứng. Quy mô là suy luận. |
| 9 | **Kích hoạt OCR ba ngưỡng**. Vùng tên, thoại và lựa chọn tách riêng. Nối dòng không khoảng trắng. Lọc furigana theo hình học: bỏ các hộp có chiều cao chữ dưới khoảng 50–60% trung vị và nằm sát phía trên dòng chính. | [ocrparam](https://raw.githubusercontent.com/HIllya51/LunaTranslator/main/docs/en/ocrparam.md), [textprocess](https://docs.lunatranslator.org/en/textprocess.html) | Ngưỡng và vùng có bằng chứng. Bộ lọc furigana là suy luận. |
| 10 | **Cache bền vững** theo văn bản nguồn đã chuẩn hóa. Người dùng sửa đè được, có phím tải lại. | [XUnity](https://github.com/bbepis/XUnity.AutoTranslator), [Time Extension](https://www.timeextension.com/news/2026/06/romhack-ing-disables-ai-translated-patch-downloads-following-langrisser-v-upset) | Thiết kế có bằng chứng. Giá trị với chuỗi lặp của gacha là suy luận. |
| 11 | **Cặp xưng hô có giai đoạn.** Phát hiện tín hiệu đổi trong câu gốc (rơi keigo, 私→俺, tên→君/お前) để gắn cờ cho người dùng xác nhận, kèm phím tắt "từ giờ đổi cặp". | [feilunhai](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/), [MTLS romcom](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json) | Thực hành cộng đồng. Tính năng là suy luận. |
| 12 | **Chính sách kính ngữ** là công tắc theo hồ sơ (giữ hoặc Việt hóa), luôn cấm chồng kính ngữ. | [feilunhai](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/), [truyenbanquyen](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/), [MTLS base](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json) | Bằng chứng cộng đồng. |
| 13 | **Danh sách từ cấm chống văn convert** theo bối cảnh (hiện đại hay cổ trang), đặt ở bộ kiểm tra chứ không nhồi vào prompt. | [MTLS base](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json) | Danh sách có nguồn (một tác giả). Đặt ở checker là suy luận, để prompt của mô hình 7B được ngắn. |
| 14 | **BA: khóa cứng chế độ ocr_only.** Nguồn JP là mặc định, EN là tùy chọn để A/B. | [PCGamingWiki](https://www.pcgamingwiki.com/wiki/Blue_Archive), [Hoshino/audio](https://bluearchive.wiki/wiki/Hoshino/audio) | Anti-cheat có bằng chứng. Việc chụp màn hình an toàn chưa được xác nhận. |
| 15 | Tạo **glossary BA** từ dữ kiện công khai trên wiki (tên, trường, CLB) và ghi chú riêng; ghi chú giọng nói tự viết sau khi đọc trang /audio của bluearchive.wiki. Chỉ giữ thuật ngữ, không dùng dữ liệu trích từ tệp game. | [bluearchive.wiki Characters](https://bluearchive.wiki/wiki/Characters), [Wikipedia](https://en.wikipedia.org/wiki/Blue_Archive) | Nguồn có bằng chứng. Cột VI là đề xuất. |
| 16 | **Một ví dụ mẫu chất lượng cao** cho mỗi nhân vật chính trong character sheet, không cần nhiều. | [Vilar 2023](https://aclanthology.org/2023.acl-long.859), [Lippmann 2025](https://arxiv.org/html/2411.02589v2) | Bằng chứng gián tiếp (không phải trên Hy-MT2). |
| 17 | **Chú thích ở lần xuất hiện đầu** (tùy chọn) cho mục glossary có gắn cờ. | [Game Developer 2018](https://www.gamedeveloper.com/design/q-a-translating-the-humor-tone-of-i-yakuza-i-games-for-the-west), [MTLS base](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json) | Thực hành có bằng chứng. Tính năng là suy luận. |
| 18 | **Tóm tắt cảnh cuộn**, làm sau và chạy nền. | [Lippmann 2025](https://arxiv.org/html/2411.02589v2) (GPT-4 Turbo, manga) | Bằng chứng trên mô hình lớn. Hiệu quả với 7B chưa rõ. |

### Mẫu prompt cho Hy-MT2 (suy luận, cần A/B)

Mẫu dưới đây ghép ba mẫu chính thức của Tencent. Các câu chỉ dẫn phải chép đúng nguyên văn từ model card. Hai câu thoại JP là câu tự viết theo phong cách của Hoshino, không phải thoại của game; bản tiếng Việt cũng là minh họa của người nghiên cứu. Cặp xưng hô được ghi rõ ngay trong khối bối cảnh, vì theo các nghiên cứu ở trên, mô hình nhỏ sẽ không tự suy ra từ lịch sử.

```text
Reference the following translations:
先生 translates to Sensei
ホシノ translates to Hoshino
おじさん translates to ông chú này

Background:
Game: Blue Archive. Speaker: ホシノ (Hoshino, student). Listener: 先生 (Sensei).
Address terms: Hoshino calls the listener "Sensei"; she refers to herself as "em",
or "ông chú này" when she says おじさん. Casual tone, no "ạ".
Previous lines:
[ホシノ] おや、先生。いらっしゃい～ => Ồ, Sensei. Vào đi~

Taking the provided background information into consideration, translate the following
segment into Vietnamese, strictly conforming to this style: natural spoken Vietnamese;
use exactly the given address terms; keep the speaker's tone; do not add or omit content.
Output only the translation, without additional explanation.

[ホシノ] うへ～、こんなに暑いと おじさんは もう動けないよ～
```

Bản mong đợi (minh họa): "Uhe~ nóng thế này thì ông chú này hết nhúc nhích nổi rồi~". Khi bộ kiểm tra gắn cờ (chẳng hạn bản dịch ra "tôi" hoặc "bạn"), lượt hỏi lại nên giữ nguyên prompt và thêm một dòng mô tả lỗi cụ thể trong Background, ví dụ "Previous attempt used 'bạn'; the required address term is 'Sensei'". Lý do: hướng dẫn sửa theo lỗi cụ thể đã cho thấy cải thiện, dù lợi ích của phản hồi quá chi tiết khi không fine-tune vẫn chưa rõ ([Ki & Carpuat](https://arxiv.org/pdf/2404.07851)).

### Quy tắc dịch: cái nào vào prompt, cái nào vào bộ kiểm tra

Mô hình 7B lượng tử hóa làm theo chỉ dẫn kém hơn. Vì vậy prompt chỉ nên chở *sự kiện* (glossary, người nói, cặp xưng hô), còn *luật* nên giao cho bộ kiểm tra xác định (deterministic) và hậu xử lý. Cách chia này là suy luận từ số IFMTBench.

| Quy tắc | Nơi thực thi | Nguồn |
|---|---|---|
| Dùng đúng cặp xưng hô được cấp; cấm "tôi/bạn" mặc định, cấm "hắn" cho nhân vật thiện cảm | Prompt (Background) + checker | [MTLS romcom](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json), [voz](https://voz.vn/t/mc-ai-novel-trans-cong-cu-dich-truyen-tranh-tieu-thuyet-ho-tro-ai.1190485/) |
| Không thêm, không bỏ nội dung, không chèn meme | Prompt (style) + checker tỉ lệ độ dài | [Localizers Exposed](https://localizers.ch/column/when-localization-stops-translating-what-cygames-cedec-2026-umamusume-presentation-actually-shows/), [GalTransl](https://github.com/GalTransl/GalTransl) |
| Tên và thuật ngữ theo glossary | Prompt (Reference) + checker | [Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B), [GalTransl](https://github.com/GalTransl/GalTransl) |
| Không chồng kính ngữ ("anh Tanaka-san") | Checker regex | [feilunhai](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/) |
| Không còn kana/kanji trong bản dịch | Checker | [GalTransl](https://github.com/GalTransl/GalTransl) |
| Từ cấm văn convert trong bối cảnh hiện đại | Checker → hỏi lại | [MTLS base](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json) |
| 「」→ "", …… → ..., ―― → —, nói lắp chỉ ở chữ đầu ("Đ-Đừng") | Hậu xử lý | [MTLS base](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json), [Umamusume Wiki](https://umamusu.wiki/Umamusume_Wiki:Contributing/Guidelines) |
| Giọng địa phương chỉ bằng vài tiểu từ, không dựng phương ngữ | Character sheet | [Kotaku 2023](https://kotaku.com/yakuza-like-a-dragon-ishin-majima-kiryu-localization-1850263200) (suy luận cho tiếng Việt) |

### Hồ sơ Blue Archive (bản nháp)

Phần lớn mục tiếng Việt dưới đây là **đề xuất**, vì không có quy ước nào được ghi lại. Chỉ "CLB Phát triển Game" là đã thấy dùng thật, và các cặp JP/EN đánh dấu `en_verified` đã có nguồn xác nhận.

```yaml
profile: blue_archive
status: draft
capture:
  mode: ocr_only            # NGS (Steam) / XIGNCODE3 (Yostar JP): không hook, không đọc bộ nhớ, không sửa file
  window: borderless
  regions:                  # bố cục cần xác nhận bằng ảnh chụp thật
    speaker_name: {translate: false, resolve_with: characters}
    affiliation_label: {translate: false, optional: true}
    dialogue: {join_lines: no_space}
    choices: {speaker: 先生}
source_language: ja         # tùy chọn en (pivot) để A/B; EN làm mất おじさん, ngôi thứ ba của Aris
honorifics:
  policy: keep              # công tắc: keep | localize
  keep: [ちゃん, さん, 先輩]  # "Nagi-chan", "Hoshino-senpai"; localize: 先輩→chị, bỏ さん
  never_double: true
protagonist:
  term: Sensei              # không dịch, không gán giới
  self_reference: tôi       # công tắc: tôi | thầy | cô ("tôi" trung tính nhưng xa cách)
pronoun_defaults:
  student->先生:      {self: em, other: Sensei, polite_particle: "ạ khi gốc dùng です/ます"}
  先生->student:      {self: "<protagonist.self_reference>", other: "em | tên"}
  アロナ/プラナ->先生: {self: em, other: Sensei}
  adult->先生:        {self: tôi, other: "ngài | Sensei"}   # Kaiser, 黒服, nhân viên GSC
  antagonist->先生:   {self: ta, other: ngươi}
  kouhai->senpai:    {self: em, other: "<tên>-senpai"}
  same_year:         {self: "tớ | mình", other: "cậu | tên"}
glossary:
  キヴォトス: {vi: Kivotos, en_verified: Kivotos}
  連邦捜査部シャーレ: {vi: Schale, en_verified: SCHALE}
  シッテムの箱: {vi: Hộp Shittim, en_verified: Shittim Chest}
  アロナ: {vi: Arona}
  プラナ: {vi: Plana}
  連邦生徒会: {vi: Hội học sinh Liên bang, en_verified: General Student Council}
  アビドス高等学校: {vi: Trường Abydos, en_verified: Abydos High School}
  ゲーム開発部: {vi: CLB Phát triển Game, attested_vi: true}
  便利屋68: {vi: Problem Solver 68, alt: Văn phòng Vạn năng 68}
  廃校対策委員会: {vi: Ủy ban Đối sách, en: Foreclosure Task Force}
  風紀委員会: {vi: Ủy ban Kỷ luật, en_unverified: Prefect Team}
  補習授業部: {vi: CLB Học bù, en_unverified: Make-Up Work Club}
  万魔殿: {vi: Vạn Ma Điện, style: han_viet_optional}
  ティーパーティー: {vi: Tea Party, alt: Hội Trà}
characters:
  ホシノ: {name: Hoshino, school: Abydos, register: casual_lazy, no_a: true,
           quirks: {うへ～: "Uhe~", おじさん_self: "ông chú này"}, tail: "~"}
  アリス: {name: Aris, alt_spelling: Arisu, self: "Aris (ngôi thứ ba)",
           jargon: {レベルアップ: "Lên cấp!", 経験値: EXP, HP: HP, 勇者: "dũng giả", パンパカパーン: "Tèn ten!"}}
  シロコ: {name: Shiroko, register: terse, quirks: {ん: "Ừm."}}
  ユウカ: {name: Yuuka}       # giữ nguyên âm dài theo bluearchive.wiki
sources: ["bluearchive.wiki (tên, trường, CLB)", ja/en.wikipedia, ghi chú riêng]   # dữ kiện công khai; không dùng tệp game
```

### Hồ sơ phong cách visual novel (bản nháp)

Hồ sơ mặc định cho VN bối cảnh Nhật hiện đại. Khác với BA, cặp xưng hô ở đây *phải tiến triển* theo giai đoạn. Các giá trị lấy từ MTLS, feilunhai và bài điểm sách 2020, còn cấu trúc là suy luận.

```yaml
profile: vn_modern_japan
setting: modern_japan       # đổi sang fantasy | historical để mở Hán-Việt và ta/ngươi, tại hạ
dialect_base: north         # nhé/không/ấy/này; south: nha/hông/á/nè; chọn một cho cả dự án
honorifics:
  keep: [さん, くん, ちゃん, さま]
  senpai: "<tên>-senpai | anh/chị khóa trên"
  translate: {先生: "thầy | cô | bác sĩ"}
  family: {お兄ちゃん: "anh | Onii-chan", お兄様: "Onii-sama"}   # tránh "anh hai" (bị chê, mang sắc Nam)
  never_double: true
pronoun_pairs:              # theo cặp nhân vật, có giai đoạn, người dùng xác nhận khi đổi
  hero<->heroine:
    - {stage: 1, hero: "tôi/cậu", heroine: "tớ/cậu"}   # chưa thân: cấm "em", cấm "anh"
    - {stage: 2, hero: "anh/em",  heroine: "em/anh"}   # thường sau tỏ tình
  shift_signals: [keigo_drop, 私→俺, name→君, name→お前]
  forbid: ["tôi/bạn cho mọi người", "tao/mày giữa người yêu chỉ vì お前",
           "bà cho phụ nữ dưới 60", "chú/bác cho người chưa già", "hắn cho nhân vật thiện cảm"]
first_person_narration: tôi
han_viet_budget: {dialogue: "5-10%", narration: "15-25%"}
banned_modern: [quý bà, phu nhân, thiếu gia, công tử, tiểu thư, ngài, đại nhân, tiểu sinh,
                mỹ lệ, kiều diễm, tuấn tú, ái mộ, nàng ta, hắn ta, phán rằng, dáng dấp]
prefer_simple: {ngắm nhìn: nhìn, chiêm ngưỡng: nhìn, sải bước: đi, phán: nói, trầm ngâm: nghĩ}
teen_register: {avoid: [thưa, dạ vâng, kính thưa, tôi xin, cho phép tôi, quả thật]}
punctuation: {「」: '""', "……": "...", "――": "—", stutter: first_word_only}
sfx: parenthesized_vi       # ドキドキ -> (thình thịch), シーン -> (im phăng phắc)
notes: first_occurrence_gloss_only
```

### Thông số đề xuất

| Thông số | Giá trị | Cơ sở |
|---|---|---|
| Lượng tử 7B | Q8_0 (~8.5 GB), hạ xuống Q6_K (~6.5 GB) nếu thiếu VRAM | IFMTBench ([report](https://arxiv.org/html/2605.22064v2)); dung lượng là ước tính |
| Bản build llama.cpp | Có STQ kernel (PR #22836) | [Model card](https://huggingface.co/tencent/Hy-MT2-7B) |
| Sampling A | temp 0.7, top_p 0.6, top_k 20, repeat_penalty 1.05 | Khuyến nghị nhà sản xuất |
| Sampling B | temp 0.2, các tham số khác như A | [Peng 2023](https://arxiv.org/pdf/2303.13780), [LlmTranslators](https://github.com/joshfreitas1984/XUnity.AutoTranslate.LlmTranslators); suy luận |
| Dòng ngữ cảnh | 4–6 (khoảng 3–8), cặp JP+VI đã duyệt, reset khi đổi cảnh | [Lippmann 2025](https://arxiv.org/html/2411.02589v2), [survey](https://arxiv.org/pdf/2506.07583) |
| Mục glossary mỗi lần | Chỉ mục khớp, trần khoảng 20 | [GalTransl](https://github.com/GalTransl/GalTransl); con số trần là suy luận (bản 1.8B chỉ có 8K ngữ cảnh) |
| Hỏi lại | Tối đa 1 lần mỗi dòng, chỉ khi bị gắn cờ | [MT-Ladder](https://arxiv.org/pdf/2406.15741) |
| Output | Văn bản thuần, không JSON, tắt thinking/CoT | [Tam 2024](https://arxiv.org/pdf/2408.02442), [Peng 2023](https://arxiv.org/pdf/2303.13780) |
| Streaming | Tắt, hoặc chỉ thay bản Google khi đã xong | [Yukihana](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/); suy luận |
| OCR stability | 0 cho cảnh tĩnh, tăng khi nhân vật hoặc nền chuyển động | [ocrparam](https://raw.githubusercontent.com/HIllya51/LunaTranslator/main/docs/en/ocrparam.md) |

## Kết luận

Điểm đáng chú ý nhất là một lệch pha. Benchmark dịch máy đo chất lượng của từng câu tách rời, nên gần như không thấy được loại lỗi mà người đọc Việt ghét nhất: xưng hô sai hoặc nhảy lung tung. Vì vậy mọi con số FLORES hay VNTL chỉ nói được mô hình *dịch nghĩa* tốt đến đâu. Chúng không nói được mô hình có *giữ được quan hệ nhân vật* hay không. Trong tiếng Việt, chính quan hệ nhân vật mới quyết định câu dịch có "mềm" hay không. Hệ quả thực tế là với JA→VI, chất lượng do hệ thống quanh mô hình quyết định nhiều hơn bản thân mô hình: theo dõi ai nói với ai, cặp xưng hô có giai đoạn, bộ kiểm tra xác định, và cache sửa được bằng tay. Cũng vì thế mà IFMTBench, chứ không phải XCOMET, là con số nên nhìn khi chọn mức lượng tử. Một bản dịch "đúng nghĩa" mà bỏ qua cặp em/Sensei đã cấp vẫn là bản dịch hỏng.

Điểm thứ hai: Blue Archive dễ hơn VN romance ở chỗ quan hệ học sinh–Sensei gần như cố định, nên cặp xưng hô cố định của app vừa khít. Nhưng nó khó hơn ở chỗ mọi quy ước tiếng Việt phải tự đặt ra, và bản tiếng Anh, vốn là nguồn tham chiếu dễ lấy nhất, lại làm mất đúng các dấu hiệu giọng nói (おじさん, Aris tự gọi tên mình) mà tiếng Việt giữ được tốt hơn tiếng Anh. Tiếng Việt có hệ danh xưng giàu hơn tiếng Anh, nên nếu làm đúng thì bản JP→VI có thể *trung thành với giọng nhân vật hơn* bản EN chính thức. Cơ hội đó chỉ thành hiện thực nếu app có bộ test JA→VI riêng để đo, vì hiện chưa ai đo hộ.
