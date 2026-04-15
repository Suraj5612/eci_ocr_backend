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
  pip install transformers torch torchvision qwen-vl-utils accelerate safetensors einops
  # GPU strongly recommended; CPU inference is very slow for a 0.9B VLM

Hardware:
  - Any CUDA GPU with ≥4GB VRAM
  - dtype is auto-selected: bfloat16 if supported by the GPU, float16 otherwise, float32 on CPU
  - CPU-only inference works but is very slow (~300s timeout)

Compatibility patches applied at runtime (do NOT rely on cache file edits):
  transformers 5.x removed 'default' and 'mrope' from ROPE_INIT_FUNCTIONS and
  now expects RotaryEmbedding modules to have compute_default_rope_parameters().
  It also no longer passes cache_position to remote-code models in generate().
  All three issues are patched here so they apply on fresh deployments.
"""

import glob
import os
import threading
import types

import numpy as np
import torch
from PIL import Image
from transformers import AutoConfig, AutoModelForCausalLM, AutoProcessor
from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS


# ---------------------------------------------------------------------------
# Patch 1: restore 'default' and 'mrope' in ROPE_INIT_FUNCTIONS
#   transformers 5.x removed these keys. The custom modeling code does
#   ROPE_INIT_FUNCTIONS[self.rope_type] which KeyErrors for 'default'/'mrope'.
# ---------------------------------------------------------------------------

def _rope_default_init(config, device=None, **kwargs):
    """Standard RoPE (no scaling) — serves as the 'default' and 'mrope' init."""
    base = getattr(config, "rope_theta", 10000.0)
    partial_rotary_factor = getattr(config, "partial_rotary_factor", 1.0)
    head_dim = getattr(config, "head_dim", config.hidden_size // config.num_attention_heads)
    dim = int(head_dim * partial_rotary_factor)
    inv_freq = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.int64).float() / dim))
    return inv_freq, 1.0  # (inv_freq, attention_scaling)


for _key in ("default", "mrope"):
    if _key not in ROPE_INIT_FUNCTIONS:
        ROPE_INIT_FUNCTIONS[_key] = _rope_default_init


# ---------------------------------------------------------------------------
# Patch 2: PreTrainedModel._init_weights — handle missing
#   compute_default_rope_parameters on custom RotaryEmbedding classes.
#   transformers 5.x _init_weights special-cases rope_type='default' to call
#   module.compute_default_rope_parameters() instead of the dict. Custom models
#   from the hub don't have this method. We add it on-the-fly.
# ---------------------------------------------------------------------------

from transformers import modeling_utils as _mu

_original_init_weights = _mu.PreTrainedModel._init_weights


def _patched_init_weights(self, module):
    if (
        "RotaryEmbedding" in type(module).__name__
        and hasattr(module, "original_inv_freq")
        and getattr(module, "rope_type", None) == "default"
        and not hasattr(module, "compute_default_rope_parameters")
    ):
        # Inject the method so _init_weights can call it
        _cfg = getattr(module, "config", None)
        module.compute_default_rope_parameters = lambda config=None, **kw: _rope_default_init(
            config if config is not None else _cfg
        )
    _original_init_weights(self, module)


_mu.PreTrainedModel._init_weights = _patched_init_weights


# ---------------------------------------------------------------------------
# Patch 3: prepare_inputs_for_generation — handle cache_position=None.
#   transformers 5.5.x no longer creates/passes cache_position for remote-code
#   models in generate(). The custom model does cache_position[0] which crashes
#   with TypeError when cache_position is None.
#   Applied to the model instance after loading (see _load()).
# ---------------------------------------------------------------------------

def _patch_prepare_inputs(model):
    original_fn = model.__class__.prepare_inputs_for_generation

    def safe_prepare(self, input_ids, cache_position=None, **kwargs):
        if cache_position is None:
            # Determine prefill vs decode from past_key_values so we set the
            # correct cache_position. Getting this wrong causes pixel_values to
            # be passed on every decode step (re-processing the image each token
            # → GPU saturated but generation ~100× slower than it should be).
            past_kv = kwargs.get("past_key_values", None)
            past_len = 0
            if past_kv is not None:
                if hasattr(past_kv, "get_seq_length"):
                    past_len = past_kv.get_seq_length()
                elif isinstance(past_kv, (list, tuple)) and len(past_kv) > 0:
                    try:
                        past_len = past_kv[0][0].shape[2]
                    except Exception:
                        past_len = 0

            cache_position = torch.arange(
                past_len,
                past_len + input_ids.shape[1],
                device=input_ids.device,
            )
        return original_fn(self, input_ids, cache_position=cache_position, **kwargs)

    model.prepare_inputs_for_generation = types.MethodType(safe_prepare, model)


# ---------------------------------------------------------------------------

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
    if device == "cuda":
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    else:
        dtype = torch.float32

    # Use from_config + manual safetensors loading to bypass accelerate's
    # meta-device init. from_pretrained with accelerate installed always uses
    # init_empty_weights() which leaves non-persistent buffers (inv_freq,
    # position_ids) as meta tensors — they are not in the checkpoint so they
    # are never filled, and .to(device) fails with "Cannot copy out of meta".
    from huggingface_hub import snapshot_download
    from safetensors.torch import load_file as st_load

    snapshot_dir = snapshot_download(MODEL_ID)
    config = AutoConfig.from_pretrained(snapshot_dir, trust_remote_code=True)

    # from_config calls __init__ directly (no accelerate meta init) so all
    # buffers are real CPU tensors. Patch 2 above fires during _init_weights.
    _model = AutoModelForCausalLM.from_config(config, trust_remote_code=True)

    # Load safetensors weights. strict=False: non-persistent buffers (inv_freq
    # etc.) are not in the checkpoint — they were already initialised in __init__.
    shard_files = sorted(glob.glob(os.path.join(snapshot_dir, "*.safetensors")))
    if not shard_files:
        raise RuntimeError(f"No .safetensors files found in {snapshot_dir}")
    state_dict = {}
    for shard in shard_files:
        state_dict.update(st_load(shard, device="cpu"))
    missing, unexpected = _model.load_state_dict(state_dict, strict=False)
    if unexpected:
        print(f"⚠️  Unexpected checkpoint keys: {unexpected[:3]}")

    # Apply Patch 3 — must be done before any generate() call
    _patch_prepare_inputs(_model)

    _model = _model.to(dtype).to(device)
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
        proc_kwargs = dict(text=[text_prompt], images=image_inputs, return_tensors="pt")
        if video_inputs is not None:
            proc_kwargs["videos"] = video_inputs
        inputs = processor(**proc_kwargs).to(device)
    else:
        inputs = processor(
            text=[text_prompt],
            images=[pil_image],
            return_tensors="pt",
        ).to(device)

    # CPU inference is very slow (~0.1 tok/s); GPU varies by card.
    # T4 (g4dn) is slower than A10G/A100 — 120s gives headroom on any CUDA GPU.
    # Run generate() in a thread so we can enforce a wall-clock timeout.
    TIMEOUT_SECONDS = 120 if torch.cuda.is_available() else 300
    MAX_NEW_TOKENS = 512   # EPIC card HTML is short; no need for 1024

    output_container: list = []
    error_container: list = []

    def _generate():
        try:
            with torch.no_grad():
                out = model.generate(
                    **inputs,
                    max_new_tokens=MAX_NEW_TOKENS,
                    do_sample=False,
                )
            output_container.append(out)
        except Exception as exc:
            error_container.append(exc)

    gen_thread = threading.Thread(target=_generate, daemon=True)
    gen_thread.start()
    gen_thread.join(timeout=TIMEOUT_SECONDS)

    if gen_thread.is_alive():
        raise TimeoutError(
            f"PaddleOCR-VL generate() did not finish within {TIMEOUT_SECONDS}s — "
            f"{'CPU inference is very slow for a 0.9B model; use a GPU' if not torch.cuda.is_available() else 'GPU took too long; check VRAM and model dtype'}"
        )
    if error_container:
        raise error_container[0]

    output_ids = output_container[0]

    # Slice off the prompt tokens; decode only the generated part
    generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
    result = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()

    print(f"✅ PaddleOCR-VL complete — {len(result)} chars")
    return result
