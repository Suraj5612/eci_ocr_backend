"""
ChandraOCR engine — VLM-based OCR using datalab-to/chandra-ocr-2.

Model: datalab-to/chandra-ocr-2 (5B params, BF16)
  - State-of-the-art OCR, 90+ languages including Hindi/Devanagari
  - Outputs Markdown/HTML — compatible with parse_smart()
  - No HF_TOKEN required (Apache 2.0 code license)

Install:
  pip install chandra-ocr[hf]
"""

import threading
from typing import Optional

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor
from chandra.model.hf import generate_hf
from chandra.model.schema import BatchInputItem
from chandra.output import parse_markdown

MODEL_ID = "datalab-to/chandra-ocr-2"

TIMEOUT_GPU = 300   # seconds — 5B model; first inference can be slow on smaller GPUs
TIMEOUT_CPU = 600   # seconds

_model = None
_lock = threading.Lock()       # guards model loading
_infer_lock = threading.Lock() # ensures only one generate_hf runs at a time


def _load():
    global _model

    print(f"📦 Loading ChandraOCR ({MODEL_ID})...")

    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    device = "auto" if torch.cuda.is_available() else "cpu"

    m = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID,
        dtype=dtype,
        device_map=device,
    )
    m.eval()
    m.processor = AutoProcessor.from_pretrained(MODEL_ID)
    m.processor.tokenizer.padding_side = "left"

    _model = m
    print("✅ ChandraOCR loaded")


def warmup():
    """Pre-load model at worker startup to avoid delay on first job."""
    with _lock:
        if _model is None:
            _load()


def _infer(pil_image: Image.Image) -> str:
    batch = [BatchInputItem(image=pil_image, prompt_type="ocr_layout")]
    result = generate_hf(batch, _model)[0]
    return parse_markdown(result.raw)


def run_chandra_ocr(image: np.ndarray) -> str:
    """
    Run ChandraOCR on a preprocessed BGR numpy array.
    Returns Markdown/HTML text compatible with parse_smart().
    """
    with _lock:
        if _model is None:
            _load()

    on_gpu = torch.cuda.is_available()
    print(f"🧠 Calling ChandraOCR (chandra-ocr-2) on {'GPU' if on_gpu else 'CPU'}...")

    pil_image = Image.fromarray(image[:, :, ::-1])  # BGR → RGB

    timeout = TIMEOUT_GPU if on_gpu else TIMEOUT_CPU

    result: list[str] = []
    error: list[Exception] = []

    def _run():
        # _infer_lock prevents a new inference from starting while a previous
        # timed-out thread is still running generate_hf on the GPU.
        with _infer_lock:
            try:
                result.append(_infer(pil_image))
            except Exception as e:
                error.append(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        raise TimeoutError(f"ChandraOCR inference exceeded {timeout}s timeout")
    if error:
        raise error[0]

    ocr_text = result[0] if result else ""
    print(f"✅ ChandraOCR complete — {len(ocr_text)} chars")
    return ocr_text
