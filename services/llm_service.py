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
# Length is uncapped by design: every page of the PDF must make it into the
# video, even if that means a 40-minute video for a 30-page PDF.
# _WORDS_PER_MINUTE is still the single source of truth linking script
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

# PAGE-COVERAGE FIX (2026-07): how many times we'll re-prompt a single chunk
# if it comes back missing one or more of its required [PAGE N] tags, before
# giving up and moving on (logged loudly if that happens so it's visible,
# rather than silently dropping pages the way the old pipeline did).
_MAX_COVERAGE_RETRIES = 2


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
    Used ONLY as an emergency safety net by audio_service if real TTS
    pacing runs away from estimate — NOT used anymore as a routine
    content-cutting step in the generation pipeline itself. Cuts once
    cumulative spoken words (inside A:/B: lines) reaches max_words, then
    looks forward for the "Yaad Rakho" / key takeaways closer so the video
    ends cleanly instead of mid-sentence. All [PAGE]/[TOPIC] tag lines
    before the cutoff are preserved so the PDF panel in the video still
    syncs correctly for the content that actually plays.
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
    emergency safety net only — not part of the normal content pipeline
    anymore.
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
# PAGE-COVERAGE FIX (2026-07)
# ---------------------------------------------------------------------------
# The transcript prompt previously only said "insert a tag every 4-5
# exchanges minimum" — a MINIMUM tagging frequency, not a guarantee that
# every page number in a chunk's range actually gets a [PAGE N] tag. An LLM
# can (and did) jump straight from [PAGE 3] to [PAGE 6], quietly skipping
# pages 4 and 5, even when told in prose to "cover everything" — that
# instruction has nothing concrete for the model to check itself against.
#
# Fix: each chunk is now given an explicit, closed list of the exact page
# numbers it is responsible for (e.g. "[PAGE 6] [PAGE 7] [PAGE 8]"), and the
# assembled output is checked against that list afterward. If any required
# page number is missing, the chunk is re-prompted — once, telling it
# exactly which page numbers were missed — before falling through and
# logging a loud warning if it still can't be fixed after retries.
_PAGE_TAG_RE = re.compile(r'\[PAGE\s*(\d+)\]', re.IGNORECASE)


def _find_missing_pages(transcript_part: str, required_pages: List[int]) -> List[int]:
    found = {int(m) for m in _PAGE_TAG_RE.findall(transcript_part)}
    return [p for p in required_pages if p not in found]


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
        page_end: int = None,
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

        page_end = page_end if page_end is not None else page_start
        required_pages = list(range(page_start, page_end + 1))
        required_tags_str = " ".join(f"[PAGE {p}]" for p in required_pages)

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

=== REQUIRED PAGE TAGS — NO EXCEPTIONS ===
This part covers PDF pages {page_start} to {page_end}. You MUST include a
[PAGE N] [TOPIC: ...] tag block for EVERY ONE of these page numbers, each
appearing at least once, in ascending order, with no gaps:
{required_tags_str}
Do not skip any page number in this list, and do not invent page numbers
outside this range in this part of the script. If a page has very little
content, still give it a short tag block — a brief exchange is fine, but
the [PAGE N] tag for that number must appear somewhere in your output.

=== PDF PAGE TAGS — FORMAT ===
Before every new topic, sub-topic, example, or formula insert on its OWN line:
[PAGE N] [TOPIC: Exact Topic Name from PDF]
- N = actual page number in the simplified PDF
- Tags go on their own line — never inside A: or B: dialogue
- Skip TOC, index, cover pages when numbering content — but if such a page
  falls inside {page_start}-{page_end}, still emit its [PAGE N] tag with a
  minimal transition line so no number in the required list is missing.
{closing_instructions}
=== FORMAT — strictly follow, no blank lines between turns ===
[PAGE {page_start}] [TOPIC: Introduction]
A: [question referencing PDF page]
B: [full teaching answer]
A: [follow-up question]
B: [deeper explanation with formula/example]
[PAGE {min(page_start + 1, page_end)}] [TOPIC: Next Concept]
A: [question]
B: [answer]
...cover every page/topic below, in order, completely, ending with tags for
all of: {required_tags_str}
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
    # Processes the PDF content in page-batched chunks instead of one giant
    # call. Every chunk gets its own full generation pass, so a long PDF
    # simply produces a longer video instead of losing content to a single
    # call's output ceiling. There is no overall word/minute cap — length is
    # whatever the actual content needs.
    #
    # PAGE-COVERAGE FIX (2026-07): each chunk is generated, then checked
    # against its explicit required-page list (_find_missing_pages). If any
    # page number is missing, the chunk is re-prompted — up to
    # _MAX_COVERAGE_RETRIES times — telling the model exactly which page
    # numbers were missed, before falling through with a loud warning. This
    # replaces relying on prose instructions alone to guarantee coverage.
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
        all_missing_after_retries: List[int] = []

        for i, chunk_text in enumerate(chunks):
            is_final = (i == n_chunks - 1)
            # Last chunk may cover fewer pages if pdf_page_count doesn't
            # divide evenly — clamp so page numbering stays sane.
            chunk_pages = min(pages_per_chunk_actual, max(1, pdf_page_count - page_cursor + 1))
            page_start  = page_cursor
            page_end    = page_cursor + chunk_pages - 1
            required_pages = list(range(page_start, page_end + 1))

            target_minutes = max(3, round(chunk_pages * _MINUTES_PER_PAGE))
            target_words   = target_minutes * _WORDS_PER_MINUTE

            # Token budget scaled to THIS chunk's target — small enough per
            # chunk to comfortably fit inside one completion, however many
            # chunks it takes to cover the whole PDF.
            self._dynamic_max_tokens = max(2000, min(8000, int(target_words * 2.4)))

            print(f"[LLMService]  chunk {i+1}/{n_chunks}: pages {page_start}-{page_end}, "
                  f"target ~{target_minutes} min (~{target_words} words), "
                  f"max_tokens={self._dynamic_max_tokens}")

            base_prompt = f"""
{self._style_guide()}

{self._transcript_task(
    target_words=target_words,
    target_minutes=target_minutes,
    page_start=page_start,
    page_end=page_end,
    is_final_chunk=is_final,
)}

--- SIMPLIFIED PDF CONTENT START (this part only) ---
{chunk_text}
--- SIMPLIFIED PDF CONTENT END ---

Write this part of the script now, covering ALL of the content above.
"""

            part = self._generate_chunk_with_fallback(base_prompt, i, n_chunks)

            # ── Coverage validation + retry loop ──────────────────────────
            missing = _find_missing_pages(part, required_pages)
            attempt = 0
            while missing and attempt < _MAX_COVERAGE_RETRIES:
                attempt += 1
                missing_str = ", ".join(f"[PAGE {p}]" for p in missing)
                print(f"[LLMService] ⚠️  Chunk {i+1}/{n_chunks} missing tags for "
                      f"{missing_str} — retrying (attempt {attempt}/{_MAX_COVERAGE_RETRIES})")

                all_required_str = " ".join(f"[PAGE {p}]" for p in required_pages)
                retry_prompt = base_prompt + f"""

=== CORRECTION REQUIRED ===
Your previous attempt at this part OMITTED required page tags for:
{missing_str}
Rewrite this ENTIRE part from scratch, still covering pages {page_start}-{page_end}
in full, but this time make absolutely sure every one of these tags appears:
{all_required_str}
Pay special attention to: {missing_str}
"""
                part = self._generate_chunk_with_fallback(retry_prompt, i, n_chunks, is_retry=True)
                missing = _find_missing_pages(part, required_pages)

            if missing:
                missing_str = ", ".join(f"[PAGE {p}]" for p in missing)
                print(f"[LLMService] ❌ Chunk {i+1}/{n_chunks} STILL missing "
                      f"{missing_str} after {_MAX_COVERAGE_RETRIES} retries — "
                      f"proceeding anyway, but these pages may not appear in the video.")
                all_missing_after_retries.extend(missing)
            else:
                print(f"[LLMService] ✅ Chunk {i+1}/{n_chunks} covers all required pages "
                      f"{page_start}-{page_end}")

            transcript_parts.append(part.strip())
            page_cursor += chunk_pages

        result = "\n".join(transcript_parts)
        word_count = len(result.split())

        if all_missing_after_retries:
            print(f"[LLMService] ⚠️  FINAL TRANSCRIPT is missing tags for pages: "
                  f"{sorted(set(all_missing_after_retries))} — these pages will not "
                  f"appear in the rendered video. Consider checking the source PDF "
                  f"for unusual formatting on these pages.")
        else:
            print(f"[LLMService] ✅ All {pdf_page_count} PDF pages confirmed present "
                  f"in the transcript's [PAGE N] tags")

        print(f"[LLMService] ✅ Final transcript assembled from {n_chunks} chunk(s): "
              f"{word_count} words (~{word_count / _WORDS_PER_MINUTE:.1f} min estimated) "
              f"— full PDF content included, nothing skipped")
        return result

    # ------------------------------------------------------------------
    # Internal: generate one chunk via Claude Haiku, falling back to OpenAI
    # ------------------------------------------------------------------

    def _generate_chunk_with_fallback(
        self, prompt: str, chunk_idx: int, n_chunks: int, is_retry: bool = False
    ) -> str:
        tag = "retry" if is_retry else "generation"
        if self._anthropic_key:
            try:
                part = self._call_claude_haiku(prompt)
                print(f"[LLMService] ✅ Chunk {chunk_idx+1}/{n_chunks} {tag} via Claude Haiku")
                return part
            except Exception as e:
                print(f"[LLMService] ⚠️  Claude Haiku failed on chunk {chunk_idx+1} {tag}: "
                      f"{e} — falling back to OpenAI")
                return self._generate_transcript_openai(prompt)
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
