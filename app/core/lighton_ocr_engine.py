"""
LightOnOCR-2 engine — VLM-based OCR using lightonai/LightOnOCR-2-1B.

Model: lightonai/LightOnOCR-2-1B (1B params, bfloat16)
  - Native transformers 5.x support — no trust_remote_code needed
  - 83.2 on OlmOCR-Bench (SOTA for 1B models)
  - ~2.5 GB VRAM in bfloat16
  - Output: plain text

Install:
  pip install transformers>=5.0.0 Pillow torch
"""

import threading

import numpy as np
import torch
from PIL import Image
from transformers import LightOnOcrForConditionalGeneration, LightOnOcrProcessor

MODEL_ID = "lightonai/LightOnOCR-2-1B"

TIMEOUT_GPU = 120   # seconds
TIMEOUT_CPU = 300   # seconds

_model = None
_processor = None
_lock = threading.Lock()


def _load():
    global _model, _processor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32

    print(f"📦 Loading {MODEL_ID} on {device} ({dtype}) ...")

    _model = LightOnOcrForConditionalGeneration.from_pretrained(
        MODEL_ID,
        torch_dtype=dtype,
    ).to(device)
    _model.eval()

    _processor = LightOnOcrProcessor.from_pretrained(MODEL_ID)

    print(f"✅ {MODEL_ID} loaded")


def warmup():
    """Pre-load model weights at worker startup to avoid first-job delay."""
    with _lock:
        if _model is None:
            _load()


def run_lighton_ocr(image: np.ndarray) -> str:
    """
    Run LightOnOCR-2 on a preprocessed BGR numpy array (from OpenCV).
    Returns plain text extracted from the image.
    """
    with _lock:
        if _model is None:
            _load()

    print("🔍 Starting LightOnOCR-2 inference...")

    device = next(_model.parameters()).device
    dtype = next(_model.parameters()).dtype

    pil_image = Image.fromarray(image[:, :, ::-1].astype(np.uint8))  # BGR → RGB

    conversation = [
        {
            "role": "user",
            "content": [{"type": "image", "image": pil_image}],
        }
    ]

    inputs = _processor.apply_chat_template(
        conversation,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    )
    inputs = {
        k: v.to(device=device, dtype=dtype) if v.is_floating_point() else v.to(device)
        for k, v in inputs.items()
    }

    timeout = TIMEOUT_GPU if torch.cuda.is_available() else TIMEOUT_CPU

    result: list[str] = []
    error: list[Exception] = []

    def _generate():
        try:
            with torch.no_grad():
                output_ids = _model.generate(**inputs, max_new_tokens=1024)
            generated_ids = output_ids[0, inputs["input_ids"].shape[1]:]
            text = _processor.decode(generated_ids, skip_special_tokens=True)
            result.append(text.strip())
        except Exception as e:
            error.append(e)

    t = threading.Thread(target=_generate, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        raise TimeoutError(f"LightOnOCR-2 inference exceeded {timeout}s timeout")
    if error:
        raise error[0]

    text = result[0] if result else ""
    print(f"✅ LightOnOCR-2 complete — {len(text)} chars")
    return text
