import re
from rapidfuzz import fuzz
from app.models.constituency import Constituency
from app.models.districts import District

def get_lines(text):
    text = clean_text(text)
    return [line.strip() for line in text.split(" ") if len(line.strip()) > 2]

def clean_text(text):
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<.*?>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def calculate_confidence(value, rules: dict):
    score = 0.0
    if not value:
        return 0.0
    # base score
    score += 0.4
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
    format_valid = bool(re.match(r"^[A-Z]{3}\d{7}$", epic or ""))
    regex_match = epic in raw_text if epic else False

    return calculate_confidence(epic, {
        "format_valid": format_valid,
        "regex_match": regex_match,
        "clean_text": True
    })

def score_mobile(mobile):
    if not mobile:
        return 0.0

    format_valid = len(mobile) == 10 and mobile[0] in "6789"

    return calculate_confidence(mobile, {
        "format_valid": format_valid,
        "regex_match": True
    })

def score_name(name):
    if not name:
        return 0.0

    # reject garbage long names
    clean = len(name.split()) <= 4

    return calculate_confidence(name, {
        "clean_text": clean
    })

def score_state(state):
    valid_states = ["उत्तर प्रदेश"]  # or DB

    return 0.99 if state in valid_states else 0.5

def score_district(district, db_match):
    return calculate_confidence(district, {
        "db_match": db_match,
        "clean_text": True
    })

def score_ac(ac, db_match):
    return calculate_confidence(ac, {
        "db_match": db_match
    })

def score_address(address):
    if not address:
        return 0.0

    length_ok = len(address) > 20

    return calculate_confidence(address, {
        "clean_text": length_ok
    })

def score_part(part):
    if not part:
        return 0.0

    has_number = any(char.isdigit() for char in part)

    return calculate_confidence(part, {
        "format_valid": has_number,
        "clean_text": True
    })

def extract_field(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip(), 0.9
    return None, 0.0

def extract_epic(text):
    # strict match
    match = re.search(r"\b[A-Z]{3}[0-9]{6,7}\b", text)
    if match:
        return match.group(), 0.98

    match = re.search(r"[A-Z0-9]{3}\s?[0-9]{6,7}", text)
    if match:
        value = match.group().replace(" ", "")
        return value, 0.8

    return None, 0.0

def extract_mobile(text):
    cleaned = re.sub(r"[^\d]", " ", text)

    # join digit chunks
    cleaned = re.sub(r"\s+", "", cleaned)

    matches = re.findall(r"[6-9]\d{9}", cleaned)

    if not matches:
        return None, 0.0

    return matches[0], 0.95 if len(matches) == 1 else 0.75

def extract_name(text):
    match = re.search(
        r"निर्वाचक का नाम[:\s]*([^\n<]+)",
        text
    )

    if match:
        value = match.group(1)

        # 🔥 remove trailing garbage
        value = re.split(r"(ईपीआईसी|EPIC|पता|क्रम)", value)[0]

        value = value.strip()

        # safety: remove numbers
        value = re.sub(r"[0-9]", "", value).strip()

        return value, 0.95

    return None, 0.0

def extract_serial(text):
    match = re.search(r"क्रम संख्या[:\s]+(\d+)", text)
    if match:
        return match.group(1), 0.97
    return None, 0.0

def extract_part_number_and_name(text):

    patterns = [
        r"(?:भाग संख्या एवं नाम|नाम संख्या एवं नाम|गण संख्या एवं नाम|Part Number and Name)[\s:]*([^\n<]+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip()
            value = re.sub(r"\s+", " ", value)
            value = value.split(",")[0]

            return value, 0.95

    return None, 0.0

def extract_state(text):
    match = re.search(
        r"(?:राज्य का नाम|State Name)[\s:]*([^\n<]+)",
        text
    )

    if match:
        value = match.group(1).strip()

        words = value.split()
        value = " ".join(words[:2])

        if len(words) > 2 and words[2] in ["प्रदेश", "Pradesh"]:
            value = " ".join(words[:3])

        value = re.sub(r"[^ऀ-ॿ\s]", "", value).strip()

        return value, 0.99

    return None, 0.0

def extract_constituency_from_label(text):
    match = re.search(
        r"(?:विधानसभा.*?नाम|संसदीय निर्वाचन क्षेत्र का नाम)[\s:]*([^\n]+)",
        text
    )

    if match:
        value = match.group(1)

        # handle <br>
        value = re.sub(r"<br\s*/?>", " ", value)

        value = re.sub(r"\s+", " ", value).strip()

        return value, 0.95

    return None, 0.0

def extract_constituency_db(text, db):
    from app.models.constituency import Constituency
    from rapidfuzz import fuzz

    constituencies = db.query(Constituency).all()

    best_match = None
    best_score = 0

    for c in constituencies:
        score = fuzz.partial_ratio(c.constituency_hindi, text)

        if score > best_score:
            best_score = score
            best_match = c

    if best_score > 85:
        return best_match, best_score / 100

    return None, 0.0

def get_district_from_constituency(c_obj, db):
    if not c_obj:
        return None, 0.0

    district = db.query(District).filter(
        District.district_id == c_obj.district_id
    ).first()

    if district:
        return district.district_name_hi, 0.98

    return None, 0.0

def match_constituency(text_value, db):
    from app.models.constituency import Constituency
    from rapidfuzz import fuzz

    if not text_value:
        return None, 0.0

    constituencies = db.query(Constituency).all()

    best = None
    best_score = 0

    for c in constituencies:
        score = fuzz.partial_ratio(c.constituency_hindi, text_value)

        if score > best_score:
            best_score = score
            best = c

    if best_score > 85:
        return best, best_score / 100

    if not text_value:
        return None, 0.0

def extract_address(text):
    match = re.search(
        r"(?:पता|माता)[:\s]*([^\n]+)",
        text
    )

    if match:
        value = match.group(1)

        value = re.split(r"(क्रम संख्या|गण संख्या|भाग संख्या|विधानसभा)", value)[0]

        return value.strip(), 0.9

    return None, 0.0

def format_field(value, confidence):
    return {
        "value": value if value else None,
        "confidence": round(confidence, 2) if value else 0.0
    }

def parse_ocr_text(text, db):
    text = clean_text(text)
    name_value, _ = extract_name(text)
    epic_value, _ = extract_epic(text)
    mobile_value, _ = extract_mobile(text)
    serial_value, _ = extract_serial(text)
    part_value, part_conf = extract_part_number_and_name(text)
    state_value, _ = extract_state(text)
    address_value, _ = extract_address(text)

    constituency_text, _ = extract_constituency_from_label(text)
    constituency_obj, c_conf = match_constituency(constituency_text, db)

    district_value, d_conf = get_district_from_constituency(
        constituency_obj, db
    )

    return {
        "name": format_field(
            name_value,
            score_name(name_value)
        ),

        "epic": format_field(
            epic_value,
            score_epic(epic_value, text)
        ),

        "mobile": format_field(
            mobile_value,
            score_mobile(mobile_value)
        ),

        "serial_number": format_field(
            serial_value,
            0.97 if serial_value else 0.0
        ),

        "part_number_and_name": format_field(
            part_value,
            max(part_conf, score_part(part_value))
        ),

        "state": format_field(
            state_value,
            score_state(state_value)
        ),

        "address": format_field(
            address_value,
            score_address(address_value)
        ),

        "assembly_constituency": format_field(
            constituency_obj.constituency_hindi if constituency_obj else None,
            score_ac(
                constituency_obj.constituency_hindi if constituency_obj else None,
                bool(constituency_obj)
            )
        ),

        "district": format_field(
            district_value,
            score_district(district_value, bool(constituency_obj))
        )
    }


def format_field(value, confidence):
    return {
        "value": value,
        "confidence": confidence
    }
