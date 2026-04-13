"""
HTML-aware voter card OCR parser.

Built from analysis of 18 real Sarvam OCR outputs for Indian EPIC (voter) cards.
Understands the two-column HTML table structure Sarvam produces:

  Left header cell:   निर्वाचक का नाम / ईपीआईसी / पता
  Right header cell:  कण संख्या / भाग संख्या एवं नाम / विधानसभा / राज्य
  Body label rows:    <td>label</td> <td>value</td>  (mobile, district, etc.)

Output shape is identical to the Claude parser:
  {"name": {"value": "...", "confidence": 0.95}, ...}
"""

import re
from html.parser import HTMLParser

# OCR variant keywords — क्रम is commonly corrupted to कण or कम by Sarvam
_SERIAL_KEYWORDS = ("कण संख्या", "क्रम संख्या", "कम संख्या")
# विधानसभा is commonly corrupted to निधानसभा by Sarvam
_CONSTITUENCY_KEYWORDS = ("विधानसभा", "निधानसभा", "निर्वाचन क्षेत्र का नाम")


# ---------------------------------------------------------------------------
# HTML → cell list
# ---------------------------------------------------------------------------

class _CellExtractor(HTMLParser):
    """Walks the HTML and collects each <td> and <th> as a string, with <br/> → \\n."""

    _CELL_TAGS = {"td", "th"}

    def __init__(self):
        super().__init__()
        self.cells: list[str] = []
        self._buf: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self._CELL_TAGS:
            self._buf = []
        elif tag == "br" and self._buf is not None:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in self._CELL_TAGS and self._buf is not None:
            self.cells.append("".join(self._buf))
            self._buf = None

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)


def _cells(html: str) -> list[str]:
    p = _CellExtractor()
    p.feed(html)
    return p.cells


# ---------------------------------------------------------------------------
# EPIC validation
# ---------------------------------------------------------------------------

_EPIC_PATTERNS = [
    r"^[A-Z]{3}\d{7}$",             # XGF2057644
    r"^[A-Z]{2}\d{8}$",             # AB12345678
    r"^[A-Z]\d{8}$",                # D06440929
    r"^[A-Z]{2}/\d{7}$",            # XG/0739631
    r"^[A-Z]{2}/\d+/\d+/\d{6,8}$", # UP/20/102/0732650
]


def _valid_epic(s: str) -> bool:
    return any(re.match(p, s) for p in _EPIC_PATTERNS)


def _normalise_epic(raw: str) -> str | None:
    """Strip spaces, normalise OCR period-for-slash, validate."""
    s = re.sub(r"\s+", "", raw).upper()
    s = s.replace(".", "/")
    s = s.rstrip("/.-")
    return s if _valid_epic(s) else None


# ---------------------------------------------------------------------------
# Field extractors — header-cell (multi-field, \\n-separated)
# ---------------------------------------------------------------------------

def _name_from_cell(cell: str) -> str | None:
    # Require at least one non-whitespace, non-colon character after the label
    m = re.search(r"निर्वाचक\s*का\s*नाम\s*:\s*([^:\n][^\n]*)", cell)
    if not m:
        return None
    v = re.sub(r"\s+", " ", m.group(1)).strip()
    if not v or v == ":":
        return None
    # Guard: if the value looks like a sentence (>5 words) it's OCR bleed
    words = v.split()
    return " ".join(words[:4]) if len(words) > 4 else v


def _epic_from_cell(cell: str) -> str | None:
    """
    Only matches the voter's own EPIC label (ईपीआईसी:).
    Skips cells that contain 'यदि उपलब्ध हो' (relative EPIC fields).

    Labelled values are trusted even when format validation fails (e.g. OCR
    reads letters as digits: 'OO84713903' → '0084713903').
    Bare token scan uses strict format validation.
    """
    if "यदि उपलब्ध हो" in cell:
        return None

    # Labelled match — trust the label; apply only light cleanup (no format gate)
    m = re.search(r"ईपीआईसी\s*:\s*([A-Z0-9][A-Z0-9/\.\- ]+)", cell, re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        # Remove OCR-inserted spaces, normalise dot→slash
        raw = re.sub(r"\s+", "", raw).upper().replace(".", "/").rstrip("/.-")
        if len(raw) >= 6:          # sanity: at least 6 chars
            return raw

    # Bare token scan — strict format validation (no label to anchor on)
    for token in re.findall(r"\b([A-Z]{1,3}[0-9/\.]{6,}[0-9])\b", cell):
        result = _normalise_epic(token)
        if result:
            return result

    return None


def _address_from_cell(cell: str) -> str | None:
    """
    Address starts after पता: and runs until the next field label or end of cell.
    Multi-line continuation lines (no label) are included.
    """
    m = re.search(
        r"पता\s*:\s*(.+?)(?=\nनिर्वाचक|\nईपीआईसी|\nकण|\nक्रम|\nभाग|\nविधानसभा|\nराज्य|$)",
        cell, re.DOTALL,
    )
    if not m:
        return None
    v = re.sub(r"\n+", " ", m.group(1))
    v = re.sub(r"\s+", " ", v).strip()
    return v if len(v) > 5 else None


def _serial_from_cell(cell: str) -> str | None:
    # कण संख्या / क्रम संख्या / कम संख्या (OCR variants of क्रम)
    m = re.search(r"(?:कण|क्रम|कम)\s*संख्या\s*:?\s*(\d+)", cell)
    return m.group(1) if m else None


def _part_from_cell(cell: str) -> str | None:
    """
    Handles correct label (एवं नाम) and OCR error variant (एवं भाग).
    Value runs until the next field label or end of cell.
    """
    m = re.search(
        r"भाग\s*संख्या\s*(?:एवं\s*(?:नाम|भाग)\s*)?:?\s*(.+?)(?=\nविधानसभा|\nराज्य|\nकण|\nक्रम|$)",
        cell, re.DOTALL,
    )
    if not m:
        return None
    v = re.sub(r"\n+", " ", m.group(1))
    v = re.sub(r"\s+", " ", v).strip()
    return v if v else None


def _constituency_from_cell(cell: str) -> str | None:
    # Capture everything from the label up to the state label (or end of cell).
    # निधानसभा is a common OCR corruption of विधानसभा.
    m = re.search(
        r"(?:विधानसभा|निधानसभा)\s*/?\s*संसदीय\s*निर्वाचन\s*क्षेत्र\s*(?:का\s*नाम)?\s*:?\s*"
        r"(.+?)(?=\nराज्य\s*(?:का\s*नाम)?|$)",
        cell, re.DOTALL,
    )
    if not m:
        return None
    # Join continuation lines (handles "लखनऊ\nमध्य", "लखनऊ\nउत्तर", etc.)
    v = re.sub(r"\n+", " ", m.group(1))
    v = re.sub(r"\s+", " ", v).strip()
    return v if v else None


def _state_from_cell(cell: str) -> str | None:
    m = re.search(r"राज्य\s*(?:का\s*नाम)?\s*:?\s*([^\n<]+)", cell)
    if not m:
        return None
    return _normalise_state(m.group(1).strip())


def _normalise_state(v: str) -> str | None:
    abbrevs = {"UP", "U.P.", "U.P", "उ0. पु0", "उ0.पु0", "UTTAR PRADESH"}
    if v.upper().strip().rstrip(".") in {a.upper().rstrip(".") for a in abbrevs}:
        return "उत्तर प्रदेश"
    # Keep only Hindi script + spaces
    cleaned = re.sub(r"[^ऀ-ॿA-Za-z\s]", "", v).strip()
    return cleaned if cleaned else None


# ---------------------------------------------------------------------------
# Field extractors — label→value adjacent cell pairs
# ---------------------------------------------------------------------------

def _mobile_from_pair(label: str, value: str) -> str | None:
    if not re.search(r"मोबाइल\s*नंबर", label):
        return None
    # Try direct match first (clean number)
    m = re.search(r"\b([6-9]\d{9})\b", value)
    if m:
        return m.group(1)
    # Strip OCR-inserted spaces and retry (e.g. "6137 5652 1980" → "613756521980")
    digits_only = re.sub(r"\s+", "", value)
    m = re.search(r"([6-9]\d{9})", digits_only)
    # Only return if exactly 10 digits matched (not a longer OCR artifact)
    if m and len(digits_only) == 10:
        return m.group(1)
    return None


def _district_from_pair(label: str, value: str) -> str | None:
    # जिला: or जिल्हा: (OCR variant)
    if re.search(r"^जिल", label.strip()):
        v = value.strip()
        return v if v and v not in ("—", "–", "-", "") else None
    return None


def _state_from_pair(label: str, value: str) -> str | None:
    if re.search(r"राज्य\s*(?:का\s*नाम)?", label):
        v = value.strip()
        return _normalise_state(v) if v and v not in ("—", "–") else None
    return None


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _score(value, *, format_valid=False, label_match=False, clean=False, db_match=False) -> float:
    if not value:
        return 0.0
    s = 0.4
    if format_valid: s += 0.2
    if label_match:  s += 0.2
    if clean:        s += 0.15
    if db_match:     s += 0.15
    return round(min(s, 0.99), 2)


def _fmt(value, confidence: float) -> dict:
    return {"value": value or None, "confidence": round(confidence, 2) if value else 0.0}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_smart(raw_html: str) -> dict:
    """
    Parse raw Sarvam OCR HTML string into structured voter-card fields.

    Returns a dict with the same shape as the Claude parser output:
      {"name": {"value": "...", "confidence": 0.95}, ...}
    """
    fields = {
        "name": None, "epic": None, "mobile": None,
        "serial_number": None, "part_number_and_name": None,
        "assembly_constituency": None, "district": None,
        "state": None, "address": None,
    }

    try:
        cells = _cells(raw_html)
    except Exception as e:
        print(f"❌ smart_parser: HTML parse error: {e}")
        cells = []

    # ── Pass 1: header cells (multi-field, \\n-delimited) ─────────────────
    for cell in cells:
        if "निर्वाचक का नाम" in cell:
            fields["name"]    = fields["name"]    or _name_from_cell(cell)
            fields["epic"]    = fields["epic"]    or _epic_from_cell(cell)
            fields["address"] = fields["address"] or _address_from_cell(cell)

        if any(kw in cell for kw in _SERIAL_KEYWORDS):
            fields["serial_number"] = fields["serial_number"] or _serial_from_cell(cell)

        if "भाग संख्या" in cell:
            fields["part_number_and_name"] = fields["part_number_and_name"] or _part_from_cell(cell)

        if any(kw in cell for kw in _CONSTITUENCY_KEYWORDS):
            fields["assembly_constituency"] = fields["assembly_constituency"] or _constituency_from_cell(cell)

        if "राज्य" in cell:
            fields["state"] = fields["state"] or _state_from_cell(cell)

    # ── Pass 2: adjacent label→value cell pairs ────────────────────────────
    for i in range(len(cells) - 1):
        label, value = cells[i], cells[i + 1]

        if fields["mobile"] is None:
            fields["mobile"] = _mobile_from_pair(label, value)

        if fields["district"] is None:
            fields["district"] = _district_from_pair(label, value)

        if fields["state"] is None:
            fields["state"] = _state_from_pair(label, value)

    # ── Build output ───────────────────────────────────────────────────────
    epic = fields["epic"]
    name = fields["name"]
    mobile = fields["mobile"]

    return {
        "name": _fmt(name, _score(
            name,
            label_match=True,
            clean=bool(name and len(name.split()) <= 4),
        )),
        "epic": _fmt(epic, _score(
            epic,
            format_valid=_valid_epic(epic or ""),
            label_match=True,   # label was present regardless of format
        )),
        "mobile": _fmt(mobile, _score(
            mobile,
            format_valid=bool(mobile and len(mobile) == 10 and mobile[0] in "6789"),
            label_match=True,
        )),
        "serial_number": _fmt(
            fields["serial_number"],
            0.97 if fields["serial_number"] else 0.0,
        ),
        "part_number_and_name": _fmt(
            fields["part_number_and_name"],
            _score(fields["part_number_and_name"], label_match=True, clean=True),
        ),
        "assembly_constituency": _fmt(
            fields["assembly_constituency"],
            _score(fields["assembly_constituency"], label_match=True),
        ),
        "district": _fmt(
            fields["district"],
            _score(fields["district"], label_match=True, clean=True),
        ),
        "state": _fmt(
            fields["state"],
            0.99 if fields["state"] == "उत्तर प्रदेश" else _score(fields["state"], label_match=True),
        ),
        "address": _fmt(
            fields["address"],
            _score(
                fields["address"],
                label_match=True,
                clean=bool(fields["address"] and len(fields["address"]) > 20),
            ),
        ),
    }
