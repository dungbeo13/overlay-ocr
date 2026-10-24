"""Local LLM runtime: llama.cpp's llama-server (127.0.0.1 only) and the GGUF models it runs.

models: which GGUF to load (config.llm_model, else the recommended one) and where llama-server is.
server: LlamaServer, the windowless server process inside a kill-on-close Job Object.
The translator that talks to it is app.translate.llm_local.LocalLlmTranslator.
"""
