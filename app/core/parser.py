import re
from rapidfuzz import fuzz
from app.models.constituency import Constituency
from app.models.districts import District


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def clean_text(text):
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<.*?>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)   # collapse horizontal whitespace only
    return text.strip()


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def calculate_confidence(value, rules: dict):
    if not value:
        return 0.0
    score = 0.4
    if rules.get("format_valid"):
        score += 0.2
    if rules.get("regex_match"):
        score += 0.15
    if rules.get("db_match"):
        score += 0.15
    if rules.get("clean_text"):
        score += 0.1
    return round(min(score, 0.99), 2)


def score_epic(epic, raw_text):
    format_valid = bool(re.match(
        r"^([A-Z]{3}\d{7}|[A-Z]{2}\d{8}|[A-Z]\d{8}|[A-Z]{2}/\d{7}|[A-Z]{2}/\d+/\d+/\d+)$",
        epic or ""
    ))
    regex_match = (epic in raw_text) if epic else False
    return calculate_confidence(epic, {
        "format_valid": format_valid,
        "regex_match": regex_match,
        "clean_text": True,
    })


def score_mobile(mobile):
    if not mobile:
        return 0.0
    format_valid = len(mobile) == 10 and mobile[0] in "6789"
    return calculate_confidence(mobile, {"format_valid": format_valid, "regex_match": True})


def score_name(name):
    if not name:
        return 0.0
    clean = len(name.split()) <= 4
    return calculate_confidence(name, {"clean_text": clean})


def score_state(state):
    valid_states = ["उत्तर प्रदेश"]
    return 0.99 if state in valid_states else 0.5


def score_district(district, db_match):
    return calculate_confidence(district, {"db_match": db_match, "clean_text": True})


def score_ac(ac, db_match):
    return calculate_confidence(ac, {"db_match": db_match})


def score_address(address):
    if not address:
        return 0.0
    length_ok = len(address) > 20
    return calculate_confidence(address, {"clean_text": length_ok})


def score_part(part):
    if not part:
        return 0.0
    has_number = any(char.isdigit() for char in part)
    return calculate_confidence(part, {"format_valid": has_number, "clean_text": True})


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------

def extract_epic(text):
    """
    Extract EPIC number. Accepted formats (matching Claude parser rules):
      ABC1234567   – 3 letters + 7 digits
      AB12345678   – 2 letters + 8 digits
      A123456789   – 1 letter  + 8 digits  (rare)
      AB/1234567   – 2 letters / 7 digits
      AB/12/345/1234567 or AB/12/345/123456  – slash-district format

    Only searches in the portion of text BEFORE the address label "पता".
    OCR may insert spaces inside the token — spaces are stripped before matching.
    """
    # Restrict search to the region before the address section
    addr_split = re.split(r"पता[\s:]*", text, maxsplit=1)
    search_region = addr_split[0] if len(addr_split) > 1 else text

    # Strip internal spaces around any candidate that looks like an EPIC token
    # (OCR noise: "A B C 1 2 3 4 5 6 7" → "ABC1234567")
    def _strip_spaces_near_epic(t):
        return re.sub(
            r"([A-Z])\s+([A-Z0-9])",
            lambda m: m.group(1) + m.group(2),
            t
        )

    cleaned = _strip_spaces_near_epic(search_region)

    # Labelled EPIC patterns (highest confidence)
    labelled_patterns = [
        r'(?:EPIC|ईपीआईसी|ईपीआईसीआई)\s*(?:No\.?|नं\.?|संख्या)?[:\s]*'
        r'([A-Z]{1,3}(?:/\d+/\d+)?/?\d{6,8})',
    ]
    for pattern in labelled_patterns:
        m = re.search(pattern, cleaned, re.IGNORECASE)
        if m:
            candidate = m.group(1).replace(" ", "").upper()
            if _is_valid_epic(candidate):
                return candidate, 0.97

    # Unlabelled — scan for a standalone token matching any valid format
    token_pattern = re.compile(
        r'\b([A-Z]{1,3}(?:/\d+/\d+/\d{6,8}|/\d{7}|\d{7,8}))\b'
    )
    for m in token_pattern.finditer(cleaned):
        candidate = m.group(1).replace(" ", "").upper()
        if _is_valid_epic(candidate):
            return candidate, 0.90

    return None, 0.0


def _is_valid_epic(s):
    """Return True if s matches any known EPIC format."""
    patterns = [
        r'^[A-Z]{3}\d{7}$',            # ABC1234567
        r'^[A-Z]{2}\d{8}$',            # AB12345678
        r'^[A-Z]\d{8}$',               # A123456789
        r'^[A-Z]{2}/\d{7}$',           # AB/1234567
        r'^[A-Z]{2}/\d+/\d+/\d{6,8}$', # AB/12/345/1234567
    ]
    return any(re.match(p, s) for p in patterns)


def extract_mobile(text):
    # Labelled
    m = re.search(r"(?:मोबाइल\s*नंबर|Mobile)[:\s]*([6-9]\d{9})", text, re.IGNORECASE)
    if m:
        return m.group(1), 0.98

    # Bare 10-digit number starting with 6-9
    matches = re.findall(r"\b([6-9]\d{9})\b", text)
    if matches:
        return matches[0], 0.6

    return None, 0.0


def extract_name(text):
    """
    Extract only the voter's OWN name — never the father/mother/relative name.
    Father/husband labels appear on a separate line (पिता/पति/माता का नाम).
    """
    # Split into lines for label-based extraction
    lines = [l.strip() for l in re.split(r"\n+", text)]

    name_label_re = re.compile(
        r'(?:निर्वाचक\s*का\s*नाम|Elector\'?s?\s*Name|Name)[:\s]*(.+)',
        re.IGNORECASE
    )
    relative_label_re = re.compile(
        r'(?:पिता|पति|माता|Father|Mother|Husband)[:\s\'s]*',
        re.IGNORECASE
    )

    for line in lines:
        m = name_label_re.search(line)
        if m:
            value = m.group(1).strip()
            # Reject if the same line also contains a relative label
            if relative_label_re.search(value):
                # Try to take only the part before the relative label
                value = relative_label_re.split(value)[0].strip()
            value = re.sub(r"\s+", " ", value).strip()
            if value and len(value.split()) <= 4:
                return value, 0.95
            elif value:
                # Long capture — take first 4 words as a best-effort
                return " ".join(value.split()[:4]), 0.75

    return None, 0.0


def extract_serial(text):
    # Labels: क्रम संख्या / कण संख्या / कम संख्या
    m = re.search(r"(?:क्रम|कण|कम)\s*संख्या[:\s]+(\d+)", text)
    if m:
        return m.group(1), 0.97
    return None, 0.0


def extract_part_number_and_name(text):
    patterns = [
        r'(?:भाग\s*संख्या|Part\s*No\.?)[:\s]*(.+?)(?:\n|निर्वाचन|Assembly|$)',
        r'Part\s*No\.?\s*(?:and|&|एवं)\s*Name[:\s]*(.+?)(?:\n|Assembly|$)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m and m.group(1).strip():
            value = re.sub(r"<br\s*/?>", " ", m.group(1))
            value = re.sub(r"\s+", " ", value).strip()
            return value, 0.95
    return None, 0.0


def extract_state(text):
    m = re.search(r"(?:राज्य\s*का\s*नाम|State\s*Name)[:\s]*([^\n<]+)", text, re.IGNORECASE)
    if m:
        value = m.group(1).strip()
        value = re.sub(r"राज्य\s*का\s*", "", value).strip()
        words = value.split()
        # Keep up to 3 words (handles "उत्तर प्रदेश" / "Uttar Pradesh")
        value = " ".join(words[:3]) if len(words) >= 3 and words[2] in ["प्रदेश", "Pradesh"] else " ".join(words[:2])
        # Keep only Hindi script + spaces
        value = re.sub(r"[^ऀ-ॿ\s]", "", value).strip()
        return value, 0.99
    return None, 0.0


def extract_constituency_from_label(text):
    patterns = [
        r'(?:निर्वाचन\s*क्षेत्र|विधानसभा|Assembly\s*Constituency)[:\s]*(.+?)(?:\n|राज्य|State|$)',
        r'(?:Assembly|Constituency)[:\s]*(.+?)(?:\n|State|$)',
        r'(?:लोकसभा|Lok\s*Sabha)[:\s]*(.+?)(?:\n|$)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m and m.group(1).strip():
            value = re.sub(r"<br\s*/?>", " ", m.group(1))
            value = re.sub(r"\s+", " ", value).strip()
            return value, 0.95
    return None, 0.0


def extract_address(text):
    """
    Extract address after the पता (address) label, stopping at the next
    major section label (constituency / state / EPIC / serial number).
    """
    m = re.search(
        r"पता[:\s]*(.+?)(?=\n(?:निर्वाचन|विधानसभा|राज्य|Assembly|State|EPIC|ईपीआईसी|क्रम|भाग|$))",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        value = re.sub(r"<br\s*/?>", " ", m.group(1))
        value = re.sub(r"\s+", " ", value).strip()
        # Remove stray digits but keep Hindi/English words
        value = re.sub(r"\b\d+\b", "", value).strip()
        if value:
            return value, 0.90

    # Fallback: grab everything after "पता" on the same line
    m2 = re.search(r"पता[:\s]*([^\n]+)", text)
    if m2:
        value = m2.group(1).strip()
        value = re.sub(r"\b\d+\b", "", value).strip()
        if value:
            return value, 0.70

    return None, 0.0


# ---------------------------------------------------------------------------
# DB-assisted matching
# ---------------------------------------------------------------------------

def match_constituency(text_value, db):
    if not text_value:
        return None, 0.0

    text_value = text_value.strip()
    constituencies = db.query(Constituency).all()

    for c in constituencies:
        if c.constituency_hindi == text_value:
            return c, 1.0

    for c in constituencies:
        if text_value in c.constituency_hindi or c.constituency_hindi in text_value:
            return c, 0.95

    best, best_score = None, 0
    for c in constituencies:
        score = fuzz.token_set_ratio(c.constituency_hindi, text_value)
        if c.constituency_hindi.split()[0] == text_value.split()[0]:
            score -= 10
        if score > best_score:
            best_score = score
            best = c

    if best_score > 80:
        return best, best_score / 100

    return None, 0.0


def get_district_from_constituency(c_obj, db):
    if not c_obj:
        return None, 0.0
    district = db.query(District).filter(District.district_id == c_obj.district_id).first()
    if district:
        return district.district_name_hi, 0.98
    return None, 0.0


def extract_district_from_address(address, db):
    if not address:
        return None, 0.0

    districts = db.query(District).all()
    best_match, best_score = None, 0

    for d in districts:
        for name in [d.district_name_hi, d.district_name_en]:
            if not name:
                continue
            score = fuzz.partial_ratio(name, address)
            if score > best_score:
                best_score = score
                best_match = d

    if best_score > 80:
        return best_match.district_name_hi, best_score / 100

    return None, 0.0


# ---------------------------------------------------------------------------
# Output helper
# ---------------------------------------------------------------------------

def format_field(value, confidence):
    return {
        "value": value if value else None,
        "confidence": round(confidence, 2) if value else 0.0,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_ocr_text(text, db):
    try:
        text = clean_text(text)

        name_value, _ = extract_name(text)
        epic_value, _ = extract_epic(text)
        mobile_value, _ = extract_mobile(text)
        serial_value, _ = extract_serial(text)
        part_value, part_conf = extract_part_number_and_name(text)
        state_value, _ = extract_state(text)
        address_value, _ = extract_address(text)
        constituency_text, _ = extract_constituency_from_label(text)

        final_constituency = constituency_text.strip() if constituency_text else None

        constituency_obj = None
        if final_constituency:
            temp_obj, score = match_constituency(final_constituency, db)
            if temp_obj and score > 0.85:
                constituency_obj = temp_obj
                final_constituency = temp_obj.constituency_hindi

        district_value, d_conf = None, 0.0
        if constituency_obj:
            district_value, d_conf = get_district_from_constituency(constituency_obj, db)
        if not district_value and address_value:
            district_value, d_conf = extract_district_from_address(address_value, db)

        return {
            "name": format_field(name_value, score_name(name_value)),
            "epic": format_field(epic_value, score_epic(epic_value, text)),
            "mobile": format_field(mobile_value, score_mobile(mobile_value)),
            "serial_number": format_field(serial_value, 0.97 if serial_value else 0.0),
            "part_number_and_name": format_field(part_value, max(part_conf, score_part(part_value))),
            "assembly_constituency": format_field(
                final_constituency,
                score_ac(final_constituency, bool(constituency_obj)),
            ),
            "district": format_field(district_value, score_district(district_value, bool(constituency_obj))),
            "state": format_field(state_value, score_state(state_value)),
            "address": format_field(address_value, score_address(address_value)),
        }

    except Exception as e:
        print("❌ Parser crash:", str(e))
        return {}
