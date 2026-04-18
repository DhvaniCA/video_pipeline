"""
video_service.py — Professional Classroom Video with Real-Audio Lip Sync
=========================================================================
FIXES in this version:
  1. INDIAN VOICES — VideoService now accepts segment_durations from
     AudioService so every clip is exactly as long as its real audio.

  2. LIP SYNC — each clip duration == real audio segment duration, so
     the speaker's mouth opens/closes in exact sync with their voice.
     The silent "pause" portion at the end of each segment (the gap
     between turns) uses is_speaking=False, mouth closed, no dots.

  3. TEXT BUBBLE TIMING — text reveal is tied to the actual speaking
     window (not an estimate), so the bubble finishes revealing just
     as the speaker finishes talking.

  4. SPEAKING_END_RATIO — the fraction of a segment that is actual
     speech vs trailing silence. AudioService appends a 300ms pause
     after every segment. We compute this ratio from real durations
     so the mouth closes precisely when the audio voice stops.

Usage in your orchestrator:
    audio_path, seg_durations = audio_service.generate_audio_from_transcript(
        transcript, audio_path
    )
    video_service.create_animated_video_from_transcript(
        transcript, video_path,
        audio_path=audio_path,
        segment_durations=seg_durations,   # ← pass real durations here
    )
"""

from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import os
import re
import math
from typing import List, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Silence appended by AudioService after each segment (must match audio_service.py)
# ---------------------------------------------------------------------------
_INTER_SEGMENT_PAUSE_SEC = 0.300   # 300 ms — keep in sync with audio_service.py

# ---------------------------------------------------------------------------
# Font loader
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


def _load_font(style: str = "regular", size: int = 28) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS.get(style, _FONT_PATHS["regular"]):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------
_CLEAN_PATTERNS = [
    (re.compile(r'\*{1,3}'),     ""),
    (re.compile(r'_{1,2}'),      ""),
    (re.compile(r'`+'),          ""),
    (re.compile(r'#+\s*'),       ""),
    (re.compile(r'\\n'),         " "),
    (re.compile(r'\n+'),         " "),
    (re.compile(r'\s{2,}'),      " "),
    (re.compile(r'^\s*[-•]\s*'), ""),
]


def _clean(text: str) -> str:
    for pattern, repl in _CLEAN_PATTERNS:
        text = pattern.sub(repl, text)
    return text.strip()


# ---------------------------------------------------------------------------
# Pixel-accurate text wrapping
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

    # ── Character definitions ───────────────────────────────────────────────
    CHAR_A = dict(
        name="Student",
        gender="male",
        skin_base=(255, 220, 177),
        skin_shadow=(235, 200, 157),
        hair_color=(60, 40, 30),
        facial_hair=(45, 35, 25),
        eye_color=(100, 75, 50),
        shirt_color=(255, 255, 255),
        tie_color=(25, 60, 140),
        bubble_bg=(255, 255, 255),
        bubble_border=(25, 60, 140),
        bubble_header_bg=(25, 60, 140),
        bubble_header_text=(255, 255, 255),
        bubble_body_text=(30, 30, 30),
        label_color=(25, 60, 140),
        side="left",
        cx=200,
        cy=450,
    )

    CHAR_B = dict(
        name="Instructor",
        gender="female",
        skin_base=(255, 218, 185),
        skin_shadow=(240, 203, 170),
        hair_color=(80, 50, 30),
        eye_color=(70, 100, 130),
        shirt_color=(255, 255, 255),
        accessory_color=(200, 50, 80),
        bubble_bg=(255, 255, 255),
        bubble_border=(120, 80, 160),
        bubble_header_bg=(120, 80, 160),
        bubble_header_text=(255, 255, 255),
        bubble_body_text=(30, 30, 30),
        label_color=(120, 80, 160),
        side="right",
        cx=1080,
        cy=450,
    )

    # ── Background ──────────────────────────────────────────────────────────
    BG_WALL          = (248, 248, 250)
    BG_FLOOR         = (235, 235, 238)
    BG_ACCENT        = (220, 220, 225)
    WHITEBOARD_BG    = (255, 255, 255)
    WHITEBOARD_FRAME = (180, 180, 185)

    # ── Bubble layout constants ─────────────────────────────────────────────
    BUBBLE_W     = 700
    BUBBLE_PAD   = 28
    HEADER_H     = 56
    LINE_H       = 38
    FONT_BODY_SZ = 26
    MAX_LINES    = 6

    def __init__(self):
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def create_animated_video_from_transcript(
        self,
        transcript: str,
        output_path: str,
        audio_path: Optional[str] = None,
        segment_durations: Optional[List[float]] = None,  # ← real durations from AudioService
    ) -> str:
        """
        Build a lip-synced classroom video from a dialogue transcript.

        Args:
            transcript:        Raw "A: ...\nB: ..." dialogue text.
            output_path:       Where to write the MP4.
            audio_path:        Path to the pre-generated MP3 (from AudioService).
            segment_durations: List of real per-segment durations (seconds),
                               including the inter-segment pause appended by
                               AudioService. If None, falls back to word-count
                               estimation (less accurate).
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        segments = self._parse_transcript(transcript)
        if not segments:
            raise Exception("No dialogue segments found in transcript.")

        # If real durations were supplied, apply them; otherwise estimate
        if segment_durations and len(segment_durations) == len(segments):
            print("[VideoService] ✅ Using real audio durations for lip sync")
            for i, seg in enumerate(segments):
                seg["duration"]    = segment_durations[i]
                # speaking_end = total segment duration minus the appended silence
                # so the mouth closes exactly when the voice stops
                speech_duration    = max(0.1, segment_durations[i] - _INTER_SEGMENT_PAUSE_SEC)
                seg["speak_end"]   = speech_duration
        else:
            print("[VideoService] ⚠️  No real durations supplied — using word-count estimate")
            for seg in segments:
                seg["speak_end"] = seg["duration"] * 0.90

        clips = []
        for seg in segments:
            char  = self.CHAR_A if seg["is_a"] else self.CHAR_B
            other = self.CHAR_B if seg["is_a"] else self.CHAR_A
            clips.append(
                self._build_clip(
                    text      = seg["text"],
                    speaker   = char,
                    listener  = other,
                    duration  = seg["duration"],
                    speak_end = seg["speak_end"],   # ← exact speech window
                )
            )

        video = concatenate_videoclips(clips, method="compose")

        if audio_path and os.path.exists(audio_path):
            audio = AudioFileClip(audio_path)
            # Trim/loop video to match audio exactly
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

    def create_simple_animated_video(
        self, segments: List[Dict], output_path: str,
        duration_per_segment: float = 5.0,
    ) -> str:
        speakers = ["User A", "User B"]
        lines = [f"{speakers[i%2]}: {seg.get('text','')}" for i, seg in enumerate(segments)]
        return self.create_animated_video_from_transcript("\n".join(lines), output_path)

    # ─────────────────────────────────────────────────────────────────────────
    # Clip builder — speak_end is now passed explicitly
    # ─────────────────────────────────────────────────────────────────────────

    def _build_clip(
        self,
        text: str,
        speaker: dict,
        listener: dict,
        duration: float,
        speak_end: float,       # seconds — mouth open only before this point
    ) -> VideoClip:
        W, H, FPS = self.W, self.H, self.FPS
        n = max(1, int(duration * FPS))

        img  = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(img)

        frames: List[np.ndarray] = []
        for f in range(n):
            t        = f / FPS
            progress = f / max(n - 1, 1)

            # ── is the speaker's voice still playing at this frame? ────────
            # speak_end is derived from the real audio duration, so this is
            # frame-accurate: mouth opens exactly when voice starts, closes
            # exactly when voice stops (pause gap at end = closed mouth).
            is_speaking = t < speak_end

            # Text reveal progress — only advances while speaking
            # Once speak_end is reached, bubble shows full text, static.
            if is_speaking:
                # Map t ∈ [0, speak_end] → progress ∈ [0, 1]
                text_progress = t / speak_end if speak_end > 0 else 1.0
            else:
                text_progress = 1.0   # all text visible during silence tail

            self._draw_bg(draw, W, H, t)
            self._draw_character(draw, listener, t, speaking=False)
            self._draw_character(draw, speaker,  t, speaking=is_speaking)
            self._draw_bubble(draw, speaker, text, text_progress, t, is_speaking)

            frames.append(np.array(img))

        def make_frame(t_sec: float) -> np.ndarray:
            return frames[min(int(t_sec * FPS), n - 1)]

        return VideoClip(make_frame, duration=duration)

    # ─────────────────────────────────────────────────────────────────────────
    # Background
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_bg(self, draw: ImageDraw.Draw, W: int, H: int, t: float):
        draw.rectangle([0, 0, W, H], fill=self.BG_WALL)

        floor_y = int(H * 0.65)
        draw.rectangle([0, floor_y, W, H], fill=self.BG_FLOOR)
        draw.line([(0, floor_y), (W, floor_y)], fill=self.BG_ACCENT, width=2)

        wb_w, wb_h = 900, 280
        wb_x = (W - wb_w) // 2
        wb_y = 35
        ft = 12
        draw.rounded_rectangle(
            [wb_x-ft, wb_y-ft, wb_x+wb_w+ft, wb_y+wb_h+ft],
            radius=8, fill=self.WHITEBOARD_FRAME)
        draw.rounded_rectangle(
            [wb_x, wb_y, wb_x+wb_w, wb_y+wb_h],
            radius=4, fill=self.WHITEBOARD_BG)
        title_h = 45
        draw.rounded_rectangle(
            [wb_x, wb_y, wb_x+wb_w, wb_y+title_h],
            radius=4, fill=(230, 240, 250))
        tf = _load_font("bold", 26)
        title = "CA Educational Session"
        tb = draw.textbbox((0, 0), title, font=tf)
        tw = tb[2] - tb[0]
        draw.text((wb_x + (wb_w-tw)//2, wb_y+10), title, fill=(40, 80, 140), font=tf)
        for i in range(3):
            y = wb_y + title_h + 20 + i*70
            draw.line([(wb_x+30, y), (wb_x+wb_w-30, y)], fill=(240, 245, 250), width=1)

        draw.rectangle([0, 0, 40, H], fill=(240, 240, 243))
        draw.rectangle([W-40, 0, W, H], fill=(240, 240, 243))

    # ─────────────────────────────────────────────────────────────────────────
    # Character drawing
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_character(self, draw: ImageDraw.Draw, char: dict, t: float, speaking: bool):
        cx, cy = char["cx"], char["cy"]
        gender = char.get("gender", "male")

        amp = 3 if speaking else 1
        bob = int(math.sin(t * 2.5) * amp)
        cy = cy + bob

        draw.ellipse([cx-60, cy+125, cx+60, cy+138], fill=(200, 200, 205))

        if gender == "male":
            self._draw_male_character(draw, cx, cy, char, t, speaking)
        else:
            self._draw_female_character(draw, cx, cy, char, t, speaking)

        fn = _load_font("bold", 24)
        bbox = draw.textbbox((0, 0), char["name"], font=fn)
        lw = bbox[2] - bbox[0]
        draw.text((cx - lw//2, cy+145), char["name"], fill=char["label_color"], font=fn)

    def _draw_male_character(self, draw, cx, cy, char, t, speaking):
        skin        = char["skin_base"]
        skin_shadow = char["skin_shadow"]
        hair        = char["hair_color"]
        shirt       = char["shirt_color"]
        tie         = char["tie_color"]
        eye         = char["eye_color"]
        facial_hair = char.get("facial_hair", hair)

        draw.rounded_rectangle([cx-50, cy+35, cx+50, cy+135],
                                radius=18, fill=shirt, outline=(220, 220, 220), width=2)
        draw.polygon([(cx-15,cy+35),(cx-25,cy+50),(cx-10,cy+55),
                      (cx,cy+45),(cx+10,cy+55),(cx+25,cy+50),(cx+15,cy+35)],
                     fill=shirt, outline=(200, 200, 200))
        draw.polygon([(cx,cy+45),(cx-8,cy+50),(cx-6,cy+100),
                      (cx,cy+105),(cx+6,cy+100),(cx+8,cy+50)], fill=tie)
        draw.polygon([(cx-8,cy+45),(cx+8,cy+45),(cx+6,cy+52),(cx-6,cy+52)], fill=tie)

        arm_swing = int(math.sin(t * 2.3) * 6) if speaking else 0
        draw.rounded_rectangle([cx-68, cy+50+arm_swing, cx-48, cy+115+arm_swing],
                                radius=10, fill=shirt, outline=(210,210,210), width=1)
        draw.rounded_rectangle([cx+48, cy+50-arm_swing, cx+68, cy+115-arm_swing],
                                radius=10, fill=shirt, outline=(210,210,210), width=1)
        draw.ellipse([cx-72, cy+110+arm_swing, cx-50, cy+125+arm_swing], fill=skin)
        draw.ellipse([cx+50, cy+110-arm_swing, cx+72, cy+125-arm_swing], fill=skin)

        draw.rectangle([cx-14, cy+18, cx+14, cy+42], fill=skin)
        draw.rectangle([cx-14, cy+35, cx+14, cy+42], fill=skin_shadow)

        hw, hh = 52, 58
        draw.ellipse([cx-hw, cy-hh, cx+hw, cy+hh-15], fill=skin)
        draw.ellipse([cx-35, cy-50, cx+35, cy-25], fill=self._lighten(skin, 10))
        draw.ellipse([cx-hw, cy-hh, cx+hw, cy-10], fill=hair)
        draw.rectangle([cx-hw, cy-hh, cx-35, cy-5], fill=hair)
        draw.rectangle([cx+35, cy-hh, cx+hw, cy-5], fill=hair)
        draw.line([(cx-10, cy-50),(cx+5, cy-35)], fill=self._darken(hair,30), width=2)

        draw.ellipse([cx-hw-5, cy-8, cx-hw+12, cy+12], fill=skin_shadow)
        draw.ellipse([cx+hw-12, cy-8, cx+hw+5, cy+12], fill=skin_shadow)

        blink = abs(math.sin(t * 0.45 + char["cx"] * 0.01)) > 0.975
        if blink:
            draw.line([(cx-28, cy-15),(cx-14, cy-15)], fill=(60,40,30), width=3)
            draw.line([(cx+14, cy-15),(cx+28, cy-15)], fill=(60,40,30), width=3)
        else:
            draw.ellipse([cx-30, cy-20, cx-12, cy-10], fill=(250,250,255))
            draw.ellipse([cx-26, cy-18, cx-16, cy-12], fill=eye)
            draw.ellipse([cx-24, cy-17, cx-19, cy-13], fill=(40,40,50))
            draw.ellipse([cx-22, cy-17, cx-20, cy-15], fill=(255,255,255))
            draw.ellipse([cx+12, cy-20, cx+30, cy-10], fill=(250,250,255))
            draw.ellipse([cx+16, cy-18, cx+26, cy-12], fill=eye)
            draw.ellipse([cx+19, cy-17, cx+24, cy-13], fill=(40,40,50))
            draw.ellipse([cx+20, cy-17, cx+22, cy-15], fill=(255,255,255))

        draw.arc([cx-32, cy-28, cx-10, cy-22], start=0, end=180, fill=hair, width=4)
        draw.arc([cx+10, cy-28, cx+32, cy-22], start=0, end=180, fill=hair, width=4)

        draw.line([(cx-3, cy-8),(cx-3, cy+5)], fill=skin_shadow, width=2)
        draw.ellipse([cx-5, cy+3, cx-1, cy+7], fill=skin_shadow)
        draw.ellipse([cx+1, cy+3, cx+5, cy+7], fill=skin_shadow)

        # ── Lip sync: mouth opens ONLY while is_speaking=True ─────────────
        if speaking:
            mouth_open = int(abs(math.sin(t * 8.0)) * 12) + 4
            draw.ellipse([cx-16, cy+12, cx+16, cy+12+mouth_open], fill=(160,80,80))
            if mouth_open > 6:
                draw.ellipse([cx-14, cy+13, cx+14, cy+17], fill=(245,245,250))
        else:
            # Closed mouth — neutral/slight smile
            draw.arc([cx-16, cy+10, cx+16, cy+22], start=10, end=170,
                     fill=(140, 70, 70), width=3)

        if facial_hair:
            stubble = self._darken(facial_hair, 10)
            draw.arc([cx-40, cy-10, cx+40, cy+35], start=25, end=155, fill=stubble, width=2)
            draw.arc([cx-16, cy+5, cx+16, cy+15], start=180, end=360, fill=stubble, width=2)

    def _draw_female_character(self, draw, cx, cy, char, t, speaking):
        skin        = char["skin_base"]
        skin_shadow = char["skin_shadow"]
        hair        = char["hair_color"]
        shirt       = char["shirt_color"]
        accessory   = char.get("accessory_color", (200, 50, 80))
        eye         = char["eye_color"]

        draw.rounded_rectangle([cx-48, cy+35, cx+48, cy+135],
                                radius=18, fill=shirt, outline=(220,220,220), width=2)
        draw.polygon([(cx-18,cy+35),(cx-28,cy+45),(cx-20,cy+55),
                      (cx,cy+40),(cx+20,cy+55),(cx+28,cy+45),(cx+18,cy+35)],
                     fill=shirt, outline=(200,200,200))
        draw.ellipse([cx-20, cy+45, cx+20, cy+50], outline=accessory, width=3)
        draw.ellipse([cx-3, cy+48, cx+3, cy+54], fill=accessory)

        arm_swing = int(math.sin(t * 2.3) * 5) if speaking else 0
        draw.rounded_rectangle([cx-66, cy+50+arm_swing, cx-46, cy+115+arm_swing],
                                radius=10, fill=shirt, outline=(210,210,210), width=1)
        draw.rounded_rectangle([cx+46, cy+50-arm_swing, cx+66, cy+115-arm_swing],
                                radius=10, fill=shirt, outline=(210,210,210), width=1)
        draw.ellipse([cx-70, cy+110+arm_swing, cx-48, cy+125+arm_swing], fill=skin)
        draw.ellipse([cx+48, cy+110-arm_swing, cx+70, cy+125-arm_swing], fill=skin)

        draw.rectangle([cx-12, cy+18, cx+12, cy+40], fill=skin)
        draw.rectangle([cx-12, cy+35, cx+12, cy+40], fill=skin_shadow)

        hw, hh = 48, 56
        draw.ellipse([cx-hw, cy-hh, cx+hw, cy+hh-15], fill=skin)
        draw.ellipse([cx-32, cy-48, cx+32, cy-22], fill=self._lighten(skin,10))
        draw.ellipse([cx-hw-5, cy-hh-5, cx+hw+5, cy-8], fill=hair)
        draw.ellipse([cx-hw-8, cy-40, cx-25, cy+20], fill=hair)
        draw.ellipse([cx+25, cy-40, cx+hw+8, cy+20], fill=hair)
        draw.ellipse([cx-25, cy-35, cx+25, cy-5], fill=hair)
        draw.arc([cx-35, cy-50, cx+35, cy-20], start=45, end=135,
                 fill=self._lighten(hair,40), width=3)

        draw.ellipse([cx-hw-3, cy-6, cx-hw+10, cy+10], fill=skin_shadow)
        draw.ellipse([cx+hw-10, cy-6, cx+hw+3, cy+10], fill=skin_shadow)
        draw.ellipse([cx-hw+2, cy+8, cx-hw+8, cy+14], fill=accessory)
        draw.ellipse([cx+hw-8, cy+8, cx+hw-2, cy+14], fill=accessory)

        blink = abs(math.sin(t * 0.45 + char["cx"] * 0.01)) > 0.975
        if blink:
            draw.line([(cx-30, cy-16),(cx-12, cy-16)], fill=(50,35,25), width=3)
            draw.line([(cx+12, cy-16),(cx+30, cy-16)], fill=(50,35,25), width=3)
            for i in range(3):
                lx = cx-26 + i*6
                draw.line([(lx, cy-16),(lx-2, cy-20)], fill=(40,30,20), width=2)
                rx = cx+14 + i*6
                draw.line([(rx, cy-16),(rx+2, cy-20)], fill=(40,30,20), width=2)
        else:
            draw.ellipse([cx-32, cy-22, cx-10, cy-10], fill=(252,252,255))
            draw.ellipse([cx-28, cy-20, cx-14, cy-12], fill=eye)
            draw.ellipse([cx-25, cy-18, cx-17, cy-13], fill=(35,35,45))
            draw.ellipse([cx-23, cy-18, cx-20, cy-15], fill=(255,255,255))
            draw.ellipse([cx+10, cy-22, cx+32, cy-10], fill=(252,252,255))
            draw.ellipse([cx+14, cy-20, cx+28, cy-12], fill=eye)
            draw.ellipse([cx+17, cy-18, cx+25, cy-13], fill=(35,35,45))
            draw.ellipse([cx+20, cy-18, cx+23, cy-15], fill=(255,255,255))
            for i in range(4):
                lx = cx-29 + i*6
                draw.line([(lx, cy-22),(lx-1, cy-25)], fill=(40,30,20), width=2)
                rx = cx+12 + i*6
                draw.line([(rx, cy-22),(rx+1, cy-25)], fill=(40,30,20), width=2)

        draw.arc([cx-34, cy-30, cx-8, cy-24], start=10, end=170,
                 fill=self._darken(hair,20), width=3)
        draw.arc([cx+8, cy-30, cx+34, cy-24], start=10, end=170,
                 fill=self._darken(hair,20), width=3)
        draw.line([(cx-2, cy-6),(cx-2, cy+4)], fill=skin_shadow, width=1)
        draw.ellipse([cx-4, cy+2, cx-1, cy+5], fill=skin_shadow)
        draw.ellipse([cx+1, cy+2, cx+4, cy+5], fill=skin_shadow)

        # ── Lip sync: only open while speaking ────────────────────────────
        if speaking:
            mouth_open = int(abs(math.sin(t * 8.0)) * 10) + 3
            draw.arc([cx-14, cy+10, cx+14, cy+16], start=180, end=360,
                     fill=(180,100,110), width=3)
            draw.arc([cx-14, cy+14, cx+14, cy+14+mouth_open], start=0, end=180,
                     fill=(190,110,120), width=3)
            if mouth_open > 5:
                draw.ellipse([cx-12, cy+14, cx+12, cy+14+mouth_open-2],
                             fill=(140,70,80))
        else:
            draw.arc([cx-14, cy+10, cx+14, cy+16], start=180, end=360,
                     fill=(180,100,110), width=3)
            draw.arc([cx-14, cy+14, cx+14, cy+22], start=10, end=170,
                     fill=(190,110,120), width=3)
            draw.arc([cx-14, cy+12, cx+14, cy+18], start=10, end=170,
                     fill=(160,90,100), width=2)

    # ─────────────────────────────────────────────────────────────────────────
    # Speech Bubble — dynamic height, no clipping
    # ─────────────────────────────────────────────────────────────────────────

    def _bubble_height(self, n_lines: int) -> int:
        return self.HEADER_H + self.BUBBLE_PAD * 2 + n_lines * self.LINE_H + 8

    def _draw_bubble(self, draw: ImageDraw.Draw, speaker: dict,
                     text: str, progress: float, t: float, is_speaking: bool):
        W, H   = self.W, self.H
        side   = speaker["side"]
        cx     = speaker["cx"]
        bw     = self.BUBBLE_W
        pad    = self.BUBBLE_PAD
        hh     = self.HEADER_H
        r      = 18

        bg     = speaker["bubble_bg"]
        border = speaker["bubble_border"]
        hdr_bg = speaker["bubble_header_bg"]
        hdr_fg = speaker["bubble_header_text"]
        txt_fg = speaker["bubble_body_text"]

        margin = 36
        bx = margin if side == "left" else W - margin - bw

        fn_body    = _load_font("regular", self.FONT_BODY_SZ)
        max_text_w = bw - pad * 2
        all_lines  = _wrap_pixels(draw, text, fn_body, max_text_w)

        # Text reveal tied to progress (which is now tied to real speak_end)
        reveal       = min(1.0, progress * 1.10)
        full_text    = " ".join(all_lines)
        chars_shown  = int(len(full_text) * reveal)
        visible_text = full_text[:chars_shown]
        vis_lines    = _wrap_pixels(draw, visible_text, fn_body, max_text_w)

        max_lines   = min(self.MAX_LINES, max(1, len(all_lines)))
        bh          = self._bubble_height(max_lines)

        if len(vis_lines) > max_lines:
            display_lines = vis_lines[-max_lines:]
        else:
            display_lines = vis_lines

        by = 22

        draw.rounded_rectangle([bx, by, bx+bw, by+bh],
                                radius=r, fill=bg, outline=border, width=3)

        draw.rounded_rectangle([bx, by, bx+bw, by+hh+r], radius=r, fill=hdr_bg)
        draw.rectangle([bx+1, by+hh, bx+bw-1, by+hh+r], fill=hdr_bg)
        draw.line([(bx, by+hh), (bx+bw, by+hh)], fill=border, width=2)

        av_r  = 16
        av_x  = bx + pad + av_r
        av_y  = by + hh // 2
        av_fill = self._lighten(hdr_bg, 65)
        draw.ellipse([av_x-av_r, av_y-av_r, av_x+av_r, av_y+av_r], fill=av_fill)
        fi  = _load_font("bold", 18)
        ini = speaker["name"][0].upper()
        ib  = draw.textbbox((0, 0), ini, font=fi)
        draw.text((av_x-(ib[2]-ib[0])//2, av_y-(ib[3]-ib[1])//2-1),
                  ini, fill=hdr_bg, font=fi)

        fn_name = _load_font("bold", 26)
        draw.text((av_x+av_r+14, by+hh//2-13), speaker["name"], fill=hdr_fg, font=fn_name)

        # Speaking dots — only animate while voice is active
        if is_speaking:
            dot_base_x = bx + bw - pad - 44
            dot_y      = by + hh // 2
            for d in range(3):
                phase      = (t * 3.2 + d * 0.55) % 3
                brightness = max(0.3, math.sin(phase * math.pi))
                dc = tuple(int(c * brightness) for c in hdr_fg)
                dx = dot_base_x + d * 15
                draw.ellipse([dx-4, dot_y-4, dx+4, dot_y+4], fill=dc)

        tail_bottom_y = by + bh
        tail_tip_y    = speaker["cy"] - 75
        tail_tip_x    = cx
        if side == "left":
            t1x, t2x = bx+55, bx+105
        else:
            t1x = bx + bw - 105
            t2x = bx + bw - 55
        draw.polygon([(t1x, tail_bottom_y),(t2x, tail_bottom_y),(tail_tip_x, tail_tip_y)],
                     fill=bg)
        draw.line([(t1x, tail_bottom_y),(tail_tip_x, tail_tip_y)], fill=border, width=3)
        draw.line([(tail_tip_x, tail_tip_y),(t2x, tail_bottom_y)], fill=border, width=3)

        tx = bx + pad
        ty = by + hh + pad

        shadow_color = tuple(max(0, c-30) for c in bg)

        for ln in display_lines:
            draw.text((tx+1, ty+1), ln, fill=shadow_color, font=fn_body)
            draw.text((tx, ty), ln, fill=txt_fg, font=fn_body)
            ty += self.LINE_H

        if chars_shown < len(full_text) and is_speaking and int(t * 4) % 2 == 0:
            last_ln = display_lines[-1] if display_lines else ""
            cur_x   = tx + int(draw.textlength(last_ln, font=fn_body)) + 4
            cur_y   = ty - self.LINE_H
            draw.rectangle([cur_x, cur_y+5, cur_x+10, cur_y+30], fill=border)

    # ─────────────────────────────────────────────────────────────────────────
    # Transcript parser — word-count duration used only as fallback
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_transcript(self, transcript: str) -> List[Dict]:
        segments: List[Dict] = []
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
            duration = max(4.5, (words / 120) * 60)   # fallback estimate
            segments.append({
                "text":      text,
                "is_a":      is_a,
                "duration":  duration,
                "speak_end": duration * 0.90,   # will be overridden if real durations supplied
            })

        if not segments:
            non_empty = [_clean(l) for l in lines if l.strip()]
            for i in range(0, min(len(non_empty), 40), 6):
                text = " ".join(non_empty[i:i+6])
                if not text:
                    continue
                words    = len(text.split())
                duration = max(4.5, (words / 120) * 60)
                segments.append({
                    "text":      text,
                    "is_a":      i % 2 == 0,
                    "duration":  duration,
                    "speak_end": duration * 0.90,
                })

        return segments

    # ─────────────────────────────────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _lighten(color: tuple, amount: int) -> tuple:
        return tuple(min(255, c + amount) for c in color)

    @staticmethod
    def _darken(color: tuple, amount: int) -> tuple:
        return tuple(max(0, c - amount) for c in color)
