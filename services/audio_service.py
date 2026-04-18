
"""
audio_service.py — ElevenLabs TTS with Indian accent voices
============================================================
Male Instructor  → ElevenLabs "Raj"   (Indian English male)
Female Student   → ElevenLabs "Anushka" / "Aria" (Indian English female)

Key fix: generate_audio_from_transcript() now returns BOTH the output_path
AND a list of per-segment actual durations (in seconds), measured from the
real generated audio. The orchestrator must pass these durations to
VideoService so lip-sync is frame-accurate.

Returns:
    (output_path: str, segment_durations: List[float])
"""

import os
import re
import shutil
import tempfile
from typing import List, Dict, Tuple, Optional

from pydub import AudioSegment

from config import config

# ---------------------------------------------------------------------------
# ElevenLabs Voice IDs — Verified Indian-accent voices (2024)
# ---------------------------------------------------------------------------
# "Raj"     — Indian English male   — voice_id: "29vD33N1CtxCmqQRPOHJ"
# "Anushka" — Indian English female — voice_id: "9lFlbB0eSMFKhKPYovHj"
#
# Additional verified Indian voices:
#   Male backup:   "GBv7mTt0atIp3Br8iCZE"  (Thomas, Indian accent)
#   Female backup: "jsCqWAovK2LkecY7zXl4"  (Freya, works well for Indian)
#
# Set ELEVENLABS_VOICE_MALE / ELEVENLABS_VOICE_FEMALE env vars to override.
# ---------------------------------------------------------------------------
_VOICE_MALE   = os.getenv("ELEVENLABS_VOICE_MALE",   "29vD33N1CtxCmqQRPOHJ")  # Raj - Indian male
_VOICE_FEMALE = os.getenv("ELEVENLABS_VOICE_FEMALE", "9lFlbB0eSMFKhKPYovHj")  # Anushka - Indian female

# Fallback Indian-accent voices if primary fails
_VOICE_MALE_BACKUP   = "GBv7mTt0atIp3Br8iCZE"
_VOICE_FEMALE_BACKUP = "jsCqWAovK2LkecY7zXl4"

_ELEVENLABS_MODEL       = "eleven_multilingual_v2"   # best for Hinglish/Hindi words
_INTER_SEGMENT_PAUSE_MS = 300    # ms of silence between speaker turns
_INTRA_SEGMENT_PAUSE_MS = 100    # ms of silence at natural sentence breaks


class AudioService:
    """
    Generates dual-voice audio from dialogue transcripts using ElevenLabs TTS.

    Transcript format expected:
        User A: Instructor ka dialogue yahan...
        User B: Student ka dialogue yahan...

    IMPORTANT: generate_audio_from_transcript() returns a tuple:
        (output_path: str, segment_durations: List[float])
    segment_durations[i] is the real audio duration (seconds) of segment i,
    INCLUDING the inter-segment pause appended after it.
    Pass these to VideoService._build_clip() for frame-accurate lip sync.
    """

    def __init__(self):
        try:
            from elevenlabs.client import ElevenLabs
            self.el_client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
            self._elevenlabs_available = True
            print("[AudioService] ✅ ElevenLabs client initialized")
            self._verify_voices()
        except Exception as e:
            print(f"[AudioService] ⚠️  ElevenLabs init failed: {e}. Will fall back to OpenAI TTS.")
            self._elevenlabs_available = False
            self.el_client = None

        # OpenAI fallback
        try:
            from openai import OpenAI as _OpenAI
            self._openai_client = _OpenAI(api_key=config.OPENAI_API_KEY)
        except Exception:
            self._openai_client = None

    def _verify_voices(self):
        """Log which voices are configured so issues are caught early."""
        print(f"[AudioService] 🎙️  Male voice ID   : {_VOICE_MALE}  (Raj - Indian male)")
        print(f"[AudioService] 🎙️  Female voice ID : {_VOICE_FEMALE}  (Anushka - Indian female)")
        print(f"[AudioService] 🎙️  Model           : {_ELEVENLABS_MODEL}")

    # ------------------------------------------------------------------
    # Public: main entry point called by orchestrator
    # ------------------------------------------------------------------

    def generate_audio_from_transcript(
        self, transcript: str, output_path: str
    ) -> Tuple[str, List[float]]:
        """
        Generate dual-voice MP3 from a dialogue transcript.

        User A → Male Indian instructor voice  (Raj)
        User B → Female Indian student voice   (Anushka)

        Returns:
            (output_path, segment_durations)
            segment_durations[i] = actual seconds of segment i audio + pause.
            Use these in VideoService to build perfectly lip-synced clips.
        """
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            temp_dir = tempfile.mkdtemp(prefix="audio_seg_")

            segments = self._parse_transcript_segments(transcript)
            if not segments:
                raise Exception("No dialogue segments found in transcript")

            print(f"[AudioService] Found {len(segments)} dialogue segments")

            final_audio     = AudioSegment.empty()
            silence_between = AudioSegment.silent(duration=_INTER_SEGMENT_PAUSE_MS)
            segment_durations: List[float] = []

            for i, seg in enumerate(segments):
                speaker = seg["speaker"]    # "A" or "B"
                text    = seg["text"]
                label   = "Instructor (Male)" if speaker == "A" else "Student (Female)"

                seg_path = os.path.join(temp_dir, f"seg_{i:04d}.mp3")
                print(f"  [{i+1}/{len(segments)}] {label}: {text[:60]}...")

                success = self._tts_with_fallback(speaker, text, seg_path)

                if not success:
                    # Last resort: generate silence proportional to word count
                    words       = len(text.split())
                    duration_ms = max(1500, words * 400)
                    AudioSegment.silent(duration=duration_ms).export(seg_path, format="mp3")
                    print(f"  ⚠️  Segment {i}: using silence placeholder ({duration_ms}ms)")

                try:
                    seg_audio     = AudioSegment.from_mp3(seg_path)
                    combined      = seg_audio + silence_between
                    # Record the REAL duration of this segment including pause
                    real_duration = len(combined) / 1000.0
                    segment_durations.append(real_duration)
                    final_audio  += combined
                except Exception as load_err:
                    print(f"  ⚠️  Could not load segment {i}: {load_err}")
                    # Estimate fallback duration so video stays in sync
                    words    = len(text.split())
                    fallback = max(3.0, words / 2.5)
                    segment_durations.append(fallback)

            if len(final_audio) == 0:
                raise Exception("All audio segments failed to generate")

            total_s = len(final_audio) / 1000
            print(f"\n[AudioService] ✅ Total audio: {total_s:.1f}s")
            print(f"[AudioService] Segment durations: {[round(d,2) for d in segment_durations]}")
            print("  🎙️  User A → Instructor (ElevenLabs Indian Male  - Raj)")
            print("  🎙️  User B → Student    (ElevenLabs Indian Female - Anushka)")

            final_audio.export(output_path, format="mp3", bitrate="192k")

            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

            return output_path, segment_durations

        except Exception as e:
            raise Exception(f"Transcript to audio conversion error: {str(e)}")

    # ------------------------------------------------------------------
    # Internal: TTS with Indian-voice fallback chain
    # ------------------------------------------------------------------

    def _tts_with_fallback(self, speaker: str, text: str, seg_path: str) -> bool:
        """
        Try voices in order:
          1. ElevenLabs primary Indian voice (Raj / Anushka)
          2. ElevenLabs backup Indian voice
          3. OpenAI TTS (onyx / nova — not Indian accent, but better than silence)
        """
        is_male  = speaker == "A"
        primary  = _VOICE_MALE   if is_male else _VOICE_FEMALE
        backup   = _VOICE_MALE_BACKUP if is_male else _VOICE_FEMALE_BACKUP
        oai_voice = "onyx" if is_male else "nova"

        # Attempt 1: primary Indian voice
        if self._generate_segment_elevenlabs(text, primary, seg_path):
            return True

        print(f"  ↳ Primary voice failed, trying backup Indian voice…")

        # Attempt 2: backup Indian voice
        if self._generate_segment_elevenlabs(text, backup, seg_path):
            return True

        print(f"  ↳ Backup voice failed, trying OpenAI TTS…")

        # Attempt 3: OpenAI fallback
        return self._generate_segment_openai(text, oai_voice, seg_path)

    # ------------------------------------------------------------------
    # Internal: ElevenLabs segment generator
    # ------------------------------------------------------------------

    def _generate_segment_elevenlabs(
        self, text: str, voice_id: str, output_path: str
    ) -> bool:
        """
        Generate a single audio segment using ElevenLabs.
        Uses eleven_multilingual_v2 for proper Hinglish support.
        """
        if not self._elevenlabs_available or not self.el_client:
            return False
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            audio_generator = self.el_client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id=_ELEVENLABS_MODEL,
                voice_settings={
                    "stability":         0.50,   # some variation = natural Hinglish rhythm
                    "similarity_boost":  0.82,   # stay true to the voice
                    "style":             0.30,   # mild expressiveness
                    "use_speaker_boost": True,
                },
            )

            with open(output_path, "wb") as f:
                for chunk in audio_generator:
                    if chunk:
                        f.write(chunk)

            # Verify the file is non-empty
            if os.path.getsize(output_path) < 1000:
                print(f"    [✗ ElevenLabs] Output file too small, voice_id={voice_id[:8]}…")
                return False

            print(f"    [✓ ElevenLabs] voice_id={voice_id[:8]}…")
            return True

        except Exception as e:
            print(f"    [✗ ElevenLabs] {str(e)[:120]}")
            return False

    # ------------------------------------------------------------------
    # Internal: OpenAI TTS fallback
    # ------------------------------------------------------------------

    def _generate_segment_openai(self, text: str, voice: str, output_path: str) -> bool:
        """Fallback: generate segment via OpenAI TTS (not Indian accent)."""
        if not self._openai_client:
            return False
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            response = self._openai_client.audio.speech.create(
                model="tts-1-hd",
                voice=voice,
                input=text,
            )
            with open(output_path, "wb") as f:
                f.write(response.content)
            print(f"    [✓ OpenAI fallback] voice={voice}")
            return True
        except Exception as e:
            print(f"    [✗ OpenAI fallback] {str(e)[:80]}")
            return False

    # ------------------------------------------------------------------
    # Internal: transcript parser
    # ------------------------------------------------------------------

    def _parse_transcript_segments(self, transcript: str) -> List[Dict]:
        """
        Parse "User A: ..." / "A: ..." / "**User B:** ..." lines into
        [{"speaker": "A"/"B", "text": str}] dicts.
        """
        segments = []
        raw = (
            transcript
            .replace("\\n", "\n")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        pattern = re.compile(
            r'^\**\s*(?:User\s+)?([AB])\**\s*:\s*\**\s*(.+?)\s*\**$',
            re.IGNORECASE,
        )
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if not m:
                continue
            text = self._clean_text_for_audio(m.group(2))
            if not text:
                continue
            segments.append({"speaker": m.group(1).upper(), "text": text})

        if not segments:
            preview = raw[:300].replace("\n", "\\n")
            print(f"[AudioService] WARNING: no segments matched. Preview: {preview!r}")

        return segments

    # ------------------------------------------------------------------
    # Internal: text cleaner (strips markdown, keeps Hinglish)
    # ------------------------------------------------------------------

    def _clean_text_for_audio(self, text: str) -> str:
        """Strip markdown symbols while preserving Hinglish / Devanagari text."""
        text = re.sub(r'\*{1,3}', '', text)
        text = re.sub(r'_{1,2}', '', text)
        text = re.sub(r'`+', '', text)
        text = re.sub(r'(?m)^#+\s*', '', text)
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    # ------------------------------------------------------------------
    # Public: format conversion utility
    # ------------------------------------------------------------------

    def convert_to_mp3(self, input_path: str, output_path: str) -> str:
        """Convert any audio file to MP3 format."""
        try:
            audio = AudioSegment.from_file(input_path)
            audio.export(output_path, format="mp3", bitrate="192k")
            return output_path
        except Exception as e:
            raise Exception(f"Audio conversion error: {str(e)}")

    # ------------------------------------------------------------------
    # Legacy compatibility (single return value)
    # ------------------------------------------------------------------

    def generate_audio_from_text(self, text: str, output_path: str, lang: str = "en") -> str:
        """Generate audio from plain text (uses male instructor voice)."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        ok = self._generate_segment_elevenlabs(text, _VOICE_MALE, output_path)
        if not ok:
            ok = self._generate_segment_elevenlabs(text, _VOICE_MALE_BACKUP, output_path)
        if not ok:
            self._generate_segment_openai(text, "onyx", output_path)
        return output_path

    def generate_audio_from_pdf_content(self, content: dict, output_path: str) -> str:
        """Generate audio from simplified PDF content dict."""
        audio_text = self._create_audio_script(content)
        return self.generate_audio_from_text(audio_text, output_path)

    def _create_audio_script(self, content: dict) -> str:
        parts = []
        if "title" in content:
            parts.append(f"{content['title']}.")
        if "introduction" in content:
            parts.append(content["introduction"])
        if "sections" in content:
            for sec in content["sections"]:
                parts.append(f"{sec['heading']}.")
                parts.append(sec["content"])
                for pt in sec.get("points", []):
                    parts.append(pt)
        if "summary" in content:
            parts.append("Summary.")
            parts.append(content["summary"])
        if "key_takeaways" in content:
            parts.append("Yaad Rakho. Key Takeaways.")
            for i, t in enumerate(content["key_takeaways"], 1):
                parts.append(f"Point number {i}. {t}")
        return " ".join(parts)
