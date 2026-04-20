"""
PaddleOCR-VL 1.5 engine — VLM-based OCR using PaddlePaddle/PaddleOCR-VL-1.5.

Model: PaddlePaddle/PaddleOCR-VL-1.5 (ERNIE-4.5-0.3B-Paddle, 0.9B params)
  - Native transformers 5.x support — no trust_remote_code, no patches needed
  - 109 languages including Hindi/Devanagari
  - ~2 GB VRAM in bfloat16
  - Output: raw OCR text (not HTML table)

Install:
  pip install transformers>=5.0.0 torch Pillow accelerate
"""

import threading

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL_ID = "PaddlePaddle/PaddleOCR-VL-1.5"

TIMEOUT_GPU = 120   # seconds
TIMEOUT_CPU = 300   # seconds

_PROMPT = "OCR:"

_model = None
_processor = None
_lock = threading.Lock()


def _load():
    global _model, _processor

    print(f"📦 Loading {MODEL_ID} ...")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

    _model = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID,
        torch_dtype=dtype,
    ).to(device).eval()

    _processor = AutoProcessor.from_pretrained(MODEL_ID)

    print(f"✅ {MODEL_ID} loaded on {device} ({dtype})")


def warmup():
    """Pre-load model weights at worker startup to avoid first-job delay."""
    with _lock:
        if _model is None:
            _load()


def run_paddleocr_vl(image: np.ndarray) -> str:
    """
    Run PaddleOCR-VL-1.5 on a preprocessed BGR numpy array (from OpenCV).
    Returns raw OCR text.
    """
    with _lock:
        if _model is None:
            _load()

    print("🔍 Starting PaddleOCR-VL-1.5 inference...")

    pil_image = Image.fromarray(image[:, :, ::-1].astype(np.uint8))  # BGR → RGB

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": pil_image},
                {"type": "text", "text": _PROMPT},
            ],
        }
    ]

    inputs = _processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    ).to(_model.device)

    timeout = TIMEOUT_GPU if torch.cuda.is_available() else TIMEOUT_CPU

    result: list[str] = []
    error: list[Exception] = []

    def _generate():
        try:
            with torch.no_grad():
                outputs = _model.generate(**inputs, max_new_tokens=512)
            decoded = _processor.decode(outputs[0][inputs["input_ids"].shape[-1]:-1])
            result.append(decoded.strip())
        except Exception as e:
            error.append(e)

    t = threading.Thread(target=_generate, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        raise TimeoutError(f"PaddleOCR-VL-1.5 inference exceeded {timeout}s timeout")
    if error:
        raise error[0]

    text = result[0] if result else ""
    print(f"✅ PaddleOCR-VL-1.5 complete — {len(text)} chars")
    return text
