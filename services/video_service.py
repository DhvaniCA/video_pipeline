"""
video_service.py — Professional Split-Screen EdTech Video
===========================================================

Layout (1280 × 720):
  ┌──────────────────────────────────────────────────────────────┐
  │  HEADER BAR  (platform badge + topic name + page number)    │
  ├─────────────────────────┬────────────────────────────────────┤
  │                         │                                    │
  │   CHARACTER PANEL       │      PDF PAGE PANEL                │
  │   (left 640 px)         │      (right 640 px)                │
  │   • Raj  (student A)    │      • live PDF page               │
  │   • Priya (teacher B)   │      • topic label + page number   │
  │   • speech bubble       │      • highlight border (speaking) │
  │                         │                                    │
  ├─────────────────────────┴────────────────────────────────────┤
  │  FOOTER  (gold progress bar + segment counter)               │
  └──────────────────────────────────────────────────────────────┘

Key features
  - Right panel renders the actual PDF page being discussed.
  - Page changes when [PAGE X] tag appears in the transcript line.
  - Topic label updates when [TOPIC: name] tag appears.
  - LLM transcript should include these tags (see llm_service prompt).
  - PDF rendered via PyMuPDF (fitz); clean placeholder shown if unavailable.
  - Characters: Raj (male student A) and Priya (female teacher B).
  - Works for both CA and CS platforms via platform= argument.
  - 7-15 min videos supported naturally (no hard cap).

MEMORY / PERFORMANCE NOTES (2026-07 rewrite)
---------------------------------------------
The previous version rendered every frame of every segment into a PIL
Image, converted it to a NumPy array, and appended it to a Python list
BEFORE handing it to MoviePy:

    frames: List[np.ndarray] = []
    for f in range(n):
        ...
        frames.append(np.array(img))
    def make_frame(t):
        return frames[min(int(t * FPS), n - 1)]

That means the entire video lived in RAM at once. At 1280x720x3 bytes
per frame, 24 fps, a 10-minute video is ~39 GB of raw frame data alone
before MoviePy/ffmpeg overhead.

This version renders each frame ON DEMAND inside `make_frame(t)`. MoviePy
(and ffmpeg underneath it) calls `make_frame` once per output frame as it
encodes, so at any instant only ONE frame exists in memory. There is no
`frames` list anywhere in this file. Peak memory now scales with a
single frame + a small bounded PDF-page cache, not with video length.

Other efficiency changes:
  - Fonts are loaded once per (style, size) and cached (`@lru_cache`)
    instead of re-reading font files off disk on every draw call.
  - Rendered PDF pages are cached with a small bounded LRU (default 6
    pages) instead of an unbounded per-segment dict, so long videos that
    revisit earlier pages don't slowly leak memory.

PAGE-COVERAGE FIX (2026-07, follow-up)
---------------------------------------
Two bugs were causing 20-30% of PDF pages to be silently dropped from
generated videos:

  1. `_is_toc_page` used an UNANCHORED keyword search across the entire
     page body (`table of contents|contents|index|sr\.?\s*no|...`) and
     returned True on ANY match, anywhere in the page. On dense CA/CS
     content this fired constantly on completely normal content pages:
       - "Sr. No." is the most common table-column header in Indian
         accounting worksheets (journals, ledgers, depreciation
         schedules) — it is NOT a TOC signal.
       - "index" matches "cost inflation index", "price index", etc.
       - "contents" matches "contents of an invoice", "contents of the
         trial balance", etc.
     Any of these mid-page hits caused `_get_pdf_page` to silently swap
     in a DIFFERENT page (scanning forward up to 4 pages), so real
     content pages never made it into the video at all.

     FIX: the keyword check is now anchored to short heading-like lines
     near the TOP of the page only (first 3 non-empty lines, page not
     too long) — a true TOC/index page's title line, not a keyword
     appearing anywhere in body text. "sr. no" was removed from the
     keyword list entirely since it's a table-header signal, not a TOC
     signal; the existing structural dot-line/short-line heuristic
     already catches genuine TOC pages without it.

  2. There was no visibility into when a page got swapped, making this
     silent-loss bug hard to catch. `_get_pdf_page` now logs whenever the
     resolved page differs from the requested page, so any remaining
     false positives are visible in logs instead of invisible.
"""

from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont
from functools import lru_cache
from collections import OrderedDict
import numpy as np
import os
import re
import math
from typing import List, Dict, Optional

_INTER_SEGMENT_PAUSE_SEC = 0.300   # keep in sync with audio_service.py

# ---------------------------------------------------------------------------
# Font loader (cached — avoids re-reading font files every draw call)
# ---------------------------------------------------------------------------
_FONT_PATHS = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
}


@lru_cache(maxsize=64)
def _load_font(style: str = "regular", size: int = 28) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS.get(style, _FONT_PATHS["regular"]):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Text cleaning  (also strips [PAGE X] and [TOPIC:...] tags from speech)
# ---------------------------------------------------------------------------
_CLEAN_PATTERNS = [
    (re.compile(r'\*{1,3}'),                          ""),
    (re.compile(r'_{1,2}'),                           ""),
    (re.compile(r'`+'),                               ""),
    (re.compile(r'#+\s*'),                            ""),
    (re.compile(r'\\n'),                              " "),
    (re.compile(r'\n+'),                              " "),
    (re.compile(r'\s{2,}'),                           " "),
    (re.compile(r'^\s*[-•]\s*'),                      ""),
    (re.compile(r'\[PAGE\s*\d+\]',      re.IGNORECASE), ""),
    (re.compile(r'\[TOPIC:\s*[^\]]+\]', re.IGNORECASE), ""),
]


def _clean(text: str) -> str:
    for pattern, repl in _CLEAN_PATTERNS:
        text = pattern.sub(repl, text)
    return text.strip()


# ---------------------------------------------------------------------------
# Pixel-accurate text wrap
# ---------------------------------------------------------------------------
def _wrap_pixels(draw: ImageDraw.Draw, text: str,
                 font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


# ---------------------------------------------------------------------------
# PDF rendering via PyMuPDF
# ---------------------------------------------------------------------------
def _try_fitz():
    try:
        import fitz
        return fitz
    except ImportError:
        return None


# Supersampling: render at 4× then LANCZOS-downsample.
# 4× means a 600px panel is rendered internally at 2400px — razor-sharp text.
_PDF_SUPERSAMPLE = 4


def _render_pdf_page(pdf_path: str, page_num: int,
                     w: int, h: int) -> Optional[Image.Image]:
    """
    High-fidelity PDF page renderer.

    Strategy:
      1. Render the PDF page at 4× the target resolution using PyMuPDF.
         This keeps all vector text/lines crisp at the rasterisation stage.
      2. Apply mild sharpening + contrast boost on the high-res image.
      3. Downsample to EXACTLY (w × h) using LANCZOS — fills the panel
         completely with no dead whitespace, maximum readable size.
      4. Return a pure-white-background RGB image ready to paste.

    The returned image is sized EXACTLY (w, h) — callers must NOT resize
    it again or they will introduce a second-pass blur.
    """
    fitz = _try_fitz()
    if not fitz:
        return None
    try:
        from PIL import ImageEnhance, ImageFilter
        doc  = fitz.open(pdf_path)
        idx  = max(0, min(page_num - 1, len(doc) - 1))
        page = doc[idx]

        # Scale to fill the target panel exactly (no aspect-ratio crop —
        # we pad with white instead).  Then multiply by supersample factor.
        scale_x = w / page.rect.width
        scale_y = h / page.rect.height
        # Use the SMALLER scale so the full page fits, then supersample
        base_scale = min(scale_x, scale_y) * _PDF_SUPERSAMPLE

        mat = fitz.Matrix(base_scale, base_scale)
        pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
        doc.close()

        hires = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        # ── Step 1: Downsample first (LANCZOS preserves edges well) ───────
        aspect = hires.width / max(1, hires.height)
        if (w / max(1, h)) > aspect:
            dh = h
            dw = int(dh * aspect)
        else:
            dw = w
            dh = int(dw / aspect)

        # Single LANCZOS downsample from 4× hires to display size
        display = hires.resize((dw, dh), Image.LANCZOS)
        hires.close()  # free the (much larger) supersampled buffer immediately

        # ── Step 2: Sharpen/enhance AFTER downscale ────────────────────────
        # Sharpening at display resolution recovers edge detail that
        # LANCZOS softens. Doing it before downscale has almost no effect.
        display = display.filter(ImageFilter.UnsharpMask(radius=0.8, percent=160, threshold=2))
        display = ImageEnhance.Contrast(display).enhance(1.20)
        # Keep whites clean — prevent over-brightening scanned PDFs
        display = ImageEnhance.Brightness(display).enhance(1.03)

        # ── Step 3: Place on pure white canvas ────────────────────────────
        canvas = Image.new("RGB", (w, h), (255, 255, 255))
        canvas.paste(display, ((w - dw) // 2, (h - dh) // 2))
        return canvas

    except Exception as e:
        print(f"[VideoService] PDF page {page_num} render error: {e}")
        return None


# ---------------------------------------------------------------------------
# TOC / cover page detection
# ---------------------------------------------------------------------------
# FIXED (2026-07): previously this matched keywords ANYWHERE in the page
# body ("Sr. No.", "index", "contents") which falsely flagged huge numbers
# of normal content pages (accounting tables almost always have a "Sr. No."
# column; "cost inflation index" / "contents of an invoice" etc. are
# completely ordinary phrases). That caused _get_pdf_page to silently swap
# in a different page and drop real content from the video.
#
# Now: the keyword check only fires when the match is one of the first few
# lines of the page AND reads like a standalone heading (short, on its own
# line) — the way an actual "Table of Contents" or "Index" page title would
# appear. "sr. no" / "sr no" was removed entirely; it is a table-column
# header, not a TOC signal. The structural dot-line/short-line heuristic
# below (unchanged) is what actually catches genuine TOC/index pages.
_TOC_KEYWORDS = re.compile(
    r'^\s*(table\s+of\s+contents|contents|index|chapter\s+list'
    r'|विषय\s*सूची|अनुक्रमणिका)\s*$',
    re.IGNORECASE,
)


@lru_cache(maxsize=512)
def _is_toc_page(pdf_path: str, page_num: int) -> bool:
    """
    Return True if the page looks like a Table of Contents, index, or cover.

    Two independent checks, EITHER of which can flag a page:
      1. Heading check: one of the first 3 non-empty lines matches a TOC/
         index heading pattern AND the page is short (<60 lines total) —
         i.e. it reads like a genuine TOC page's title, not a keyword
         appearing incidentally deep in a content page.
      2. Structural check: TOC pages have many short lines with dot-leaders
         or trailing page numbers (e.g. "Chapter 3 ..... 42").

    Cached (per pdf_path/page_num) since this is called repeatedly while
    scanning forward for the next non-TOC page.
    """
    fitz = _try_fitz()
    if not fitz or not pdf_path:
        return False
    try:
        doc  = fitz.open(pdf_path)
        idx  = max(0, min(page_num - 1, len(doc) - 1))
        text = doc[idx].get_text("text")
        doc.close()

        if not text.strip():       # blank / image-only page
            return False

        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return False

        # ── Check 1: anchored heading match ────────────────────────────
        # Only look at the first few lines, and only trust it on pages
        # that are short enough to plausibly BE a TOC/index (a genuine TOC
        # page is short; a 2-page dense content page discussing "the index
        # number" is not going to look like this).
        if len(lines) < 60:
            for line in lines[:3]:
                if _TOC_KEYWORDS.match(line):
                    return True

        # ── Check 2: structural check (unchanged) ──────────────────────
        dot_lines   = sum(1 for l in lines if re.search(r'\.{3,}|\s{3,}\d+\s*$', l))
        short_lines = sum(1 for l in lines if len(l) < 60)
        toc_score   = (dot_lines / len(lines)) + (short_lines / len(lines))
        return toc_score > 1.2   # both ratios > 0.6 each

    except Exception:
        return False


def _pdf_page_count(pdf_path: str) -> int:
    fitz = _try_fitz()
    if not fitz or not pdf_path or not os.path.exists(pdf_path):
        return 0
    try:
        doc = fitz.open(pdf_path)
        n   = len(doc)
        doc.close()
        return n
    except Exception:
        return 0


# ===========================================================================
# VideoService
# ===========================================================================
class VideoService:

    # ── Canvas ──────────────────────────────────────────────────────────────
    W   = 1280
    H   = 720
    FPS = 24

    # ── Panel split ─────────────────────────────────────────────────────────
    HEADER_H = 64
    FOOTER_H = 36
    CHAR_W   = 640   # left half
    PDF_W    = 640   # right half

    # ── PDF page cache size ──────────────────────────────────────────────────
    # Bounded LRU: keeps memory flat even for very long videos that revisit
    # earlier pages, instead of caching every rendered page forever.
    PDF_CACHE_MAX_PAGES = 6

    # ── Brand palette ────────────────────────────────────────────────────────
    BRAND_DARK  = (20,  52, 105)
    BRAND_MID   = (35,  88, 162)
    BRAND_LIGHT = (58, 128, 208)
    BRAND_GOLD  = (210, 158,  28)
    WHITE       = (255, 255, 255)
    OFF_WHITE   = (247, 248, 252)
    LIGHT_GRAY  = (228, 230, 238)
    MED_GRAY    = (175, 180, 192)
    TEXT_DARK   = ( 22,  28,  40)
    BG_WALL     = (240, 242, 250)
    BG_FLOOR    = (222, 226, 238)

    # ── Characters ──────────────────────────────────────────────────────────
    # Raj   = student (A) — left side, slightly lower (seated student posture)
    # Priya = teacher (B) — right side, slightly higher (standing, authoritative)
    CHAR_A = dict(
        name="Raj", role="Student", gender="male",
        skin_base=(255, 220, 177), skin_shadow=(232, 198, 155),
        hair_color=(48, 33, 18), facial_hair=(38, 28, 16),
        eye_color=(88, 62, 38),
        shirt_color=(240, 245, 255),   # light blue shirt — student casual
        tie_color=(20, 52, 128),
        bubble_bg=(255,255,255), bubble_border=(20,52,128),
        bubble_header_bg=(20,52,128), bubble_header_text=(255,255,255),
        bubble_body_text=(22,28,40), label_color=(20,52,128),
        side="left",
        cx=155,   # left position
        cy=445,   # slightly lower — student seated feel
        scale=0.88,  # slightly smaller than teacher
    )

    CHAR_B = dict(
        name="Priya", role="Teacher", gender="female",
        skin_base=(255, 218, 185), skin_shadow=(238, 200, 168),
        hair_color=(68, 43, 22),
        eye_color=(62, 92, 122),
        shirt_color=(255, 255, 255),   # white formal shirt — teacher professional
        accessory_color=(182, 42, 72),
        bubble_bg=(255,255,255), bubble_border=(108,62,152),
        bubble_header_bg=(108,62,152), bubble_header_text=(255,255,255),
        bubble_body_text=(22,28,40), label_color=(108,62,152),
        side="right",
        cx=490,   # right position, closer to centre
        cy=418,   # higher — teacher standing, authoritative
        scale=1.0,  # full size
    )

    # ── Bubble layout ────────────────────────────────────────────────────────
    BUBBLE_W     = 590
    BUBBLE_PAD   = 20
    HEADER_BH    = 50
    LINE_H       = 33
    FONT_BODY_SZ = 22
    MAX_LINES    = 6

    # ────────────────────────────────────────────────────────────────────────
    def __init__(self):
        self._pdf_path:  Optional[str] = None
        self._pdf_pages: int           = 0
        # Bounded LRU cache: OrderedDict of {page_num: Optional[Image.Image]}
        self._pdf_cache: "OrderedDict[int, Optional[Image.Image]]" = OrderedDict()
        self._page_timeline: List[Dict] = []

    # =========================================================================
    # Public API
    # =========================================================================

    def create_animated_video_from_transcript(
        self,
        transcript:        str,
        output_path:       str,
        audio_path:        Optional[str]        = None,
        segment_durations: Optional[List[float]] = None,
        pdf_path:          Optional[str]        = None,
        platform:          str                  = "ca",
        subject_label:     str                  = "",
    ) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        self._pdf_path  = pdf_path
        self._pdf_pages = _pdf_page_count(pdf_path) if pdf_path else 0
        self._pdf_cache.clear()

        if not subject_label:
            subject_label = "CA" if platform == "ca" else "CS"
        platform_full = (
            "Chartered Accountancy" if platform == "ca" else "Company Secretary"
        )

        segments = self._parse_transcript(transcript)
        if not segments:
            raise Exception("No dialogue segments found in transcript.")

        if segment_durations and len(segment_durations) == len(segments):
            print("[VideoService] ✅ Using real audio durations")
            for i, seg in enumerate(segments):
                seg["duration"]  = segment_durations[i]
                seg["speak_end"] = max(0.1, segment_durations[i] - _INTER_SEGMENT_PAUSE_SEC)
        else:
            print("[VideoService] ⚠️  Estimating durations")
            for seg in segments:
                seg["speak_end"] = seg["duration"] * 0.90

        total_segs = len(segments)

        # Rebuild the page timeline now that REAL durations are known
        # (the first parse used word-count estimates).
        self._rebuild_page_timeline(segments)
        total_len_min = (self._page_timeline[-1]["t_end"] / 60) if self._page_timeline else 0
        print(f"[VideoService] Timeline rebuilt: {len(self._page_timeline)} page blocks, "
              f"total {total_len_min:.1f} min")

        # Coverage sanity check — log (don't fail) if pages referenced in the
        # transcript don't span the full PDF, so gaps are visible in logs.
        if self._pdf_pages > 0 and self._page_timeline:
            referenced_pages = {block["page"] for block in self._page_timeline}
            missing = sorted(set(range(1, self._pdf_pages + 1)) - referenced_pages)
            if missing:
                print(f"[VideoService] ⚠️  Transcript does not reference "
                      f"{len(missing)}/{self._pdf_pages} PDF page(s): {missing}")

        clips    = []
        global_t = 0.0
        for idx, seg in enumerate(segments):
            char  = self.CHAR_A if seg["is_a"] else self.CHAR_B
            other = self.CHAR_B if seg["is_a"] else self.CHAR_A
            clips.append(
                self._build_clip(
                    text           = seg["text"],
                    speaker        = char,
                    listener       = other,
                    duration       = seg["duration"],
                    speak_end      = seg["speak_end"],
                    seg_index      = idx,
                    total_segs     = total_segs,
                    platform_full  = platform_full,
                    subject_label  = subject_label,
                    global_t_start = global_t,
                )
            )
            global_t += seg["duration"]

        video = concatenate_videoclips(clips, method="compose")

        if audio_path and os.path.exists(audio_path):
            audio = AudioFileClip(audio_path)
            video = (
                video.loop(duration=audio.duration)
                if audio.duration > video.duration
                else video.subclip(0, audio.duration)
            )
            video = video.set_audio(audio)

        video.write_videofile(
            output_path, fps=self.FPS,
            codec="libx264", audio_codec="aac",
            preset="medium", logger=None,
        )
        video.close()
        self._pdf_cache.clear()
        return output_path

    def create_simple_animated_video(
        self, segments: List[Dict], output_path: str,
        duration_per_segment: float = 5.0,
    ) -> str:
        lines = [f"{'A' if i%2==0 else 'B'}: {s.get('text','')}" for i, s in enumerate(segments)]
        return self.create_animated_video_from_transcript("\n".join(lines), output_path)

    # =========================================================================
    # Clip builder — LAZY. Renders one frame at a time on demand; never
    # materializes a list of frames for the whole segment.
    # =========================================================================

    def _build_clip(
        self,
        text: str, speaker: dict, listener: dict,
        duration: float, speak_end: float,
        seg_index: int, total_segs: int,
        platform_full: str, subject_label: str,
        global_t_start: float = 0.0,
    ) -> VideoClip:
        """
        Build one dialogue segment as a lazily-evaluated VideoClip.

        Nothing here allocates per-frame storage. `make_frame(t_local)` is
        handed to MoviePy, which calls it exactly once per output frame at
        encode time — so only a single PIL Image / NumPy array exists in
        memory at any given moment, regardless of segment length.
        """
        FPS = self.FPS

        def make_frame(t_local: float) -> np.ndarray:
            global_t    = global_t_start + t_local
            is_speaking = t_local < speak_end
            text_prog   = (t_local / speak_end) if (is_speaking and speak_end > 0) else 1.0
            progress    = (
                (global_t_start / max(1, global_t_start + duration))
                if total_segs == 1
                else (seg_index + (t_local / duration if duration > 0 else 1.0)) / total_segs
            )

            cur_page, cur_topic = self._page_at(global_t)
            pdf_img = self._get_pdf_page(cur_page)

            # Page-turn flash: 0.35s white overlay measured from the start
            # of the current page/topic block in the global timeline.
            flash_dur    = 0.35
            t_into_block = global_t
            for block in self._page_timeline:
                if block["page"] == cur_page and block["topic"] == cur_topic:
                    t_into_block = global_t - block["t_start"]
                    break
            flash_alpha = max(0.0, 1.0 - t_into_block / flash_dur) if t_into_block < flash_dur else 0.0

            img  = Image.new("RGB", (self.W, self.H))
            draw = ImageDraw.Draw(img)

            self._draw_char_panel(draw, t_local)
            self._draw_character(draw, listener, t_local, speaking=False)
            self._draw_character(draw, speaker,  t_local, speaking=is_speaking)
            self._draw_bubble(draw, speaker, text, text_prog, t_local, is_speaking)
            self._draw_pdf_panel(
                img, draw, pdf_img, cur_page, cur_topic,
                t_local, is_speaking, flash_alpha,
            )
            draw.line(
                [(self.CHAR_W, self.HEADER_H), (self.CHAR_W, self.H - self.FOOTER_H)],
                fill=self.BRAND_MID, width=3,
            )
            self._draw_header(draw, platform_full, subject_label, cur_topic, cur_page)
            self._draw_footer(draw, progress, seg_index, total_segs)

            return np.array(img)

        return VideoClip(make_frame, duration=duration).set_fps(FPS)

    # =========================================================================
    # Header bar
    # =========================================================================

    def _draw_header(
        self, draw: ImageDraw.Draw,
        platform_full: str, subject_label: str,
        topic_name: str, page_number: int,
    ):
        W, Hh = self.W, self.HEADER_H
        draw.rectangle([0, 0, W, Hh], fill=self.BRAND_DARK)

        # Platform badge (gold)
        badge_txt = f" {subject_label} Tutor "
        fn_b      = _load_font("bold", 21)
        bb        = draw.textbbox((0, 0), badge_txt, font=fn_b)
        bw        = bb[2] - bb[0] + 14
        bh        = bb[3] - bb[1] + 8
        bx, by_b  = 14, (Hh - bh) // 2
        draw.rounded_rectangle([bx, by_b, bx+bw, by_b+bh], radius=5, fill=self.BRAND_GOLD)
        draw.text((bx+7, by_b+4), badge_txt.strip(), fill=self.BRAND_DARK, font=fn_b)

        # Topic (centred)
        fn_t  = _load_font("bold", 21)
        topic = topic_name[:55] + "…" if len(topic_name) > 55 else topic_name
        tb    = draw.textbbox((0, 0), topic, font=fn_t)
        draw.text(
            ((W - (tb[2]-tb[0])) // 2, (Hh - (tb[3]-tb[1])) // 2),
            topic, fill=self.WHITE, font=fn_t,
        )

        # Page indicator (right)
        if page_number > 0:
            fn_p  = _load_font("regular", 18)
            pt    = f"Page {page_number}"
            ptb   = draw.textbbox((0, 0), pt, font=fn_p)
            draw.text(
                (W - (ptb[2]-ptb[0]) - 18, (Hh - (ptb[3]-ptb[1])) // 2),
                pt, fill=self.BRAND_GOLD, font=fn_p,
            )

        draw.line([(0, Hh-2), (W, Hh-2)], fill=self.BRAND_LIGHT, width=2)

    # =========================================================================
    # Footer bar
    # =========================================================================

    def _draw_footer(
        self, draw: ImageDraw.Draw,
        progress: float, seg_index: int, total_segs: int,
    ):
        W, H, Fh = self.W, self.H, self.FOOTER_H
        fy = H - Fh
        draw.rectangle([0, fy, W, H], fill=(16, 40, 84))
        draw.line([(0, fy), (W, fy)], fill=self.BRAND_LIGHT, width=2)

        bx, by_p = 16, fy + 12
        bw, bh   = W - 32, 11
        draw.rounded_rectangle([bx, by_p, bx+bw, by_p+bh], radius=5, fill=(38, 62, 118))
        filled = int(bw * min(1.0, progress))
        if filled > 5:
            draw.rounded_rectangle([bx, by_p, bx+filled, by_p+bh], radius=5, fill=self.BRAND_GOLD)

        fn_c   = _load_font("regular", 14)
        ct     = f"{seg_index+1} / {total_segs}"
        ctb    = draw.textbbox((0, 0), ct, font=fn_c)
        draw.text((W - (ctb[2]-ctb[0]) - 10, fy + 7), ct, fill=self.MED_GRAY, font=fn_c)

    # =========================================================================
    # Character panel background (left 640 px)
    # =========================================================================

    def _draw_char_panel(self, draw: ImageDraw.Draw, t: float):
        cw      = self.CHAR_W
        hh, fh  = self.HEADER_H, self.FOOTER_H
        body_h  = self.H - hh - fh
        floor_y = hh + int(body_h * 0.68)

        draw.rectangle([0, hh, cw, self.H - fh], fill=self.BG_WALL)
        draw.rectangle([0, floor_y, cw, self.H - fh], fill=self.BG_FLOOR)
        draw.line([(0, floor_y), (cw, floor_y)], fill=(208, 212, 228), width=2)

        # Mini whiteboard
        wb_w, wb_h = 260, 118
        wb_x = (cw - wb_w) // 2
        wb_y = hh + 14
        draw.rounded_rectangle([wb_x-4, wb_y-4, wb_x+wb_w+4, wb_y+wb_h+4],
                                radius=5, fill=(155, 160, 172))
        draw.rounded_rectangle([wb_x, wb_y, wb_x+wb_w, wb_y+wb_h],
                                radius=4, fill=(252, 252, 255))
        fn_wb = _load_font("bold", 16)
        draw.text((wb_x+12, wb_y+9), "Live Session", fill=self.BRAND_MID, font=fn_wb)
        for i in range(3):
            ly = wb_y + 38 + i * 28
            draw.line([(wb_x+14, ly), (wb_x+wb_w-14, ly)], fill=(232, 236, 248), width=1)

    # =========================================================================
    # PDF panel (right 640 px)
    # =========================================================================

    def _draw_pdf_panel(
        self, img: Image.Image, draw: ImageDraw.Draw,
        pdf_img: Optional[Image.Image],
        page_number: int, topic_name: str,
        t: float, is_speaking: bool,
        flash_alpha: float = 0.0,
    ):
        """
        Right panel — renders the PDF page with maximum clarity.

        Layout within right 640px:
          top    : HEADER_H  (shared header already drawn)
          middle : PDF page  (fills all available height minus bottom strip)
          bottom : 40px info strip (topic + page number)

        The pdf_img from _get_pdf_page is already rendered at the correct
        size — it is pasted directly with NO secondary resize to prevent blur.
        Only a thin shadow (2px offset) and a 1px border are drawn.
        """
        px   = self.CHAR_W          # 640 — left edge of right panel
        hh   = self.HEADER_H        # 64
        fh   = self.FOOTER_H        # 36
        pw   = self.PDF_W           # 640
        LABEL_H = 40                # bottom info strip

        # ── Panel background ──────────────────────────────────────────────
        draw.rectangle([px, hh, px + pw, self.H - fh], fill=(245, 246, 250))

        if pdf_img is not None:
            dw, dh = pdf_img.width, pdf_img.height   # already correct size — NO resize

            # Centre within the panel (should already be centred by _render_pdf_page)
            px_off = px + (pw - dw) // 2
            py_off = hh + ((self.H - fh - hh - LABEL_H - dh) // 2)
            py_off = max(hh + 2, py_off)   # never overlap header

            # 2px drop shadow — thin, does not eat into PDF area
            draw.rectangle(
                [px_off + 2, py_off + 2, px_off + dw + 2, py_off + dh + 2],
                fill=(185, 188, 200),
            )
            # White background card (for PDFs with transparent/coloured bg)
            draw.rectangle(
                [px_off, py_off, px_off + dw, py_off + dh],
                fill=(255, 255, 255),
            )
            # Paste the high-res PDF page — THIS is the actual content
            img.paste(pdf_img, (px_off, py_off))

            # Border — blue pulse when speaking, light gray when silent
            brd_col = self.BRAND_LIGHT if is_speaking else (200, 204, 218)
            brd_w   = 2 if is_speaking else 1
            draw.rectangle(
                [px_off - brd_w, py_off - brd_w,
                 px_off + dw + brd_w, py_off + dh + brd_w],
                outline=brd_col, width=brd_w,
            )

            # Page-turn flash overlay (white fade, 0.3 s)
            if flash_alpha > 0.02:
                ov = Image.new("RGBA", (dw, dh),
                               (255, 255, 255, int(flash_alpha * 220)))
                img.paste(ov, (px_off, py_off), mask=ov)
        else:
            self._draw_pdf_placeholder(
                draw, px, hh, pw,
                self.H - hh - fh - LABEL_H,
                page_number, topic_name,
            )

        # ── Bottom info strip ─────────────────────────────────────────────
        ly = self.H - fh - LABEL_H
        draw.rectangle([px, ly, px + pw, self.H - fh], fill=(18, 38, 82))
        draw.line([(px, ly), (px + pw, ly)], fill=self.BRAND_LIGHT, width=2)

        fn_lbl = _load_font("bold",    15)
        fn_pg  = _load_font("bold",    14)

        ts = topic_name[:50] + "…" if len(topic_name) > 50 else topic_name
        draw.text((px + 10, ly + 11), ts, fill=(255, 255, 255), font=fn_lbl)

        if page_number > 0:
            pg  = f"Pg {page_number}"
            pgb = draw.textbbox((0, 0), pg, font=fn_pg)
            pw_ = (pgb[2] - pgb[0]) + 14
            ph_ = (pgb[3] - pgb[1]) + 8
            px_ = px + pw - pw_ - 8
            py_ = ly + (LABEL_H - ph_) // 2
            draw.rounded_rectangle([px_, py_, px_ + pw_, py_ + ph_],
                                    radius=4, fill=self.BRAND_GOLD)
            draw.text((px_ + 7, py_ + 4), pg,
                      fill=self.BRAND_DARK, font=fn_pg)


    def _draw_pdf_placeholder(
        self, draw: ImageDraw.Draw,
        px: int, hh: int, pw: int, ph: int,
        page_num: int, topic: str,
    ):
        cx_p = px + pw // 2
        cy_p = hh + ph // 2
        cw2, ch2 = pw - 48, 200
        cl  = px + 24
        ct  = cy_p - ch2 // 2
        draw.rounded_rectangle([cl, ct, cl+cw2, ct+ch2],
                                radius=10, fill=self.WHITE,
                                outline=self.BRAND_MID, width=2)
        ic_x, ic_y = cx_p - 25, ct + 20
        draw.rounded_rectangle([ic_x, ic_y, ic_x+50, ic_y+62],
                                radius=4, fill=(228,234,248), outline=self.BRAND_LIGHT, width=2)
        for i in range(4):
            draw.line([(ic_x+7, ic_y+12+i*11), (ic_x+43, ic_y+12+i*11)],
                      fill=self.BRAND_LIGHT, width=2)
        fn_t = _load_font("bold", 18)
        fn_d = _load_font("regular", 15)
        ts   = topic[:34] + "…" if len(topic) > 34 else topic
        tb   = draw.textbbox((0, 0), ts, font=fn_t)
        draw.text((cx_p-(tb[2]-tb[0])//2, ct+98), ts, fill=self.BRAND_DARK, font=fn_t)
        if page_num > 0:
            pg  = f"Page {page_num}"
            pgb = draw.textbbox((0, 0), pg, font=fn_d)
            draw.text((cx_p-(pgb[2]-pgb[0])//2, ct+130), pg, fill=self.BRAND_MID, font=fn_d)
        nt  = "Refer to your Simplified PDF"
        ntb = draw.textbbox((0, 0), nt, font=fn_d)
        draw.text((cx_p-(ntb[2]-ntb[0])//2, ct+158), nt, fill=self.MED_GRAY, font=fn_d)

    def _get_pdf_page(self, page_number: int) -> Optional[Image.Image]:
        """
        Return a fully rendered PIL Image sized to exactly fit the PDF panel.

        Render target = the full usable area of the right panel:
          width  = PDF_W  (640 px) — zero side padding, maximum page width
          height = H - HEADER_H - FOOTER_H - LABEL_H (40 px bottom strip)

        _render_pdf_page uses 4× supersampling then LANCZOS-downscales to
        exactly this size — the returned image must NEVER be resized again.
        TOC/index/cover pages are skipped automatically.

        Cache is a bounded LRU (PDF_CACHE_MAX_PAGES). This keeps memory flat
        even for very long videos that jump between many pages, instead of
        an unbounded dict that only ever grows.

        NOTE (2026-07 fix): resolution is now logged whenever the page that
        actually gets rendered differs from the page that was requested, so
        any TOC-detector false positive (or genuine skip) is visible in
        logs instead of silently dropping content from the video.
        """
        if not self._pdf_path or page_number < 1:
            return None

        # Skip TOC pages — find the next real content page
        resolved = page_number
        for candidate in range(page_number, min(page_number + 4, self._pdf_pages + 1)):
            if not _is_toc_page(self._pdf_path, candidate):
                resolved = candidate
                break

        if resolved != page_number:
            print(f"[VideoService] ⚠️  Page {page_number} flagged as TOC-like — "
                  f"using page {resolved} instead")

        if resolved in self._pdf_cache:
            self._pdf_cache.move_to_end(resolved)   # mark as recently used
            return self._pdf_cache[resolved]

        # Exact panel dimensions — no padding taken out here,
        # _draw_pdf_panel handles its own 4px inset shadow margin
        render_w = self.PDF_W - 8        # 632 px — fills panel with 4px margin each side
        render_h = self.H - self.HEADER_H - self.FOOTER_H - 40  # 600 px usable height
        rendered = _render_pdf_page(self._pdf_path, resolved, render_w, render_h)

        self._pdf_cache[resolved] = rendered
        self._pdf_cache.move_to_end(resolved)
        while len(self._pdf_cache) > self.PDF_CACHE_MAX_PAGES:
            self._pdf_cache.popitem(last=False)   # evict least-recently-used

        return rendered

    # =========================================================================
    # Character drawing
    # =========================================================================

    def _draw_character(self, draw: ImageDraw.Draw, char: dict, t: float, speaking: bool):
        cx, cy = char["cx"], char["cy"]
        scale  = char.get("scale", 1.0)
        cy    += int(math.sin(t * 2.5) * (3 if speaking else 1))

        # Shadow ellipse — larger for teacher (scale=1.0), smaller for student
        sw = int(56 * scale)
        draw.ellipse([cx-sw, cy+118, cx+sw, cy+130], fill=(188, 190, 200))

        if char.get("gender") == "male":
            self._draw_male(draw, cx, cy, char, t, speaking, scale)
        else:
            self._draw_female(draw, cx, cy, char, t, speaking, scale)

        # Name label with role badge
        fn_name = _load_font("bold", 20)
        fn_role = _load_font("regular", 14)

        name = char["name"]
        role = char.get("role", "")

        nb   = draw.textbbox((0, 0), name, font=fn_name)
        nw   = nb[2] - nb[0]

        # Role badge background pill
        role_txt = f"  {role}  "
        rb       = draw.textbbox((0, 0), role_txt, font=fn_role)
        rw       = rb[2] - rb[0]
        rh       = rb[3] - rb[1] + 6
        badge_col = (20, 52, 128) if char["side"] == "left" else (108, 62, 152)

        label_y = cy + 136
        # Role pill
        draw.rounded_rectangle(
            [cx - rw//2 - 2, label_y, cx + rw//2 + 2, label_y + rh],
            radius=4, fill=badge_col,
        )
        draw.text((cx - rw//2, label_y + 3), role_txt.strip(),
                  fill=(255, 255, 255), font=fn_role)

        # Name below pill
        draw.text((cx - nw//2, label_y + rh + 2), name,
                  fill=char["label_color"], font=fn_name)

    def _s(self, cx: int, cy: int, dx: int, dy: int, scale: float) -> tuple:
        """Scale a point (cx+dx, cy+dy) around (cx, cy)."""
        return (cx + int(dx * scale), cy + int(dy * scale))

    def _sr(self, cx, cy, x1, y1, x2, y2, scale):
        """Scale a rectangle around character centre (cx, cy)."""
        return [cx+int((x1-cx)*scale), cy+int((y1-cy)*scale),
                cx+int((x2-cx)*scale), cy+int((y2-cy)*scale)]

    def _draw_male(self, draw, cx, cy, char, t, speaking, scale=1.0):
        sk, ss  = char["skin_base"], char["skin_shadow"]
        hair    = char["hair_color"]
        shirt   = char["shirt_color"]
        tie     = char["tie_color"]
        eye     = char["eye_color"]
        fh_c    = char.get("facial_hair", hair)
        sw      = int(math.sin(t*2.3)*5*scale) if speaking else 0
        # Apply scale: all offsets from (cx,cy) are multiplied by scale
        def p(dx, dy): return (cx+int(dx*scale), cy+int(dy*scale))
        def r(x1,y1,x2,y2): return [cx+int((x1)*scale), cy+int((y1)*scale),
                                      cx+int((x2)*scale), cy+int((y2)*scale)]

        # Body
        draw.rounded_rectangle([cx-46,cy+30,cx+46,cy+124], radius=15, fill=shirt, outline=(212,212,212), width=2)
        draw.polygon([(cx-13,cy+30),(cx-23,cy+46),(cx-8,cy+50),(cx,cy+40),(cx+8,cy+50),(cx+23,cy+46),(cx+13,cy+30)], fill=shirt, outline=(192,192,192))
        draw.polygon([(cx,cy+40),(cx-6,cy+46),(cx-4,cy+92),(cx,cy+97),(cx+4,cy+92),(cx+6,cy+46)], fill=tie)
        draw.polygon([(cx-6,cy+40),(cx+6,cy+40),(cx+4,cy+47),(cx-4,cy+47)], fill=tie)
        # Arms
        draw.rounded_rectangle([cx-63,cy+46+sw,cx-45,cy+104+sw], radius=8, fill=shirt, outline=(202,202,202), width=1)
        draw.rounded_rectangle([cx+45,cy+46-sw,cx+63,cy+104-sw], radius=8, fill=shirt, outline=(202,202,202), width=1)
        draw.ellipse([cx-67,cy+100+sw,cx-46,cy+113+sw], fill=sk)
        draw.ellipse([cx+46,cy+100-sw,cx+67,cy+113-sw], fill=sk)
        # Neck
        draw.rectangle([cx-12,cy+14,cx+12,cy+36], fill=sk)
        draw.rectangle([cx-12,cy+30,cx+12,cy+36], fill=ss)
        # Head
        hw = 48
        draw.ellipse([cx-hw,cy-52,cx+hw,cy+52-14], fill=sk)
        draw.ellipse([cx-hw,cy-52,cx+hw,cy-7], fill=hair)
        draw.rectangle([cx-hw,cy-52,cx-32,cy-3], fill=hair)
        draw.rectangle([cx+32,cy-52,cx+hw,cy-3], fill=hair)
        draw.ellipse([cx-hw-4,cy-6,cx-hw+10,cy+10], fill=ss)
        draw.ellipse([cx+hw-10,cy-6,cx+hw+4,cy+10], fill=ss)
        # Blink
        blink = abs(math.sin(t*0.45+char["cx"]*0.01)) > 0.975
        if blink:
            draw.line([(cx-26,cy-13),(cx-12,cy-13)], fill=(45,30,16), width=3)
            draw.line([(cx+12,cy-13),(cx+26,cy-13)], fill=(45,30,16), width=3)
        else:
            draw.ellipse([cx-28,cy-18,cx-10,cy-8],  fill=(248,248,255))
            draw.ellipse([cx-24,cy-16,cx-14,cy-10], fill=eye)
            draw.ellipse([cx-22,cy-15,cx-17,cy-11], fill=(36,36,46))
            draw.ellipse([cx-20,cy-15,cx-18,cy-13], fill=(255,255,255))
            draw.ellipse([cx+10,cy-18,cx+28,cy-8],  fill=(248,248,255))
            draw.ellipse([cx+14,cy-16,cx+24,cy-10], fill=eye)
            draw.ellipse([cx+17,cy-15,cx+22,cy-11], fill=(36,36,46))
            draw.ellipse([cx+18,cy-15,cx+20,cy-13], fill=(255,255,255))
        draw.arc([cx-30,cy-26,cx-8,cy-20],  start=0, end=180, fill=hair, width=4)
        draw.arc([cx+8, cy-26,cx+30,cy-20], start=0, end=180, fill=hair, width=4)
        draw.line([(cx-2,cy-6),(cx-2,cy+3)], fill=ss, width=2)
        draw.ellipse([cx-4,cy+1,cx-1,cy+5], fill=ss)
        draw.ellipse([cx+1,cy+1,cx+4,cy+5], fill=ss)
        # Mouth
        if speaking:
            mo = int(abs(math.sin(t*8.0))*11)+4
            draw.ellipse([cx-14,cy+10,cx+14,cy+10+mo], fill=(152,72,72))
            if mo > 6:
                draw.ellipse([cx-12,cy+11,cx+12,cy+15], fill=(240,240,246))
        else:
            draw.arc([cx-14,cy+8,cx+14,cy+20], start=10, end=170, fill=(130,62,62), width=3)
        if fh_c:
            draw.arc([cx-36,cy-8,cx+36,cy+31], start=25, end=155, fill=self._darken(fh_c,10), width=2)

    def _draw_female(self, draw, cx, cy, char, t, speaking, scale=1.0):
        sk, ss  = char["skin_base"], char["skin_shadow"]
        hair    = char["hair_color"]
        shirt   = char["shirt_color"]
        acc     = char.get("accessory_color", (182,42,72))
        eye     = char["eye_color"]
        sw      = int(math.sin(t*2.3)*5*scale) if speaking else 0
        def p(dx, dy): return (cx+int(dx*scale), cy+int(dy*scale))
        def r(x1,y1,x2,y2): return [cx+int((x1)*scale), cy+int((y1)*scale),
                                      cx+int((x2)*scale), cy+int((y2)*scale)]

        draw.rounded_rectangle([cx-44,cy+30,cx+44,cy+124], radius=15, fill=shirt, outline=(212,212,212), width=2)
        draw.polygon([(cx-16,cy+30),(cx-26,cy+41),(cx-18,cy+50),(cx,cy+36),(cx+18,cy+50),(cx+26,cy+41),(cx+16,cy+30)], fill=shirt, outline=(192,192,192))
        draw.ellipse([cx-18,cy+40,cx+18,cy+45], outline=acc, width=3)
        draw.ellipse([cx-2,cy+43,cx+2,cy+49], fill=acc)
        draw.rounded_rectangle([cx-62,cy+46+sw,cx-43,cy+104+sw], radius=8, fill=shirt, outline=(202,202,202), width=1)
        draw.rounded_rectangle([cx+43,cy+46-sw,cx+62,cy+104-sw], radius=8, fill=shirt, outline=(202,202,202), width=1)
        draw.ellipse([cx-66,cy+100+sw,cx-44,cy+113+sw], fill=sk)
        draw.ellipse([cx+44,cy+100-sw,cx+66,cy+113-sw], fill=sk)
        draw.rectangle([cx-10,cy+14,cx+10,cy+36], fill=sk)
        draw.rectangle([cx-10,cy+30,cx+10,cy+36], fill=ss)
        hw = 44
        draw.ellipse([cx-hw,cy-50,cx+hw,cy+50-14], fill=sk)
        draw.ellipse([cx-hw-3,cy-54,cx+hw+3,cy-6],  fill=hair)
        draw.ellipse([cx-hw-6,cy-36,cx-22,cy+16],   fill=hair)
        draw.ellipse([cx+22,cy-36,cx+hw+6,cy+16],   fill=hair)
        draw.ellipse([cx-hw-2,cy-4,cx-hw+8,cy+8],   fill=ss)
        draw.ellipse([cx+hw-8,cy-4,cx+hw+2,cy+8],   fill=ss)
        draw.ellipse([cx-hw+2,cy+6,cx-hw+7,cy+12],  fill=acc)
        draw.ellipse([cx+hw-7,cy+6,cx+hw-2,cy+12],  fill=acc)
        blink = abs(math.sin(t*0.45+char["cx"]*0.01)) > 0.975
        if blink:
            draw.line([(cx-28,cy-14),(cx-10,cy-14)], fill=(42,28,16), width=3)
            draw.line([(cx+10,cy-14),(cx+28,cy-14)], fill=(42,28,16), width=3)
        else:
            draw.ellipse([cx-30,cy-20,cx-8, cy-8],  fill=(248,248,255))
            draw.ellipse([cx-26,cy-18,cx-12,cy-10], fill=eye)
            draw.ellipse([cx-23,cy-16,cx-15,cy-11], fill=(32,32,42))
            draw.ellipse([cx-21,cy-16,cx-18,cy-13], fill=(255,255,255))
            draw.ellipse([cx+8, cy-20,cx+30,cy-8],  fill=(248,248,255))
            draw.ellipse([cx+12,cy-18,cx+26,cy-10], fill=eye)
            draw.ellipse([cx+15,cy-16,cx+23,cy-11], fill=(32,32,42))
            draw.ellipse([cx+18,cy-16,cx+21,cy-13], fill=(255,255,255))
            for i in range(4):
                draw.line([(cx-27+i*6,cy-20),(cx-28+i*6,cy-23)], fill=(36,26,16), width=2)
                draw.line([(cx+10+i*6,cy-20),(cx+11+i*6,cy-23)], fill=(36,26,16), width=2)
        draw.arc([cx-32,cy-28,cx-6, cy-22], start=10, end=170, fill=self._darken(hair,20), width=3)
        draw.arc([cx+6, cy-28,cx+32,cy-22], start=10, end=170, fill=self._darken(hair,20), width=3)
        draw.line([(cx-1,cy-4),(cx-1,cy+3)], fill=ss, width=1)
        draw.ellipse([cx-3,cy+1,cx-1,cy+4], fill=ss)
        draw.ellipse([cx+1,cy+1,cx+3,cy+4], fill=ss)
        if speaking:
            mo = int(abs(math.sin(t*8.0))*9)+3
            draw.arc([cx-12,cy+8, cx+12,cy+14], start=180, end=360, fill=(172,92,102), width=3)
            draw.arc([cx-12,cy+12,cx+12,cy+12+mo], start=0, end=180, fill=(182,102,112), width=3)
            if mo > 5:
                draw.ellipse([cx-10,cy+12,cx+10,cy+12+mo-2], fill=(130,62,72))
        else:
            draw.arc([cx-12,cy+8, cx+12,cy+14], start=180, end=360, fill=(172,92,102), width=3)
            draw.arc([cx-12,cy+12,cx+12,cy+20], start=10,  end=170, fill=(182,102,112), width=3)

    # =========================================================================
    # Speech bubble  (stays within left 640 px)
    # =========================================================================

    def _bubble_height(self, n: int) -> int:
        return self.HEADER_BH + self.BUBBLE_PAD * 2 + n * self.LINE_H + 6

    def _draw_bubble(
        self, draw: ImageDraw.Draw, speaker: dict,
        text: str, progress: float, t: float, is_speaking: bool,
    ):
        side = speaker["side"]
        cx   = speaker["cx"]
        bw   = self.BUBBLE_W
        pad  = self.BUBBLE_PAD
        hh_b = self.HEADER_BH
        hh   = self.HEADER_H
        r    = 15

        bg, border     = speaker["bubble_bg"],          speaker["bubble_border"]
        hdr_bg, hdr_fg = speaker["bubble_header_bg"],   speaker["bubble_header_text"]
        txt_fg         = speaker["bubble_body_text"]

        margin = 8
        bx = margin if side == "left" else (self.CHAR_W - margin - bw)
        by = hh + 5

        fn_body    = _load_font("regular", self.FONT_BODY_SZ)
        max_tw     = bw - pad * 2
        all_lines  = _wrap_pixels(draw, text, fn_body, max_tw)

        full_text   = " ".join(all_lines)
        chars_shown = int(len(full_text) * min(1.0, progress * 1.1))
        vis_lines   = _wrap_pixels(draw, full_text[:chars_shown], fn_body, max_tw)

        max_lines    = min(self.MAX_LINES, max(1, len(all_lines)))
        bh           = self._bubble_height(max_lines)
        display_lines = vis_lines[-max_lines:] if len(vis_lines) > max_lines else vis_lines

        draw.rounded_rectangle([bx,by,bx+bw,by+bh], radius=r, fill=bg, outline=border, width=2)
        draw.rounded_rectangle([bx,by,bx+bw,by+hh_b+r], radius=r, fill=hdr_bg)
        draw.rectangle([bx+1,by+hh_b,bx+bw-1,by+hh_b+r], fill=hdr_bg)
        draw.line([(bx,by+hh_b),(bx+bw,by+hh_b)], fill=border, width=2)

        av_r = 13
        av_x = bx + pad + av_r
        av_y = by + hh_b // 2
        draw.ellipse([av_x-av_r,av_y-av_r,av_x+av_r,av_y+av_r], fill=self._lighten(hdr_bg,58))
        fi  = _load_font("bold", 15)
        ini = speaker["name"][0].upper()
        ib  = draw.textbbox((0,0), ini, font=fi)
        draw.text((av_x-(ib[2]-ib[0])//2, av_y-(ib[3]-ib[1])//2-1), ini, fill=hdr_bg, font=fi)

        fn_name = _load_font("bold", 21)
        draw.text((av_x+av_r+10, by+hh_b//2-10), speaker["name"], fill=hdr_fg, font=fn_name)

        if is_speaking:
            dbx = bx + bw - pad - 38
            dby = by + hh_b // 2
            for d in range(3):
                br = max(0.3, math.sin(((t*3.2+d*0.55) % 3) * math.pi))
                dc = tuple(int(c*br) for c in hdr_fg)
                dx = dbx + d * 13
                draw.ellipse([dx-4,dby-4,dx+4,dby+4], fill=dc)

        # Tail
        tb_y  = by + bh
        tt_y  = speaker["cy"] - 78
        tt_x  = cx
        if side == "left":
            t1x, t2x = bx+48, bx+92
        else:
            t1x, t2x = bx+bw-92, bx+bw-48
        draw.polygon([(t1x,tb_y),(t2x,tb_y),(tt_x,tt_y)], fill=bg)
        draw.line([(t1x,tb_y),(tt_x,tt_y)], fill=border, width=2)
        draw.line([(tt_x,tt_y),(t2x,tb_y)], fill=border, width=2)

        tx  = bx + pad
        ty  = by + hh_b + pad
        shd = tuple(max(0, c-26) for c in bg)
        for ln in display_lines:
            draw.text((tx+1,ty+1), ln, fill=shd,    font=fn_body)
            draw.text((tx,  ty),   ln, fill=txt_fg,  font=fn_body)
            ty += self.LINE_H

        if chars_shown < len(full_text) and is_speaking and int(t*4)%2 == 0:
            last = display_lines[-1] if display_lines else ""
            cx_c = tx + int(draw.textlength(last, font=fn_body)) + 4
            cy_c = ty - self.LINE_H
            draw.rectangle([cx_c, cy_c+5, cx_c+9, cy_c+27], fill=border)

    # =========================================================================
    # Transcript parser — extracts [PAGE X] and [TOPIC: ...] hints
    # =========================================================================

    _PAGE_RE     = re.compile(r'\[PAGE\s*(\d+)\]',        re.IGNORECASE)
    _TOPIC_RE    = re.compile(r'\[TOPIC:\s*([^\]]+)\]',    re.IGNORECASE)
    _LINE_RE     = re.compile(r'^(?:User\s*)?([AB])\s*:\s*(.+)$', re.IGNORECASE)
    _TAG_ONLY_RE = re.compile(
        r'^\s*(\[PAGE\s*\d+\]|\[TOPIC:\s*[^\]]+\])',      re.IGNORECASE
    )

    def _parse_transcript(self, transcript: str) -> List[Dict]:
        """
        Robust parser:
          - Reads STANDALONE tag lines:   [PAGE 3] [TOPIC: Bank Reconciliation]
          - Also reads inline tags inside A:/B: dialogue
          - Builds an initial global _page_timeline (rebuilt again with real
            durations once TTS timing is known — see _rebuild_page_timeline)
        """
        segments: List[Dict] = []
        current_page  = 1
        current_topic = "Introduction"
        pending_page  = None
        pending_topic = None

        for raw_line in transcript.replace("\\n", "\n").splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # ── Pure tag line (no speaker prefix) ────────────────────────
            if self._TAG_ONLY_RE.match(line) and not self._LINE_RE.match(line):
                pm = self._PAGE_RE.search(line)
                tm = self._TOPIC_RE.search(line)
                if pm:
                    current_page  = int(pm.group(1))
                    pending_page  = current_page
                if tm:
                    current_topic = tm.group(1).strip()
                    pending_topic = current_topic
                continue

            # ── Dialogue line ─────────────────────────────────────────────
            m = self._LINE_RE.match(line)
            if not m:
                continue

            is_a     = m.group(1).upper() == "A"
            raw_text = m.group(2)

            # Inline tags inside the dialogue
            pm = self._PAGE_RE.search(raw_text)
            if pm:
                current_page = int(pm.group(1))
                pending_page = current_page
            tm = self._TOPIC_RE.search(raw_text)
            if tm:
                current_topic = tm.group(1).strip()
                pending_topic = current_topic

            # Apply any pending tags from previous tag line
            if pending_page  is not None: current_page  = pending_page
            if pending_topic is not None: current_topic = pending_topic
            pending_page  = None
            pending_topic = None

            text = _clean(raw_text)
            if not text:
                continue

            words    = len(text.split())
            duration = max(4.5, (words / 120) * 60)
            segments.append({
                "text":      text,
                "is_a":      is_a,
                "duration":  duration,
                "speak_end": duration * 0.90,
                "page":      current_page,
                "topic":     current_topic,
            })

        # ── Fallback: unformatted text ────────────────────────────────────
        if not segments:
            non_empty = [_clean(l) for l in transcript.splitlines() if l.strip()]
            for i in range(0, min(len(non_empty), 80), 6):
                text = " ".join(non_empty[i:i+6])
                if not text:
                    continue
                words    = len(text.split())
                duration = max(4.5, (words / 120) * 60)
                segments.append({
                    "text": text, "is_a": i % 2 == 0,
                    "duration": duration, "speak_end": duration * 0.90,
                    "page":  max(1, i // 8 + 1),
                    "topic": "Introduction",
                })

        self._rebuild_page_timeline(segments)
        total_min = (self._page_timeline[-1]["t_end"] / 60) if self._page_timeline else 0
        print(f"[VideoService] {len(segments)} segments, "
              f"{len(self._page_timeline)} page blocks, "
              f"total ≈{total_min:.1f} min")
        return segments

    def _rebuild_page_timeline(self, segments: List[Dict]) -> None:
        """
        Build self._page_timeline = [{t_start, t_end, page, topic}, ...] in
        wall-clock seconds from a list of segments (each with a known
        "duration"). Called once with estimated durations during parsing,
        and again after real TTS durations are known.
        """
        timeline: List[Dict] = []
        cumulative  = 0.0
        block_start = 0.0
        prev_page   = segments[0]["page"]  if segments else 1
        prev_topic  = segments[0]["topic"] if segments else "Introduction"

        for seg in segments:
            if seg["page"] != prev_page or seg["topic"] != prev_topic:
                timeline.append({
                    "t_start": block_start,
                    "t_end":   cumulative,
                    "page":    prev_page,
                    "topic":   prev_topic,
                })
                block_start = cumulative
                prev_page   = seg["page"]
                prev_topic  = seg["topic"]
            cumulative += seg["duration"]

        timeline.append({
            "t_start": block_start,
            "t_end":   cumulative,
            "page":    prev_page,
            "topic":   prev_topic,
        })
        self._page_timeline = timeline

    def _page_at(self, global_t: float) -> tuple:
        """Return (page, topic) for any global video timestamp."""
        for block in self._page_timeline:
            if block["t_start"] <= global_t < block["t_end"]:
                return block["page"], block["topic"]
        if self._page_timeline:
            last = self._page_timeline[-1]
            return last["page"], last["topic"]
        return 1, "Introduction"

    # =========================================================================
    # Colour utilities
    # =========================================================================

    @staticmethod
    def _lighten(c: tuple, a: int) -> tuple:
        return tuple(min(255, x+a) for x in c)

    @staticmethod
    def _darken(c: tuple, a: int) -> tuple:
        return tuple(max(0, x-a) for x in c)
