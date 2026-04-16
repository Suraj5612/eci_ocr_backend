"""
SmolVLM engine — VLM-based OCR using HuggingFaceTB/SmolVLM2-2.2B-Instruct.

Model: HuggingFaceTB/SmolVLM2-2.2B-Instruct (SigLIP vision + SmolLM2 LM, 2.2B params)
  - Not gated — no HF_TOKEN required
  - ~4.5 GB download, ~5 GB VRAM fp16
  - Better multilingual coverage than 500M; still weaker on Hindi/Devanagari than MiniCPM-V

Hardware:
  - CUDA GPU: fp16 (~5 GB VRAM)
  - CPU: fp32 (very slow — ~5–10 min/image)
"""

import threading
from typing import Optional

import numpy as np
import torch
from PIL import Image

MODEL_ID = "HuggingFaceTB/SmolVLM2-2.2B-Instruct"

MAX_NEW_TOKENS = 512
TIMEOUT_GPU = 120   # seconds
TIMEOUT_CPU = 600   # seconds; 2.2B on CPU is very slow

_PROMPT = (
    "Extract all text from this Indian voter ID (EPIC) card. "
    "Return the output as an HTML table that mirrors the card's layout: "
    "field labels in <th> cells, field values in <td> cells. "
    "Include every field visible on the card, including Devanagari (Hindi) and English text."
)

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_model: Optional[object] = None
_processor: Optional[object] = None
_load_lock = threading.Lock()


def _load():
    global _model, _processor
    if _model is not None:
        return _model, _processor

    with _load_lock:
        if _model is not None:
            return _model, _processor

        try:
            from transformers import AutoModelForImageTextToText as _AutoModel  # transformers 5.x
        except ImportError:
            from transformers import AutoModelForVision2Seq as _AutoModel       # transformers 4.x
        from transformers import AutoProcessor

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32

        print(f"📦 Loading {MODEL_ID} on {device} ({dtype}) ...")

        _processor = AutoProcessor.from_pretrained(MODEL_ID)
        _model = _AutoModel.from_pretrained(
            MODEL_ID,
            torch_dtype=dtype,
        ).to(device)
        _model.eval()

        if device == "cuda":
            torch.cuda.empty_cache()

        print(f"✅ {MODEL_ID} ready")
        return _model, _processor


def warmup():
    """Pre-load model weights at worker startup to avoid first-job latency."""
    _load()


def run_smolvlm(image: np.ndarray) -> str:
    """
    Run SmolVLM-500M-Instruct on a preprocessed BGR numpy array (OpenCV format).

    Args:
        image: BGR uint8 array from OpenCV (output of crop_rois / download_image).

    Returns:
        Raw string output from the model.

    Raises:
        TimeoutError: inference did not complete within TIMEOUT_GPU/CPU seconds.
    """
    print("🔍 Starting SmolVLM inference...")

    model, processor = _load()

    pil_image = Image.fromarray(image[:, :, ::-1].astype(np.uint8))

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": _PROMPT},
            ],
        }
    ]

    device = next(model.parameters()).device
    prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
    inputs = processor(text=prompt, images=[pil_image], return_tensors="pt").to(device)

    timeout = TIMEOUT_GPU if torch.cuda.is_available() else TIMEOUT_CPU

    output_container: list = []
    error_container: list = []

    def _generate():
        try:
            with torch.inference_mode():
                generated_ids = model.generate(
                    **inputs,
                    max_new_tokens=MAX_NEW_TOKENS,
                    do_sample=False,    # greedy
                )
            # Decode only the newly generated tokens
            new_tokens = generated_ids[:, inputs["input_ids"].shape[1]:]
            result = processor.decode(new_tokens[0], skip_special_tokens=True)
            output_container.append(result.strip())
        except Exception as exc:
            error_container.append(exc)

    gen_thread = threading.Thread(target=_generate, daemon=True)
    gen_thread.start()
    gen_thread.join(timeout=timeout)

    if gen_thread.is_alive():
        raise TimeoutError(
            f"SmolVLM inference did not finish within {timeout}s"
        )

    if error_container:
        raise error_container[0]

    result = output_container[0]

    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    print(f"✅ SmolVLM complete — {len(result)} chars")
    return result
