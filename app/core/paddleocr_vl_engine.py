"""
PaddleOCR-VL engine — VLM-based OCR using PaddlePaddle/PaddleOCR-VL.

Unlike the classic PaddleOCR (paddleocr_engine.py), this is a 0.9B
vision-language model loaded via HuggingFace transformers. It understands
document structure natively and outputs HTML/Markdown, making it a
direct drop-in replacement for Sarvam.

Model: PaddlePaddle/PaddleOCR-VL (Qwen2.5-VL based, 0.9B params)
  - 109 languages including Hindi/Devanagari
  - olmOCR score: 80.0 (vs Sarvam's proprietary baseline)
  - 2.20 pages/sec on H100

Install:
  pip install transformers torch qwen-vl-utils
  # GPU strongly recommended; CPU inference is very slow for a 0.9B VLM

Hardware:
  - CUDA GPU (even a 4GB VRAM GPU works for 0.9B at fp16)
  - Render Standard (2GB RAM) is borderline — use at least Render Pro for
    reliable CPU-only inference
"""

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor

try:
    from qwen_vl_utils import process_vision_info
    _HAS_QWEN_UTILS = True
except ImportError:
    _HAS_QWEN_UTILS = False

MODEL_ID = "PaddlePaddle/PaddleOCR-VL"

_model = None
_processor = None


def _load():
    global _model, _processor
    if _model is not None:
        return _model, _processor

    print(f"📦 Loading {MODEL_ID} (first run downloads ~1.8GB to ~/.cache/huggingface/) ...")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    _model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        torch_dtype=dtype,
        trust_remote_code=True,
    ).to(device)
    _model.eval()

    _processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)

    print(f"✅ {MODEL_ID} loaded on {device} ({dtype})")
    return _model, _processor


def warmup():
    """Pre-load model weights at worker startup to avoid first-job delay."""
    _load()


def run_paddleocr_vl(image: np.ndarray) -> str:
    """
    Run PaddleOCR-VL on a preprocessed BGR numpy array (from OpenCV).
    Returns raw model output — HTML or Markdown depending on the prompt.

    The worker saves this as raw_text. Plug parse_smart() back in once
    you've confirmed the output format matches Sarvam's HTML structure.
    """
    print("🔍 Starting PaddleOCR-VL inference...")

    model, processor = _load()
    device = next(model.parameters()).device

    # OpenCV is BGR — convert to RGB PIL Image
    rgb_array = image[:, :, ::-1].astype(np.uint8)
    pil_image = Image.fromarray(rgb_array)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": pil_image},
                {
                    "type": "text",
                    "text": (
                        "This is an Indian voter (EPIC) card. "
                        "Extract all text exactly as printed, preserving the table structure as HTML. "
                        "Include all Hindi and English text."
                    ),
                },
            ],
        }
    ]

    text_prompt = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )

    if _HAS_QWEN_UTILS:
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = processor(
            text=[text_prompt],
            images=image_inputs,
            videos=video_inputs,
            return_tensors="pt",
        ).to(device)
    else:
        # Fallback — works for most Qwen2.5-VL checkpoints without qwen-vl-utils
        inputs = processor(
            text=[text_prompt],
            images=[pil_image],
            return_tensors="pt",
        ).to(device)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=1024,
            do_sample=False,
        )

    # Slice off the prompt tokens; decode only the generated part
    generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
    result = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()

    print(f"✅ PaddleOCR-VL complete — {len(result)} chars")
    return result
