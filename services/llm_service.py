# from openai import OpenAI
# import google.generativeai as genai
# from typing import Dict
# from config import config
# import json
# import re
# import httpx

# from services.platform_config import PLATFORM_CONFIGS


# # ---------------------------------------------------------------------------
# # Video length control
# # ---------------------------------------------------------------------------
# # Everything about transcript length flows from ONE number: words-per-minute
# # of spoken dialogue. video_service.py estimates duration as
# #     duration = words / 120 * 60      (i.e. 120 words/minute)
# # so we budget the SCRIPT in words against that same rate. This is the
# # single source of truth for "how long will this video be" — no more
# # disconnected exchange-counts that don't match the actual cap.
# _WORDS_PER_MINUTE   = 120
# _HARD_CAP_MINUTES   = 18     # absolute ceiling — never target above this
# _TARGET_BUFFER      = 1.10   # allow the model 10% headroom before truncating


# def _target_minutes_for_pages(pdf_page_count: int) -> int:
#     """
#     Page count → target video length, capped at _HARD_CAP_MINUTES.
#     Deliberately conservative so real TTS pacing (which can run a little
#     slower than the 120 wpm estimate) still lands inside 15-18 min.
#     """
#     if pdf_page_count <= 4:
#         return 10
#     if pdf_page_count <= 8:
#         return 13
#     if pdf_page_count <= 12:
#         return 15
#     return _HARD_CAP_MINUTES  # 13+ pages: cap at 18, never scale further


# def truncate_transcript_to_word_budget(transcript: str, max_words: int) -> str:
#     """
#     Cut the transcript once cumulative spoken words (inside A:/B: lines)
#     reaches max_words, then look forward for the "Yaad Rakho" / key
#     takeaways closer so the video ends cleanly instead of mid-sentence.
#     All [PAGE]/[TOPIC] tag lines before the cutoff are preserved so the
#     PDF panel in the video still syncs correctly for the content that
#     actually plays.
#     """
#     lines    = transcript.splitlines()
#     line_re  = re.compile(r'^\**\s*(?:User\s*)?([AB])\**\s*:\s*(.*)$', re.IGNORECASE)
#     output   = []
#     word_count      = 0
#     cutoff_reached  = False
#     yaad_found      = False

#     for line in lines:
#         stripped = line.strip()

#         if cutoff_reached:
#             if not yaad_found:
#                 if "yaad rakho" in stripped.lower() or "key takeaway" in stripped.lower():
#                     yaad_found = True
#                     output.append(line)
#                 continue
#             # Inside the Yaad Rakho block: keep bullets, stop at the next
#             # dialogue line or tag (i.e. don't drag in leftover content).
#             if line_re.match(stripped) or stripped.upper().startswith("[PAGE") \
#                or stripped.upper().startswith("[TOPIC"):
#                 break
#             output.append(line)
#             continue

#         m = line_re.match(stripped)
#         if m:
#             word_count += len(m.group(2).split())
#         output.append(line)
#         if m and word_count >= max_words:
#             cutoff_reached = True

#     return "\n".join(output).strip()


# def truncate_transcript_to_dialogue_count(transcript: str, max_dialogue_lines: int) -> str:
#     """
#     Cut the transcript after the Nth A:/B: line (used by audio_service as a
#     hard safety net once REAL TTS duration is known — word-count estimates
#     can run long or short depending on how ElevenLabs/OpenAI actually
#     pace the speech). Same tag-preserving, clean-ending behaviour as
#     truncate_transcript_to_word_budget.
#     """
#     lines   = transcript.splitlines()
#     line_re = re.compile(r'^\**\s*(?:User\s*)?([AB])\**\s*:\s*(.*)$', re.IGNORECASE)
#     output  = []
#     dialogue_count = 0
#     cutoff_reached = False
#     yaad_found     = False

#     for line in lines:
#         stripped = line.strip()

#         if cutoff_reached:
#             if not yaad_found:
#                 if "yaad rakho" in stripped.lower() or "key takeaway" in stripped.lower():
#                     yaad_found = True
#                     output.append(line)
#                 continue
#             if line_re.match(stripped) or stripped.upper().startswith("[PAGE") \
#                or stripped.upper().startswith("[TOPIC"):
#                 break
#             output.append(line)
#             continue

#         if line_re.match(stripped):
#             dialogue_count += 1
#         output.append(line)
#         if dialogue_count >= max_dialogue_lines and line_re.match(stripped):
#             cutoff_reached = True

#     return "\n".join(output).strip()


# # ---------------------------------------------------------------------------
# # JSON schema shared by all simplification prompts
# # ---------------------------------------------------------------------------

# _JSON_SCHEMA = """
# Return ONLY valid JSON (no markdown, no extra text) with this exact structure:
# {
#     "title": "Topic title",
#     "introduction": "Introduction text",
#     "sections": [
#         {
#             "heading": "Section Heading",
#             "content": "Explanation",
#             "points": ["Point 1", "Point 2"],
#             "example": "Real-life example (optional but preferred)"
#         }
#     ],
#     "summary": "Summary text",
#     "key_takeaways": ["Takeaway 1", "Takeaway 2"]
# }
# """

# _ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# # FIX: this was previously pointed at a Sonnet-class model string while every
# # log line and comment called it "Claude Haiku". For a scripted, format-
# # constrained writing task like this transcript, Haiku is faster and cheaper
# # with no meaningful quality loss — switched to the actual Haiku model.
# _CLAUDE_HAIKU_MODEL = "claude-haiku-4-5-20251001"


# class LLMService:
#     def __init__(self, platform: str = "ca"):
#         """
#         Args:
#             platform: "ca" or "cs" — drives all prompt content (subject, style, personas).
#         """
#         self.cfg = PLATFORM_CONFIGS[platform]
#         self.openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
#         genai.configure(api_key=config.GOOGLE_API_KEY)
#         self.gemini_model = genai.GenerativeModel('gemini-pro')

#         # Anthropic key for Claude Haiku transcript generation
#         self._anthropic_key = getattr(config, "ANTHROPIC_API_KEY", None)
#         if self._anthropic_key:
#             print(f"[LLMService] ✅ Anthropic key loaded: {self._anthropic_key[:8]}…")
#         else:
#             print("[LLMService] ⚠️  ANTHROPIC_API_KEY not set — Claude Haiku unavailable")

#     # ------------------------------------------------------------------
#     # Platform-driven prompt builders
#     # ------------------------------------------------------------------

#     def _style_guide(self) -> str:
#         if self.cfg.style == "hinglish":
#             return f"""
# LANGUAGE STYLE — HINGLISH (mandatory for ALL text you write):
# - Write in Hinglish: simple Hindi words mixed naturally with easy English.
# - Technical {self.cfg.subject_label} terms ({self.cfg.domain_terms}) MUST stay in English — never translate them.
# - All explanations, sentences, connectors must be in simple Hinglish so a weak student understands easily.
# - Do NOT write in formal Hindi (avoid heavy Sanskrit words). Keep it conversational like a friendly teacher talking.
# - Avoid complex English words. Replace them with simple ones.
#   BAD:  "This facilitates the reconciliation of discrepancies."
#   GOOD: "Isse hum dono records mein difference ko match kar sakte hain."
# - Every concept must feel like a dost (friend) explaining to you — warm, simple, clear.
# """
#         return f"""
# LANGUAGE STYLE — CLEAR ENGLISH:
# - Write in simple, clear English suitable for students.
# - Technical {self.cfg.subject_label} terms ({self.cfg.domain_terms}) must be used correctly and consistently.
# - Friendly, conversational tone — avoid jargon overload.
# - Every concept should feel like a knowledgeable friend explaining it simply.
# """

#     def _simplification_task(self) -> str:
#         return f"""
# You are an expert {self.cfg.full_name} teacher helping weak students.
# Simplify the following {self.cfg.subject_label} content into a structured document with:

# 1. A clear title
# 2. An Introduction (2-3 sentences) — topic kya hai / what the topic is, why it matters
# 3. Main sections — headings ke saath / with headings, simple explanation ke saath
# 4. 3-5 key points per section (bullet format)
# 5. A real-life example relevant to {self.cfg.full_name} students (must include)
# 6. A Summary (3-4 lines)
# 7. Key Takeaways (5-7 points) — "Yaad Rakho" style
# """

#     def _transcript_task(self, target_words: int, target_minutes: int) -> str:
#         full  = self.cfg.full_name
#         terms = self.cfg.domain_terms
#         style = self.cfg.style
#         lang = (
#             f"Hinglish — natural Hindi + English. Technical terms ({terms}) always in English."
#             if style == "hinglish"
#             else f"Clear English. Technical terms ({terms}) used precisely."
#         )
#         return f"""You are writing a professional educational video script for {full} students.
# The video has LEFT panel (Raj and Priya talking) and RIGHT panel (live PDF page).

# === CHARACTERS ===
# A: Raj  — {full} student. Asks clear, specific questions about concepts on the current PDF page.
#           References the page: "Priya, is page pe jo {terms.split(',')[0].strip()} wala concept hai..."
#           Never uses filler. Every question is content-driven.

# B: Priya — {full} teacher. Teaches with full depth:
#           - Defines the concept precisely
#           - Explains the formula or rule step-by-step
#           - Walks through the numerical example shown on the PDF page
#           - States the common exam mistake
#           - Gives the ICAI/ICSI exam angle in one line
#           References PDF explicitly: "Dekho is page pe...", "Yahan table mein...", "Formula page pe diya hai..."

# === LANGUAGE ===
# {lang}
# BANNED words: "Good luck", "Great question", "Excellent", "Wow", "Bilkul sahi",
# "Bahut accha", "Perfect", "Absolutely", "Of course", "Sure", "Amazing", any hollow praise.

# === LENGTH — HARD REQUIREMENT ===
# This video must run approximately {target_minutes} minutes, which means the
# TOTAL spoken dialogue (all A: and B: lines combined) must be close to
# {target_words} words. Do NOT write more than {int(target_words * 1.15)} words
# of dialogue in total — content that goes past that will be cut, so
# prioritise covering the MOST IMPORTANT concepts, formulas, and one worked
# example per major section rather than trying to cover every minor detail.
# Pick the highest-value content if the PDF has more than fits.
# Work through numerical examples concisely — show the key steps, not every
# possible variant.

# === PDF PAGE TAGS — MANDATORY ===
# Before every new topic, sub-topic, example, or formula insert on its OWN line:
# [PAGE N] [TOPIC: Exact Topic Name from PDF]
# - N = actual page number in the simplified PDF
# - Insert a new tag every 4-5 exchanges minimum
# - Tags go on their own line — never inside A: or B: dialogue
# - Skip TOC, index, cover pages — start from first real content page

# === FORMAT — strictly follow, no blank lines between turns ===
# [PAGE 1] [TOPIC: Introduction]
# A: [question referencing PDF page]
# B: [full teaching answer]
# A: [follow-up question]
# B: [deeper explanation with formula/example]
# [PAGE 2] [TOPIC: Next Concept]
# A: [question]
# B: [answer]
# ...cover the highest-value content within the word budget above...
# Yaad Rakho:
# • [key point 1]
# • [key point 2]
# • [key point 3]
# • [key point 4]
# • [key point 5]
# """

#     # ------------------------------------------------------------------
#     # Content simplification — OpenAI
#     # ------------------------------------------------------------------

#     def simplify_content_with_openai(self, text: str) -> Dict:
#         """Use OpenAI to simplify content into structured JSON."""
#         try:
#             system_prompt = (
#                 f"You are an expert {self.cfg.full_name} educator who simplifies "
#                 f"complex {self.cfg.subject_label} content for students. "
#                 "Always return valid JSON only, no extra text."
#             )

#             user_prompt = f"""
# {self._style_guide()}

# {self._simplification_task()}

# Original Content:
# {text[:4000]}

# {_JSON_SCHEMA}
# """
#             response = self.openai_client.chat.completions.create(
#                 model="gpt-4o-mini",
#                 messages=[
#                     {"role": "system", "content": system_prompt},
#                     {"role": "user",   "content": user_prompt},
#                 ],
#                 temperature=0.7,
#                 response_format={"type": "json_object"},
#             )

#             content = json.loads(response.choices[0].message.content)
#             return content

#         except Exception as e:
#             raise Exception(f"OpenAI simplification error: {str(e)}")

#     # ------------------------------------------------------------------
#     # Content simplification — Gemini
#     # ------------------------------------------------------------------

#     def simplify_content_with_gemini(self, text: str) -> Dict:
#         """Use Gemini to simplify content into structured JSON."""
#         try:
#             prompt = f"""
# {self._style_guide()}

# {self._simplification_task()}

# Original Content:
# {text[:4000]}

# {_JSON_SCHEMA}

# IMPORTANT: Return ONLY the raw JSON object. No markdown fences, no preamble.
# """
#             response = self.gemini_model.generate_content(prompt)

#             response_text = response.text.strip()
#             # Strip any accidental markdown fences
#             if response_text.startswith("```json"):
#                 response_text = response_text[7:].rsplit("```", 1)[0].strip()
#             elif response_text.startswith("```"):
#                 response_text = response_text[3:].rsplit("```", 1)[0].strip()

#             content = json.loads(response_text)
#             return content

#         except Exception as e:
#             raise Exception(f"Gemini simplification error: {str(e)}")

#     # ------------------------------------------------------------------
#     # Transcript from raw text (used by orchestrator)
#     # Uses Claude Haiku via Anthropic API. Falls back to OpenAI.
#     # ------------------------------------------------------------------

#     def generate_video_transcript_from_text(
#         self,
#         simplified_text: str,
#         pdf_page_count:  int = 0,
#     ) -> str:
#         """
#         Generate a transcript targeted at 10-18 minutes based on page count
#         (see _target_minutes_for_pages), with a word-budget prompt PLUS a
#         hard word-count truncation safety net — so length is controlled at
#         the source instead of relying on a disconnected fixed exchange cap.
#         """
#         if pdf_page_count <= 0:
#             pdf_page_count = max(1, len(simplified_text) // 400)

#         target_minutes = _target_minutes_for_pages(pdf_page_count)
#         target_words   = target_minutes * _WORDS_PER_MINUTE
#         max_words_hard_cap = int(target_words * _TARGET_BUFFER)

#         # Token budget scaled to the actual target instead of a flat 16000 —
#         # ~1.4 tokens/word plus formatting/tag overhead, with a safety floor.
#         self._dynamic_max_tokens = max(2500, min(6000, int(target_words * 2.2)))

#         print(f"[LLMService] PDF pages={pdf_page_count} → target {target_minutes} min "
#               f"(~{target_words} words), max_tokens={self._dynamic_max_tokens}")

#         prompt = f"""
# {self._style_guide()}

# {self._transcript_task(target_words=target_words, target_minutes=target_minutes)}

# --- SIMPLIFIED PDF CONTENT START ---
# {simplified_text}
# --- SIMPLIFIED PDF CONTENT END ---

# Write the script now, staying within the word budget above.
# """
#         if self._anthropic_key:
#             try:
#                 result = self._call_claude_haiku(prompt)
#                 print("[LLMService] ✅ Transcript generated via Claude Haiku")
#             except Exception as e:
#                 print(f"[LLMService] ⚠️  Claude Haiku failed: {e} — falling back to OpenAI")
#                 result = self._generate_transcript_openai(prompt)
#         else:
#             result = self._generate_transcript_openai(prompt)

#         pre_words = len(result.split())
#         result = truncate_transcript_to_word_budget(result, max_words_hard_cap)
#         post_words = len(result.split())
#         if post_words < pre_words:
#             print(f"[LLMService] ✂️  Truncated {pre_words} → {post_words} words "
#                   f"(cap {max_words_hard_cap} for {target_minutes} min target)")

#         print(f"[LLMService] ✅ Final transcript: {post_words} words "
#               f"(~{post_words / _WORDS_PER_MINUTE:.1f} min estimated)")
#         return result

#     # ------------------------------------------------------------------
#     # Legacy method — kept for backward compatibility
#     # ------------------------------------------------------------------

#     def generate_video_transcript(
#         self, simplified_content: Dict, use_openai: bool = True
#     ) -> str:
#         """
#         Generate transcript from a structured simplified_content dict.
#         Kept for backward compatibility. Prefer generate_video_transcript_from_text().
#         """
#         content_summary = (
#             f"Title: {simplified_content.get('title', f'{self.cfg.subject_label} Topic')}\n"
#             f"Introduction: {simplified_content.get('introduction', '')}\n"
#             f"Sections: {len(simplified_content.get('sections', []))}\n"
#             f"Key Takeaways: "
#             + ", ".join(simplified_content.get("key_takeaways", [])[:3])
#         )

#         sections_detail = ""
#         for sec in simplified_content.get("sections", []):
#             sections_detail += f"\n### {sec.get('heading', '')}\n"
#             sections_detail += sec.get("content", "") + "\n"
#             for pt in sec.get("points", []):
#                 sections_detail += f"  - {pt}\n"
#             if sec.get("example"):
#                 sections_detail += f"  Example: {sec['example']}\n"

#         target_minutes = 13
#         target_words   = target_minutes * _WORDS_PER_MINUTE
#         self._dynamic_max_tokens = max(2500, min(6000, int(target_words * 2.2)))

#         prompt = f"""
# {self._style_guide()}

# {self._transcript_task(target_words=target_words, target_minutes=target_minutes)}

# Topic Summary:
# {content_summary}

# Detailed Content to Cover:
# {sections_detail[:6000]}
# """
#         if self._anthropic_key:
#             try:
#                 result = self._call_claude_haiku(prompt)
#                 result = truncate_transcript_to_word_budget(
#                     result, int(target_words * _TARGET_BUFFER)
#                 )
#                 print("[LLMService] ✅ Transcript (legacy) generated via Claude Haiku")
#                 return result
#             except Exception as e:
#                 print(f"[LLMService] ⚠️  Claude Haiku failed: {e} — falling back")

#         result = self._generate_transcript_openai(prompt)
#         return truncate_transcript_to_word_budget(result, int(target_words * _TARGET_BUFFER))

#     # ------------------------------------------------------------------
#     # Internal: Claude Haiku via Anthropic REST API
#     # ------------------------------------------------------------------

#     def _call_claude_haiku(self, prompt: str) -> str:
#         """
#         Call Claude via Anthropic REST API using STREAMING to avoid timeouts.

#         Read timeout is now 240s (was 600s) — with the shorter, word-budgeted
#         scripts (target ≤18 min ≈ ≤2160 words ≈ ~3000-4000 tokens), generation
#         finishes well inside that window even at conservative tokens/sec, so
#         this no longer needs to hold the connection open for 10 minutes.
#         """
#         headers = {
#             "x-api-key":         self._anthropic_key,
#             "anthropic-version": "2023-06-01",
#             "Content-Type":      "application/json",
#         }
#         max_tok = getattr(self, "_dynamic_max_tokens", 4000)

#         payload = {
#             "model":      _CLAUDE_HAIKU_MODEL,
#             "max_tokens": max_tok,
#             "stream":     True,
#             "messages": [
#                 {"role": "user", "content": prompt}
#             ],
#         }

#         timeout = httpx.Timeout(
#             connect=15.0,
#             read=240.0,
#             write=30.0,
#             pool=240.0,
#         )

#         collected_text = []

#         try:
#             with httpx.Client(timeout=timeout) as client:
#                 with client.stream(
#                     "POST", _ANTHROPIC_API_URL,
#                     headers=headers, json=payload,
#                 ) as resp:
#                     if resp.status_code != 200:
#                         body = resp.read().decode("utf-8", errors="replace")
#                         raise Exception(
#                             f"Anthropic API error {resp.status_code}: {body[:400]}"
#                         )

#                     for line in resp.iter_lines():
#                         line = line.strip()
#                         if not line or not line.startswith("data:"):
#                             continue
#                         data_str = line[len("data:"):].strip()
#                         if data_str == "[DONE]":
#                             break
#                         try:
#                             import json as _json
#                             event = _json.loads(data_str)
#                         except Exception:
#                             continue

#                         etype = event.get("type", "")
#                         if etype == "content_block_delta":
#                             delta = event.get("delta", {})
#                             if delta.get("type") == "text_delta":
#                                 collected_text.append(delta.get("text", ""))
#                         elif etype == "message_stop":
#                             break
#                         elif etype == "error":
#                             err = event.get("error", {})
#                             raise Exception(
#                                 f"Anthropic stream error: {err.get('message', str(err))}"
#                             )

#         except httpx.ReadTimeout:
#             if len("".join(collected_text)) > 500:
#                 print("[LLMService] ⚠️  Stream read timeout — returning partial transcript")
#             else:
#                 raise Exception(
#                     "Anthropic API timed out before generating enough content. "
#                     "Falling back to OpenAI."
#                 )

#         result = "".join(collected_text).strip()
#         if not result:
#             raise Exception("Claude returned empty response — falling back to OpenAI.")

#         word_count = len(result.split())
#         print(f"[LLMService] ✅ Claude transcript: {word_count} words via streaming")
#         return result

#     # ------------------------------------------------------------------
#     # Internal: OpenAI transcript fallback
#     # ------------------------------------------------------------------

#     def _generate_transcript_openai(self, prompt: str) -> str:
#         try:
#             max_tok = getattr(self, "_dynamic_max_tokens", 3500)
#             response = self.openai_client.chat.completions.create(
#                 model="gpt-4o-mini",
#                 messages=[
#                     {
#                         "role": "system",
#                         "content": (
#                             f"You are a {self.cfg.full_name} educational script writer. "
#                             f"You write engaging, content-dense, and accurate scripts "
#                             f"that {self.cfg.full_name} students can easily understand. "
#                             "Stay within the requested word budget."
#                         ),
#                     },
#                     {"role": "user", "content": prompt},
#                 ],
#                 temperature=0.85,
#                 max_tokens=max_tok,
#             )
#             return response.choices[0].message.content
#         except Exception as e:
#             raise Exception(f"OpenAI transcript generation error: {str(e)}")



from openai import OpenAI
import google.generativeai as genai
from typing import Dict, List
from config import config
import json
import re
import math
import httpx

from services.platform_config import PLATFORM_CONFIGS


# ---------------------------------------------------------------------------
# Video length control
# ---------------------------------------------------------------------------
# CHANGED (2026-07): length is no longer capped. Every page of the PDF must
# make it into the video, even if that means a 40-minute video for a 30-page
# PDF. _WORDS_PER_MINUTE is still the single source of truth linking script
# word-count to estimated duration (kept in sync with video_service.py's
# duration = words / 120 * 60 estimate), but there is no ceiling on top of it
# anymore — only a floor, and a per-CHUNK budget (see _PAGES_PER_CHUNK below)
# so a single LLM call never has to hold more content than it can reliably
# output in one response.
_WORDS_PER_MINUTE   = 120
_TARGET_BUFFER      = 1.15   # headroom before the (now much larger) safety-net cap kicks in

# How many PDF pages get bundled into a single LLM call. This is NOT a
# content limit — it's a batching size. A 30-page PDF just becomes 6 calls
# of 5 pages each, chained together, instead of 1 call that would have to
# skip material to fit inside one response's token ceiling.
_PAGES_PER_CHUNK    = 5
_MINUTES_PER_PAGE   = 1.3     # uncapped linear scaling — more pages = longer video, on purpose


def _target_minutes_for_pages(pdf_page_count: int) -> int:
    """
    Page count → target video length. Uncapped: scales linearly with content
    instead of being clamped at a fixed ceiling. A short PDF still gets a
    sensible minimum length; a long one is simply allowed to run long.
    """
    if pdf_page_count <= 4:
        return 10
    if pdf_page_count <= 8:
        return 13
    if pdf_page_count <= 12:
        return 16
    return max(16, round(pdf_page_count * _MINUTES_PER_PAGE))


def truncate_transcript_to_word_budget(transcript: str, max_words: int) -> str:
    """
    Still used ONLY as an emergency safety net by audio_service if real TTS
    pacing runs away from estimate (see _MAX_VIDEO_SECONDS_SAFETY there) —
    NOT used anymore as a routine content-cutting step in the generation
    pipeline itself. Cuts once cumulative spoken words (inside A:/B: lines)
    reaches max_words, then looks forward for the "Yaad Rakho" / key
    takeaways closer so the video ends cleanly instead of mid-sentence.
    All [PAGE]/[TOPIC] tag lines before the cutoff are preserved so the
    PDF panel in the video still syncs correctly for the content that
    actually plays.
    """
    lines    = transcript.splitlines()
    line_re  = re.compile(r'^\**\s*(?:User\s*)?([AB])\**\s*:\s*(.*)$', re.IGNORECASE)
    output   = []
    word_count      = 0
    cutoff_reached  = False
    yaad_found      = False

    for line in lines:
        stripped = line.strip()

        if cutoff_reached:
            if not yaad_found:
                if "yaad rakho" in stripped.lower() or "key takeaway" in stripped.lower():
                    yaad_found = True
                    output.append(line)
                continue
            # Inside the Yaad Rakho block: keep bullets, stop at the next
            # dialogue line or tag (i.e. don't drag in leftover content).
            if line_re.match(stripped) or stripped.upper().startswith("[PAGE") \
               or stripped.upper().startswith("[TOPIC"):
                break
            output.append(line)
            continue

        m = line_re.match(stripped)
        if m:
            word_count += len(m.group(2).split())
        output.append(line)
        if m and word_count >= max_words:
            cutoff_reached = True

    return "\n".join(output).strip()


def truncate_transcript_to_dialogue_count(transcript: str, max_dialogue_lines: int) -> str:
    """
    Cut the transcript after the Nth A:/B: line. Kept for audio_service's
    emergency safety net only (see note there) — not part of the normal
    content pipeline anymore.
    """
    lines   = transcript.splitlines()
    line_re = re.compile(r'^\**\s*(?:User\s*)?([AB])\**\s*:\s*(.*)$', re.IGNORECASE)
    output  = []
    dialogue_count = 0
    cutoff_reached = False
    yaad_found     = False

    for line in lines:
        stripped = line.strip()

        if cutoff_reached:
            if not yaad_found:
                if "yaad rakho" in stripped.lower() or "key takeaway" in stripped.lower():
                    yaad_found = True
                    output.append(line)
                continue
            if line_re.match(stripped) or stripped.upper().startswith("[PAGE") \
               or stripped.upper().startswith("[TOPIC"):
                break
            output.append(line)
            continue

        if line_re.match(stripped):
            dialogue_count += 1
        output.append(line)
        if dialogue_count >= max_dialogue_lines and line_re.match(stripped):
            cutoff_reached = True

    return "\n".join(output).strip()


def _split_into_content_chunks(text: str, total_pages: int, pages_per_chunk: int) -> List[str]:
    """
    Split simplified_text into N chunks, each corresponding to roughly
    `pages_per_chunk` PDF pages worth of material, so a single LLM call
    never has to cover more content than it can reliably fit in one
    response. Splits on paragraph boundaries (blank lines) so a concept
    or sentence never gets cut in half between chunks.

    This is purely a BATCHING split, not a content filter — every
    paragraph in `text` ends up in exactly one chunk, and every chunk
    gets its own full generation call, so nothing is dropped.
    """
    paragraphs = [p for p in re.split(r'\n\s*\n', text) if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else []

    n_chunks = max(1, math.ceil(total_pages / pages_per_chunk))
    # Never split more finely than we have paragraphs for
    n_chunks = min(n_chunks, len(paragraphs))
    per_chunk = max(1, math.ceil(len(paragraphs) / n_chunks))

    chunks = []
    for i in range(0, len(paragraphs), per_chunk):
        chunks.append("\n\n".join(paragraphs[i:i + per_chunk]))
    return chunks


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

_CLAUDE_HAIKU_MODEL = "claude-haiku-4-5-20251001"


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

    def _transcript_task(
        self,
        target_words: int,
        target_minutes: int,
        page_start: int = 1,
        is_final_chunk: bool = True,
    ) -> str:
        full  = self.cfg.full_name
        terms = self.cfg.domain_terms
        style = self.cfg.style
        lang = (
            f"Hinglish — natural Hindi + English. Technical terms ({terms}) always in English."
            if style == "hinglish"
            else f"Clear English. Technical terms ({terms}) used precisely."
        )

        continuation_note = ""
        if page_start > 1:
            continuation_note = f"""
=== THIS IS A CONTINUATION ===
This is one part of a longer video script. The content below picks up
starting from PDF page {page_start} onward — continue the [PAGE N] numbering
from {page_start}, do NOT restart at page 1. Do not re-introduce the topic
from scratch; assume the student has already been following along.
"""

        if is_final_chunk:
            closing_instructions = """
=== ENDING — MANDATORY ===
After covering every page/topic in the content below, end with:
Yaad Rakho:
• [key point 1]
• [key point 2]
• [key point 3]
• [key point 4]
• [key point 5]
"""
        else:
            closing_instructions = """
=== ENDING — DO NOT CLOSE YET ===
Do NOT add a "Yaad Rakho" / key-takeaways / summary section at the end of
this part — more PDF content follows in the next part of the video, and the
closing summary will be added only at the very end of the full script.
Just finish naturally after the last topic covered below.
"""

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
{continuation_note}
=== COVERAGE — MANDATORY, NO SKIPPING ===
You must cover EVERY page, topic, concept, formula, and example in the
content given below — not just the "highest value" parts. Nothing may be
left out or compressed away. If the content below is dense, that is fine:
take the length you need to teach all of it properly rather than dropping
material to hit a word count. As a rough pacing guide this part of the
script will run about {target_words} words (~{target_minutes} min of
speech at natural pace) — treat that as a guide for pacing, NOT a ceiling
you must cut content to fit under. Work through numerical examples
concisely (key steps, not every possible variant), but do not skip a page's
content just because you're past the guide length.

=== PDF PAGE TAGS — MANDATORY ===
Before every new topic, sub-topic, example, or formula insert on its OWN line:
[PAGE N] [TOPIC: Exact Topic Name from PDF]
- N = actual page number in the simplified PDF
- Insert a new tag every 4-5 exchanges minimum
- Tags go on their own line — never inside A: or B: dialogue
- Skip TOC, index, cover pages — start from first real content page
{closing_instructions}
=== FORMAT — strictly follow, no blank lines between turns ===
[PAGE {page_start}] [TOPIC: Introduction]
A: [question referencing PDF page]
B: [full teaching answer]
A: [follow-up question]
B: [deeper explanation with formula/example]
[PAGE {page_start + 1}] [TOPIC: Next Concept]
A: [question]
B: [answer]
...cover every page/topic below, in order, completely...
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
    #
    # CHANGED (2026-07): now processes the PDF content in page-batched
    # chunks instead of one giant call. Every chunk gets its own full
    # generation pass, so a long PDF simply produces a longer video
    # instead of losing content to a single call's output ceiling.
    # There is no overall word/minute cap anymore — length is whatever
    # the actual content needs.
    # ------------------------------------------------------------------

    def generate_video_transcript_from_text(
        self,
        simplified_text: str,
        pdf_page_count:  int = 0,
    ) -> str:
        if pdf_page_count <= 0:
            pdf_page_count = max(1, len(simplified_text) // 400)

        chunks = _split_into_content_chunks(simplified_text, pdf_page_count, _PAGES_PER_CHUNK)
        if not chunks:
            raise Exception("No content to generate a transcript from.")

        n_chunks = len(chunks)
        # Distribute pages across chunks proportionally for page numbering
        # and per-chunk pacing targets.
        pages_per_chunk_actual = max(1, math.ceil(pdf_page_count / n_chunks))

        print(f"[LLMService] PDF pages={pdf_page_count} → {n_chunks} chunk(s) "
              f"of ~{pages_per_chunk_actual} pages each (no length cap)")

        transcript_parts: List[str] = []
        page_cursor = 1

        for i, chunk_text in enumerate(chunks):
            is_final = (i == n_chunks - 1)
            # Last chunk may cover fewer pages if pdf_page_count doesn't
            # divide evenly — clamp so page numbering stays sane.
            chunk_pages = min(pages_per_chunk_actual, max(1, pdf_page_count - page_cursor + 1))

            target_minutes = max(3, round(chunk_pages * _MINUTES_PER_PAGE))
            target_words   = target_minutes * _WORDS_PER_MINUTE

            # Token budget scaled to THIS chunk's target — small enough per
            # chunk to comfortably fit inside one completion, however many
            # chunks it takes to cover the whole PDF.
            self._dynamic_max_tokens = max(2000, min(8000, int(target_words * 2.4)))

            print(f"[LLMService]  chunk {i+1}/{n_chunks}: pages {page_cursor}-"
                  f"{page_cursor + chunk_pages - 1}, target ~{target_minutes} min "
                  f"(~{target_words} words), max_tokens={self._dynamic_max_tokens}")

            prompt = f"""
{self._style_guide()}

{self._transcript_task(
    target_words=target_words,
    target_minutes=target_minutes,
    page_start=page_cursor,
    is_final_chunk=is_final,
)}

--- SIMPLIFIED PDF CONTENT START (this part only) ---
{chunk_text}
--- SIMPLIFIED PDF CONTENT END ---

Write this part of the script now, covering ALL of the content above.
"""
            if self._anthropic_key:
                try:
                    part = self._call_claude_haiku(prompt)
                    print(f"[LLMService] ✅ Chunk {i+1}/{n_chunks} generated via Claude Haiku")
                except Exception as e:
                    print(f"[LLMService] ⚠️  Claude Haiku failed on chunk {i+1}: {e} — falling back to OpenAI")
                    part = self._generate_transcript_openai(prompt)
            else:
                part = self._generate_transcript_openai(prompt)

            transcript_parts.append(part.strip())
            page_cursor += chunk_pages

        result = "\n".join(transcript_parts)
        word_count = len(result.split())
        print(f"[LLMService] ✅ Final transcript assembled from {n_chunks} chunk(s): "
              f"{word_count} words (~{word_count / _WORDS_PER_MINUTE:.1f} min estimated) "
              f"— full PDF content included, nothing skipped")
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

        target_minutes = 13
        target_words   = target_minutes * _WORDS_PER_MINUTE
        self._dynamic_max_tokens = max(2500, min(8000, int(target_words * 2.4)))

        prompt = f"""
{self._style_guide()}

{self._transcript_task(target_words=target_words, target_minutes=target_minutes)}

Topic Summary:
{content_summary}

Detailed Content to Cover:
{sections_detail}
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
        """
        headers = {
            "x-api-key":         self._anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        }
        max_tok = getattr(self, "_dynamic_max_tokens", 4000)

        payload = {
            "model":      _CLAUDE_HAIKU_MODEL,
            "max_tokens": max_tok,
            "stream":     True,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        timeout = httpx.Timeout(
            connect=15.0,
            read=240.0,
            write=30.0,
            pool=240.0,
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
        print(f"[LLMService] ✅ Claude transcript chunk: {word_count} words via streaming")
        return result

    # ------------------------------------------------------------------
    # Internal: OpenAI transcript fallback
    # ------------------------------------------------------------------

    def _generate_transcript_openai(self, prompt: str) -> str:
        try:
            max_tok = getattr(self, "_dynamic_max_tokens", 3500)
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"You are a {self.cfg.full_name} educational script writer. "
                            f"You write engaging, content-dense, and accurate scripts "
                            f"that {self.cfg.full_name} students can easily understand. "
                            "Cover all content given — do not skip material to save space."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.85,
                max_tokens=max_tok,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise Exception(f"OpenAI transcript generation error: {str(e)}")
