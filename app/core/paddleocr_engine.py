"""
PaddleOCR engine for Hindi voter card images.

Expects a preprocessed image (BGR numpy array) from the worker.
Returns plain text consumed by parse_ocr_text() in core/parser.py.

PaddleOCR advantages for Hindi:
  - PP-OCRv4 model: faster and more accurate than v3 for Devanagari
  - Built-in text direction detection (handles slightly rotated cards)
  - Significantly faster than EasyOCR on CPU (no heavy CRNN inference loop)
  - Hindi model covers Devanagari script natively

Install:
  pip install paddleocr paddlepaddle

Models are downloaded once to ~/.paddleocr/ and cached — no re-download on restart.
"""

import numpy as np
from paddleocr import PaddleOCR

# ---------------------------------------------------------------------------
# Singleton reader — models are downloaded on first use and cached at
# ~/.paddleocr/. Subsequent starts load from disk (~2-5s, much faster
# than EasyOCR). Initialised at worker startup via warmup().
#
# use_angle_cls=True  — detects and corrects text orientation (useful for
#                       voter cards photographed at a slight angle)
# lang='hi'           — loads Hindi (Devanagari) recognition model;
#                       PaddleOCR automatically handles embedded English
#                       (EPIC numbers, labels) with this setting
# ---------------------------------------------------------------------------
_ocr: PaddleOCR | None = None


def _get_ocr() -> PaddleOCR:
    global _ocr
    if _ocr is None:
        print("📦 Loading PaddleOCR models (downloaded once, cached at ~/.paddleocr/)...")
        # Explicit mobile models — avoids the PP-OCRv5 server det (~500MB+)
        # that OOM-kills the process on macOS Apple Silicon.
        #
        # PP-OCRv4_mobile_det          — lightweight detector (~4MB), works for all scripts
        # devanagari_PP-OCRv5_mobile_rec — best available Hindi/Devanagari recogniser, still mobile
        #
        # lang/ocr_version are ignored when model names are set explicitly (PaddleOCR 3.x behaviour).
        _ocr = PaddleOCR(
            text_detection_model_name="PP-OCRv4_mobile_det",
            text_recognition_model_name="devanagari_PP-OCRv5_mobile_rec",
            use_textline_orientation=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )
        print("✅ PaddleOCR models loaded")
    return _ocr


def warmup():
    """
    Pre-load PaddleOCR models into memory at worker startup.
    Prevents the first OCR job from incurring the model load delay.
    """
    _get_ocr()


# ---------------------------------------------------------------------------
# PaddleOCR runner
# ---------------------------------------------------------------------------

def run_paddleocr(image: np.ndarray) -> str:
    """
    Run PaddleOCR on a preprocessed BGR numpy array.
    Returns plain UTF-8 text, one detected line per newline.
    Results are sorted top-to-bottom so the regex parser sees labels
    before their values.

    PaddleOCR v3 uses predict() and returns a list of result dicts:
      rec_texts  — recognised text strings
      rec_scores — confidence per string
      rec_polys  — bounding polygons [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    """
    print("🔍 Starting PaddleOCR...")

    ocr = _get_ocr()
    raw_results = ocr.predict(image)

    if not raw_results:
        print("⚠️ PaddleOCR returned no results")
        return ""

    # Each element in raw_results corresponds to one page/image.
    page = raw_results[0]
    texts  = page.get("rec_texts", [])
    scores = page.get("rec_scores", [])
    polys  = page.get("rec_polys", [])

    if not texts:
        print("⚠️ PaddleOCR: no text detected")
        return ""

    # Sort top-to-bottom by the minimum y-coordinate of each polygon
    entries = list(zip(polys, texts, scores))
    entries_sorted = sorted(entries, key=lambda e: min(pt[1] for pt in e[0]))

    text_lines = []
    for (_poly, text, _score) in entries_sorted:
        text = text.strip()
        if text:
            text_lines.append(text)

    result = "\n".join(text_lines).strip()

    print(f"✅ PaddleOCR complete — {len(texts)} regions, {len(result)} chars")
    return result
