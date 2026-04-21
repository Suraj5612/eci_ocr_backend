"""
Resolve raw OCR constituency string to canonical DB values.

After parse_smart() extracts assembly_constituency, call resolve_constituency()
to match it against the DB constituency table and replace with the correct
Hindi name + district.
"""

from rapidfuzz import process, fuzz
from sqlalchemy.orm import Session

from app.models.constituency import Constituency
from app.models.districts import District

# Fuzzy match threshold — strings must be at least this similar (0–100)
_MATCH_THRESHOLD = 65


def resolve_constituency(
    db: Session, raw_value: str
) -> tuple[str | None, str | None]:
    """
    Fuzzy-match raw OCR constituency string against constituency_hindi in DB.

    Returns (constituency_hindi, district_name_hi) if a confident match is
    found, or (None, None) otherwise.
    """
    if not raw_value or not raw_value.strip():
        return None, None

    rows = db.query(Constituency).all()
    if not rows:
        return None, None

    hindi_names = [r.constituency_hindi for r in rows]

    result = process.extractOne(
        raw_value.strip(),
        hindi_names,
        scorer=fuzz.partial_ratio,
    )
    if not result or result[1] < _MATCH_THRESHOLD:
        print(
            f"[constituency_resolver] no match for '{raw_value}' "
            f"(best score: {result[1] if result else 0})"
        )
        return None, None

    matched_hindi = result[0]
    score = result[1]
    constituency = next((r for r in rows if r.constituency_hindi == matched_hindi), None)
    if not constituency:
        return None, None

    district = (
        db.query(District)
        .filter(District.district_id == constituency.district_id)
        .first()
    )
    district_hi = district.district_name_hi if district else None

    print(
        f"[constituency_resolver] '{raw_value}' → '{matched_hindi}' "
        f"(score={score}, district='{district_hi}')"
    )
    return matched_hindi, district_hi
