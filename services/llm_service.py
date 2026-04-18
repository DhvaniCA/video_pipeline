from openai import OpenAI
import google.generativeai as genai
from typing import Dict
from config import config
import json


# ---------------------------------------------------------------------------
# Language style used across ALL outputs
#
# "Hinglish" = simple Hindi words mixed with easy English.
# Rule: English technical terms (Bank Reconciliation, Debit, Credit, etc.)
#       stay in English. Explanations, connectors, transitions → Hindi/Hinglish.
#
# Example good Hinglish:
#   "Bank Reconciliation Statement ek aisa document hai jo hamare
#    Cash Book aur Bank Statement ke beech ka difference batata hai."
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

_TRANSCRIPT_TASK = """
Ek 5-7 minute ka conversational script likhein do characters ke beech:
  - User A = Rahul (male student, curious, asks questions)
  - User B = Priya (female teacher, friendly, explains clearly)

Requirements:
1. Pure Hinglish dialogue — jaise real Indian students/teachers bolte hain
2. Rahul (User A) confusion express karta hai aur questions puchta hai
3. Priya (User B) simple Hinglish mein clearly explain karti hai
4. Real-life Indian examples zaroor use karein (shop, bank, ghar ka kharcha, etc.)
5. 5-7 minutes spoken length (approx 750-1050 words total)
6. End mein "Yaad Rakho" summary with 3 key points
7. Warm, encouraging, fun tone — students ko boring na lage

Format EXACTLY like this (no extra lines between):
User A: [dialogue]
User B: [dialogue]
"""


class LLMService:
    def __init__(self):
        self.openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        genai.configure(api_key=config.GOOGLE_API_KEY)
        self.gemini_model = genai.GenerativeModel('gemini-pro')

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
            # Strip any accidental markdown fences
            if response_text.startswith("```json"):
                response_text = response_text[7:].rsplit("```", 1)[0].strip()
            elif response_text.startswith("```"):
                response_text = response_text[3:].rsplit("```", 1)[0].strip()

            content = json.loads(response_text)
            return content

        except Exception as e:
            raise Exception(f"Gemini simplification error: {str(e)}")

    # ------------------------------------------------------------------
    # Video transcript generation
    # ------------------------------------------------------------------

    def generate_video_transcript(
        self, simplified_content: Dict, use_openai: bool = True
    ) -> str:
        """
        Generate a Hinglish conversational transcript for the animated video.
        User A = Rahul (male student)  → audio: Indian male voice
        User B = Priya (female teacher) → audio: Indian female voice
        """
        try:
            content_summary = (
                f"Title: {simplified_content.get('title', 'CA Topic')}\n"
                f"Introduction: {simplified_content.get('introduction', '')}\n"
                f"Sections: {len(simplified_content.get('sections', []))}\n"
                f"Key Takeaways: "
                + ", ".join(simplified_content.get("key_takeaways", [])[:3])
            )

            # Include actual section content so the transcript is accurate
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
{sections_detail[:3000]}
"""
            if use_openai:
                response = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Aap ek Hinglish educational script writer hain. "
                                "Aap engaging, simple aur accurate scripts likhte hain "
                                "jo Indian CA students ko easily samajh aayein."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.85,
                )
                return response.choices[0].message.content
            else:
                response = self.gemini_model.generate_content(prompt)
                return response.text

        except Exception as e:
            raise Exception(f"Transcript generation error: {str(e)}")
