# Real-time JP game / VN translation tools and pipelines (2024-2026): what to adopt for a JP->VI OCR -> local LLM -> overlay

Research date: 2026-10-24. Sources are mostly primary (GitHub READMEs, official docs). Web search was weak for Reddit, VOZ, and Facebook threads, so community best practice is thinner than the tool documentation. Throughout, "author claim" means the tool's own docs, and "user report" means third-party guides, forum posts, or blogs.

---

## 1. LunaTranslator (HIllya51/LunaTranslator): features as of 2025-2026 and quality settings

### Takeaway
LunaTranslator is the de-facto successor to Textractor and the most complete real-time pipeline. Its pieces are HOOK, emulator HOOK, embedded translation, OCR with stability-based auto-triggering, a "General LLM Interface" that is OpenAI-compatible with context-history and glossary placeholders, ordered text-processing filters, and pre/post-translation dictionaries. Its prompt-template design (placeholders for context and glossary injected only when a term matches) is the most directly reusable idea for a JP->VI overlay.

### Cited Findings
**Capabilities (author claims, README)**
- HOOK covers "nearly all common and niche visual novels" (README: "几乎适配了所有的常见和冷门的视觉小说"). Some games support embedded translation written directly into the game. Emulator hooking covers NS (Switch), PSP, PSV, and PS2. — [LunaTranslator README](https://github.com/HIllya51/LunaTranslator)
- It ships a built-in "high-accuracy" local OCR model plus "many other online & offline OCR engines". Translators include LLM and offline engines. Language-learning features: Japanese segmentation, furigana display, AnkiConnect, and Yomitan. Also TTS and Windows speech recognition. GPL-3.0, code-signed via SignPath. — [LunaTranslator README](https://github.com/HIllya51/LunaTranslator)
- Docs are published in Vietnamese at docs.lunatranslator.org/vi/ (sections: Cách sử dụng cơ bản, HOOK, Dịch nhúng, Trình giả lập HOOK, OCR, API dịch phong phú, Học ngôn ngữ, TTS, speech recognition). The docs page does not confirm whether the app UI is localized to Vietnamese. — [LunaTranslator Vietnamese docs](https://docs.lunatranslator.org/vi/)
- Doc page list in the repo (docs/en) includes ocrparam, embedtranslate, textprocess, transoptimi, guochandamoxing (LLM), hooksettings, emugames, internaldict, ttsofname, and others. — [GitHub docs/en tree](https://github.com/HIllya51/LunaTranslator/tree/main/docs/en)

**LLM translator configuration (author docs)**
- The "General Large Model Interface" works with OpenAI, Gemini, Claude, Groq, OpenRouter, Azure, DeepSeek, Alibaba Bailian, Moonshot, Zhipu, and API relays such as new-api. You can clone it several times ("+") to run multiple LLM configs side by side and compare outputs. — [LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)
- Multiple API keys separated by `|` are "automatically rotated", with weights adjusted on errors. — [LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)
- **Context:** "Number of contextual messages" sends past original and translated lines to the model, and "Setting this to 0 will disable this optimization." — [LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)
- **Prompt templating:** custom system prompt, user message, and prefill, with placeholders `{sentence}`, `{srclang}`, `{tgtlang}`, `{contextOriginal[N]}`, `{contextTranslation[N]}`, `{contextBoth[N]}`. — [LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)
- **Glossary injection:** `{DictWithPrompt[XXXXX]}` inserts matching entries from the Proper Noun list, prefixed by an instruction. "If no matching entry is found, this field will be cleared." — [LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)
- Exposed sampling and generation parameters: temperature, max tokens (with a toggle for the renamed max_completion_tokens), top_p, frequency penalty, reasoning effort (Gemini thinkingBudget mapping: none/minimal -> 0, low -> 512, medium -> -1, high -> 24576), a DeepSeek thinking.type switch, and custom key-value params. The docs give no numeric recommendations for temperature or context count. — [LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)
- **Dedicated offline MT models** with publisher-fixed prompts (no custom prompt): Hy-MT2 (Tencent, general), Index-Translate (IndexTeam, general), and SakuraLLM and GalTransl (JP->ZH only). — [LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)

**Translation optimization / dictionaries (author docs)**
- Pre-translation substitution (Regex or Escape), Proper Noun translation, post-translation result correction, a custom Python optimizer, and skipping punctuation-only sentences. — [LunaTranslator transoptimi](https://docs.lunatranslator.org/en/transoptimi.html)
- Proper nouns are injected into the prompt when the LLM prompt contains `DictWithPrompt`. Otherwise (traditional MT, or an LLM without the placeholder) the term is swapped for a placeholder "ZX?Z" following VNR's approach and restored after translation. — [LunaTranslator transoptimi](https://docs.lunatranslator.org/en/transoptimi.html)
- VNDB metadata pre-fills a character-name dictionary. English users get English names, and others get empty entries or the original text. Game-specific terms should go in per-game "Game Settings -> Translation Optimization", with "Follow Default" and "Inherit Default" switches. — [LunaTranslator transoptimi](https://docs.lunatranslator.org/en/transoptimi.html)

**Text processing filters (author docs; user-orderable)**
- Filters: non-Japanese (non-Shift-JIS) characters, control characters, English punctuation, "characters outside 「」", curly braces `{kanji/furigana}` (ruby markup removal), Unicode normalization, numbers, letters, and angle brackets `<>` (HTML tags, mainly TyranoScript). Line tools: extract N lines, remove duplicate characters ("AAAABBBBCCCC->ABC"), four duplicate-line filters for repeated or progressively redrawn lines, and filter line breaks (removed for Japanese, replaced by spaces for other source languages). Also string/regex replacement and custom Python `POSTSOLVE(line)`. — [LunaTranslator textprocess](https://docs.lunatranslator.org/en/textprocess.html)
- Several duplicate filters "only appl[y] to text read in HOOK mode". Only five filters work in embedded-translation mode. — [LunaTranslator textprocess](https://docs.lunatranslator.org/en/textprocess.html)

**OCR auto-trigger (author docs; ocrparam.md)**
- Three modes: Periodic Execution (simplest, recommended if unsure), Analyze Image Update (OCR once the image stops changing), and Mouse/Keyboard Trigger + Wait for Stability (defaults: left click, Enter, release of Ctrl/Shift/Alt; only while the bound game window is in the foreground). — [ocrparam.md](https://raw.githubusercontent.com/HIllya51/LunaTranslator/main/docs/en/ocrparam.md)
- Parameters: an **Image Stability Threshold** (successive frames compared), an **Image Consistency Threshold** (compared against the frame at the last OCR, which the docs call "the most important parameter"), and a **Text Similarity Threshold** (edit distance vs. the previous OCR text, emitted only if large enough, which prevents re-translating tiny OCR jitter). — [ocrparam.md](https://raw.githubusercontent.com/HIllya51/LunaTranslator/main/docs/en/ocrparam.md)
- Tips: set stability to 0 for static scenes and raise it for Live2D or animated backgrounds. If OCR fires too often, raise consistency. With fastest text speed, delay and stability can be 0 in trigger mode. Multi-region mode recognizes each box independently. — [ocrparam.md](https://raw.githubusercontent.com/HIllya51/LunaTranslator/main/docs/en/ocrparam.md)

**User reports (Vietnamese)**
- Vietnamese blog guide (images dated Oct 2024 to Sep 2025): the author uses OCR, not HOOK, mostly for emulators. They report Google Lens OCR is best but needs internet and adds latency, while Local OCR and Windows OCR are faster but weaker. Their setup is Gemini via "General LLM Interface": "gemini 2.0 flash lite" for being cheap, Streaming Output off, and **context lines ~3-10 "để giữ cách xưng hô nhất quán"** (to keep pronouns and forms of address consistent). Vietnamese output was "dịch hơi bị sai 1 chút, nhưng tổng thể vẫn ok". Gemini was "khá ok, mượt". Cost was "a few dollars" per long VN. Pain points: antivirus false positives, API quota errors (fix: multiple keys), and no JP-VI dictionary integration. — [Yukihana Games blog (VI)](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/)
- A Vietnamese YouTube video exists titled "Khám phá phần mềm dịch trực tiếp game nước ngoài như Wuthering Waves & Visual novel tốt nhất 2025" (content not verified). — [YouTube](https://www.youtube.com/watch?v=8mqEnZzuycg)
- Third-party 2026 guide: LunaTranslator is "the current community recommendation" and "more actively maintained" than Textractor. — [VN Paths guide](https://vnpaths.com/how-to-translate-a-visual-novel/)

### Inferences
- The concrete pattern to adopt is a templated prompt with `{context}` and `{glossary}` slots that collapse to empty when nothing matches. This keeps prompts short for a local LLM, so latency stays low and the model is less distracted.
- Also adopt the dual-path glossary. With an LLM, inject only the glossary entries that occur in the line. With non-LLM fallback MT, use a placeholder-swap-and-restore.
- For OCR, LunaTranslator's three-threshold gating (frame stability -> diff vs. last-OCR frame -> text edit distance) is a proven way to avoid re-translating jittery OCR output. It maps directly onto a screen-capture overlay.
- The Vietnamese user's reasoning for context lines (consistent xưng hô: anh/em/tôi/cậu/mình) is the single most JP->VI-specific quality lever surfaced. Vietnamese pronouns depend on the relationship, which Japanese often leaves implicit. Keeping a speaker- and relationship-aware context window plus a glossary of character pronoun pairs is likely high value. This is an inference, not a measured result.
- Running several LLM configs side by side (LunaTranslator's "+" clone) suggests a useful dev feature: A/B two models or prompts in the overlay.

### Gaps
- No exact current LunaTranslator version or release date was retrieved (the README excerpt did not show it). Check the GitHub Releases page.
- The docs fetched don't mention speaker-name extraction as a feature. Whether LunaTranslator separates speaker name from dialogue for the LLM prompt was not confirmed (a `ttsofname` page exists, apparently for TTS by character name).
- Whether the app UI ships a Vietnamese locale was not confirmed (only the docs are confirmed in Vietnamese).
- No LunaTranslator GitHub issue threads about translation quality or recommended temperature were retrieved. The docs give no numeric recommendations.
- The en/useocr.html page returned 404, so the full OCR engine list was not retrieved.

---

## 2. Textractor / VNR / ITHVNR and other hookers: hooking vs. OCR, engine coverage, and anti-cheat risk in online and gacha games

### Takeaway
For offline VNs (KiriKiri, RealLive, Ren'Py, TyranoScript, many Unity titles), hooking gives clean, exact text and is described as "the most reliable" method. OCR is the fallback for emulators, odd engines, and image-rendered text. Hooking means DLL injection into the game process, the same technique family that anti-cheat targets. No tool author or publisher statement found explicitly clears hooking in Genshin or Blue Archive, while publishers' general ToS treat third-party process tools as ban-worthy. For gacha and online games, a capture-only OCR overlay is the defensible design.

### Cited Findings
- Textractor: "x86/x64 video game text hooker for Windows 7+ (and Wine)", based on ITHVNR. Auto-hooks "many game engines (including some not supported by VNR!)", supports /H hook codes and hook searching, GPL-3.0, ~2.7k stars, 546 open issues. The README carries no warning about online games or anti-cheat. — [Textractor GitHub](https://github.com/Artikash/Textractor)
- Third-party 2026 guide: Textractor "has not been actively maintained for some time" and newer games can break it. It auto-finds hooks for KiriKiri, Kirikiri Z, and RealLive, while rarer engines need manual codes from the VN Hooking Wiki. LunaTranslator is said to support Unity, Ren'Py, and TyranoScript. Hooking is "the most reliable real-time translation method for most Japanese visual novels". OCR is for older titles, emulators, unusual engines, or image-rendered text. — [VN Paths guide](https://vnpaths.com/how-to-translate-a-visual-novel/)
- textractor-translator (MRGRD56) is a third-party add-on that connects Textractor to OpenAI-compatible LLMs (including local models), with per-game translation context. — [GitHub textractor-translator](https://github.com/MRGRD56/textractor-translator) (via search snippet; README not fully read)
- LunaTranslator's HOOK-mode-only duplicate filters exist because hooked text often arrives repeated or redrawn character by character. Ruby arrives as `{kanji/furigana}` markup in some engines, and TyranoScript text carries `<>` tags. — [LunaTranslator textprocess](https://docs.lunatranslator.org/en/textprocess.html)
- Anti-cheat context: a security paper describes in-process cheats as relying on "dll injection and hooking". — [Black Hat EU 2014 paper](https://www.blackhat.com/docs/eu-14/materials/eu-14-StJohn-Next-Level-Cheating-And-Leveling-Up-Mitigations-wp.pdf)
- Genshin: per a secondary source, mods violate the ToS and players have been banned for third-party software such as FPS unlockers. — [Sportskeeda](https://sportskeeda.com/esports/can-genshin-impact-mods-get-banned-official-guidelines-past-instances) (secondary; not an official HoYoverse statement)
- A commercial overlay translator on Steam warns users to use it "with online/competitive games at your own risk" and check each game's ToS. — [OVR Overlay Translator (Steam)](https://store.steampowered.com/app/4304620/OVR_Overlay_Translator/) (via search snippet)
- Modifying localisation files: a developer on another free-to-play game (Gems of War) said changing "any game files, including the localisation files may flag your account for hacking and incur an automatic ban". — [Gems of War forum](https://community.gemsofwar.com/t/will-i-get-banned-for-modifying-language-files/55235/7) (different publisher; illustrative only)
- A Vietnamese user's guide notes LunaTranslator offers an admin-mode executable for games requiring admin, and that antivirus flags the tool. — [Yukihana Games blog (VI)](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/)

### Inferences
- For gacha targets (Blue Archive, Genshin, Wuthering Waves, Umamusume and similar), the overlay should rely on screen capture only. That means no process injection, no memory reads, and no file patching. This is the only design not structurally similar to cheats. Even so, "safe" can't be guaranteed, because no publisher has blessed it.
- Hooking would be a secondary, opt-in "offline VN mode", and the UI should state the risk explicitly. Hooked text eliminates OCR errors and furigana contamination, and often gives exact line boundaries and sometimes speaker names (engine-dependent).
- Capture method matters too. Exclusive fullscreen can block capture, and Translumo requires borderless or windowed mode (see section 3). A Windows Graphics Capture-based approach on borderless windows is the low-risk path.

### Gaps
- No official HoYoverse, Nexon/Yostar (Blue Archive), or Kuro (Wuthering Waves) statement on OCR overlays or text hookers was found. No confirmed ban reports tied to Textractor or LunaTranslator in gacha games were found either (Reddit search didn't surface threads).
- Whether Textractor's last release is still v5.2.0 (2022) was not confirmed from the fetched page (Releases section empty in the capture).
- Engine-level details on speaker-name extraction via hooks (for example, KiriKiri name-tag lines) were not retrieved.

---

## 3. Other tools: Sugoi, Translator++, MTool, XUnity.AutoTranslator, LinguaGacha, Translumo, GameTranslate, OwlOCR

### Takeaway
The ecosystem splits into (a) real-time readers (LunaTranslator, Textractor + add-ons, Translumo, overlay apps) and (b) offline extract -> batch-LLM -> reinject patchers (MTool, Translator++, LinguaGacha, XUnity for Unity). Batch tools reach higher quality because they can build a glossary first, see full context, and proofread. A real-time overlay should borrow their glossary-first and caching ideas.

### Cited Findings
**XUnity.AutoTranslator (Unity in-game replacement)**
- It hooks Unity text components (UGUI, NGUI, TextMeshPro, FairyGUI, Utage, with TextMesh and IMGUI disabled by default) and replaces displayed text in place. It tries several whitespace and newline variants per lookup. Version 5.4.0 (BepInEx/MelonLoader/IPA/ReiPatcher). — [XUnity README](https://github.com/bbepis/XUnity.AutoTranslator)
- **Caching:** generated translations go to `_AutoGeneratedTranslations.txt` (lowest priority). User .txt or .zip files override it, and ALT+R reloads. Also: SubstitutionFile (pre-translation replacement with `{{A}}` parameters), Pre/PostprocessorsFile, and `r:` regex and `sr:` splitter regex (split text, translate parts, reassemble). — [XUnity README](https://github.com/bbepis/XUnity.AutoTranslator)
- Rich text: `HandleRichText=True`. Rich-text strings "cannot currently be parameterized". — [XUnity README](https://github.com/bbepis/XUnity.AutoTranslator)
- **Spam limits:** MaxCharactersPerTranslation defaults to 200 ("Cannot be greater than 1000"; "Never redistribute this mod with this value greater than 400"). A 1 s wait before queuing, an 8000-request session cap, and "no concurrency!" for built-in endpoints. Auto-shutdown after 5 consecutive empty results or a 4000-item queue. — [XUnity README](https://github.com/bbepis/XUnity.AutoTranslator)
- IL2CPP support is incomplete ("Subpar text hooking capabilities", no IMGUI). — [XUnity README](https://github.com/bbepis/XUnity.AutoTranslator)
- LLM endpoints are third-party: LlmTranslators (OpenAI/Ollama), AutoLLMTranslator, and AutoPollinationTranslator. AutoChatGptTranslator is obsolete. CustomTranslate is a simple GET endpoint (from/to/text). — [XUnity README](https://github.com/bbepis/XUnity.AutoTranslator)
- LlmTranslators plugin: OpenAI, Ollama (`localhost:11434/api/chat`), and LM Studio (`localhost:1234/v1/chat/completions`) endpoints, configured in YAML (`systemPrompt`, `glossaryPrompt`, `modelParams`; the example uses temperature 0.2). Glossary YAML entries carry `raw`, `result`, optional `transliteration`, and `context`, and only matching terms are appended to the prompt. The README advises including source and target language, genre and tone, name-handling rules, tag and line-break rules, and "output only the translation". The example glossary header is "Use the translation for exact matches." — [LlmTranslators GitHub](https://github.com/joshfreitas1984/XUnity.AutoTranslate.LlmTranslators)
- A Sugoi offline endpoint exists for XUnity (tested with Sugoi Toolkit V4.0). Author claims quality "comparab[le] with Deepl" and no throttling, and recommends ≥8 GB RAM and optional CUDA. — [XUnity Sugoi endpoint](https://github.com/Vin-meido/XUnity-AutoTranslator-SugoiOfflineTranslatorEndpoint)

**Sugoi Toolkit**
- It is a local JP->EN offline MT (Fairseq-style) used as a backend with Textractor, XUnity, and others. A third-party guide lists it as a local companion to Textractor. — [airmore.ai](https://airmore.ai/ai-game-translation-tools.html); [TenshiTranslator (PyPI)](https://pypi.org/project/TenshiTranslator) (requires Sugoi + NVIDIA GPU)
- A Vietnamese user notes offline engines like Sugoi need separate installation. — [Yukihana Games blog (VI)](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/)

**MTool / Translator++ / LinguaGacha (batch extract -> LLM -> reinject)**
- A Chinese 2025 tutorial says MTool only extracts and injects text and does not translate by itself. Translation goes through a separate tool (AiNiee) plus an API (DeepSeek V3, or ChatGPT), and KeywordGacha builds a terminology glossary so names stay consistent. — [Bilibili tutorial](https://www.bilibili.com/opus/1038930345281454117)
- Translator++ (Dreamsavior) is an RPG Maker-focused tool that loads the game's data folder and tracks per-entry progress. Users report newer versions are paywalled, and a free version suffices per a Chinese 2025 tutorial. — [itch.io post](https://itch.io/post/11494913); [Bilibili Translator++ tutorial](https://www.bilibili.com/opus/1037789322806296599); [Translator++ 4.8.31 Patreon](https://www.patreon.com/posts/71280843)
- LinguaGacha v0.130.1 (2026-10-21) is an LLM translator for RenPy, RPG Maker MV/MZ/XP/VX/VXAce, WOLF RPG 3, Godot 4, and KV/Message-JSON from MTool, SExtractor, and VNTextPatch. Pipeline: **term extraction -> full translation -> automatic proofreading**. It supports OpenAI, Google, Anthropic, SakuraLLM, and local backends, recommends DeepSeek, and lists "16 languages" (Vietnamese not named on the page). Commercial use requires the author's permission. — [LinguaGacha GitHub](https://github.com/neavo/LinguaGacha)

**OCR overlays**
- Translumo v1.1.0 combines Windows OCR (recommended) and EasyOCR, and "uses a machine learning model to score each OCR result and selects the best one". Recognition: EN/RU/JA/ZH-Hans/KO. Translators: Google, DeepL, Yandex. Requires borderless or windowed mode for the overlay. ~5.9k stars. — [Translumo GitHub](https://github.com/ramjke/Translumo)

**Community sentiment on unreviewed AI translation**
- June 2026: romhack.ing disabled downloads of machine-translated patches after backlash over an AI-only Langrisser V patch ("No Human Proofreading Pass"). Critics cited nonsense output and lost nuance, while defenders said imperfect is better than nothing. — [Time Extension](https://www.timeextension.com/news/2026/06/romhack-ing-disables-ai-translated-patch-downloads-following-langrisser-v-upset)

### Inferences
- For a real-time overlay, borrow XUnity's persistent translation cache. Key it on normalized source text and let users override it by hand (like `_AutoGeneratedTranslations.txt` plus user files). Repeated UI strings and recurring lines in gacha games then cost zero LLM calls, and users can hand-correct the cache.
- Also borrow the "splitter regex" idea for templated gacha UI strings (for example "{name} obtained ×{n}"), and LinguaGacha's term-extraction pass: pre-scan a glossary per game (character names, skills, factions) before play.
- Translumo's multi-engine OCR plus learned-scorer selection is an option for accuracy, at a latency cost.

### Gaps
- No primary source was found for GameTranslate or OwlOCR (not searched in depth due to budget), or for MTool's official feature list or its built-in real-time/LLM features.
- Whether Sugoi Toolkit added an LLM backend in 2025-2026 is unconfirmed.
- Translator++'s current LLM (ChatGPT/Gemini) support was not confirmed from primary sources. The "ChatGPT, DeepSeek, custom system prompt" changelog found belongs to a RimWorld Steam mod, not Translator++.

---

## 4. OCR specifics for game text: furigana, vertical text, stylized fonts, name boxes, line joining

### Takeaway
Game-specialized Japanese OCR is now available locally (meikiocr, Apache-2.0, detection plus line recognition). Tools handle ruby mainly at the text level in hook mode (`{kanji/furigana}` stripping). In OCR mode they rely on region selection, multi-region boxes, and line-break filtering. No source documented an explicit "remove furigana from OCR" algorithm.

### Cited Findings
- meikiocr: "high-speed, high-accuracy, local ocr for japanese video games". It runs two stages, line detection (meiki.text.detect.v0, up to 64 boxes) then line recognition (meiki.text.recognition.v0, up to 48 chars per line), on ONNX (onnxruntime-gpu for GPU). Vertical text is "in beta". The author claims it "significantly outperforms general-purpose ocr tools like paddleocr or easyocr" on this domain. Apache-2.0. No furigana handling is documented. — [meikiocr GitHub](https://github.com/rtr46/meikiocr); [meikiocr demo](https://rtr46-meikiocr.hf.space)
- The Meikipop docs list meikiocr as fully offline and tuned for game text, and owocr as a high-accuracy "hybrid" provider that needs an owocr daemon. — [Meikipop OCR providers](https://www.mintlify.com/rtr46/meikipop/advanced/ocr-providers); [Meikipop available providers](https://www.mintlify.com/rtr46/meikipop/development/available-providers)
- LunaTranslator text filters relevant to OCR output: filter line breaks (Japanese lines are joined without spaces), Unicode normalization (full-width forms), "Filter Characters Outside of 「」" (useful to drop speaker names outside the dialogue quotes), and regex replacement. — [LunaTranslator textprocess](https://docs.lunatranslator.org/en/textprocess.html)
- LunaTranslator multi-region OCR recognizes each box independently, for example a separate name box and dialogue box. — [ocrparam.md](https://raw.githubusercontent.com/HIllya51/LunaTranslator/main/docs/en/ocrparam.md)
- User report: Google Lens OCR is best quality but online and slower, while Local and Windows OCR are faster but less accurate. — [Yukihana Games blog (VI)](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/)
- A 2026 guide mentions VNTranslator offers OCR "with pre-processing for styled fonts". — [VN Paths guide](https://vnpaths.com/how-to-translate-a-visual-novel/)
- Translumo requires borderless or windowed games for correct overlay display. — [Translumo GitHub](https://github.com/ramjke/Translumo)

### Inferences
- For furigana contamination in OCR, the overlay probably needs its own handling. One option is a geometric filter: drop detected boxes whose glyph height is under about 50-60% of the median line height and that sit directly above or right of a main line. Another is to rely on a line-detector trained to ignore ruby. Neither was documented in sources; both are design suggestions.
- Use separate capture regions (name box vs. dialogue box) to get speaker names, then pass "Speaker: line" to the LLM. That supports Vietnamese pronoun choice and matches the multi-region pattern in LunaTranslator.
- Join wrapped lines with no space before sending to the LLM (Japanese has no word spaces), matching LunaTranslator's "Filter Line Breaks" behavior for JA source.

### Gaps
- No source was found that documents how meikiocr, owocr, or manga-ocr treat furigana (ignored vs. emitted).
- No benchmark numbers (CER, latency in ms) for meikiocr vs. manga-ocr, PaddleOCR, or Windows OCR on game text appeared in the text-extractable parts of the README.
- No source was found on OCR handling of stylized fonts in gacha UIs specifically.

---

## 5. Community best-practice settings for LLM translation, and how the Vietnamese community uses these tools

### Takeaway
Documented practice converges on: a short system prompt that says "output only the translation"; low temperature (0.2 shown in the XUnity LLM plugin example); a few lines of context (Vietnamese user: 3-10 lines for consistent pronouns); glossary injection of only the matching terms; and per-game dictionaries. Vietnamese players mostly use LunaTranslator with Gemini or Google via its General LLM Interface, translating JP->VI directly. Batch "Việt hoá" with MTool or Translator++ follows the Chinese AiNiee/LinguaGacha workflow, but specific Vietnamese guides were not found.

### Cited Findings
- XUnity LlmTranslators example: temperature 0.2. The prompt should specify language pair, genre and tone, name-handling rules, tag and line-break preservation, and "output only the translation". Results "depend heavily on the prompt and model." — [LlmTranslators GitHub](https://github.com/joshfreitas1984/XUnity.AutoTranslate.LlmTranslators)
- LunaTranslator exposes context-count, prefill, and glossary placeholders but no recommended numbers. Prefill (assistant-message prefix) is supported, which is useful for forcing output format. — [LunaTranslator LLM docs](https://docs.lunatranslator.org/en/guochandamoxing.html)
- Vietnamese user practice: Gemini 2.0 Flash-Lite, streaming off, 3-10 context lines for consistent xưng hô, multiple API keys to dodge quota errors, a cost of a few USD per long VN, and quality judged "hơi bị sai 1 chút, nhưng tổng thể vẫn ok" for VI vs. near-error-free for EN. — [Yukihana Games blog (VI)](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/)
- Chinese batch workflow (the model the Vietnamese "Việt hoá" scene likely follows): MTool extract -> KeywordGacha glossary -> AiNiee + DeepSeek V3 / ChatGPT -> MTool inject. — [Bilibili tutorial](https://www.bilibili.com/opus/1038930345281454117)
- LinguaGacha's recommended flow: term extraction -> translation -> auto-proofreading. — [LinguaGacha GitHub](https://github.com/neavo/LinguaGacha)
- LunaTranslator has Vietnamese docs. — [LunaTranslator Vietnamese docs](https://docs.lunatranslator.org/vi/)
- Community caution: an AI-only translation patch with no human proofreading pass was pulled from romhack.ing in June 2026. — [Time Extension](https://www.timeextension.com/news/2026/06/romhack-ing-disables-ai-translated-patch-downloads-following-langrisser-v-upset)

### Inferences
- A sensible default for a local-LLM JP->VI overlay, synthesized from the sources rather than benchmarked:
  - temperature about 0.1-0.3
  - 3-8 prior source/target pairs as context
  - speaker-tagged input
  - a per-game glossary with character names and the Vietnamese pronoun pair for each relationship (for example "Sensei <-> học sinh: thầy/em")
  - only matching glossary lines injected
  - prefill or "output only the translation" to stop chatter
  - streaming off for overlay stability (or streaming on with replace-on-complete)
- Pivoting through English is not documented as common practice. The Vietnamese user translated JP->VI directly with Gemini. For a small local model, though, JP->VI quality may lag JP->EN, as the user's own EN vs. VI observation suggests. Consider testing direct vs. pivot, and models with explicit Vietnamese coverage (for example Tencent Hy-MT, listed as a general offline model in LunaTranslator; its Vietnamese coverage was not verified here).
- Caching plus a user-editable correction file (XUnity-style), together with LunaTranslator-style post-translation correction rules, lets Vietnamese users fix recurring mistranslations without changing the model.

### Gaps
- No Reddit (r/visualnovels, r/LearnJapanese, r/gachagaming), Fuwanovel, or VNDB threads with shared LLM prompts or settings surfaced in search. Community-shared system prompts could not be quoted.
- No Vietnamese VOZ, Facebook, or Việt hoá group guides on MTool, Translator++, or LunaTranslator with ChatGPT or Gemini were found beyond one WordPress blog and one YouTube title. Vietnamese batch-Việt hoá practice remains undocumented here.
- No benchmark comparing local LLMs for JP->VI game text was found (by contrast, JP->ZH has SakuraLLM and GalTransl as dedicated models).
