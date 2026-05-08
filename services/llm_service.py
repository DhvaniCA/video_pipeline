from openai import OpenAI
import google.generativeai as genai
from typing import Dict
from config import config
import json
import httpx


# ---------------------------------------------------------------------------
# Language style used across ALL outputs
# ---------------------------------------------------------------------------

_HINGLISH_STYLE_GUIDE = """
LANGUAGE STYLE — HINGLISH (mandatory for ALL text you write):
- Write in Hinglish: simple Hindi words mixed naturally with easy English.
- Technical CA terms (Debit, Credit, Bank Reconciliation, Trial Balance, etc.) MUST stay in English — never translate them.
- All explanations, sentences, connectors must be in simple Hinglish so a weak student understands easily.
- Do NOT write in formal Hindi (avoid heavy Sanskrit words). Keep it conversational like a friendly teacher talking.
- Avoid complex English words. Replace them with simple ones.
  BAD:  "This facilitates the reconciliation of discrepancies."
  GOOD: "Isse hum dono records mein difference ko match kar sakte hain."
- Every concept must feel like a dost (friend) explaining to you — warm, simple, clear.
"""

_SIMPLIFICATION_TASK = """
Aap ek expert CA teacher hain jo weak students ko padha rahe hain.
Neeche diye gaye CA content ko simplify karein aur ek structured document banayein jisme:

1. Ek clear title ho
2. Ek Introduction (2-3 sentences) — topic kya hai, kyun zaroori hai
3. Main sections — headings ke saath, simple explanation ke saath
4. Har section mein 3-5 key points (bullet format)
5. Ek real-life Indian example (sabse helpful part — must include)
6. Ek Summary (3-4 lines)
7. Key Takeaways (5-7 points) — "Yaad Rakho" style
"""

_JSON_SCHEMA = """
Return ONLY valid JSON (no markdown, no extra text) with this exact structure:
{
    "title": "Topic ka naam (English mein theek hai)",
    "introduction": "Hinglish introduction text",
    "sections": [
        {
            "heading": "Section Heading",
            "content": "Hinglish explanation",
            "points": ["Point 1 in Hinglish", "Point 2 in Hinglish"],
            "example": "Real-life Indian example (optional but preferred)"
        }
    ],
    "summary": "Hinglish summary",
    "key_takeaways": ["Takeaway 1 in Hinglish", "Takeaway 2 in Hinglish"]
}
"""

# ---------------------------------------------------------------------------
# Transcript generation constants
# ---------------------------------------------------------------------------

_TRANSCRIPT_TASK = """
Ek 7-15 minute ka conversational script likhein do characters ke beech:
  - User A = Rahul (male student, curious, puchta hai questions)
  - User B = Priya (female teacher, friendly, clearly explain karti hai)

STRICT RULES:
1. Pure Hinglish dialogue — jaise real Indian students/teachers bolte hain.
2. Rahul (User A) confusion express karta hai aur specific questions puchta hai.
3. Priya (User B) simple Hinglish mein clearly explain karti hai — ek ek concept ko.
4. SABHI concepts jo simplified PDF mein hain, unhein cover karo — koi concept miss mat karo.
5. Real-life Indian examples zaroor use karein (dukaan, bank, ghar ka kharcha, salary slip, etc.).
6. 7-15 minutes spoken length (approx 1050-2250 words total) — content cover karne ke liye enough time lo.
7. End mein "Yaad Rakho" section with 5 key points — sabse important cheezein.
8. Warm, encouraging, engaging tone — student ko boring na lage.

FILLER WORDS — BILKUL MAT LIKHNA:
- "Acha!", "Hmm", "Waise", "Toh", "Suno", "Dekho" jab sirf time fill kar rahe ho
- Koi bhi line jo concept se related nahi hai — jaise "Chalo shuru karte hain", "Bahut badiya sawaal hai",
  "Great question!", "Bilkul sahi kaha", "Haan haan", "Main samajh sakti hoon"
- Every single line MUST carry actual educational content about the topic.
- No praise loops, no meta-commentary, no filler transitions.

FORMAT — EXACTLY like this, no blank lines between turns:
A: [Rahul ka dialogue — concept ke baare mein specific question ya confusion]
B: [Priya ka dialogue — direct, informative explanation with example]
"""

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_CLAUDE_HAIKU_MODEL = "claude-sonnet-4-6"


class LLMService:
    def __init__(self):
        self.openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        genai.configure(api_key=config.GOOGLE_API_KEY)
        self.gemini_model = genai.GenerativeModel('gemini-pro')

        # Anthropic key for Claude Haiku transcript generation
        self._anthropic_key = getattr(config, "ANTHROPIC_API_KEY", None)
        if self._anthropic_key:
            print(f"[LLMService] ✅ Anthropic key loaded: {self._anthropic_key[:8]}…")
        else:
            print("[LLMService] ⚠️  ANTHROPIC_API_KEY not set — Claude Haiku unavailable")

    # ------------------------------------------------------------------
    # Content simplification — OpenAI
    # ------------------------------------------------------------------

    def simplify_content_with_openai(self, text: str) -> Dict:
        """Use OpenAI to simplify CA content into Hinglish structured JSON."""
        try:
            system_prompt = (
                "Aap ek expert CA educator hain jo weak students ke liye "
                "complex accounting content ko simple Hinglish mein explain karte hain. "
                "Aap hamesha valid JSON return karte hain, koi extra text nahi."
            )

            user_prompt = f"""
{_HINGLISH_STYLE_GUIDE}

{_SIMPLIFICATION_TASK}

Original Content (English):
{text[:4000]}

{_JSON_SCHEMA}
"""
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.7,
                response_format={"type": "json_object"},
            )

            content = json.loads(response.choices[0].message.content)
            return content

        except Exception as e:
            raise Exception(f"OpenAI simplification error: {str(e)}")

    # ------------------------------------------------------------------
    # Content simplification — Gemini
    # ------------------------------------------------------------------

    def simplify_content_with_gemini(self, text: str) -> Dict:
        """Use Gemini to simplify CA content into Hinglish structured JSON."""
        try:
            prompt = f"""
{_HINGLISH_STYLE_GUIDE}

{_SIMPLIFICATION_TASK}

Original Content (English):
{text[:4000]}

{_JSON_SCHEMA}

IMPORTANT: Return ONLY the raw JSON object. No markdown fences, no preamble.
"""
            response = self.gemini_model.generate_content(prompt)

            response_text = response.text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:].rsplit("```", 1)[0].strip()
            elif response_text.startswith("```"):
                response_text = response_text[3:].rsplit("```", 1)[0].strip()

            content = json.loads(response_text)
            return content

        except Exception as e:
            raise Exception(f"Gemini simplification error: {str(e)}")

    # ------------------------------------------------------------------
    # Transcript from raw text (NEW — used by orchestrator)
    # Uses Claude Haiku via Anthropic API directly.
    # Source: text extracted from client-uploaded simplified PDF.
    # ------------------------------------------------------------------

    def generate_video_transcript_from_text(self, simplified_text: str) -> str:
        """
        Generate a 7-15 min Hinglish conversational transcript from the
        raw text of the client-uploaded simplified PDF.

        Uses Claude Haiku (claude-haiku-4-5-20251001) via Anthropic REST API.
        Falls back to OpenAI gpt-4o-mini if Anthropic key is unavailable.
        """
        prompt = f"""
{_HINGLISH_STYLE_GUIDE}

{_TRANSCRIPT_TASK}

Below is the FULL content from the simplified PDF that you MUST cover completely.
Do not skip any concept, definition, formula, or example present in this content.

--- SIMPLIFIED PDF CONTENT START ---
{simplified_text[:12000]}
--- SIMPLIFIED PDF CONTENT END ---

Now write the complete 7-15 minute Hinglish dialogue script covering every concept above.
"""
        # Try Claude Haiku first
        if self._anthropic_key:
            try:
                result = self._call_claude_haiku(prompt)
                print("[LLMService] ✅ Transcript generated via Claude Haiku")
                return result
            except Exception as e:
                print(f"[LLMService] ⚠️  Claude Haiku failed: {e} — falling back to OpenAI")

        # Fallback: OpenAI
        return self._generate_transcript_openai(prompt)

    # ------------------------------------------------------------------
    # Legacy method — kept for backward compatibility
    # ------------------------------------------------------------------

    def generate_video_transcript(
        self, simplified_content: Dict, use_openai: bool = True
    ) -> str:
        """
        Generate transcript from a structured simplified_content dict.
        Kept for backward compatibility. Prefer generate_video_transcript_from_text().
        """
        content_summary = (
            f"Title: {simplified_content.get('title', 'CA Topic')}\n"
            f"Introduction: {simplified_content.get('introduction', '')}\n"
            f"Sections: {len(simplified_content.get('sections', []))}\n"
            f"Key Takeaways: "
            + ", ".join(simplified_content.get("key_takeaways", [])[:3])
        )

        sections_detail = ""
        for sec in simplified_content.get("sections", []):
            sections_detail += f"\n### {sec.get('heading', '')}\n"
            sections_detail += sec.get("content", "") + "\n"
            for pt in sec.get("points", []):
                sections_detail += f"  - {pt}\n"
            if sec.get("example"):
                sections_detail += f"  Example: {sec['example']}\n"

        prompt = f"""
{_HINGLISH_STYLE_GUIDE}

{_TRANSCRIPT_TASK}

Topic Summary:
{content_summary}

Detailed Content to Cover:
{sections_detail[:6000]}
"""
        if self._anthropic_key:
            try:
                result = self._call_claude_haiku(prompt)
                print("[LLMService] ✅ Transcript (legacy) generated via Claude Haiku")
                return result
            except Exception as e:
                print(f"[LLMService] ⚠️  Claude Haiku failed: {e} — falling back")

        return self._generate_transcript_openai(prompt)

    # ------------------------------------------------------------------
    # Internal: Claude Haiku via Anthropic REST API
    # ------------------------------------------------------------------

    def _call_claude_haiku(self, prompt: str) -> str:
        """
        Call claude-haiku-4-5-20251001 directly via Anthropic REST API.
        max_tokens = 8192 to support 7-15 min transcripts (~2000+ words).
        """
        headers = {
            "x-api-key":         self._anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        }
        payload = {
            "model":      _CLAUDE_HAIKU_MODEL,
            "max_tokens": 8192,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(_ANTHROPIC_API_URL, json=payload, headers=headers)

        if resp.status_code != 200:
            raise Exception(
                f"Anthropic API error {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        # Extract text from first content block
        content_blocks = data.get("content", [])
        text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
        if not text_parts:
            raise Exception("No text content returned from Claude Haiku")

        return "\n".join(text_parts).strip()

    # ------------------------------------------------------------------
    # Internal: OpenAI transcript fallback
    # ------------------------------------------------------------------

    def _generate_transcript_openai(self, prompt: str) -> str:
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Aap ek Hinglish educational script writer hain. "
                            "Aap engaging, content-dense aur accurate scripts likhte hain "
                            "jo Indian CA students ko easily samajh aayein. "
                            "Hamesha 7-15 minute ka complete script likhein."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.85,
                max_tokens=4096,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise Exception(f"OpenAI transcript generation error: {str(e)}")
