"""
audio_service.py — ElevenLabs TTS with Indian accent voices
============================================================
Uses direct httpx calls (no SDK) so the API key is always
explicitly passed — no risk of None slipping through.

Male Instructor  → ElevenLabs "Raj"    (Indian English male)
Female Student   → ElevenLabs "Sana"   (Indian English female)

Returns:
    (output_path: str, segment_durations: List[float])
"""

import os
import re
import shutil
import tempfile
import httpx
from typing import List, Dict, Tuple

from pydub import AudioSegment

from config import config

# ---------------------------------------------------------------------------
# Voice IDs
# ---------------------------------------------------------------------------
_VOICE_MALE          = os.getenv("ELEVENLABS_VOICE_MALE",   "29vD33N1CtxCmqQRPOHJ")  # Raj
_VOICE_FEMALE        = os.getenv("ELEVENLABS_VOICE_FEMALE", "tKZQTIqwDrPzLv6MrPxF")  # Sana
_VOICE_MALE_BACKUP   = "ojNjxYKrSUDwsRrANSYc"
_VOICE_FEMALE_BACKUP = "Txmsc1sMMJjB3YTRQgpO"

_ELEVENLABS_MODEL    = "eleven_multilingual_v2"
_ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"   # never changes
_INTER_SEGMENT_PAUSE_MS = 300
_REQUEST_TIMEOUT        = 60.0


class AudioService:
    """
    Generates dual-voice audio from dialogue transcripts.
    Uses direct httpx calls to ElevenLabs — no SDK dependency.
    """

    def __init__(self):
        # Resolve API key — env var takes priority over config
        self._el_key = (
            os.getenv("ELEVENLABS_API_KEY")
            or getattr(config, "ELEVENLABS_API_KEY", None)
        )

        if not self._el_key:
            print("[AudioService] ⚠️  ELEVENLABS_API_KEY is None/empty — ElevenLabs disabled")
            self._elevenlabs_available = False
        else:
            print(f"[AudioService] ✅ ElevenLabs key loaded: {self._el_key[:8]}…")
            print(f"[AudioService] 🎙️  Male   voice : {_VOICE_MALE}")
            print(f"[AudioService] 🎙️  Female voice : {_VOICE_FEMALE}")
            print(f"[AudioService] 🎙️  Model        : {_ELEVENLABS_MODEL}")
            self._elevenlabs_available = True

        # OpenAI fallback
        try:
            from openai import OpenAI as _OpenAI
            oai_key = (
                os.getenv("OPENAI_API_KEY")
                or getattr(config, "OPENAI_API_KEY", None)
            )
            self._openai_client = _OpenAI(api_key=oai_key)
            print("[AudioService] ✅ OpenAI fallback ready")
        except Exception as e:
            print(f"[AudioService] ⚠️  OpenAI fallback unavailable: {e}")
            self._openai_client = None

    # ------------------------------------------------------------------
    # Public: main entry point
    # ------------------------------------------------------------------

    def generate_audio_from_transcript(
        self, transcript: str, output_path: str
    ) -> Tuple[str, List[float]]:
        """
        Generate dual-voice MP3. Returns (output_path, segment_durations).
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="audio_seg_")

        try:
            segments = self._parse_transcript_segments(transcript)
            if not segments:
                raise Exception("No dialogue segments found in transcript")

            print(f"[AudioService] Found {len(segments)} dialogue segments")

            final_audio     = AudioSegment.empty()
            silence_between = AudioSegment.silent(duration=_INTER_SEGMENT_PAUSE_MS)
            segment_durations: List[float] = []

            for i, seg in enumerate(segments):
                speaker  = seg["speaker"]
                text     = seg["text"]
                label    = "Instructor (Male)" if speaker == "A" else "Student (Female)"
                seg_path = os.path.join(temp_dir, f"seg_{i:04d}.mp3")

                print(f"  [{i+1}/{len(segments)}] {label}: {text[:70]}...")

                success = self._tts_with_fallback(speaker, text, seg_path)

                if not success:
                    words       = len(text.split())
                    duration_ms = max(1500, words * 400)
                    AudioSegment.silent(duration=duration_ms).export(seg_path, format="mp3")
                    print(f"  ⚠️  Segment {i}: silence placeholder ({duration_ms}ms)")

                try:
                    seg_audio = AudioSegment.from_mp3(seg_path)
                    combined  = seg_audio + silence_between
                    segment_durations.append(len(combined) / 1000.0)
                    final_audio += combined
                except Exception as load_err:
                    print(f"  ⚠️  Could not load segment {i}: {load_err}")
                    segment_durations.append(max(3.0, len(text.split()) / 2.5))

            if len(final_audio) == 0:
                raise Exception("All audio segments failed to generate")

            total_s = len(final_audio) / 1000
            print(f"\n[AudioService] ✅ Total audio: {total_s:.1f}s")
            print(f"[AudioService] Durations: {[round(d, 2) for d in segment_durations]}")

            final_audio.export(output_path, format="mp3", bitrate="192k")
            return output_path, segment_durations

        finally:
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal: fallback chain
    # ------------------------------------------------------------------

    def _tts_with_fallback(self, speaker: str, text: str, seg_path: str) -> bool:
        is_male   = speaker == "A"
        primary   = _VOICE_MALE          if is_male else _VOICE_FEMALE
        backup    = _VOICE_MALE_BACKUP   if is_male else _VOICE_FEMALE_BACKUP
        oai_voice = "onyx"               if is_male else "nova"

        if self._elevenlabs_tts(text, primary, seg_path):
            return True
        print("  ↳ Primary voice failed, trying backup…")

        if self._elevenlabs_tts(text, backup, seg_path):
            return True
        print("  ↳ Backup voice failed, trying OpenAI TTS…")

        return self._openai_tts(text, oai_voice, seg_path)

    # ------------------------------------------------------------------
    # Internal: ElevenLabs via direct httpx (no SDK)
    # ------------------------------------------------------------------

    def _elevenlabs_tts(self, text: str, voice_id: str, output_path: str) -> bool:
        """Call ElevenLabs REST API directly — xi-api-key always explicitly set."""
        if not self._elevenlabs_available:
            return False

        url = f"{_ELEVENLABS_BASE_URL}/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key":   self._el_key,
            "Content-Type": "application/json",
            "Accept":       "audio/mpeg",
        }
        payload = {
            "text":     text,
            "model_id": _ELEVENLABS_MODEL,
            "voice_settings": {
                "stability":         0.50,
                "similarity_boost":  0.82,
                "style":             0.30,
                "use_speaker_boost": True,
            },
        }

        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
                resp = client.post(url, json=payload, headers=headers)

            if resp.status_code != 200:
                print(f"    [✗ ElevenLabs] HTTP {resp.status_code} voice={voice_id[:8]}…: "
                      f"{resp.text[:200]}")
                return False

            with open(output_path, "wb") as f:
                f.write(resp.content)

            size = os.path.getsize(output_path)
            if size < 1000:
                print(f"    [✗ ElevenLabs] File too small ({size}B) voice={voice_id[:8]}…")
                return False

            print(f"    [✓ ElevenLabs] voice={voice_id[:8]}… ({size // 1024}KB)")
            return True

        except Exception as e:
            print(f"    [✗ ElevenLabs] voice={voice_id[:8]}… exception: {e}")
            return False

    # ------------------------------------------------------------------
    # Internal: OpenAI TTS fallback
    # ------------------------------------------------------------------

    def _openai_tts(self, text: str, voice: str, output_path: str) -> bool:
        if not self._openai_client:
            return False
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            response = self._openai_client.audio.speech.create(
                model="tts-1-hd", voice=voice, input=text,
            )
            with open(output_path, "wb") as f:
                f.write(response.content)
            print(f"    [✓ OpenAI fallback] voice={voice}")
            return True
        except Exception as e:
            print(f"    [✗ OpenAI fallback] {e}")
            return False

    # ------------------------------------------------------------------
    # Internal: transcript parser
    # ------------------------------------------------------------------

    def _parse_transcript_segments(self, transcript: str) -> List[Dict]:
        segments = []
        raw = transcript.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
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
            text = self._clean_text(m.group(2))
            if text:
                segments.append({"speaker": m.group(1).upper(), "text": text})

        if not segments:
            preview = raw[:300].replace("\n", "\\n")
            print("[AudioService] WARNING: no segments matched. Preview: %r" % preview)
        return segments

    def _clean_text(self, text: str) -> str:
        text = re.sub(r'\*{1,3}', '', text)
        text = re.sub(r'_{1,2}', '', text)
        text = re.sub(r'`+', '', text)
        text = re.sub(r'(?m)^#+\s*', '', text)
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    # ------------------------------------------------------------------
    # Public utilities / legacy compatibility
    # ------------------------------------------------------------------

    def convert_to_mp3(self, input_path: str, output_path: str) -> str:
        try:
            AudioSegment.from_file(input_path).export(output_path, format="mp3", bitrate="192k")
            return output_path
        except Exception as e:
            raise Exception(f"Audio conversion error: {e}")

    def generate_audio_from_text(self, text: str, output_path: str, lang: str = "en") -> str:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if not self._elevenlabs_tts(text, _VOICE_MALE, output_path):
            if not self._elevenlabs_tts(text, _VOICE_MALE_BACKUP, output_path):
                self._openai_tts(text, "onyx", output_path)
        return output_path

    def generate_audio_from_pdf_content(self, content: dict, output_path: str) -> str:
        return self.generate_audio_from_text(self._create_audio_script(content), output_path)

    def _create_audio_script(self, content: dict) -> str:
        parts = []
        if "title" in content:
            parts.append(f"{content['title']}.")
        if "introduction" in content:
            parts.append(content["introduction"])
        for sec in content.get("sections", []):
            parts.append(f"{sec['heading']}.")
            parts.append(sec["content"])
            for pt in sec.get("points", []):
                parts.append(pt)
        if "summary" in content:
            parts.append(f"Summary. {content['summary']}")
        if "key_takeaways" in content:
            parts.append("Yaad Rakho. Key Takeaways.")
            for i, t in enumerate(content["key_takeaways"], 1):
                parts.append(f"Point number {i}. {t}")
        return " ".join(parts)
