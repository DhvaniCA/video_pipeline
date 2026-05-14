from openai import OpenAI
import google.generativeai as genai
from typing import Dict
from config import config
import json
import httpx

def _truncate_transcript(transcript: str, max_exchanges: int = 35) -> str:
    """
    Hard-limit the transcript to max_exchanges A+B pairs.
    Preserves all [PAGE] / [TOPIC] tags and the Yaad Rakho section.
    Called after LLM generation as a safety net against over-long output.
    """
    import re as _re
    lines        = transcript.splitlines()
    exchange_count = 0
    output       = []
    line_re      = _re.compile(r'^[AB]\s*:', _re.IGNORECASE)

    for line in lines:
        if line_re.match(line.strip()):
            exchange_count += 1
            if exchange_count > max_exchanges:
                break
        output.append(line)
        # If we hit the Yaad Rakho section, include it and stop
        if "yaad rakho" in line.lower() or "key takeaway" in line.lower():
            # collect the bullet points that follow
            pass   # they'll be added normally in the loop

    return "\n".join(output).strip()


from services.platform_config import PLATFORM_CONFIGS


# ---------------------------------------------------------------------------
# JSON schema shared by all simplification prompts
# ---------------------------------------------------------------------------

_JSON_SCHEMA = """
Return ONLY valid JSON (no markdown, no extra text) with this exact structure:
{
    "title": "Topic title",
    "introduction": "Introduction text",
    "sections": [
        {
            "heading": "Section Heading",
            "content": "Explanation",
            "points": ["Point 1", "Point 2"],
            "example": "Real-life example (optional but preferred)"
        }
    ],
    "summary": "Summary text",
    "key_takeaways": ["Takeaway 1", "Takeaway 2"]
}
"""

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_CLAUDE_HAIKU_MODEL = "claude-sonnet-4-6"


class LLMService:
    def __init__(self, platform: str = "ca"):
        """
        Args:
            platform: "ca" or "cs" — drives all prompt content (subject, style, personas).
        """
        self.cfg = PLATFORM_CONFIGS[platform]
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
    # Platform-driven prompt builders
    # ------------------------------------------------------------------

    def _style_guide(self) -> str:
        if self.cfg.style == "hinglish":
            return f"""
LANGUAGE STYLE — HINGLISH (mandatory for ALL text you write):
- Write in Hinglish: simple Hindi words mixed naturally with easy English.
- Technical {self.cfg.subject_label} terms ({self.cfg.domain_terms}) MUST stay in English — never translate them.
- All explanations, sentences, connectors must be in simple Hinglish so a weak student understands easily.
- Do NOT write in formal Hindi (avoid heavy Sanskrit words). Keep it conversational like a friendly teacher talking.
- Avoid complex English words. Replace them with simple ones.
  BAD:  "This facilitates the reconciliation of discrepancies."
  GOOD: "Isse hum dono records mein difference ko match kar sakte hain."
- Every concept must feel like a dost (friend) explaining to you — warm, simple, clear.
"""
        return f"""
LANGUAGE STYLE — CLEAR ENGLISH:
- Write in simple, clear English suitable for students.
- Technical {self.cfg.subject_label} terms ({self.cfg.domain_terms}) must be used correctly and consistently.
- Friendly, conversational tone — avoid jargon overload.
- Every concept should feel like a knowledgeable friend explaining it simply.
"""

    def _simplification_task(self) -> str:
        return f"""
You are an expert {self.cfg.full_name} teacher helping weak students.
Simplify the following {self.cfg.subject_label} content into a structured document with:

1. A clear title
2. An Introduction (2-3 sentences) — topic kya hai / what the topic is, why it matters
3. Main sections — headings ke saath / with headings, simple explanation ke saath
4. 3-5 key points per section (bullet format)
5. A real-life example relevant to {self.cfg.full_name} students (must include)
6. A Summary (3-4 lines)
7. Key Takeaways (5-7 points) — "Yaad Rakho" style
"""

    def _transcript_task(self, target_exchanges: int = 60) -> str:
        a    = "Raj"
        b    = self.cfg.teacher_name
        subj = self.cfg.subject_label
        full = self.cfg.full_name
        terms= self.cfg.domain_terms
        style= self.cfg.style
        lang = (
            f"Hinglish — natural Hindi + English. Technical terms ({terms}) always in English."
            if style == "hinglish"
            else f"Clear English. Technical terms ({terms}) used precisely."
        )
        half = target_exchanges // 2
        return f"""You are writing a professional educational video script for {full} students.
The video has LEFT panel (Raj and Priya talking) and RIGHT panel (live PDF page).

=== CHARACTERS ===
A: Raj  — {full} student. Asks clear, specific questions about concepts on the current PDF page.
          References the page: "Priya, is page pe jo {terms.split(',')[0].strip()} wala concept hai..."
          Never uses filler. Every question is content-driven.

B: Priya — {full} teacher. Teaches with full depth:
          - Defines the concept precisely
          - Explains the formula or rule step-by-step
          - Walks through the numerical example shown on the PDF page
          - States the common exam mistake
          - Gives the ICAI/ICSI exam angle in one line
          References PDF explicitly: "Dekho is page pe...", "Yahan table mein...", "Formula page pe diya hai..."

=== LANGUAGE ===
{lang}
BANNED words: "Good luck", "Great question", "Excellent", "Wow", "Bilkul sahi",
"Bahut accha", "Perfect", "Absolutely", "Of course", "Sure", "Amazing", any hollow praise.

=== COVERAGE RULE — MOST IMPORTANT ===
You MUST cover EVERY section, definition, formula, table, and worked example in the PDF.
Do NOT skip any concept. Do NOT summarise when you should teach.
Work through every numerical example step by step.
The video must be a complete lecture on the topic — not a highlight reel.
Target: approximately {target_exchanges} exchanges ({half} for Raj, {half} for Priya).
If the PDF has more content than {target_exchanges} exchanges can cover, use MORE exchanges.
Maximum allowed: {target_exchanges + 20} exchanges. Never cut content to fit a limit.

=== PDF PAGE TAGS — MANDATORY ===
Before every new topic, sub-topic, example, or formula insert on its OWN line:
[PAGE N] [TOPIC: Exact Topic Name from PDF]
- N = actual page number in the simplified PDF
- Insert a new tag every 4-5 exchanges minimum
- Tags go on their own line — never inside A: or B: dialogue
- Skip TOC, index, cover pages — start from first real content page

=== FORMAT — strictly follow, no blank lines between turns ===
[PAGE 1] [TOPIC: Introduction]
A: [question referencing PDF page]
B: [full teaching answer]
A: [follow-up question]
B: [deeper explanation with formula/example]
[PAGE 2] [TOPIC: Next Concept]
A: [question]
B: [answer]
...cover all PDF content...
Yaad Rakho:
• [key point 1]
• [key point 2]
• [key point 3]
• [key point 4]
• [key point 5]
• [key point 6]
• [key point 7]
"""

    # ------------------------------------------------------------------
    # Content simplification — OpenAI
    # ------------------------------------------------------------------

    def simplify_content_with_openai(self, text: str) -> Dict:
        """Use OpenAI to simplify content into structured JSON."""
        try:
            system_prompt = (
                f"You are an expert {self.cfg.full_name} educator who simplifies "
                f"complex {self.cfg.subject_label} content for students. "
                "Always return valid JSON only, no extra text."
            )

            user_prompt = f"""
{self._style_guide()}

{self._simplification_task()}

Original Content:
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
        """Use Gemini to simplify content into structured JSON."""
        try:
            prompt = f"""
{self._style_guide()}

{self._simplification_task()}

Original Content:
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
    # Transcript from raw text (used by orchestrator)
    # Uses Claude Haiku via Anthropic API. Falls back to OpenAI.
    # ------------------------------------------------------------------

    def generate_video_transcript_from_text(
        self,
        simplified_text: str,
        pdf_page_count:  int = 0,
    ) -> str:
        """
        Generate a full-coverage transcript scaled to pdf_page_count.
        NEVER cuts content — covers every page of the PDF completely.

        Page count → target exchanges mapping:
          1-4  pages  →  40 exchanges  (~10 min)
          5-8  pages  →  60 exchanges  (~15 min)
          9-12 pages  →  80 exchanges  (~20 min)
          13+  pages  →  100 exchanges (~22 min max)

        max_tokens is always 16000 so Claude never runs out of space.
        No truncation — the full transcript is returned as-is.
        """
        if pdf_page_count <= 0:
            pdf_page_count = max(1, len(simplified_text) // 400)

        if pdf_page_count <= 4:
            target_exchanges = 40
            target_min       = 10
        elif pdf_page_count <= 8:
            target_exchanges = 60
            target_min       = 15
        elif pdf_page_count <= 12:
            target_exchanges = 80
            target_min       = 20
        else:
            target_exchanges = 100
            target_min       = 22

        # Always 16000 — never cut Claude short mid-generation
        self._dynamic_max_tokens = 16000

        print(f"[LLMService] PDF pages={pdf_page_count} → "
              f"{target_exchanges} exchanges target, ~{target_min} min, "
              f"max_tokens=16000")

        # Send full PDF text — no slicing, no content loss
        prompt = f"""
{self._style_guide()}

{self._transcript_task(target_exchanges=target_exchanges)}

--- SIMPLIFIED PDF CONTENT START ---
{simplified_text}
--- SIMPLIFIED PDF CONTENT END ---

Write the COMPLETE script now covering EVERY section in the PDF above.
Do not stop until all PDF content is covered and you reach the Yaad Rakho section.
"""
        if self._anthropic_key:
            try:
                result = self._call_claude_haiku(prompt)
                print("[LLMService] ✅ Transcript generated via Claude Haiku")
            except Exception as e:
                print(f"[LLMService] ⚠️  Claude Haiku failed: {e} — falling back to OpenAI")
                result = self._generate_transcript_openai(prompt)
        else:
            result = self._generate_transcript_openai(prompt)

        word_count = len(result.split())
        print(f"[LLMService] ✅ Final transcript: {word_count} words "
              f"({word_count//130:.0f}-{word_count//110:.0f} min estimated)")
        return result

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
            f"Title: {simplified_content.get('title', f'{self.cfg.subject_label} Topic')}\n"
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
{self._style_guide()}

{self._transcript_task()}

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
        Call Claude via Anthropic REST API using STREAMING to avoid timeouts.

        Why streaming:
          - A 12-15 min transcript is ~2000-2500 words / 16000 tokens.
          - Non-streaming: httpx waits for the FULL response before returning.
            At ~60 tokens/sec that is 250+ seconds — well past any 120s timeout.
          - Streaming: the HTTP connection stays alive and receives chunks as they
            arrive, so no read-timeout is triggered mid-generation.

        Timeout config:
          - connect: 15s (fail fast if Anthropic is unreachable)
          - read:    600s (10 min — enough for the longest possible script)
          - write:   30s  (sending the request payload)
          - pool:    600s (match read)
        """
        headers = {
            "x-api-key":         self._anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        }
        # Use dynamic token limit if set by generate_video_transcript_from_text
        max_tok = getattr(self, "_dynamic_max_tokens", 4000)

        payload = {
            "model":      _CLAUDE_HAIKU_MODEL,
            "max_tokens": max_tok,
            "stream":     True,        # ← streaming keeps connection alive
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        timeout = httpx.Timeout(
            connect=15.0,
            read=600.0,    # 10 min read window — covers even the longest script
            write=30.0,
            pool=600.0,
        )

        collected_text = []

        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST", _ANTHROPIC_API_URL,
                    headers=headers, json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        body = resp.read().decode("utf-8", errors="replace")
                        raise Exception(
                            f"Anthropic API error {resp.status_code}: {body[:400]}"
                        )

                    for line in resp.iter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            import json as _json
                            event = _json.loads(data_str)
                        except Exception:
                            continue

                        # Anthropic streaming event types:
                        #   content_block_delta  → has delta.text
                        #   message_stop         → generation complete
                        etype = event.get("type", "")
                        if etype == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                collected_text.append(delta.get("text", ""))
                        elif etype == "message_stop":
                            break
                        elif etype == "error":
                            err = event.get("error", {})
                            raise Exception(
                                f"Anthropic stream error: {err.get('message', str(err))}"
                            )

        except httpx.ReadTimeout:
            # If we already collected substantial text, return what we have
            if len("".join(collected_text)) > 500:
                print("[LLMService] ⚠️  Stream read timeout — returning partial transcript")
            else:
                raise Exception(
                    "Anthropic API timed out before generating enough content. "
                    "Falling back to OpenAI."
                )

        result = "".join(collected_text).strip()
        if not result:
            raise Exception("Claude returned empty response — falling back to OpenAI.")

        word_count = len(result.split())
        print(f"[LLMService] ✅ Claude transcript: {word_count} words via streaming")

        # Safety net: if Claude still over-generated, truncate to 35 A/B exchanges max
        result = _truncate_transcript(result, max_exchanges=35)
        final_words = len(result.split())
        print(f"[LLMService] ✅ After truncation: {final_words} words")
        return result


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
                            f"You are a {self.cfg.full_name} educational script writer. "
                            f"You write engaging, content-dense, and accurate scripts "
                            f"that {self.cfg.full_name} students can easily understand. "
                            "Always write a complete 7-15 minute script."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.85,
                max_tokens=4000,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise Exception(f"OpenAI transcript generation error: {str(e)}")
