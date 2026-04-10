from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import os
import re
import math
from typing import List, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Font loader — Expanded with better fallbacks and quality fonts
# ---------------------------------------------------------------------------
_FONT_PATHS = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
    ],
}


def _load_font(style: str = "regular", size: int = 28) -> ImageFont.FreeTypeFont:
    """Load font with better fallback handling."""
    for path in _FONT_PATHS.get(style, _FONT_PATHS["regular"]):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Final fallback to default font
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Text cleaning — strip LLM markdown / escape artifacts before display
# ---------------------------------------------------------------------------
_CLEAN_PATTERNS = [
    (re.compile(r'\*{1,3}'),    ""),   # *** ** *
    (re.compile(r'_{1,2}'),     ""),   # __ _
    (re.compile(r'`+'),         ""),   # backticks
    (re.compile(r'#+\s*'),      ""),   # headings
    (re.compile(r'\\n'),        " "),  # literal backslash-n
    (re.compile(r'\n+'),        " "),  # actual newlines
    (re.compile(r'\s{2,}'),     " "),  # multiple spaces
    (re.compile(r'^\s*[-•]\s*'), ""),  # leading bullets
]


def _clean(text: str) -> str:
    for pattern, repl in _CLEAN_PATTERNS:
        text = pattern.sub(repl, text)
    return text.strip()


# ---------------------------------------------------------------------------
# Pixel-accurate text wrapping (uses actual font metrics)
# ---------------------------------------------------------------------------
def _wrap_pixels(draw: ImageDraw.Draw, text: str,
                 font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    line = ""
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
# VideoService
# ---------------------------------------------------------------------------
class VideoService:
    W   = 1280
    H   = 720
    FPS = 24

    # ── Character palettes ──────────────────────────────────────────────────
    CHAR_A = dict(
        name="Alex",
        body=(45, 120, 210),
        skin=(255, 213, 170),
        hair=(55, 28, 8),
        bubble_bg=(228, 240, 255),
        bubble_border=(45, 120, 210),
        bubble_header_bg=(45, 120, 210),
        bubble_header_text=(255, 255, 255),
        bubble_body_text=(18, 30, 60),
        label=(45, 120, 210),
        side="left",
        cx=190,
        cy=470,
    )
    CHAR_B = dict(
        name="Blake",
        body=(34, 160, 85),
        skin=(255, 213, 170),
        hair=(20, 20, 80),
        bubble_bg=(220, 248, 232),
        bubble_border=(34, 160, 85),
        bubble_header_bg=(34, 160, 85),
        bubble_header_text=(255, 255, 255),
        bubble_body_text=(12, 40, 22),
        label=(34, 160, 85),
        side="right",
        cx=1090,
        cy=470,
    )

    # ── Background colours ───────────────────────────────────────────────────
    BG_DARK   = (14, 15, 28)
    BG_FLOOR  = (9, 9, 18)
    BG_GRID   = (26, 28, 50)
    STAGE_LINE = (52, 55, 98)

    # ── Bubble layout ────────────────────────────────────────────────────────
    BUBBLE_W   = 680      # Increased from 630 for more text space
    BUBBLE_H   = 280      # Increased from 240 for more lines
    BUBBLE_PAD = 26       # Increased from 22 for better spacing
    HEADER_H   = 52       # Increased from 48 for better header visibility

    def __init__(self):
        pass

    # =========================================================================
    # Public API
    # =========================================================================

    def create_animated_video_from_transcript(
        self,
        transcript: str,
        output_path: str,
        audio_path: Optional[str] = None,
    ) -> str:
        """Main entry point — same signature as before."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        segments = self._parse_transcript(transcript)
        if not segments:
            raise Exception("No dialogue segments found in transcript.")

        clips = []
        for seg in segments:
            char  = self.CHAR_A if seg["is_a"] else self.CHAR_B
            other = self.CHAR_B if seg["is_a"] else self.CHAR_A
            clips.append(self._build_clip(seg["text"], char, other, seg["duration"]))

        video = concatenate_videoclips(clips, method="compose")

        if audio_path and os.path.exists(audio_path):
            audio = AudioFileClip(audio_path)
            if audio.duration > video.duration:
                video = video.loop(duration=audio.duration)
            else:
                video = video.subclip(0, audio.duration)
            video = video.set_audio(audio)

        video.write_videofile(
            output_path, fps=self.FPS,
            codec="libx264", audio_codec="aac",
            preset="medium", logger=None,
        )
        video.close()
        return output_path

    # Legacy shim — keeps orchestrator compatible
    def create_simple_animated_video(
        self, segments: List[Dict], output_path: str,
        duration_per_segment: float = 5.0,
    ) -> str:
        speakers = ["User A", "User B"]
        lines = [f"{speakers[i%2]}: {seg.get('text','')}" for i, seg in enumerate(segments)]
        return self.create_animated_video_from_transcript("\n".join(lines), output_path)

    # =========================================================================
    # Clip builder
    # =========================================================================

    def _build_clip(self, text: str, speaker: dict, listener: dict, duration: float) -> VideoClip:
        W, H, FPS = self.W, self.H, self.FPS
        n = max(1, int(duration * FPS))

        img  = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)

        frames: List[np.ndarray] = []
        for f in range(n):
            t        = f / FPS
            progress = f / max(n - 1, 1)

            self._draw_bg(draw, W, H, t)
            self._draw_character(draw, listener, t, speaking=False)
            self._draw_character(draw, speaker,  t, speaking=True)
            self._draw_bubble(draw, speaker, text, progress, t)

            frames.append(np.array(img))

        def make_frame(t_sec: float) -> np.ndarray:
            return frames[min(int(t_sec * FPS), n - 1)]

        return VideoClip(make_frame, duration=duration)

    # =========================================================================
    # Background
    # =========================================================================

    def _draw_bg(self, draw: ImageDraw.Draw, W: int, H: int, t: float):
        draw.rectangle([0, 0, W, H], fill=self.BG_DARK)

        for x in range(0, W, 80):
            draw.line([(x, 0), (x, H)], fill=self.BG_GRID, width=1)
        for y in range(0, H, 80):
            draw.line([(0, y), (W, y)], fill=self.BG_GRID, width=1)

        for i in range(14):
            px = int((math.sin(t * 0.35 + i * 1.27) * 0.45 + 0.5) * W)
            py = int((math.cos(t * 0.28 + i * 0.91) * 0.45 + 0.5) * (H - 130))
            r  = 2 + i % 4
            c  = (36 + i * 6, 34 + i * 5, 72 + i * 7)
            draw.ellipse([px-r, py-r, px+r, py+r], fill=c)

        draw.rectangle([0, H - 130, W, H], fill=self.BG_FLOOR)
        draw.line([(0, H-130), (W, H-130)], fill=self.STAGE_LINE, width=2)

    # =========================================================================
    # Character drawing
    # =========================================================================

    def _draw_character(self, draw: ImageDraw.Draw, char: dict, t: float, speaking: bool):
        cx, cy = char["cx"], char["cy"]
        skin   = char["skin"]
        body   = char["body"]
        hair   = char["hair"]

        amp = 5 if speaking else 2
        bob = int(math.sin(t * 3.0) * amp)
        cy  = cy + bob

        # Shadow
        draw.ellipse([cx-52, cy+118, cx+52, cy+132], fill=(6, 6, 16))

        # Body
        draw.rounded_rectangle([cx-44, cy+38, cx+44, cy+128], radius=16, fill=body)
        collar = self._lighten(body, 48)
        draw.rounded_rectangle([cx-14, cy+38, cx+14, cy+64], radius=6, fill=collar)

        # Arms (speaker arm swings)
        swing = int(math.sin(t * 2.6) * 7) if speaking else 0
        draw.rounded_rectangle([cx-64, cy+48+swing,  cx-44, cy+108+swing],  radius=9, fill=body)
        draw.rounded_rectangle([cx+44, cy+48-swing,  cx+64, cy+108-swing],  radius=9, fill=body)

        # Neck
        draw.rectangle([cx-11, cy+16, cx+11, cy+42], fill=skin)

        # Head
        hr = 46
        draw.ellipse([cx-hr, cy-hr-8, cx+hr, cy+hr-8], fill=skin)

        # Hair
        draw.ellipse([cx-hr, cy-hr-8, cx+hr, cy-2],    fill=hair)
        draw.rectangle([cx-hr, cy-hr-8, cx+hr, cy-28], fill=hair)

        # Blink
        blink = abs(math.sin(t * 0.47 + char["cx"] * 0.01)) > 0.972
        ey    = 2 if blink else 7
        draw.ellipse([cx-22, cy-18, cx-8,  cy-18+ey], fill=(22, 22, 22))
        draw.ellipse([cx+8,  cy-18, cx+22, cy-18+ey], fill=(22, 22, 22))
        if not blink:
            draw.ellipse([cx-19, cy-17, cx-15, cy-13], fill=(255, 255, 255))
            draw.ellipse([cx+13, cy-17, cx+17, cy-13], fill=(255, 255, 255))

        # Eyebrows
        draw.line([(cx-24, cy-27), (cx-7,  cy-25)], fill=hair, width=3)
        draw.line([(cx+7,  cy-25), (cx+24, cy-27)], fill=hair, width=3)

        # Mouth
        if speaking:
            mo = int(abs(math.sin(t * 8.5)) * 11) + 3
            draw.ellipse([cx-13, cy+4, cx+13, cy+4+mo], fill=(175, 55, 55))
            draw.line([(cx-13, cy+5), (cx+13, cy+5)], fill=(80, 18, 18), width=2)
        else:
            draw.arc([cx-13, cy+3, cx+13, cy+15],
                     start=12, end=168, fill=(155, 75, 75), width=3)

        # Name label
        fn = _load_font("bold", 24)  # Increased from 22
        bbox = draw.textbbox((0, 0), char["name"], font=fn)
        lw   = bbox[2] - bbox[0]
        draw.text((cx - lw//2, cy + 140), char["name"], fill=char["label"], font=fn)

    # =========================================================================
    # Speech Bubble — distinct per speaker, with header name tag
    # =========================================================================

    def _draw_bubble(self, draw: ImageDraw.Draw, speaker: dict,
                     text: str, progress: float, t: float):
        W, H   = self.W, self.H
        side   = speaker["side"]
        cx     = speaker["cx"]
        bw     = self.BUBBLE_W
        bh     = self.BUBBLE_H
        pad    = self.BUBBLE_PAD
        hh     = self.HEADER_H
        r      = 18

        bg     = speaker["bubble_bg"]
        border = speaker["bubble_border"]
        hdr_bg = speaker["bubble_header_bg"]
        hdr_fg = speaker["bubble_header_text"]
        txt_fg = speaker["bubble_body_text"]

        # Bubble X: A on left, B on right — never overlap characters
        margin = 36
        bx = margin if side == "left" else W - margin - bw
        by = 28

        # ── Main bubble body ─────────────────────────────────────────────
        draw.rounded_rectangle([bx, by, bx+bw, by+bh],
                                radius=r, fill=bg, outline=border, width=3)

        # ── Header strip (coloured, speaker-specific) ─────────────────────
        # Top rounded portion
        draw.rounded_rectangle([bx, by, bx+bw, by+hh+r],
                                radius=r, fill=hdr_bg)
        # Square off the bottom part of the header so it merges with body
        draw.rectangle([bx+1, by+hh, bx+bw-1, by+hh+r], fill=hdr_bg)
        # Separator
        draw.line([(bx, by+hh), (bx+bw, by+hh)], fill=border, width=2)

        # Avatar circle with initial
        av_r = 16  # Increased from 15
        av_x = bx + pad + av_r
        av_y = by + hh // 2
        av_fill = self._lighten(hdr_bg, 65)
        draw.ellipse([av_x-av_r, av_y-av_r, av_x+av_r, av_y+av_r], fill=av_fill)
        fi = _load_font("bold", 18)  # Increased from 17
        ini = speaker["name"][0].upper()
        ib  = draw.textbbox((0, 0), ini, font=fi)
        # Draw initial in header color on lighter circle
        draw.text((av_x - (ib[2]-ib[0])//2,
                   av_y - (ib[3]-ib[1])//2 - 1),
                  ini, fill=hdr_bg, font=fi)

        # Speaker name - larger and bolder
        fn_name = _load_font("bold", 26)  # Increased from 24
        draw.text((av_x + av_r + 14, by + hh//2 - 13),  # Adjusted positioning
                  speaker["name"], fill=hdr_fg, font=fn_name)

        # Animated "speaking" dots
        dot_base_x = bx + bw - pad - 44
        dot_y      = by + hh // 2
        for d in range(3):
            phase = (t * 3.2 + d * 0.55) % 3
            brightness = max(0.3, math.sin(phase * math.pi))
            dc = tuple(int(c * brightness) for c in hdr_fg)
            dx = dot_base_x + d * 15
            draw.ellipse([dx-4, dot_y-4, dx+4, dot_y+4], fill=dc)

        # ── Tail pointer ─────────────────────────────────────────────────
        tail_bottom_y = by + bh
        tail_tip_y    = speaker["cy"] - 75
        tail_tip_x    = cx

        if side == "left":
            t1x, t2x = bx + 55, bx + 105
        else:
            t1x = bx + bw - 105
            t2x = bx + bw - 55

        # Fill covers the border gap at the base of the bubble
        draw.polygon([(t1x, tail_bottom_y), (t2x, tail_bottom_y),
                      (tail_tip_x, tail_tip_y)], fill=bg)
        draw.line([(t1x, tail_bottom_y), (tail_tip_x, tail_tip_y)], fill=border, width=3)
        draw.line([(tail_tip_x, tail_tip_y), (t2x, tail_bottom_y)],  fill=border, width=3)

        # ── Body text with typewriter reveal ─────────────────────────────
        fn_body    = _load_font("regular", 28)  # Increased from 26
        max_text_w = bw - pad * 2

        full_lines = _wrap_pixels(draw, text, fn_body, max_text_w)
        full_text  = " ".join(full_lines)

        # Reveal speed: finish revealing by ~55% of segment duration
        reveal = min(1.0, progress * 1.9)
        chars_shown = int(len(full_text) * reveal)
        visible = full_text[:chars_shown]
        vis_lines = _wrap_pixels(draw, visible, fn_body, max_text_w)

        tx     = bx + pad
        ty     = by + hh + pad + 4  # Added 4px top padding for better spacing
        line_h = 40  # Increased from 36 for better readability
        max_lines = (bh - hh - pad * 2) // line_h

        # Draw text with subtle shadow for better readability
        shadow_offset = 1
        shadow_color = tuple(max(0, c - 40) for c in bg)  # Darker shadow based on bg
        
        for ln in vis_lines[:max_lines]:
            # Draw subtle shadow
            draw.text((tx + shadow_offset, ty + shadow_offset), ln, fill=shadow_color, font=fn_body)
            # Draw main text
            draw.text((tx, ty), ln, fill=txt_fg, font=fn_body)
            ty += line_h

        # Blinking cursor while still revealing text
        if chars_shown < len(full_text) and int(t * 4) % 2 == 0:
            last_ln = vis_lines[-1] if vis_lines else ""
            cur_x   = tx + int(draw.textlength(last_ln, font=fn_body)) + 4
            cur_y   = ty - line_h
            draw.rectangle([cur_x, cur_y + 6, cur_x + 11, cur_y + 32], fill=border)

    # =========================================================================
    # Transcript parsing — robust, handles LLM output quirks
    # =========================================================================

    def _parse_transcript(self, transcript: str) -> List[Dict]:
        """
        Accepts lines like:
          "User A: ..."  "A: ..."  "user b: ..."  "B: ..."
        Cleans LLM markdown from each line's content.
        Returns: [{"text": str, "is_a": bool, "duration": float}]
        """
        segments: List[Dict] = []

        # Normalise literal \\n → real newlines
        raw   = transcript.replace("\\n", "\n")
        lines = raw.splitlines()

        pattern = re.compile(r'^(?:User\s*)?([AB])\s*:\s*(.+)$', re.IGNORECASE)

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if not m:
                continue

            is_a = m.group(1).upper() == "A"
            text = _clean(m.group(2))
            if not text:
                continue

            words    = len(text.split())
            duration = max(4.5, (words / 120) * 60)
            segments.append({"text": text, "is_a": is_a, "duration": duration})

        # Fallback: chunk all non-empty lines if nothing was parsed
        if not segments:
            non_empty = [_clean(l) for l in lines if l.strip()]
            for i in range(0, min(len(non_empty), 40), 6):
                text = " ".join(non_empty[i:i+6])
                if not text:
                    continue
                words    = len(text.split())
                duration = max(4.5, (words / 120) * 60)
                segments.append({"text": text, "is_a": i % 2 == 0, "duration": duration})

        return segments

    # =========================================================================
    # Utilities
    # =========================================================================

    @staticmethod
    def _lighten(color: tuple, amount: int) -> tuple:
        return tuple(min(255, c + amount) for c in color)