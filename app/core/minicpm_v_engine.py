"""
MiniCPM-V engine — VLM-based OCR using openbmb/MiniCPM-V-2_6.

Model: openbmb/MiniCPM-V-2_6 (Qwen2 LLM + SigLIP vision encoder, 8B params)
  - Strong multilingual OCR including Hindi/Devanagari
  - Native document/card structure understanding
  - Outputs structured HTML — compatible with parse_smart()

Install:
  pip install transformers torch torchvision pillow sentencepiece timm
  pip install bitsandbytes          # required for 4-bit NF4 on <22 GB VRAM GPUs (T4 16 GB, RTX 4060 8 GB, etc.)
  # CUDA build (RTX 4060 / cu128):
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

Hardware:
  - CUDA GPU ≥8 GB VRAM: auto-selected 4-bit NF4 quantization (~4.5 GB active)
  - CUDA GPU ≥22 GB VRAM (e.g. A10G 24 GB): loaded in bf16/fp16 (~16 GB), no bitsandbytes needed
  - T4 (16 GB) and V100-16 GB: use 4-bit NF4 (fp16 weights alone fill the 16 GB, no room for activations)
  - CPU: fp32, inference is extremely slow (~300 s/image)
"""

import os
import threading
from typing import Optional

import numpy as np
import torch
from PIL import Image

MODEL_ID = "openbmb/MiniCPM-V-2_6"

# Inference limits — generous for EPIC card content (typically ~150–300 tokens)
MAX_NEW_TOKENS = 512
TIMEOUT_GPU = 120   # seconds; covers any CUDA GPU including slow T4
TIMEOUT_CPU = 300   # seconds; CPU inference is very slow for an 8B model

# Set True to force 4-bit NF4 regardless of VRAM (useful for local testing)
FORCE_4BIT = True

# ---------------------------------------------------------------------------
# Focused system prompt for EPIC card OCR
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are an OCR system specialising in Indian voter identity cards (EPIC cards). "
    "Your sole task is to extract every printed field exactly as it appears — no translation, "
    "no paraphrasing, no omissions. Preserve the table structure of the card by using HTML "
    "(<table>, <tr>, <th>, <td>). Include all Devanagari (Hindi) script and English text."
)

_USER_PROMPT = (
    "Extract all text from this Indian voter ID (EPIC) card. "
    "Return the output as an HTML table that mirrors the card's layout: "
    "field labels in <th> cells, field values in <td> cells. "
    "Include every field visible on the card."
)

# ---------------------------------------------------------------------------
# Singleton — model + tokenizer loaded once and reused across all jobs
# ---------------------------------------------------------------------------
_model: Optional[object] = None
_tokenizer: Optional[object] = None
_load_lock = threading.Lock()


def _vram_gb() -> float:
    """Total VRAM of the first CUDA device, or 0.0 if no GPU."""
    if not torch.cuda.is_available():
        return 0.0
    return torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)


def _load():
    """Thread-safe lazy load. Returns (model, tokenizer) on every call."""
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    with _load_lock:
        if _model is not None:          # re-check inside the lock
            return _model, _tokenizer

        from transformers import AutoModel, AutoTokenizer

        hf_token = os.getenv("HF_TOKEN")
        if not hf_token:
            raise EnvironmentError(
                "HF_TOKEN is not set. MiniCPM-V-2_6 is a gated model. "
                "1) Accept the license at https://huggingface.co/openbmb/MiniCPM-V-2_6  "
                "2) Create a token at https://huggingface.co/settings/tokens  "
                "3) Add HF_TOKEN=hf_... to your .env file."
            )

        device = "cuda" if torch.cuda.is_available() else "cpu"
        vram = _vram_gb()

        print(f"📦 Loading {MODEL_ID} on {device} ({vram:.1f} GB VRAM) ...")

        load_kwargs: dict = {"trust_remote_code": True, "token": hf_token}
        strategy: str

        if device == "cuda":
            if vram >= 22 and not FORCE_4BIT:
                # High-VRAM path — full bf16/fp16 (A10G 24 GB, A100 40/80 GB, etc.)
                # T4 and V100-16GB have exactly 16 GB — NOT enough for fp16 weights + activations
                dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                load_kwargs["torch_dtype"] = dtype
                strategy = "bf16" if dtype == torch.bfloat16 else "fp16"
            else:
                # <22 GB VRAM (T4 16 GB, RTX 4060 8 GB, etc.) — 4-bit NF4 quantization (~4.5 GB)
                try:
                    from transformers import BitsAndBytesConfig
                    compute_dtype = (
                        torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                    )
                    load_kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=compute_dtype,
                        bnb_4bit_use_double_quant=True,  # saves ~0.4 bits/param extra
                    )
                    strategy = f"4-bit NF4 (double-quant, compute {compute_dtype})"
                except ImportError:
                    # bitsandbytes not installed — warn and try fp16 anyway
                    load_kwargs["torch_dtype"] = torch.float16
                    strategy = (
                        f"fp16 — WARNING: bitsandbytes not installed; "
                        f"~16 GB required, only {vram:.1f} GB available — likely OOM"
                    )
        else:
            load_kwargs["torch_dtype"] = torch.float32
            strategy = "fp32/CPU (very slow)"

        print(f"  ▸ Strategy: {strategy}")

        _model = AutoModel.from_pretrained(MODEL_ID, **load_kwargs)

        # BitsAndBytesConfig manages device placement internally.
        # For non-quantized loads, move to the target device explicitly.
        if "quantization_config" not in load_kwargs and device != "cpu":
            _model = _model.to(device)

        _model.eval()

        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True, token=hf_token)

        if device == "cuda":
            torch.cuda.empty_cache()

        print(f"✅ {MODEL_ID} ready")
        return _model, _tokenizer


def warmup():
    """Pre-load model weights at worker startup to avoid first-job latency."""
    _load()


def run_minicpm_v(image: np.ndarray) -> str:
    """
    Run MiniCPM-V-2_6 on a preprocessed BGR numpy array (OpenCV format).

    Returns the raw model output (HTML/text). Feed to parse_smart() once
    the output format is confirmed to match smart_parser expectations.

    Args:
        image: BGR uint8 array from OpenCV (output of crop_rois / download_image).

    Returns:
        Raw string — HTML table or plain text depending on model compliance.

    Raises:
        TimeoutError: inference did not complete within TIMEOUT_GPU/CPU seconds.
        Any exception propagated from model.chat().
    """
    print("🔍 Starting MiniCPM-V inference...")

    model, tokenizer = _load()

    # OpenCV BGR → RGB PIL Image
    pil_image = Image.fromarray(image[:, :, ::-1].astype(np.uint8))

    msgs = [{"role": "user", "content": [pil_image, _USER_PROMPT]}]

    timeout = TIMEOUT_GPU if torch.cuda.is_available() else TIMEOUT_CPU

    output_container: list = []
    error_container: list = []

    def _generate():
        try:
            with torch.inference_mode():
                result = model.chat(
                    image=None,         # pass images via msgs content, not here
                    msgs=msgs,
                    tokenizer=tokenizer,
                    max_new_tokens=MAX_NEW_TOKENS,
                    sampling=False,     # greedy — deterministic, faster, no temperature
                    system_prompt=_SYSTEM_PROMPT,
                )
            output_container.append(result)
        except TypeError:
            # Older releases don't support system_prompt kwarg — retry without it
            try:
                with torch.inference_mode():
                    result = model.chat(
                        image=None,
                        msgs=msgs,
                        tokenizer=tokenizer,
                        max_new_tokens=MAX_NEW_TOKENS,
                        sampling=False,
                    )
                output_container.append(result)
            except Exception as exc:
                error_container.append(exc)
        except Exception as exc:
            error_container.append(exc)

    gen_thread = threading.Thread(target=_generate, daemon=True)
    gen_thread.start()
    gen_thread.join(timeout=timeout)

    if gen_thread.is_alive():
        hint = (
            "pip install bitsandbytes for 4-bit quantization"
            if not torch.cuda.is_available()
            else "check VRAM usage and model dtype"
        )
        raise TimeoutError(
            f"MiniCPM-V inference did not finish within {timeout}s — {hint}"
        )

    if error_container:
        raise error_container[0]

    result = output_container[0]
    if not isinstance(result, str):
        result = str(result)
    result = result.strip()

    if torch.cuda.is_available():
        torch.cuda.empty_cache()    # release KV-cache and activation memory

    print(f"✅ MiniCPM-V complete — {len(result)} chars")
    return result
