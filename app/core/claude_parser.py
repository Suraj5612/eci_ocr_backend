import json
import re
from anthropic import Anthropic
from app.core.config import settings

# Lazy client — only instantiated on first call, avoids crash at import if key is missing
_client = None

def _get_client():
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set in environment")
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def parse_with_claude(raw_text: str) -> dict | None:
    """
    Primary OCR parser. Sends raw Sarvam OCR markdown to Claude and extracts
    structured voter card fields.

    Returns a flat dict with string values (or null), no confidence scores.
    Returns None on failure — caller should fall back to regex parser.
    """
    if not raw_text or not raw_text.strip():
        return None

    prompt = f"""You are an OCR data extraction system for Indian voter (EPIC) cards.
The text was extracted from a voter card image. It may contain Hindi + English text, markdown, <br/> tags, and OCR noise.

---

FIELD EXTRACTION RULES:

1. name
   - Look for label: "निर्वाचक का नाम" or "Elector's Name" or "Name :"
   - Take only the voter's OWN name — never the father/mother/relative name
   - Max ~4 words. If it looks like a sentence, it's wrong.

2. epic
   - Look for label: "EPIC" or "ईपीआईसी" or "ईपीआईसीआई" followed by the number
   - IMPORTANT: only search for EPIC in the text BEFORE the address label "पता" — ignore any alphanumeric codes that appear after it
   - OCR may insert spaces inside the token — remove all spaces from the matched value before validating
   - Valid formats (accept any one of these):
       * ABC1234567    → 3 uppercase letters + 7 digits
       * AB12345678    → 2 uppercase letters + 8 digits
       * A123456789    → 1 uppercase letter + 8 digits (rare but valid)
       * AB/1234567    → 2 letters / 7 digits
       * AB/12/345/1234567 or AB/12/345/123456 → slash-separated district format
   - If the labeled value matches any format above, prefer it (confidence is higher)
   - If no label, scan for a standalone token matching any format above
   - Return null if nothing matches — do NOT guess

3. mobile
   - Look for label: "मोबाइल नंबर" or "Mobile"
   - Must be exactly 10 digits, first digit 6/7/8/9
   - If no label, use bare 10-digit number starting with 6–9 as fallback
   - Return null if not found

4. serial_number
   - Look for label: "क्रम संख्या" or "कण संख्या" or "कम संख्या" followed by digits
   - Return digits only (as string)

5. part_number_and_name
   - Look for label: "भाग संख्या" or "Part No." or "Part No. and Name"
   - Include both the number and the booth name that follows the label

6. assembly_constituency
   - Look for label: "निर्वाचन क्षेत्र" or "विधानसभा" or "Assembly Constituency"
   - Also check "लोकसभा" or "Lok Sabha" as fallback
   - Return the constituency name in Hindi

7. district
   - NOT directly labeled on voter cards — derive it from context:
     a. If constituency is found and district is mentioned nearby, use it
     b. Look for "जिला" in the address or nearby text
     c. If clearly inferable from address text, extract it
   - Return Hindi name, or null if uncertain

8. state
   - Look for label: "राज्य का नाम" or "State Name"
   - Return Hindi name only (e.g. "उत्तर प्रदेश")
   - Strip any "राज्य का" prefix if present

9. address
   - Voter's residential address — appears after the name section
   - Hindi text preferred. Remove digits if mixed in.
   - Exclude name, EPIC, constituency, and state from the address value

---

STRICT OUTPUT RULES:
- Your ENTIRE response must be only the JSON object — no explanation, no bullet points, no reasoning, no markdown fences.
- Start your response with {{ and end with }}. Nothing before or after.
- null for any field not found — never guess.
- Keep all Hindi text exactly as-is (no transliteration).
- serial_number must be a string of digits, not a number type.

OUTPUT FORMAT:
{{
  "name": null,
  "epic": null,
  "mobile": null,
  "serial_number": null,
  "part_number_and_name": null,
  "assembly_constituency": null,
  "district": null,
  "state": null,
  "address": null
}}

OCR TEXT:
\"\"\"
{raw_text}
\"\"\"
"""

    print("🤖 Claude parser: starting...")

    try:
        client = _get_client()

        print("📤 Claude parser: sending OCR text to Claude...")
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        print("📥 Claude parser: response received")

        text = response.content[0].text.strip()

        # Strip any accidental markdown fences
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()

        # Claude sometimes writes reasoning before the JSON — extract just the {...} block
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            print(f"❌ Claude parser: no JSON object found in response\nRaw response: {text!r}")
            return None
        text = json_match.group(0)

        parsed = json.loads(text)
        print("✅ Claude parser: JSON parsed successfully")

        # Normalise: replace empty strings with None
        result = {k: (v if v != "" else None) for k, v in parsed.items()}

        return result

    except RuntimeError as e:
        print(f"❌ Claude parser config error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Claude parser: invalid JSON returned — {e}\nRaw response: {text!r}")
        return None
    except Exception as e:
        print(f"❌ Claude parser error: {e}")
        return None
