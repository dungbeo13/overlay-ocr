# LLMs for Japanese game/VN translation (JA->EN evidence, JA->VI gaps) and prompting/context techniques

Research date: 2026-10-24. Context: local real-time JA->VI translator, currently Tencent Hy-MT2 7B/1.8B GGUF on an RTX 4080 SUPER 16 GB (llama.cpp), with per-game glossary, character pronoun pairs and recent dialogue as context.

## Q1. Benchmarks: VNTL leaderboard and other JA game/manga benchmarks, plus anything for Japanese->Vietnamese

### Takeaway
VNTL, the only VN-specific leaderboard, has not added a model since about early 2025. Its newest entries are DeepSeek-V3, Grok-2-1212 and Phi-4. It scores single lines with embedding similarity, so it can't rank 2025-2026 models. Fresher JA evidence comes from JP-TL-Bench (pairwise LLM judge, 2025-26), WMT25 en-ja, and the Hy-MT2 paper (May 2026). I found no published benchmark for Japanese->Vietnamese LLM translation (no ja-vi FLORES/WMT/NTREX numbers). JA->VI choices have to rest on proxies plus your own test set.

### Cited Findings
**VNTL leaderboard (lmg-anon), methodology and caveats**
- Method: 256 JA->EN samples. 128 come from VNTL's eval set and 128 from a newer set that isn't public yet. Each LLM translates one line per sample. The score is the cosine similarity between the output and a human reference, averaged into "Accuracy". chrF is reported but does not affect the ranking. No LLM judge is used. — [VNTL leaderboard (HF dataset)](https://huggingface.co/datasets/lmg-anon/vntl-leaderboard)
- Caveats stated by the author: the results are "preliminary" and the evaluation script may change. The embedding model is imperfect, so correct translations sometimes score low. 256 pairs is a small sample and covers a narrow range of VN settings and speech styles. — [VNTL leaderboard](https://huggingface.co/datasets/lmg-anon/vntl-leaderboard)
- Top of the board (accuracy): GPT-4o-2024-05-13 0.7516, GPT-4o-2024-08-06 0.7497, Claude-3-Opus 0.7459, Claude-3.5-Sonnet-0620 0.7440, DeepSeek-V3 0.7424, Claude-3.5-Sonnet-1022 0.7280, GPT-4o-mini 0.7223. — [VNTL leaderboard](https://huggingface.co/datasets/lmg-anon/vntl-leaderboard)
- Best local/open entries: Qwen2.5-72B Q5_K_M 0.7079, vntl-gemma2-27b Q5_K_M 0.7067, Qwen2.5-32B Q5_K_M 0.7066, vntl-llama3-8b-v2 Q8_0 0.6952, Llama-3.3-70B Q4_K_M 0.6881, Phi-4 Q4_K_M 0.6860, Gemma-2-27b-it Q5_K_M 0.6793. — [VNTL leaderboard](https://huggingface.co/datasets/lmg-anon/vntl-leaderboard)
- Mid and small entries: Aya-23-35B 67.71, Mistral-Nemo-2407 67.38, Qwen2.5-14B-Instruct 66.48, Gemini-Flash-1.5 66.20, vntl-gemma2-2b 65.72, Aya-Expanse-32B 65.50, Qwen2.5-7B-Instruct 65.18, Gemma-2-9b-it 61.94, Aya-Expanse-8B 61.91. — [benchmarklist.com VNTL mirror](https://benchmarklist.com/benchmarks/vntl/)
- Classic MT tools on the same test: Sugoi Translator 0.6093, Google Translate 0.5395, Naver Papago 0.4560, Alibaba 0.4089. — [VNTL leaderboard](https://huggingface.co/datasets/lmg-anon/vntl-leaderboard)
- Staleness: the mirror says "Results updated May 6, 2026", but that is an import date. It lists no Gemini 2.5/3, Claude 4, GPT-5, Qwen3 or Gemma 3, and its newest entries are DeepSeek-V3, Grok-2-1212, Llama-3.3-70B and Phi-4. The benchmark was released 2024-06-09. — [benchmarklist.com VNTL mirror](https://benchmarklist.com/benchmarks/vntl/). The HF collection shows a last update of June 8, 2024. — [VNTL leaderboard](https://huggingface.co/datasets/lmg-anon/vntl-leaderboard)
- The benchmark repo says it is "still in development" and that its datasets (Senren, Mashiro) are "Currently not readily available". — [lmg-anon/vntl-benchmark GitHub](https://github.com/lmg-anon/vntl-benchmark)

**JP-TL-Bench (Shisa.AI, 2025; arXiv 2601.00223)**
- About 70 EN<->JA items. Each test model is compared pairwise against 20 frozen anchor models, with gemini-2.5-flash as the default judge. Results are reported as win rate (WR%) plus Bradley-Terry scores (0-10). A run costs about $7. — [shisa-ai/jp-tl-bench](https://github.com/shisa-ai/jp-tl-bench)
- Overall WR%: gemini-2.5-pro 96.15, gemini-2.5-flash 92.92, Qwen3-30B-A3B-Instruct-2507 84.33, shisa-v2-llama3.1-405b 81.45, gpt-4o 76.02, shisa-v2-unphi4-14b 72.81, Llama-3.1-Swallow-8B-v0.5 62.16, NVIDIA-Nemotron-Nano-12B-v2 59.91, Llama-3.3-70B 58.05, phi-4 49.80, Qwen3-4B 44.68, LFM2-2.6B 43.83, Llama-3.1-8B 38.84. No JA->EN-only split is published and Gemma 3 is not tested. — [shisa-ai/jp-tl-bench](https://github.com/shisa-ai/jp-tl-bench)
- The benchmark flags JA->EN errors that sentence-level metrics miss, such as wrong gendered pronouns. — [JP-TL-Bench paper, arXiv 2601.00223](https://arxiv.org/pdf/2601.00223)

**WMT25 (general MT, 2025)**
- Preliminary en-ja AutoRank: Shy-hunyuan-MT 1.0 (top), In2x 2.3, Gemini-2.5-Pro 2.4. The organizers say human evaluation is the more reliable measure. — [Preliminary Ranking of WMT25, arXiv 2508.14909](https://arxiv.org/pdf/2508.14909)
- Slator reports the Hunyuan system "appears to have been trained to optimize directly for the metrics" used in WMT25, which may inflate its automatic scores. — [Slator, WMT25 preliminary results](https://slator.com/wmt25-preliminary-results-gemini-2-5-pro-gpt-4-1-lead-ai-translation/)
- Secondary blog claim, not verified against the official findings: Gemini 2.5 Pro reached the top human-evaluation cluster in 14 of 16 pairs. — [laratranslate blog](https://blog.laratranslate.com/translation-model-benchmark/)
- Hunyuan-MT-7B's model card claims "first place in 30 out of the 31 language categories" at WMT25. — [tencent/Hunyuan-MT-7B](https://huggingface.co/tencent/Hunyuan-MT-7B)

**Hy-MT2 technical report (May 2026), the newest broad MT comparison including small models**
- Metrics are XCOMET-XXL / CometKiwi / GEMBA. On FLORES-200 XX<->XX: Hy-MT2-7B 86.89 / 76.03 / 87.23, Hy-MT2-1.8B 79.77 / 73.41 / 78.64, Gemini 3.1 Pro (thinking) 88.74 / 77.60 / 90.97, GPT-5.5 (thinking) 88.44 / 77.36 / 90.93, DeepSeek-V4-Pro 86.81 / 76.66 / 89.86, Microsoft Translator 80.13 / 72.42 / 83.85. — [Hy-MT2 report, arXiv 2605.22064v2](https://arxiv.org/html/2605.22064v2)
- On WMT25: Hy-MT2-7B 63.86 / 71.21 / 82.24, TranslateGemma-27B 58.02 / 66.08 / 81.24, Gemini 3.1 Pro 57.58 / 69.06 / 82.23. My extraction notes that the table columns were partly misaligned, so treat individual cells cautiously. — [Hy-MT2 report](https://arxiv.org/html/2605.22064v2)
- The report has no per-language results for ja, vi, ja-vi or zh-vi. — [Hy-MT2 report](https://arxiv.org/html/2605.22064v2)

**Manga / anime**
- "Context-Informed Machine Translation of Manga using Multimodal LLMs" (COLING 2025) reports state-of-the-art JA-EN and the first JA-PL manga dataset. Code and data are at github.com/plippmann/multimodal-manga-translation. — [arXiv 2411.02589](https://arxiv.org/html/2411.02589v2). Its findings on context are under Q3.

**Japanese->Vietnamese specifically**
- I found no LLM benchmark for JA<->VI. The only listed ja-vi result is pre-LLM: KSE'18, best BLEU 9.64 with BPE plus back-translation, against an NMT baseline of 8.18. — [NLP-Vietnamese-progress, machine_translation.md](https://github.com/undertheseanlp/NLP-Vietnamese-progress/blob/master/tasks/machine_translation.md)
- VNJPTranslate (Apr 2025), a Sailor-1.8B fine-tune for Vi-Ja, was withdrawn in Oct 2025 for "a critical error in Section 3.1, leading to invalid results". — [arXiv 2504.00339](https://arxiv.org/abs/2504.00339)
- A Claude-based low-resource MT paper covers Japanese and Vietnamese, but only in English-centric pairs. It also reports FLORES-200 contamination for Claude. — [arXiv 2404.13813](https://arxiv.org/html/2404.13813v1)
- Vietnamese community practice (anecdote): a Vietnamese LunaTranslator guide says Gemini works well for game translation and recommends Gemini 2.0 Flash Lite as cheap and stable. — [yukihanagamesblog LunaTranslator guide](https://yukihanagamesblog.wordpress.com/huong-dan-su-dung-lunar-translator-cho-visual-novel-va-otome-games/). NamuWiki says the most-used LunaTranslator model is Gemini 2.0 Flash. — [Luna Translator, NamuWiki](https://en.namu.wiki/w/Luna%20Translator)
- RSTGameTranslation, a Vietnamese real-time screen translator for games, supports Gemini, Groq, ChatGPT, Ollama, Mistral, LM Studio and Google Translate, and lets users enter game info for contextual translation. — [RSTGameTranslation](https://thanhkeke97.github.io/RSTGameTranslation/index_vi.html)

### Inferences
- In VNTL, the frontier API models sit within about 0.05 accuracy of the best local 27-72B models. The metric is embedding similarity on single lines, so it tells you little about the context-dependent failures that matter for VI (address terms and pronouns).
- For 2026 models, JP-TL-Bench and the Hy-MT2 report are better proxies. Both suggest a small model (Qwen3-30B-A3B MoE, Hy-MT2-7B) can approach big API models on general JA translation. Neither measures game dialogue or Vietnamese address terms.
- With no ja-vi benchmark, the practical way to choose is a small in-house JA->VI test set: a few hundred lines from target games, scored with a pairwise LLM judge in the JP-TL-Bench style, plus a pronoun/address checklist.

### Gaps
- I couldn't access the HF Space version of the VNTL leaderboard (401). If it has newer entries, they are not reflected here.
- No ja->vi FLORES/NTREX/WMT numbers for any modern LLM were found. WMT25 general did not appear to include Vietnamese, but I didn't verify the full language list.
- I didn't find the official WMT25 en-ja human ranking.
- I found no benchmark of LLMs on Vietnamese pronouns and address terms.

## Q2. Translation-specialized and general open models (2025-2026): 16 GB fit, licences, JA quality, Vietnamese support

### Takeaway
Hy-MT2 (Apache-2.0, May 2026, supports vi and ja, 1.8B/7B/30B-A3B) is the strongest small open MT model with Vietnamese in published benchmarks. It is a reasonable default, ideally at Q8_0/Q6_K rather than Q4_K_M. The Q4_K_M 7B quant loses about 8 points on instruction-following, which governs glossary and context use. TranslateGemma's prompt format is rigid: a single user turn, a 2K-token context and no official glossary/context support, which makes it a poor fit for a glossary-and-context pipeline. SakuraLLM and Sugoi output Chinese or English only and are non-commercial or JA-EN specific. They are useful as prompt-design references, not as JA->VI engines.

### Cited Findings
**Hy-MT2 (Tencent)**
- Open-sourced 2026-05-21 in 1.8B, 7B and 30B-A3B (MoE) sizes. The licence is Apache-2.0. Vietnamese (vi) is in the supported list, which the model card describes as 33 languages, though its table has about 38 entries. HF metadata lists the "7B" as 8B parameters. — [tencent/Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B)
- Context: OpenRouter lists 8,192 tokens for the 1.8B. NVIDIA docs give 256K for the 30B. The 7B card states no context length. — [OpenRouter hy-mt2-1.8b](https://openrouter.ai/models/tencent/hy-mt2-1.8b); [NVIDIA NeMo Hy-MT2](https://docs.nvidia.com/nemo/automodel/nightly/model-coverage/llm/tencent/hy-mt2.html); [tencent/Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B)
- Recommended sampling for 1.8B/7B: temperature 0.7, top_p 0.6, top_k 20, repetition_penalty 1.05. There is no default system prompt. — [tencent/Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B)
- GGUF exists for the 7B. The card says it needs the STQ kernel from llama.cpp PR #22836. The 1.8B also has 2-bit and 1.25-bit (AngelSlim) GGUFs. — [tencent/Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B)
- Quantization (FLORES XX<->XX XCOMET / IFMTBench):
  - 7B: BF16 87.06 / 83.14, FP8 86.92 / 82.38, Q4_K_M 86.90 / 75.11.
  - 1.8B: BF16 79.21 / 69.36, Q4_K_M 77.19 / 63.47, 2-bit 76.31 / 58.99.
  
  Q4_K_M keeps FLORES quality but loses about 8 IFMTBench points on the 7B. — [Hy-MT2 report](https://arxiv.org/html/2605.22064v2)
- IFMTBench (translation instruction-following) totals: Hy-MT2-7B 83.14, Gemma4-26B-A4B 79.22, Qwen3.6-35B-A3B 74.66, Gemma4-E4B 71.70, Hy-MT2-1.8B 69.36, Gemma4-E2B 57.59, Gemini 3.1 Pro 89.08, GPT-5.5 87.39. — [Hy-MT2 report](https://arxiv.org/html/2605.22064v2)
- Predecessor HY-MT1.5-7B: FLORES XX<->XX XCOMET 80.98, against 86.89 for Hy-MT2-7B. The row may be misaligned in my extraction. — [Hy-MT2 report](https://arxiv.org/html/2605.22064v2)

**Hunyuan-MT-7B / Chimera-7B (Tencent, 2025-09-01)**
- Supports vi and ja. Prompt for XX<->XX: "Translate the following segment into <target_language>, without additional explanation." Sampling: temp 0.7, top_p 0.6, top_k 20, rep 1.05. Chimera is an ensemble model that fuses several candidate translations into one ("Only output the refined translation"). FP8 releases exist. — [tencent/Hunyuan-MT-7B](https://huggingface.co/tencent/Hunyuan-MT-7B)

**TranslateGemma (Google, Jan 2026; arXiv 2601.09012)**
- Built on Gemma 3 in 4B/12B/27B sizes and evaluated on 55 languages. Google says the 12B beats the Gemma 3 27B baseline on WMT24++ by MetricX. — [Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/translategemma/)
- WMT24++ MetricX (lower is better): 4B 5.32, 12B 3.60, 27B 3.09. COMET: 81.6, 83.5, 84.4. — [google/translategemma-12b-it](https://huggingface.co/google/translategemma-12b-it)
- Gemma licence, gated. User and Assistant roles only. The user content must be exactly one entry with source_lang_code, target_lang_code and text. Total input context is 2K tokens. Extra context, glossaries and system instructions are "not officially supported". The examples use greedy decoding. — [google/translategemma-12b-it](https://huggingface.co/google/translategemma-12b-it)

**Seed-X (ByteDance, Jul 2025)**
- A 7B MT model covering 28 languages, including vi. The Instruct model is trained to translate via CoT and then RL. — [Seed-X report, arXiv 2507.13618](https://arxiv.org/html/2507.13618v1); [ComfyUI wiki news](https://comfyui-wiki.com/en/news/2025-07-24-bytedance-seed-x-7b-open-source-model)
- Licence sources conflict: OpenMDW per ComfyUI news, while the arXiv record shows CC BY-NC-ND 4.0, which may be the paper's licence. — [ComfyUI wiki](https://comfyui-wiki.com/en/news/2025-07-24-bytedance-seed-x-7b-open-source-model); [arXiv 2507.13618](https://arxiv.org/pdf/2507.13618v1)

**SakuraLLM (JA->ZH only)**
- Sakura-14B-Qwen2.5-v1.0 (2024-10-08), 7B-Qwen2.5-v1.0 (2024-11-23) and 1.5B-Qwen2.5-v1.0 (2024-10-12). GalTransl-v2.6 7B targets VN scripts. All are CC BY-NC-SA 4.0 with commercial use prohibited. The target is Simplified Chinese. — [SakuraLLM GitHub](https://github.com/SakuraLLM/SakuraLLM)

**VNTL fine-tunes (JA->EN only)**
- vntl-llama3-8b-v2 is based on rinna/llama-3-youko-8b, a Llama 3 derivative under the Llama 3 licence. The author describes it as "more accurate and stable" but "more literal" than the previous version and recommends temperature 0. — [lmg-anon/vntl-llama3-8b-v2-gguf](https://huggingface.co/lmg-anon/vntl-llama3-8b-v2-gguf)

**General models (JA quality evidence)**
- Qwen3-30B-A3B-Instruct-2507 ranks 3rd on JP-TL-Bench (84.33 WR), behind only Gemini 2.5 Pro and Flash. Qwen3-4B scores 44.68. — [jp-tl-bench](https://github.com/shisa-ai/jp-tl-bench)
- A 2026 guide says Qwen3 supports 119 languages with strong Japanese. — [classmethod local LLM guide 2026](https://dev.classmethod.jp/en/articles/local-llm-guide-2026/)
- Anecdotes from an r/LocalLLaMA thread, early 2025, snapshot Dec 2025:
  - gemma-3-12b-it-qat is suggested.
  - Sugoi-14B-Ultra is called "the best small ja-en MTL model", but secondhand.
  - PlaMo 2 Translate (about 12 GB VRAM) is praised for carrying tone into English.
  - Gemma-3-12B and Mistral-Small-3.1-24B are both called accurate, with Mistral rated higher.
  - LFM2-350M-ENJP-MT is suggested as a small option.
  
  — [Reddit snapshot](https://reddit.sentinel-team.org/posts/1pubu7x/snapshots/2025-12-24T10%3A17%3A59.352397Z)
- Opinion from a guide: Japanese grammar trips up smaller models, a 32B is noticeably better than a 7B, and keigo is "particularly hard" for local models under 32B. — [insiderllm guide](https://insiderllm.com/guides/best-local-llms-translation/)
- Real-time API picks come from an AI-generated blog (Jan 2026) that reuses VNTL scores; these are opinions, not measurements. It rules out DeepSeek-V3 for real-time use because of 7.5-19 s TTFT. — [skerritt.blog](https://skerritt.blog/best-openrouter-models-for-real-time-visual-novel-translation/)

### Inferences
- **VRAM, rough bits-per-weight arithmetic, not measured.** Hy-MT2-7B (about 8B parameters) needs about 8.5 GB at Q8_0, about 6.5 GB at Q6_K and about 5 GB at Q4_K_M, plus KV cache. Q8_0/Q6_K fits next to a game on 16 GB. That matters because the Hy-MT2 report shows Q4_K_M costing about 8 IFMTBench points on exactly the instruction-following that glossaries and pronoun rules rely on, while FP8 loses under 1 point. I expect Q8_0 to behave like FP8, but that is not tested.
- Other options against 16 GB:
  - Gemma-3-12B or TranslateGemma-12B (about 7-8 GB at Q4) fits.
  - Qwen3-14B at Q4 (about 9 GB) fits.
  - Qwen3-30B-A3B at Q4 (about 18 GB) does not fit alongside a game without offloading MoE experts to the CPU.
- TranslateGemma's 2K context and single-message template conflict with injecting a glossary, a character sheet and previous lines. Using it that way means hand-building Gemma 3 control-token prompts outside the supported format, with unknown quality.
- Hunyuan-MT-7B and Chimera are superseded by Hy-MT2 on Tencent's own tables, and Hy-MT2 is Apache-2.0. Chimera's "fuse several candidates" idea maps onto a cheap two-candidate approach, discussed under Q3.

### Gaps
- No per-language ja->vi or ja->en scores for Hy-MT2, TranslateGemma, Seed-X or Qwen3 were found.
- I didn't confirm that TranslateGemma's 55-language list includes vi and ja. WMT24++ does include vi_VN and ja_JP, but that is unverified here.
- I didn't verify the Hunyuan-MT-7B licence name or territorial restrictions; the HF card didn't state them.
- I didn't find Aya Expanse's or Gemma 3/4's Vietnamese support documentation in this pass.
- The context length of Hy-MT2-7B is unstated, and it is unclear whether the STQ kernel requirement applies to all Hy-MT2 GGUFs or only the low-bit ones.

## Q3. Prompting and context techniques with evidence (context lines, character sheets, glossaries, few-shot, scene vs line, style, refinement, temperature, JSON, failure modes)

### Takeaway
The best-supported levers are three. First, a modest window of preceding dialogue, roughly 3-10 lines or one "page/scene". More context doesn't help and can hurt, and smaller LLMs often ignore context. Second, explicit metadata (speaker name, gender, aliases) and a glossary injected only when a term appears. Third, low or zero temperature for general LLMs. Draft-then-refine gives modest COMET gains with diminishing returns. Chain-of-thought and heavy JSON constraints can hurt. Pronoun and gender errors from Japanese pro-drop remain the main unsolved failure, and context alone does not reliably fix them.

### Cited Findings
**How much context and what unit**
- Manga study using GPT-4 Turbo at temperature 0.5:
  - Page-level units beat line-by-line: JA-EN ChrF 32.7 -> 36.0 and xCOMET 0.716 -> 0.758.
  - Context beyond one page did not help: about 35.7-35.9 ChrF, against 36.6 for page plus image.
  - Translating a whole volume in one query was worst at 34.9.
  - A rolling summary (chain-of-density) was best among the long-context methods.
  - The page image helped JA-EN (LBL 32.7 -> LBL-VIS 35.6).
  
  — [Lippmann et al., COLING 2025, arXiv 2411.02589](https://arxiv.org/html/2411.02589v2)
- In the same study, one-shot and five-shot prompts gave "no measurable difference" on validation. In human MQM, the LLM output had fewer minor and major errors than the official translation but more critical errors (160 vs 107). — [arXiv 2411.02589](https://arxiv.org/html/2411.02589v2)
- Survey results:
  - Hendy et al. (2023, GPT-3.5) varied context from 1 to 32 sentences and found wider windows helped, with few-shot redundant for document-level MT.
  - DocHPLT found inconsistent benefits from larger windows, with full-document training underperforming 10-sentence chunks.
  - Common settings are 3-5 previous sentence pairs or chunks.
  
  — [Survey on Context-Aware MT with LLMs, arXiv 2506.07583](https://arxiv.org/pdf/2506.07583); [DocHPLT, arXiv 2508.13079](https://arxiv.org/pdf/2508.13079)
- DelTA reports that with large windows, LLMs treat the input as a cohesive document and "lose track of the detailed information in each sentence". — [DelTA, arXiv 2410.08143](https://arxiv.org/pdf/2410.08143)
- Nine LLMs, translation-specialized ones included, were tested for context use. Their gains over encoder-decoder models are "not reflected in pronoun translation performance". LLMs can be context-insensitive, and smaller models more so. — [Mohammed & Niculae, arXiv 2410.14391](https://arxiv.org/abs/2410.14391); [survey/search summary](https://aclanthology.org/2025.mtsummit-1.10.pdf)

**Character info, speaker labels, honorifics**
- VNTL's fine-tune format includes a Metadata block (`[character] Name: ... | Gender: ... | Aliases`, plus `[element]` term entries), speaker-prefixed source lines, and previous JA/EN pairs as context. This is a trained-in format, not an ablation result. — [vntl-llama3-8b-v2-gguf](https://huggingface.co/lmg-anon/vntl-llama3-8b-v2-gguf)
- GalTransl's developers state that translation quality is better "when names can be correctly extracted", using a name/message JSON input. This is a developer claim, not a benchmark. — [GalTransl GitHub](https://github.com/GalTransl/GalTransl)
- WMT25 character-aware paper: speaker embeddings trained on fictional dialogue preserved speaker traits better than plain fine-tuning (EN->JA novels). Honorifics improved less clearly, which the authors attribute to speaker-hearer relationships. — [WMT 2025, 2025.wmt-1.10](https://aclanthology.org/2025.wmt-1.10.pdf)
- Culturally tailored instructional prompts, with explicit guidance on Japanese communication norms, improved cultural fit in EN-JA translation. — [Tenzer et al., arXiv 2509.11921](https://arxiv.org/abs/2509.11921)
- Vietnamese address terms are kinship-based, depend on relative age, relationship and attitude, and overlap between "I" and "you". — [Wikipedia: Vietnamese pronouns](https://en.wikipedia.org/wiki/Vietnamese_pronouns)
- Anecdote from a practitioner translating books with LLMs: because Vietnamese address forms are effectively open-ended, they had an LLM generate a relationship and address-form summary from the text and attach it as an instruction. — [TranslateBooksWithLLMs discussion #238](https://github.com/hydropix/TranslateBooksWithLLMs/discussions/238)

**Glossary / term injection**
- Sakura v1.0 accepts a "GPT dict" (`src->dst #note`, one per line) in the user message. The authors say v1.0 improved accuracy "especially pronoun accuracy" and supports glossaries for consistent names and pronouns. With an empty glossary, they recommend the plain prompt. — [SakuraLLM GitHub](https://github.com/SakuraLLM/SakuraLLM)
- GalTransl sends a GPT-dict entry only when the term appears in the current request. It also uses pre- and post-translation replacement dictionaries, with conditional triggers to avoid over-replacement. — [GalTransl GitHub](https://github.com/GalTransl/GalTransl)
- Hy-MT2's official terminology template begins "Reference the following translations:" with lines of the form "X translates to Y", followed by the translation instruction. The paper shows the template but reports no glossary-specific metric. — [tencent/Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B); [Hy-MT2 report](https://arxiv.org/html/2605.22064v2)

**Few-shot examples**
- For PaLM translation, few-shot example quality mattered most. Different random 5-shot prompts changed sentence-level BLEURT by more than 1 point for most sentences. — [Vilar et al., ACL 2023](https://aclanthology.org/2023.acl-long.859)
- In the manga study, 1-shot was as good as 5-shot. — [arXiv 2411.02589](https://arxiv.org/html/2411.02589v2)

**Temperature, task and style prompts, CoT**
- ChatGPT MT quality "depends largely on temperature", and lower is usually better. Task-specific and domain-specific prompts help. CoT hurt because it encouraged word-by-word translation. Hallucination was more common in non-English-centric directions. — [Peng et al., Findings EMNLP 2023, arXiv 2303.13780](https://arxiv.org/pdf/2303.13780)
- Recommended defaults differ by model:
  - Sakura: temp 0.1, top_p 0.3, plus frequency penalty 0.1-0.2 if output degenerates. — [SakuraLLM](https://github.com/SakuraLLM/SakuraLLM)
  - VNTL: temp 0, no repetition penalty. — [vntl-llama3-8b-v2-gguf](https://huggingface.co/lmg-anon/vntl-llama3-8b-v2-gguf)
  - Hy-MT2 and Hunyuan-MT: temp 0.7, top_p 0.6, top_k 20, rep 1.05, vendor-tuned. — [Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B); [Hunyuan-MT-7B](https://huggingface.co/tencent/Hunyuan-MT-7B)
  - TranslateGemma: greedy. — [translategemma-12b-it](https://huggingface.co/google/translategemma-12b-it)
- Hy-MT2 has an official style template ("strictly conform to" a `{target_style}`) and is benchmarked on translation instruction-following (IFMTBench). — [Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B)

**Two-pass / refine / ensemble**
- TEaR (translate, estimate, refine) improves translations from self-feedback alone, as measured by COMET and MQM. — [TEaR, arXiv 2402.16379](https://www.arxiv.org/pdf/2402.16379)
- TasTe: most refined outputs beat their drafts, with the largest gains on "Bad" drafts. — [TasTe, arXiv 2406.08434](https://arxiv.org/pdf/2406.08434)
- MT-Ladder: a 2B refiner raises COMET, with less gain on each further iteration. — [Ladder, arXiv 2406.15741](https://arxiv.org/pdf/2406.15741)
- Earlier work saw insignificant gains or occasional declines from self-refinement. — [search summary citing arXiv 2402.16379](https://arxiv.org/html/2402.16379v1)
- Post-editing guided by MQM error annotations improves TER, BLEU and COMET. The benefit of fine-grained feedback is unclear without fine-tuning. — [Ki & Carpuat, arXiv 2404.07851](https://arxiv.org/pdf/2404.07851)
- Hunyuan-MT-Chimera-7B fuses several candidate translations into one refined output. — [tencent/Hunyuan-MT-7B](https://huggingface.co/tencent/Hunyuan-MT-7B)

**Structured / JSON output**
- Format restrictions, JSON mode especially, significantly degraded reasoning. JSON was competitive on classification. Looser formatting, or generating freely and then converting, preserved performance. Translation was not tested. — [Tam et al., "Let Me Speak Freely?", EMNLP 2024 Industry, arXiv 2408.02442](https://arxiv.org/pdf/2408.02442)
- Community tools use JSON input. GalTransl uses name/message JSON, and the manga study's long-context variants used JSON output. No ablation of JSON against plain output for translation was found. — [GalTransl](https://github.com/GalTransl/GalTransl); [arXiv 2411.02589](https://arxiv.org/html/2411.02589v2)

**Known failure modes**
- Zero-pronoun antecedents in JA->EN need inter-sentence context, and BLEU fails to capture whether they were resolved. — [Shimazu et al., LREC 2020](https://aclanthology.org/2020.lrec-1.447/)
- Zero-pronoun data augmentation helps intra-sentence cases but not when the needed information lies outside the sentence. — [Ri et al., arXiv 2107.00318](https://arxiv.org/pdf/2107.00318)
- Sakura lists pronoun errors and context understanding as open problems. Its system prompt explicitly forbids adding pronouns absent from the source. — [SakuraLLM GitHub](https://github.com/SakuraLLM/SakuraLLM)
- Wrong gendered pronouns are a flagged JA->EN error class. — [JP-TL-Bench](https://arxiv.org/pdf/2601.00223)
- GalTransl's automatic checks reflect common LLM failures: repeated words, leftover Japanese, line-break count mismatches, output much longer than the source (hallucination or padding), unfollowed dictionary terms, and untranslated text. — [GalTransl](https://github.com/GalTransl/GalTransl)
- Kansai dialect and samurai-style speech have no consensus target rendering, so metric "errors" may be valid choices. — [arXiv 2411.02589](https://arxiv.org/html/2411.02589v2)
- Unsupported advice from an AI-generated blog: 10-15 previous lines, temp 0, "keep honorifics where appropriate". — [skerritt.blog](https://skerritt.blog/best-openrouter-models-for-real-time-visual-novel-translation/)

### Inferences
- **Previous lines.** For a real-time line-at-a-time translator, about 3-10 previous source lines (ideally with their accepted VI translations) is the evidence-backed range. That approximates the "page" unit that worked best for manga. A short rolling scene summary is better than a long transcript. Translating the current line while seeing the scene beats isolated lines, but full-scene re-translation adds latency for little proven gain.
- **Pronouns and address terms.** Because LLMs, small ones especially, may ignore context, put the decisive facts explicitly in the prompt rather than hoping the model infers them from history: the current speaker, the likely addressee, and the fixed VI address pair for that pair of characters (for example "A->B: tớ/cậu"). This is what Sakura's and VNTL's metadata do. Japanese often omits who is spoken to, so speaker/addressee tracking from the game hook or OCR is likely the single highest-value context signal for Vietnamese.
- **Glossary.** Inject only the entries whose source term appears in the current line or context, as GalTransl does. This keeps prompts short for small models and Hy-MT2's 8K context, and it reduces spurious substitutions. Pair it with a post-translation check that the target term appears.
- **Temperature.** Use near-greedy (0-0.3) for general LLMs. For Hy-MT2, start from the vendor-tuned 0.7/0.6/20 and A/B test it against lower values on your own set. No independent evidence for either was found.
- **Refinement.** A second refine pass is likely too slow for real-time on one GPU. A cheaper option is to translate first and refine in the background or on re-display, or only when automatic checks flag a problem (leftover kana or kanji, glossary miss, length ratio).
- **Output format.** Keep output as plain text for the translation itself, and put structure (speaker, glossary) on the input side. If JSON output is needed, use a simple single-field schema.

### Gaps
- No controlled study found on the optimal number of previous lines specifically for game/VN dialogue, or on line-by-line versus scene translation with small (<10B) local models.
- No ablation found showing the size of the effect of character sheets (gender, speech style) on pronoun accuracy for JA->any with LLMs. The evidence is format design and developer claims, plus the speaker-embedding paper.
- No study found on JSON output versus plain output for translation quality specifically.
- No study found on Vietnamese address-term selection by LLMs.

## Q4. Published community prompt formats for VN translation (LunaTranslator / VNTL / Sakura / GalTransl / vendor templates) and evidence they help

### Takeaway
The main community formats are public. Sakura uses a ChatML system prompt plus a "GPT dict" glossary. VNTL uses Llama-3 headers with a Metadata block of character name, gender and aliases. GalTransl uses name/message JSON with three dictionary types, and Tencent publishes official terminology, context and style templates for Hy-MT2. Their benefit is supported by developer release notes and design rationale, not independent ablations. The formats are tuned to their own fine-tuned models, and Sakura warns that other prompt formats may not get the same quality.

### Cited Findings
- **Sakura v1.0.** The system prompt casts the model as a light-novel translator, asks for fluent light-novel style, and asks for correct pronoun use without adding pronouns absent in the source. The user prompt carries the optional glossary (`src->dst #info` lines) before the instruction and the Japanese text, with multi-line input joined by newlines. Sampling: temp 0.1, top_p 0.3, rep 1, max 512 new tokens. The page notes that projects using other prompt formats aren't guaranteed the same quality. — [SakuraLLM GitHub](https://github.com/SakuraLLM/SakuraLLM)
- **VNTL v2.** Llama-3 chat headers: Metadata (character/element entries), then Japanese (`[Speaker]: line`), then English. Earlier pairs repeat as context, and the final English block is left for completion. Temperature 0. — [lmg-anon/vntl-llama3-8b-v2-gguf](https://huggingface.co/lmg-anon/vntl-llama3-8b-v2-gguf)
- **GalTransl** (GPL-3.0, JA->ZH galgame patch tool):
  - Name/message JSON input.
  - Multi-turn mode sends the rules once, then only new text and terms.
  - `gpt.contextNum` sets the number of previous translated lines sent.
  - Three dictionary types (pre-translation, GPT, post-translation).
  - Automatic error checks, with batch retranslation of flagged lines.
  
  — [GalTransl GitHub](https://github.com/GalTransl/GalTransl)
- **Hy-MT2 official templates.** Terminology ("Reference the following translations: … translates to …"), contextual (background placed before the source, "taking the provided background information into consideration"), formatted (preserve keys and placeholders), and style ("strictly conform to" a target style). There is no system prompt. — [tencent/Hy-MT2-7B](https://huggingface.co/tencent/Hy-MT2-7B)
- **Hunyuan-MT.** XX<->XX template "Translate the following segment into <target_language>, without additional explanation." ZH template "把下面的文本翻译成<target_language>，不要额外解释。" — [tencent/Hunyuan-MT-7B](https://huggingface.co/tencent/Hunyuan-MT-7B)
- **TranslateGemma.** Fixed structured user content (source_lang_code, target_lang_code, text) and no system role. — [google/translategemma-12b-it](https://huggingface.co/google/translategemma-12b-it)
- **LunaTranslator.** An open-source Windows tool combining text hooking, OCR fallback, translation, furigana and TTS. It is a front-end to services, not a model. — [Zendot on LunaTranslator](https://zendot.org/en/posts/hillya51-lunatranslator); [NamuWiki](https://en.namu.wiki/w/Luna%20Translator)
- **Evidence of help.** Sakura states that v1.0, which added glossary support and pronoun-focused training, improved pronoun accuracy, preserved `\n` better and reduced line-count mismatches. No numbers are given beyond perplexity for v0.9. — [SakuraLLM GitHub](https://github.com/SakuraLLM/SakuraLLM). VNTL v2 is described as more accurate and stable than v1. On the leaderboard, vntl-llama3-8b-v2 scores 0.6952 against 0.6922 for v1, and vntl-gemma2-27b reaches 0.7067, about equal to Qwen2.5-32B. — [vntl-llama3-8b-v2-gguf](https://huggingface.co/lmg-anon/vntl-llama3-8b-v2-gguf); [VNTL leaderboard](https://huggingface.co/datasets/lmg-anon/vntl-leaderboard)

### Inferences
- For Hy-MT2 specifically, the safest path is to stay close to Tencent's own templates. Combine the contextual template (glossary hits, the speaker/addressee/address-pair note and the last N lines as "background") with the terminology template, and keep the instruction in English or Chinese as in training. Ad-hoc chat-style system prompts are untested for this model, which has no default system prompt.
- Sakura's "do not add pronouns not in the source" rule is the wrong default for VI. Vietnamese usually needs an explicit address term where Japanese omits one. The correct adaptation is "use the specified address pair for this speaker->listener", not "avoid pronouns".

### Gaps
- I didn't retrieve LunaTranslator's built-in LLM prompt text, or any Vietnamese community-shared system prompts for JA->VI.
- No independent A/B evidence that any of these community formats beats a plain prompt on a general model was found.
