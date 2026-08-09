"""
Local LLM wrapper (Phase 3) — single-shot suggestion generation via
llama.cpp (llama-cpp-python). No completion mode, no streaming tokens:
one question in, one answer out. Streaming/mid-sentence prediction is the
gated Stretch phase in the build plan and depends on Phase 0's latency
numbers, not on this module.

Like stt/engine.py and audio/vad.py, the import is deferred into
get_llm() so importing this module doesn't require llama-cpp-python (or a
GGUF file on disk) just to run `manage.py check`, admin, REST endpoints,
etc.

Deliberately NOT shipping a hardcoded model choice. The build plan flags
"which local LLM size to benchmark first" as an open decision that depends
on your server's actual RAM/GPU — point LLM_MODEL_PATH at whatever GGUF
you want to try first (see .env.example for the full knob list); nothing
else in this module needs to change to swap models or quantizations.
"""

import logging
import os
import threading

from ai.prompts import SUGGESTION_SYSTEM_PROMPT, build_suggestion_prompt

logger = logging.getLogger(__name__)

_llm = None
_llm_lock = threading.Lock()


class LocalLLM:
    def __init__(self, model_path: str):
        from llama_cpp import Llama  # deferred import, see module docstring

        if not model_path or not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"LLM_MODEL_PATH ({model_path!r}) does not point at an existing "
                "file. Download a GGUF model and set LLM_MODEL_PATH in .env — "
                "see .env.example for the rest of the LLM_* knobs."
            )

        self.model_name = os.path.basename(model_path)
        self.llm = Llama(
            model_path=model_path,
            n_ctx=int(os.environ.get("LLM_N_CTX", "4096")),
            # 0 = CPU only. Set > 0 once you've benchmarked GPU offload on
            # your actual hardware — see the build plan's open decision.
            n_gpu_layers=int(os.environ.get("LLM_N_GPU_LAYERS", "0")),
            n_threads=int(os.environ.get("LLM_N_THREADS", str(os.cpu_count() or 4))),
            verbose=False,
        )

    def suggest_answer(self, question: str, context_turns: list[dict]) -> dict:
        """
        question: extracted question text to answer.
        context_turns: recent [{"speaker": ..., "text": ...}] turns, oldest
        first — see ai/prompts.py for how these are used.
        Returns {"text": str, "model": str}.
        """
        user_prompt = build_suggestion_prompt(question, context_turns)
        max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "200"))
        temperature = float(os.environ.get("LLM_TEMPERATURE", "0.4"))

        response = self.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": SUGGESTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = response["choices"][0]["message"]["content"].strip()
        return {"text": text, "model": self.model_name}


def get_llm() -> LocalLLM:
    """
    Thread-safe lazy singleton, same shape as stt.engine.get_engine() and
    audio.vad.get_vad(). Raises FileNotFoundError (via LocalLLM.__init__)
    if LLM_MODEL_PATH isn't configured — callers (audio/consumers.py) are
    expected to catch that, since a missing local model shouldn't take the
    rest of the session pipeline down; it just means no suggestions for
    now.
    """
    global _llm
    if _llm is None:
        with _llm_lock:
            if _llm is None:
                _llm = LocalLLM(os.environ.get("LLM_MODEL_PATH", ""))
    return _llm
