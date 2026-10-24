# Vietnamese fan-translation and localization conventions for JP->VI (games, VN, LN, anime, manga)

Research date: 2026-10-24. Access notes:
- The main Vietnamese LN hub, Cổng Light Novel / Hako (docln.net, ln.hako.vn, docln.sbs), returned HTTP 403 to WebFetch and a Cloudflare bot check in the browser. I did not try to get past the check. Claims about Hako threads below come from search-result snippets only and are marked "(snippet only)".
- voz.vn worked in the browser.
- The dichsach.com page failed with a TLS certificate error, so it is also "(snippet only)".
- web.archive.org cannot be fetched from this environment.
- Most search engines return few results for Vietnamese community posts (Facebook and Discord are not indexed). So the strongest primary sources here are:
  - one 2020 reader review of licensed LN translations (truyenbanquyen.com);
  - one 2012 translator blog post on xưng hô;
  - a 2026 open-source JP->VI LLM style guide (MTLS repo, by voz user Thang_97);
  - voz threads (2023-2026).
- Learner sites (riki, dekiru, japo) supply the pronoun and particle semantics.

---

## 1. Xưng hô: mapping Japanese pronouns and relationships to Vietnamese pronoun pairs

### Takeaway
Vietnamese practitioners agree that xưng hô is the single most important (and most often botched) choice. It is driven by:
- the relationship and the age gap;
- the emotional temperature of the line;
- the stage of the relationship.

It is not driven by the Japanese pronoun alone. Pairs are expected to evolve (bạn/mình or tôi/cậu -> cậu/tớ -> anh/em). The most criticized faults are inconsistency (ta/ngươi one chapter, anh/em the next) and using an intimacy level that does not match the source.

### Cited Findings
- **Pair inventory and use cases** from a translator/editor blog (feilunhai1010, "Tám nhảm về cách xưng hô trong tiểu thuyết", 2012-06-09). Older, but the most explicit practitioner taxonomy found. [Source](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/)
  - **Bạn – Mình**: new or not-close acquaintances; shy or timid characters, even lovers.
  - **Cậu – Tớ**: the most common choice in translations. The author calls it semantically "an toàn" (safe) but not always accurate.
  - **Anh/Cô – Tôi**: characters who dislike each other, argue, or keep distance. These pairs often later become **Anh – Em**.
  - **Anh – Em**: established couples, often switched at the confession scene, when emotion "bộc phát" (bursts out).
  - **Mày – Tao**: the rudest form, for enemies or very close tough male friends.
  - **Ngươi – Ta**: combat/action stories, and sometimes master and servant.
  - **Name – Name**: using only names; the author finds it hard to do well.
- **Choice factors** (same blog, 2012): the relationship, the tone of the line (anger, joy, teasing, scolding), and the emotion. Anger at a loved one is not the same as anger at an enemy. [Source](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/)
- **Pronoun changes over time** (same blog, 2012): pairs should move stranger (bạn–mình) -> close friends (cậu–tớ) -> lovers (anh–em). The translator has to decide when, because the Japanese often does not mark the change. In a comment, the author rejects giving every character in a historical setting "ta – ngươi", because readers then cannot tell who is speaking. [Source](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/)
- **Staged xưng hô in a 2026 romcom/school-life JP->VI LLM style guide** (MTLS repo `romcom_school_life_vi.json`, by voz user Thang_97). This is a single author's ruleset, not group consensus. [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json)
  - Male lead (16) self-reference:
    - tôi / mình by default;
    - "anh" to younger people;
    - "con / cháu" to adults, depending on the relationship.
  - Male lead to others:
    - "mày / cậu" with a close male friend;
    - "chị / [tên]-senpai" to an older girl;
    - "thầy / cô" to teachers;
    - "cô" to his stepmother (not "bà cô" or "mẹ kế" in direct address).
  - Male lead to the same-age heroine, by chapter:
    - ch.1–2: "cậu / [tên]", and "em" is explicitly avoided while they are not close;
    - ch.3–5: "em";
    - ch.6+: affectionate "em".
  - Heroine (tsundere) to the male lead, by chapter:
    - ch.1–2: "cậu";
    - ch.3–5: "anh";
    - ch.6+: "anh" with emotion.
    - Rule: avoid early intimate "anh".
  - Heroine to her stepfather: "bác / bác [tên]" (polite, not yet close).
  - Side characters:
    - energetic male friend: tao/mày with close friends, cậu with others;
    - warm stepmother: calls the kids "con", self-refers "cô", and avoids "bà"/"tôi" because they feel distant.
  - Explicit "do not" rules:
    - no "bà" for middle-aged women ("bà" is for 60+);
    - no "chú/bác" for people who are not old;
    - no "anh/chị ấy" in inner monologue once the two are close.
- **Reader criticism of licensed LN xưng hô** (Katsuki, truyenbanquyen.com, "【CHUYÊN ĐỀ TBQ】[IPM] – Ông Hoàng Tắc Trách", 2020-03-02). [Source](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)
  - Oregairu (Hikari edition): the "tôi – cậu" and "tớ – cậu" choices are criticized as the wrong intimacy level between the characters.
  - Re:Zero (IPM): inconsistent address, "anh Subaru" in one volume and bare "Subaru" in the next.
- **voz users on AI-assisted translation** (thread "MC-AI Novel Trans", 2025-26). (snippet only for the exact wording) [Source](https://voz.vn/t/mc-ai-novel-trans-cong-cu-dich-truyen-tranh-tieu-thuyet-ho-tro-ai.1190485/)
  - Xưng hô errors are the main complaint about raw AI output. One quoted complaint: "Lúc thì 'ta/ngươi', lúc thì 'anh/em' loạn cào cào".
  - The tool advertises a relationship table: e.g., set "A là cha của B" and the AI uses "cha/con" or "ta/con".
  - Users say the AI draft is only part of the job; "quan trọng là chỉnh lại xưng hô".
- **Japanese pronoun semantics, per Vietnamese learner sites** (snippet only). [dekiru](https://dekiru.vn/blog/detail-20160612203134372.htm); [mcbooks](https://mcbooks.vn/toi-tieng-nhat-la-gi-va-20-cach-noi-toi-trong-tieng-nhat/)
  - 俺 (ore): rough, the most common male self-reference in everyday speech, impolite with strangers or superiors.
  - 僕 (boku): softer male, fine with family and friends but avoided in formal settings.
  - お前 (omae): glossed as "mày"; 手前 (temae) is even ruder.
  - 私 (watashi): neutral/polite; 私 read as わたくし (watakushi) is the most humble form.
  - A boyfriend can use ore/omae with his girlfriend. That is why お前 between lovers should not be rendered "mày".
- **Vietnamese pronoun norms behind these choices.** [vielala](https://www.vielala.com/post/pronouns-in-vietnamese); [preply](https://preply.com/en/blog/vietnamese-pronouns/)
  - tớ/cậu is casual and friendly, more Northern, unisex.
  - Schoolgirls tend to use tớ/cậu, schoolboys mày/tao.
  - Southern speech uses tui with ông/bà for peers.
- **Office keigo and address terms** (Cáp Thị Ngọc Huyền, Tạp chí ĐH Duy Tân, 2024; compares two films). The paper says the different language types (agglutinative vs. isolating) and communication cultures cause confusion when choosing address terms. I could not extract the PDF text for its concrete mappings. [Source](https://files02.duytan.edu.vn/svruploads/dtu-duytan/upload/file/638644936616925414-10(66).-Đối-chiếu-về-từ-xưng-hô-trong-công-sở-giữa-tiếng-Nhật-và-tiếng-Việt-(Cáp-Thị-Ngọc-Huyền).pdf)

### Inferences
These are my synthesis of the sourced principles plus standard Vietnamese usage, for prompt design. **None of the example pairs below is quoted from a source.**

**Core method:** decide the pair per character dyad, not per Japanese pronoun, and store it in a glossary. MTLS and the voz tools both do this, and the docln AI guide's advice to feed 2–5 translated chapters to the model serves the same goal.

**First-person defaults:**
- **俺**
  - teen boy to a close male friend: tao/mày or tớ/cậu;
  - to a girl he is not close to: tôi/cậu;
  - first-person narration: tôi;
  - delinquent to an enemy: tao/mày.
  - e.g. 「俺は行かねえよ」 -> "Tao không đi đâu." / "Tôi không đi đâu."
- **僕**: tôi/mình/tớ; for a child speaking to adults: cháu/con/em.
  - e.g. 「僕、何もしてないよ」 -> "Cháu có làm gì đâu ạ."
- **私**
  - neutral/polite: tôi;
  - a girl among friends: tớ/mình;
  - an employee to a boss: tôi or em, depending on the age gap.
- **あたし**: tớ/mình (casual female); chị for big-sister types.
- **わし / 儂** (old man)
  - to grandkids: ông/cháu;
  - as a sage or master: ta/lão (e.g., "lão phu" for wuxia-flavored elders).
- **拙者**: tại hạ.
- **余 / 朕**: ta / trẫm (rulers).
- **わらわ (妾)**: ta / bổn cung / thiếp, depending on the setting.
- Hán-Việt and archaic pronouns ("bản cô nương", "lão phu", "tại hạ", "ngươi") belong only in historical, fantasy or wuxia registers. MTLS bans ngài, đại nhân, tiểu sinh and similar forms in modern casual contexts.

**Second-person defaults:**
- **お前**
  - male friend: mày/cậu;
  - husband or boyfriend to his partner: em;
  - superior to subordinate: cậu/em.
  - e.g. 「お前のことが好きだ」 (boy to girl) -> "Anh thích em." Not "Tao thích mày."
- **貴様 / てめえ**: mày / ngươi / thằng khốn.
- **君**: cậu/em.
- **あなた**
  - wife to husband: anh/mình;
  - polite to a stranger: anh/chị/bạn.
- Avoid "bạn" in fantasy and in family speech: it is the "tôi/bạn everywhere" textbook register.

**Special cases:**
- **Girl using ボク (ボクっ娘).** Vietnamese cannot mark gendered self-reference. Options:
  - unisex "tớ" or "mình", plus a one-time TN explaining that she uses the masculine ボク;
  - or keep the quirk visible through other speech habits.
- **Keigo** maps to Vietnamese politeness particles and lexicon rather than to pronouns:
  - polite です/ます to a superior -> add "ạ / dạ / vâng";
  - 尊敬語 -> ngài / quý khách (shop or service contexts only);
  - 謙譲語 -> "em xin phép", "cho phép tôi".
  - A sudden drop from keigo to plain speech should become a change of pair (e.g., em/anh -> tôi/anh or tao/mày) or the loss of "ạ".
- **Pronoun shifts in the source** (e.g., 私 -> 俺 when angry, or name -> 君 as feelings grow) should be rendered as visible Vietnamese pair shifts at the same line. They should not be smoothed into one pair.

**Recurring errors to forbid in a prompt:**
- "tôi/bạn" for everyone;
- giving lovers "tao/mày" just because the source has お前;
- addressing the same person as anh and then cậu with no story reason;
- calling a 30-something woman "bà";
- third-person "hắn" for sympathetic modern characters. "hắn" is a convert-style habit; prefer cậu ấy / anh ta / nó by stance. This last point is my inference.

### Gaps
- No published Hako/docln, Valvrare or VN-patch group style sheet on pronouns could be retrieved: docln is blocked, and Facebook/Discord are not indexed.
- No Vietnamese academic thesis specifically on JP->VI pronoun translation in anime, LN or games was found. The Duy Tan 2024 paper is office/film focused and its text could not be extracted.
- No sourced guidance was found specifically on ボクっ娘 or on 拙者/わらわ rendering. The mappings above are inference.

---

## 2. Honorifics: keep -san/-chan/-kun/-senpai/-sensei/onii-chan, or localize?

### Takeaway
There is no single standard; it is a live split.
- Older translator advice and some licensed publishers convert honorifics into Vietnamese kinship and address terms.
- Many anime/LN readers (2020 review) and at least one 2026 LLM style guide keep the Japanese suffixes. They keep -san/-kun/-chan/-sama, translate -sensei as thầy/cô, and handle senpai by context.
- Readers criticize both directions: over-localized forms ("anh hai" for お兄様) and literal Vietnamese calques ("đàn anh/đàn chị + name").
- One rule is firm: never double them ("anh Ninomiya-san").

### Cited Findings
- **Convert, never double** (2012 blog): translators should convert suffixes into Vietnamese equivalents rather than keep them. Write "Anh Ninomiya ơi" or "Ninomiya san", never "Anh Ninomiya san ơi". [Source](https://feilunhai1010.wordpress.com/2012/06/09/tam-nham-ve-cach-xung-ho-trong-tieu-thuyet/)
- **Keep the Japanese, add notes** (2020-03-02 review): the reviewer wants LN translations to keep the original honorifics/xưng hô, with notes where needed. Their phrasing: "Chúng ta dịch truyện Nhật thì nên dịch theo văn phong Nhật". [Source](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)
  - **Mahouka (Nhã Nam)**: Miyuki's お兄様 (Onii-sama) rendered as "anh hai" is criticized. Quote: "Nhã Nam dịch thẳng thừng ra là 'Anh hai'". It loses her reverent, adoring tone, and "anh hai" is also a Southern-flavored term less natural for readers nationwide.
  - **IPM title**: お姉様 (Onee-sama) -> "chị" judged wrong and confusing.
  - **Hikari**: "Anh hai Koremitsu" reads "sượng" (stiff).
  - **Uranix title**: praised for keeping xưng hô. But calques like "đàn chị Momo" and "đàn anh Tatsuki" sound "gượng" (forced).
  - **Kem Đá (Hyouka)**: praised for keeping Ibara's "Chi-chan", "Fuku-chan".
- **Mixed keep/translate policy** in a 2026 JP->VI LLM style guide (MTLS `base_style_vi.json`, v1.0, single author). [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json)
  - Keep romanized: -san, -kun, -chan, -sama.
  - Translate by context: -senpai -> "anh/chị khóa trên".
  - Always translate: -sensei -> "thầy/cô".
  - Explain the suffixes in a footnote at first use in chapter 1.
  - The romcom file also allows 先輩 -> "anh/chị khóa trên" or keeping "-senpai". A male lead may address an older girl as "chị" or "[tên]-senpai". [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json)
- **Two directions, per a commercial manga-translation article**: full Việt hóa of xưng hô, or keeping Japanese elements. (snippet only) [Source](https://idichthuat.com/kinh-nghiem-dich-thuat-truyen-manga/)
- **Keep-or-convert as a per-work decision, per an AI-LN guide**: -san/-kun/-sama and watashi/boku/ore signal personality, so translators must decide per work whether to keep them or convert to anh/chị/em, tớ/cậu. (snippet only; page not fetchable) [Source](https://dichsach.com/blog/posts/huong-dan-dich-light-novel-nhat-ai)
- **Japanese loan address terms have currency in Vietnam**, which supports keeping them for anime audiences:
  - Docln-hosted LN titles use "senpai" directly in Vietnamese, e.g. "100 điều em không biết về senpai". [Source](https://docln.net/truyen/5797-100-dieu-em-khong-biet-ve-senpai)
  - A Vietnamese explainer notes netizens use "onii chan" for favorite male anime characters. [Source](https://www.kienminh.edu.vn/onii%C2%A0chan-la-gi-cach-xung-ho-va-y-nghia-cua-onii-chan)

### Inferences
**Practical 2020s default for anime/VN/gacha-literate audiences:**
1. Keep -san/-kun/-chan/-sama/-senpai on names in dialogue, where the reader already knows them.
2. Translate sensei -> thầy/cô (or "bác sĩ" for doctors, "thầy" for masters).
3. Translate bare kinship terms in narration and in families into Vietnamese (anh, chị, em, mẹ, bố).
4. Never stack a Vietnamese title on a Japanese suffix.

**Onii-chan family:**
- Neutral options are "anh", "anh ơi", or keeping "Onii-chan".
- お兄様 suggests keeping "Onii-sama" or using "anh trai" with a reverent tone ("thưa anh" or "ạ" particles) rather than "anh hai".
- "anh hai / chị hai" is Southern; Northern usage is "anh cả / chị cả" or plain "anh/chị". Mixing them is a known complaint.

**Prompt rule:** let the client set a toggle (keep_honorifics: true/false). Either way, the pronoun pair must still be Vietnamese: "-san" on the name does not remove the need to choose tôi/cậu vs em/anh.

Example JP->VI pairs (my renderings; not from sources):
- 「先輩、待ってください!」
  - keep-style: "Senpai, đợi em với ạ!"
  - localized: "Anh ơi, đợi em với ạ!"
- 「田中さん、おはようございます」
  - keep-style: "Chào buổi sáng, Tanaka-san."
  - localized (office, older male): "Em chào anh Tanaka ạ."
- 「お兄ちゃん、起きてよ!」 -> "Anh ơi, dậy đi mà!" or "Onii-chan, dậy đi!"
- 「先生、質問があります」 -> "Thưa thầy/cô, em có câu hỏi ạ."

### Gaps
- No survey or poll data on reader preference (keep vs localize) for 2020–2026 was found.
- No retrieved Hako/Valvrare/fansub group policy statements; the docln threads were blocked.
- No information was found on how Muse Việt Nam / POPS official subtitles handle honorifics. I found no commentary or criticism of their xưng hô.

---

## 3. Names and terms: Latin (Hepburn) vs Hán-Việt, skill/item names, katakana loanwords

### Takeaway
For modern Japanese works, Japanese names are written in Latin script (Hepburn), family name first. Hán-Việt rendering of Japanese names is not used in modern anime/LN/game translation, and terms are kept consistent via a glossary. Hán-Việt vocabulary is rationed by register:
- near zero in modern teen dialogue;
- more in formal, historical or technical text.

Older licensed manga (1990s) did Vietnamese-phonetic name localization (Doraemon), and this is now seen as a period practice. English katakana terms are usually rendered by meaning or kept in their English form. Romaji-English hybrids are mocked.

### Cited Findings
- **Name order and examples** (MTLS base_style_vi, 2026): family name first, then given name, in Hepburn. [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json)
  - 白川賢人 -> Shirakawa Kento
  - 神薙翔太 -> Kannagi Shouta
  - ソフィア -> Sophia
- **Hán-Việt budget by context** (same file, key "CONTEXTUAL SINO-VIETNAMESE ADAPTATION"): [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json)

  | Context | Hán-Việt share |
  |---|---|
  | modern casual (teen dialogue, school life) | ~5–10% |
  | narrative description | ~15–25% |
  | formal / historical / technical | ~30–40% |

  - Banned in modern contexts:
    - quý bà, phu nhân, thiếu gia, công tử, tiểu thư;
    - ngài, quý vị, đại nhân, tiểu sinh;
    - mỹ lệ, kiều diễm, tuấn tú, ái mộ, hân hoan, u sầu.
- **Name consistency is a reader complaint** (2020 review):
  - Date A Live (Amak): "Shido" and "Shidou" mixed.
  - Toaru Majutsu no Index (IPM): "Image Breaker" is a mistranslation of "Imagine Breaker".
  - Sword Art Online (IPM): "Bông hồng xanh" (青薔薇, Blue Rose) was changed to "Aobara" after fan pushback. This shows fans prefer the original name over a translated name.
  - Oregairu (Hikari): hybrids like "cà phê au lait" ("nửa ta nửa tây") and "Hikkikomoru" are mocked.
  - Nuns should be "sơ", not "xơ".
  - [Source](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)
- **Docln users complain about raw MT** with inconsistent xưng hô and character names across chapters. (snippet only) [Source](https://docln.net/thao-luan/2789-bao-cao-truyen-vi-pham?page=2)
- **1990s Doraemon (Kim Đồng, 1992)**: the names were Vietnamized phonetically. Shizuka/Suneo/Jaian became Xu-ka/Xe-ko/Chai-en, and only Nobita was kept. Editors "liberally altered" names and details; the edition was translated via Thai and unlicensed at first. [Saigoneer](https://saigoneer.com/saigon-culture/12042-doraemon-and-25-years-of-fostering-friendship-in-vietnam); [ZNews](https://znews.vn/znews-post1489814.html)
- **Japanese culture terms** (MTLS romcom guide, 2026):

  | Japanese | Vietnamese |
  |---|---|
  | 高校 | trường cấp 3 / THPT |
  | 部活 | CLB |
  | 弁当 | hộp cơm kiểu Nhật (bento) at first mention |
  | おにぎり | cơm nắm |
  | 焼きそば | mì xào (yakisoba) |
  | ラーメン | mì ramen |
  | お茶 | trà (no "trà Nhật") |
  | 義妹 | em gái kế |
  | 幼なじみ | bạn thời thơ ấu / bạn từ bé |
  | 告白 | tỏ tình |
  | 付き合う | hẹn hò |
  | 彼女 / 彼氏 | bạn gái / bạn trai |

  [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json)
- **Official gacha localization (a Chinese-developed game, but a reference point for anime-style audiences)**: the Vietnamese Genshin Impact keeps the Japanese-inspired region name in romaji, "Inazuma" (稲妻), rather than Hán-Việt. Item and set names are translated into Vietnamese, with Hán-Việt in the Chinese-flavored areas, e.g. "Inazuma - Hoa Văn Lôi Điện". [Fandom VI wiki](https://genshin-impact.fandom.com/vi/wiki/Inazuma); [example item](https://genshin-impact.fandom.com/vi/wiki/Inazuma_-_Hoa_V%C4%83n_L%C3%B4i_%C4%90i%E1%BB%87n)

### Inferences
**Recommended rules for modern-setting works:**
- Japanese person and place names: Hepburn, family name first, consistent long vowels (choose "Shouta" or "Shōta" or "Shota" once per project; MTLS uses ou).
- Never use Hán-Việt for Japanese names in modern works. "Đức Xuyên Gia Khang" for 徳川家康 is an old historiographic convention, not used in anime or game translation. I did not find a source for this; it is general knowledge.

**Skill, item and attack names:**
- If the source is English katakana (ファイアボール), use the English (Fireball) or a clean Vietnamese rendering (Cầu Lửa). Keep it consistent with the in-game UI.
- If the source is kanji compounds in a fantasy or wuxia setting (天翔龍閃), Hán-Việt is acceptable and expected (Thiên Tường Long Thiểm).
- In a modern setting, prefer thuần Việt or English.

**Katakana loanwords:**
- Keep widely known Japanese culture words (bento, ramen, onsen, senpai, yukata), with a gloss on first use.
- Translate English-origin katakana by meaning.

### Gaps
- No Vietnamese Wikipedia naming convention page for Japanese names was retrieved.
- No source documents when Vietnamese publishers stopped localizing names (e.g., whether newer Doraemon editions changed Xuka/Xeko/Chaien).
- No sourced data on Vietnamese official localizations of Japanese-developed games (VNG, Garena or publisher-run). I found nothing on Japanese games with official Vietnamese text and their naming policy.

---

## 4. Văn phong: particles, anti-"convert" style, word order, SFX, dialect, swearing, cute speech, TN

### Takeaway
"Văn convert" (raw Quick Translator/Google output) is the community's main negative reference point. It has scrambled or inverted word order, dense obscure Hán-Việt, and stiff diction, and readers say it "infects" translators' Vietnamese.

Good style means:
- dịch ý (meaning), not word-for-word;
- thuần Việt in modern settings;
- simple verbs (nhìn, đi, nói, nghĩ);
- natural interjections;
- particles chosen by speech act (nhỉ/nhé/đấy/mà/cơ/chứ/hả).

SFX are either translated into Vietnamese onomatopoeia, kept with a gloss, or replaced by a short description. TNs are allowed but should not be overused.

### Cited Findings
- **What "convert" is**:
  - Raw translation produced with Quick Translator or Google Dịch. Text is "lộn xộn, ngược câu", full of hard Hán-Việt, and stiff.
  - The editor's job is to make it "thuần Việt hơn, đọc dễ hiểu hơn".
  - Reading too much convert makes "tiếng Việt của bạn sẽ bị lậm convert".
  - [osach.net](https://osach.net/blogs/truyen-convert-la-gi-145); [linqqmignon, 2021-06-05](https://linqqmignon.wordpress.com/2021/06/05/raw-va-convert-trong-edit-truyen/); [bnsach forum](https://bnsach.com/forum/threads/13452/); [dembuon](https://dembuon.vn/threads/huong-dan-cach-dich-truyen-tu-ban-convert.42352/)
- **Docln community threads** (snippet only):
  - A thread titled "Những từ Quick Translator mà bạn thường lậm" ("Quick Translator words you're addicted to") exists. Its snippet says not to force 1-to-1 equivalents; colloquial or sarcastic nuance must be translated by situation. [Source](https://docln.net/thao-luan/3338-nhung-tu-quick-translator-ma-ban-thuong-lam)
  - Another thread frames LN translation as localization that needs a grasp of Vietnamese style and culture. [Source](https://docln.net/thao-luan/2879-xin-kinh-nghiem-dich-thuat)
  - Docln rules ask for clear, readable translations, no unedited Google Translate, respect for the original, and no overuse of personal notes. [Rules for translations](https://docln.net/thao-luan/2745-quy-dinh-doi-voi-truyen-dich); [General rules](https://docln.net/thao-luan/20-quy-dinh-chung-doi-voi-cac-du-an-dang-tai-cong-light-novel-bao-gom-truyen-dichconvert-va-oln?page=8)
- **Concrete anti-convert substitutions** (MTLS base guide, 2026). [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json)

  | Use | Not |
  |---|---|
  | nhìn | ngắm nhìn, chiêm ngưỡng |
  | đi | bước đi, sải bước |
  | nói | phán, thốt lên |
  | nghĩ | suy tư, trầm ngâm |
  | chạy | phi nhanh |

  Examples:
  - 彼女は美しい -> "Cô ấy đẹp quá / xinh đẹp thật", not "Cô ấy thật mỹ lệ".
  - 美しい女性 -> "Người phụ nữ xinh đẹp", not "Vị quý bà mỹ lệ".
  - 結果発表 -> "Thông báo kết quả".
- **More JP->VI examples** (MTLS romcom guide, 2026). It also bans archaic style ("nàng ta", "hắn ta", "phán rằng", "dáng dấp", "phong thái") in modern romcom. [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json)
  - 美しい女性が入ってきた -> "Một người phụ nữ xinh đẹp bước vào", not "Một quý bà mỹ lệ bước vào".
  - 彼女は綺麗だね -> "Cô ấy đẹp nhỉ" / "xinh thật", not "Cô ấy mỹ lệ nhỉ". Note ね -> nhỉ.
  - 義母のジェシカ -> "Mẹ kế Jessica" / "Cô Jessica", not "Mẹ kế quý bà Jessica".
  - Wrong: "Nàng ta sải bước nhẹ nhàng, dáng dấp ưa nhã khiến hắn ta ái mộ". Right: "Cô ấy đi rất nhẹ nhàng và thanh lịch, khiến anh thích thú".
- **Teen speech register** (same romcom guide). [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/genres/romcom_school_life_vi.json)
  - Fillers: ừ, à, hả, ơ, ờ, used naturally and not overused.
  - Interjections by emotion:
    - surprise: "Ơ!?", "Hả!?", "Cái gì!?", "Nghiêm túc à!?";
    - embarrassment: "Ư...", "Đ-Đừng nói thế!", "Không phải đâu!";
    - irritation: "Chết tiệt...", "Đúng là...";
    - joy: "Tuyệt!", "Hay quá!", "Được rồi!".
  - Avoid adult or formal markers in teen speech: "thưa", "dạ vâng", "kính thưa", "tôi xin", "cho phép tôi".
  - Avoid literary fillers: "thật là", "quả thật".
  - Dialogue may be choppy; narration is more literary than dialogue.
- **Punctuation and format** (MTLS base guide). [Source](https://raw.githubusercontent.com/thang97-21/MTLS/main/pipeline/style_guides/base_style_vi.json)
  - Double quotes for dialogue.
  - "..." (three periods) for ellipses.
  - A single "!".
  - "—" for interrupted speech.
  - Vietnamese, not English, comma rules.
  - Scene breaks "***".
  - Inner monologue in italics.
- **Sentence-final particles, per Vietnamese learner sites** (not academic):
  - ね -> "nhỉ" when seeking agreement ("Phim hay nhỉ"); "nhé" when requesting a future action.
  - よ -> "đấy" when informing; "đây" for an action the speaker is about to do; "đâu" with negation. It carries assertion or confidence. One site notes よ often works like "!" and ね like a tag question "phải không?". Intonation changes よ's sense from "đấy" to "nhé" to urging.
  - [riki.edu.vn](https://riki.edu.vn/goc-chia-se/cach-dung-vi-to-tieng-nhat/); [japo.news](https://vn.japo.news/contents/tieng-nhat/124050.html); [vinanippon](https://vinanippon.edu.vn/cach-su-dung-tro-tu-dem-trong-tieng-nhat/)
- **Onomatopoeia/SFX**:
  - Three strategies: translate to a Vietnamese onomatopoeia (e.g., "RẦM", "ẦM"); keep the original with a note when it is too culture-specific; or replace it with a short description.
  - Japanese has thousands of giongo/gitaigo (the article claims 4,500+ in one dictionary). (commercial site, snippet only) [Source](https://idichthuat.com/kinh-nghiem-dich-thuat-truyen-manga/)
  - An old scanlation-group post (W-group, 2010-11-29) argues SFX convey action as well as sound, so skipping them loses part of the work. [Source](https://wgrouptrans.wordpress.com/2010/11/29/h%E1%BB%97-tr%E1%BB%A3-d%E1%BB%8Bch-sfx-cho-cc-translator/)
- **Editorial particle errors are noticed by readers.** In a Sakura LN volume 10, "nhé" was replaced by "lẽ" in many places; the publisher called it an editing error. The same review says Tsuki's translations are "ổn nhưng không có hồn". [Source](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)

### Inferences
**Particle mapping table.** This is my synthesis for prompts. Choose by speech act and the character's dialect base; do not map 1:1, and drop the particle when Vietnamese intonation already carries it.

| Japanese | Function | Vietnamese options |
|---|---|---|
| ね / よね | agreement, softening | nhỉ / nhé / ha / nha / phải không |
| よ | assertion, informing | đấy / đó / mà / cơ / đâu (with negation) |
| わ (feminine) | soft assertion | đấy / cơ / mà / nhé |
| ぞ / ぜ (rough masculine) | emphatic | đấy / đó / nhé / đi, or just "!" |
| かな / かしら | wondering | nhỉ / không biết... |
| っけ | recalling | nhỉ / ấy nhỉ |
| じゃん / でしょ | "isn't it obvious" | chứ gì / còn gì / đúng không |
| の? / なの? | question | à / hả / á |
| のだ / んだ | explanatory | mà / cơ / là vì |
| さ | filler | thì / mà |

Dialect: Northern nhé / không / ấy / này vs. Southern nha / hông / á / nè. Pick one base per project. Using Southern forms only for specific characters is a legitimate way to render 関西弁 or rural dialect.

Worked example lines:
- 「行くぞ!」 -> "Đi thôi!"
- 「そうだよね」 -> "Đúng thế nhỉ."
- 「知らないわよ」 -> "Tôi đâu có biết."
- 「大丈夫だって」 -> "Đã bảo là không sao mà."

**Cute speech (〜だもん, 〜なのです, 〜にゃ, character catchphrases):**
- だもん -> "...mà!" / "...cơ!"
- なのです -> "...đó ạ"
- Animal verbal tics: keep as "nya" or render "meo".
- Keep catchphrases consistent across the script.

**Swearing:**
- くそ -> "chết tiệt" / "khốn kiếp".
- てめえ -> mày / thằng khốn.
- Avoid regional obscenities unless the source is very vulgar, and keep the vulgarity level matched to the source.

**SFX in games and VNs** (where the text is often just the sound):
- In parentheses: ドキドキ -> (thình thịch), シーン -> (im phăng phắc).
- Leave big stylized manga SFX untouched with a small subtitle, or redraw them when that is feasible.

**TN conventions:**
- Brief inline "(*)" with an end-of-chapter note, or "(TN: ...)".
- In subtitles, put a short note at the top of the screen.
- Gloss honorifics and culture terms once, at first appearance.

**Word order:** restructure Japanese SOV and long pre-nominal modifiers into Vietnamese head-first order. Split long relative clauses; for example, 昨日駅で会った女の子 becomes "cô gái tôi gặp ở ga hôm qua", not a calque.

### Gaps
- The docln "Quick Translator words" list itself (the actual banned-word list) could not be read: Cloudflare blocked it.
- No academic Vietnamese contrastive study of JP sentence-final particles in subtitle or LN translation was found; only learner-site glosses.
- No sourced guidance was found on rendering 関西弁 or other dialects into Vietnamese regional speech. The dialect advice above is inference.

---

## 5. Published style guides / "quy chuẩn dịch", and official localizations vs fan styles

### Takeaway
The publicly indexable "quy chuẩn" material is thin and fragmented:
- Hako/docln has platform rules (readability, no raw MT, respect the original, limit notes) and discussion threads, but no single public style sheet I could retrieve.
- VN Việt hóa groups publish mostly technical guides (encoding, fonts, engines), not language style sheets.
- The most detailed explicit JP->VI style ruleset found is a 2026 open-source LLM pipeline (MTLS / LN-VN-Translator by voz user Thang_97).

Official channels (licensed LN/manga publishers, Muse Việt Nam, POPS) are judged by readers on consistency and faithfulness. Fan readers often prefer keeping Japanese address terms and names that publishers sometimes localize.

### Cited Findings
- **Docln platform rules**: clear, easy-to-read text, not raw Google Translate, respect for the original without excessive edits, and limited personal notes. Docln has also posted an "AI dịch" announcement and a user "HƯỚNG DẪN DỊCH TRUYỆN BẰNG AI" thread. The AI guide advises giving the model 2–5 already-translated chapters so it learns the xưng hô and style. (snippet only; 403 / Cloudflare) [Rules for translations](https://docln.net/thao-luan/2745-quy-dinh-doi-voi-truyen-dich); [General rules](https://docln.net/thao-luan/20-quy-dinh-chung-doi-voi-cac-du-an-dang-tai-cong-light-novel-bao-gom-truyen-dichconvert-va-oln?page=8); [AI guide](https://docln.net/thao-luan/3268-huong-dan-dich-truyen-bang-ai); [AI announcement](https://docln.sbs/thao-luan/3097-thong-bao-ve-ai-dich)
- **LN-VN-Translator** (voz, Thang_97, posted 2026-01-06): a Gemini Gem "master prompt" of 1,914 lines / 95,809 characters plus 5 reference knowledge files. The author recommends Gemini 3 Flash Thinking. The GitHub repo `thangdam97/LN-VN-Translator` returned 404 when checked (2026-10-24). [voz thread](https://voz.vn/t/chia-se-bo-prompt-dich-light-novel-nhat-viet-khong-api-khong-python-khong-web-ngoai-chay-thang-tren-web-gemini.1192919/)
- **MTLS** (same author, GitHub `thang97-21/MTLS`):
  - targets "Yen Press-grade English"; Vietnamese output is marked "Beta — In Development";
  - ships `base_style_vi.json`, `genres/romcom_school_life_vi.json` and `publishers/overlap_vi.json`;
  - planned: `isekai_fantasy_vi.json`, `mystery_thriller_vi.json`, `kadokawa_vi.json`, `media_factory_vi.json`.
  - Its quality goals: no MT artifacts, text that reads like original Vietnamese prose, language that fits each character's age, and tone that fits the genre.
  - [Repo](https://github.com/thang97-21/MTLS); [style_guides dir](https://github.com/thang97-21/MTLS/tree/main/pipeline/style_guides)
- **VN Việt hóa community resources**:
  - Hoshi Visual Novel lists VN groups such as Gero Saga, Vietnam Key Fanclub, Nine Translation and Otome VN. (snippet only) [Source](http://www.hoshivsub.com/)
  - Mangochan's "[Hướng dẫn] Tiếng Việt trong Visual Novel" is technical: Shift-JIS engines do not support Vietnamese, so prefer Unicode/UTF-8 engines such as KiriKiri or (O)NScripter, and fix fonts or TCVN3 encodings. [Source](https://mangochan.site/forum/threads/huong-dan-tieng-viet-trong-visual-novel.34708/huong-dan-tieng-viet-trong-visual-novel.34708.html)
  - Nyanpasu runs a news and release site for Việt hóa VNs. [Source](https://nyanpasu.cyou/)
  - A 2022 guide shows how to filter VNDB for Vietnamese releases. [Source](https://banbanhxinhdop.wordpress.com/2022/02/15/guide-tra-cuu-visual-novel-co-viet-hoa-tren-vndb/)
  - viethoagame.com has real-time game-translation tool threads. [Source](https://viethoagame.com/threads/wip-thu-nghiem-tool-dich-game-real-time.421/)
- **Valvrare Team** describes itself as an LN translation team (also Anime & Manga). Search found no published style rules from it and no VN projects. [docln group page](https://docln.net/nhom-dich/4-valvrare-team); [site](https://valvrareteam.net/)
- **GSGA Fansub** ("Anime hay, Phụ đề cũng phải tốt") announced that from 08/2025 it would no longer do heavy typesetting. No public style rules were found. [Source](https://good-subs-good-anime-vietsub.github.io/)
- **Official vs. licensed quality, reader view** (2020-03-02 review): [Source](https://truyenbanquyen.com/%E3%80%90chuyen-de-tbq%E3%80%91ipm-ong-hoang-tac-trach/)
  - SkyNovel ranked best for translation.
  - Uranix praised for keeping Japanese style and gripping fight scenes.
  - Amak's "Đến Thế Giới Mới Với Smartphone" vol.1 "y như google dịch".
  - IPM criticized for errors and inconsistency.
  - Mahouka (Nhã Nam) has a subject-reversal error (vol.6 p.119).
- **Official anime streaming in Vietnam**:
  - Muse Việt Nam is the Vietnamese channel of Muse Asia (Muse Communication, Taiwan) and streams licensed anime with Vietnamese subtitles on YouTube. Some titles were removed to move to Crunchyroll. [trainghiemso](https://trainghiemso.vn/muse-vietnam/); [lag.vn](https://lag.vn/tin/giai-tri/nhieu-bo-anime-bi-go-khoi-youtube-chuyen-sang-crunchyroll-fan-viet-lieu-da-san-sang-len-app-.html)
  - POPS streams anime with subtitles and dubs. [POPS](https://popsww.com/vi/entertainment/)
- **Market context**: Japanese manga is about 70% of comics distributed in Vietnam, per a Kim Đồng editor. [Wikipedia: Vietnamese comics](https://en.wikipedia.org/wiki/Vietnamese_comics)

### Inferences
**Fan vs official style divide.** Fans and LN readers (2020) favor:
- keeping honorifics and Japanese terms;
- strict name and xưng hô consistency;
- a "Japanese feel".

Some licensed publishers have localized more heavily ("anh hai", "chị"), occasionally inconsistently. Official subtitles are constrained by line length and broad audiences. This pattern is inferred from the sources; I found no direct commentary on Muse/POPS subtitle style.

**For an LLM prompt, the closest thing to a community "quy chuẩn" is:**
1. a per-project glossary of names, terms, xưng hô pairs per dyad and honorific policy;
2. thuần Việt in modern settings, rationed Hán-Việt;
3. no văn convert;
4. relationship-stage pronoun evolution;
5. consistent particles and dialect base;
6. sparing TNs at first appearance.

### Gaps
- No retrievable formal style guide from any named Vietnamese fansub, scanlation or VN group: Facebook and Discord are not indexed, and docln is blocked.
- No Vietnamese-language commentary was found on Muse/POPS subtitle style or errors.
- No commentary was found on official Vietnamese localizations of Japanese-developed games, e.g. which Japanese games ship official Vietnamese text and with whose localization.
- No data on VNG localization style for Japanese IP.
- The 2024 Duy Tan academic paper could not be text-extracted, so its film-based findings on office keigo -> Vietnamese are not included.
