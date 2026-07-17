# """
# audio_service.py — ElevenLabs TTS with Indian accent voices
# ============================================================
# Uses direct httpx calls (no SDK) so the API key is always
# explicitly passed — no risk of None slipping through.

# Male Instructor  → ElevenLabs "Raj"    (Indian English male)
# Female Student   → ElevenLabs "Sana"   (Indian English female)

# Returns:
#     (output_path: str, segment_durations: List[float], transcript_used: str)

# EFFICIENCY / LENGTH-CONTROL NOTES (2026-07 rewrite)
# ----------------------------------------------------
# 1. Sticky quota skip: previously, once ElevenLabs quota ran out, EVERY
#    remaining segment still tried primary ElevenLabs (fail) → backup
#    ElevenLabs (fail) → OpenAI (success) — two guaranteed wasted network
#    round-trips per segment for the rest of the job. Now, the first
#    "quota_exceeded" response sets a sticky flag and all later segments
#    skip straight to OpenAI TTS.

# 2. Concurrent generation: segments are independent TTS calls (network-
#    bound), so they're now generated in parallel via a thread pool instead
#    of one-at-a-time. Final audio is still assembled in the original
#    dialogue order.

# 3. Hard duration cap: even with word-budgeted transcripts from
#    llm_service, real TTS pacing can vary. After assembly, if total audio
#    exceeds MAX_VIDEO_SECONDS (18 min), it's trimmed to the last full
#    segment that fits, and the transcript text is truncated to match (via
#    llm_service.truncate_transcript_to_dialogue_count) so the video's PDF
#    panel / dialogue stays in sync with what's actually in the final audio.
# """

# import os
# import re
# import shutil
# import tempfile
# import threading
# from concurrent.futures import ThreadPoolExecutor
# import httpx
# from typing import List, Dict, Tuple

# from pydub import AudioSegment

# from config import config
# from services.llm_service import truncate_transcript_to_dialogue_count

# # ---------------------------------------------------------------------------
# # Voice IDs
# # ---------------------------------------------------------------------------
# _VOICE_MALE          = os.getenv("ELEVENLABS_VOICE_MALE",   "29vD33N1CtxCmqQRPOHJ")  # Raj
# _VOICE_FEMALE        = os.getenv("ELEVENLABS_VOICE_FEMALE", "tKZQTIqwDrPzLv6MrPxF")  # Sana
# _VOICE_MALE_BACKUP   = "ojNjxYKrSUDwsRrANSYc"
# _VOICE_FEMALE_BACKUP = "Txmsc1sMMJjB3YTRQgpO"

# _ELEVENLABS_MODEL    = "eleven_multilingual_v2"
# _ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"   # never changes
# _INTER_SEGMENT_PAUSE_MS = 300
# _REQUEST_TIMEOUT        = 60.0

# _MAX_TTS_WORKERS       = 4          # concurrent TTS requests
# _MAX_VIDEO_SECONDS     = 18 * 60    # hard ceiling — matches llm_service cap


# class AudioService:
#     """
#     Generates dual-voice audio from dialogue transcripts.
#     Uses direct httpx calls to ElevenLabs — no SDK dependency.
#     """

#     def __init__(self):
#         # Resolve API key — env var takes priority over config
#         self._el_key = (
#             os.getenv("ELEVENLABS_API_KEY")
#             or getattr(config, "ELEVENLABS_API_KEY", None)
#         )

#         if not self._el_key:
#             print("[AudioService] ⚠️  ELEVENLABS_API_KEY is None/empty — ElevenLabs disabled")
#             self._elevenlabs_available = False
#         else:
#             print(f"[AudioService] ✅ ElevenLabs key loaded: {self._el_key[:8]}…")
#             print(f"[AudioService] 🎙️  Male   voice : {_VOICE_MALE}")
#             print(f"[AudioService] 🎙️  Female voice : {_VOICE_FEMALE}")
#             print(f"[AudioService] 🎙️  Model        : {_ELEVENLABS_MODEL}")
#             self._elevenlabs_available = True

#         # Sticky flag: once ElevenLabs reports quota_exceeded, stop trying it
#         # for the rest of this job (both primary and backup voices share the
#         # same account quota, so retrying either is pointless).
#         self._el_quota_exhausted = threading.Event()

#         # OpenAI fallback
#         try:
#             from openai import OpenAI as _OpenAI
#             oai_key = (
#                 os.getenv("OPENAI_API_KEY")
#                 or getattr(config, "OPENAI_API_KEY", None)
#             )
#             self._openai_client = _OpenAI(api_key=oai_key)
#             print("[AudioService] ✅ OpenAI fallback ready")
#         except Exception as e:
#             print(f"[AudioService] ⚠️  OpenAI fallback unavailable: {e}")
#             self._openai_client = None

#     # ------------------------------------------------------------------
#     # Public: main entry point
#     # ------------------------------------------------------------------

#     def generate_audio_from_transcript(
#         self, transcript: str, output_path: str
#     ) -> Tuple[str, List[float], str]:
#         """
#         Generate dual-voice MP3.
#         Returns (output_path, segment_durations, transcript_used).

#         transcript_used is the ORIGINAL transcript unless the hard 18-min
#         cap had to trim trailing segments — in that case it's the
#         correspondingly truncated transcript, so video_service (which
#         parses the transcript for PAGE/TOPIC sync) stays consistent with
#         the audio that's actually in the final file.
#         """
#         os.makedirs(os.path.dirname(output_path), exist_ok=True)
#         temp_dir = tempfile.mkdtemp(prefix="audio_seg_")
#         self._el_quota_exhausted.clear()

#         try:
#             segments = self._parse_transcript_segments(transcript)
#             if not segments:
#                 raise Exception("No dialogue segments found in transcript")

#             n = len(segments)
#             print(f"[AudioService] Found {n} dialogue segments "
#                   f"(generating up to {_MAX_TTS_WORKERS} at a time)")

#             seg_paths: List[str] = [
#                 os.path.join(temp_dir, f"seg_{i:04d}.mp3") for i in range(n)
#             ]

#             # ── Generate all segments concurrently (network-bound work) ──
#             def _generate_one(i: int) -> None:
#                 seg      = segments[i]
#                 speaker  = seg["speaker"]
#                 text     = seg["text"]
#                 label    = "Instructor (Male)" if speaker == "A" else "Student (Female)"
#                 print(f"  [{i+1}/{n}] {label}: {text[:70]}...")

#                 success = self._tts_with_fallback(speaker, text, seg_paths[i])
#                 if not success:
#                     words       = len(text.split())
#                     duration_ms = max(1500, words * 400)
#                     AudioSegment.silent(duration=duration_ms).export(seg_paths[i], format="mp3")
#                     print(f"  ⚠️  Segment {i}: silence placeholder ({duration_ms}ms)")

#             with ThreadPoolExecutor(max_workers=_MAX_TTS_WORKERS) as pool:
#                 list(pool.map(_generate_one, range(n)))

#             # ── Assemble in original order ────────────────────────────────
#             final_audio     = AudioSegment.empty()
#             silence_between = AudioSegment.silent(duration=_INTER_SEGMENT_PAUSE_MS)
#             segment_durations: List[float] = []

#             for i, seg_path in enumerate(seg_paths):
#                 try:
#                     seg_audio = AudioSegment.from_mp3(seg_path)
#                     combined  = seg_audio + silence_between
#                     segment_durations.append(len(combined) / 1000.0)
#                     final_audio += combined
#                 except Exception as load_err:
#                     print(f"  ⚠️  Could not load segment {i}: {load_err}")
#                     segment_durations.append(max(3.0, len(segments[i]["text"].split()) / 2.5))

#             if len(final_audio) == 0:
#                 raise Exception("All audio segments failed to generate")

#             # ── Hard cap: trim to MAX_VIDEO_SECONDS if real pacing overshot ──
#             transcript_used = transcript
#             total_s = len(final_audio) / 1000.0
#             if total_s > _MAX_VIDEO_SECONDS:
#                 cumulative = 0.0
#                 keep_count = 0
#                 for d in segment_durations:
#                     if cumulative + d > _MAX_VIDEO_SECONDS:
#                         break
#                     cumulative += d
#                     keep_count += 1
#                 keep_count = max(1, keep_count)

#                 print(f"[AudioService] ✂️  Total {total_s:.1f}s exceeds "
#                       f"{_MAX_VIDEO_SECONDS}s cap — trimming to first {keep_count}/{n} segments "
#                       f"(~{cumulative:.1f}s)")

#                 final_audio = final_audio[: int(cumulative * 1000)]
#                 segment_durations = segment_durations[:keep_count]
#                 transcript_used = truncate_transcript_to_dialogue_count(transcript, keep_count)

#             total_s = len(final_audio) / 1000
#             print(f"\n[AudioService] ✅ Total audio: {total_s:.1f}s "
#                   f"({total_s/60:.1f} min)")

#             final_audio.export(output_path, format="mp3", bitrate="192k")
#             return output_path, segment_durations, transcript_used

#         finally:
#             try:
#                 shutil.rmtree(temp_dir)
#             except Exception:
#                 pass

#     # ------------------------------------------------------------------
#     # Internal: fallback chain
#     # ------------------------------------------------------------------

#     def _tts_with_fallback(self, speaker: str, text: str, seg_path: str) -> bool:
#         is_male   = speaker == "A"
#         primary   = _VOICE_MALE          if is_male else _VOICE_FEMALE
#         backup    = _VOICE_MALE_BACKUP   if is_male else _VOICE_FEMALE_BACKUP
#         oai_voice = "onyx"               if is_male else "nova"

#         if not self._el_quota_exhausted.is_set():
#             ok, quota_hit = self._elevenlabs_tts(text, primary, seg_path)
#             if ok:
#                 return True
#             if quota_hit:
#                 self._el_quota_exhausted.set()
#                 print("  ↳ ElevenLabs quota exhausted — skipping ElevenLabs for rest of job")
#             else:
#                 print("  ↳ Primary voice failed, trying backup…")
#                 ok, quota_hit = self._elevenlabs_tts(text, backup, seg_path)
#                 if ok:
#                     return True
#                 if quota_hit:
#                     self._el_quota_exhausted.set()
#                     print("  ↳ ElevenLabs quota exhausted — skipping ElevenLabs for rest of job")
#                 else:
#                     print("  ↳ Backup voice failed, trying OpenAI TTS…")

#         return self._openai_tts(text, oai_voice, seg_path)

#     # ------------------------------------------------------------------
#     # Internal: ElevenLabs via direct httpx (no SDK)
#     # ------------------------------------------------------------------

#     def _elevenlabs_tts(self, text: str, voice_id: str, output_path: str) -> Tuple[bool, bool]:
#         """
#         Call ElevenLabs REST API directly — xi-api-key always explicitly set.
#         Returns (success, quota_exceeded) so the caller can set the sticky
#         skip flag on quota errors specifically (vs. other transient failures).
#         """
#         if not self._elevenlabs_available:
#             return False, False

#         url = f"{_ELEVENLABS_BASE_URL}/v1/text-to-speech/{voice_id}"
#         headers = {
#             "xi-api-key":   self._el_key,
#             "Content-Type": "application/json",
#             "Accept":       "audio/mpeg",
#         }
#         payload = {
#             "text":     text,
#             "model_id": _ELEVENLABS_MODEL,
#             "voice_settings": {
#                 "stability":         0.50,
#                 "similarity_boost":  0.82,
#                 "style":             0.30,
#                 "use_speaker_boost": True,
#             },
#         }

#         try:
#             os.makedirs(os.path.dirname(output_path), exist_ok=True)

#             with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
#                 resp = client.post(url, json=payload, headers=headers)

#             if resp.status_code != 200:
#                 body = resp.text[:300]
#                 quota_hit = "quota_exceeded" in body
#                 print(f"    [✗ ElevenLabs] HTTP {resp.status_code} voice={voice_id[:8]}…: {body[:200]}")
#                 return False, quota_hit

#             with open(output_path, "wb") as f:
#                 f.write(resp.content)

#             size = os.path.getsize(output_path)
#             if size < 1000:
#                 print(f"    [✗ ElevenLabs] File too small ({size}B) voice={voice_id[:8]}…")
#                 return False, False

#             print(f"    [✓ ElevenLabs] voice={voice_id[:8]}… ({size // 1024}KB)")
#             return True, False

#         except Exception as e:
#             print(f"    [✗ ElevenLabs] voice={voice_id[:8]}… exception: {e}")
#             return False, False

#     # ------------------------------------------------------------------
#     # Internal: OpenAI TTS fallback
#     # ------------------------------------------------------------------

#     def _openai_tts(self, text: str, voice: str, output_path: str) -> bool:
#         if not self._openai_client:
#             return False
#         try:
#             os.makedirs(os.path.dirname(output_path), exist_ok=True)
#             response = self._openai_client.audio.speech.create(
#                 model="tts-1-hd", voice=voice, input=text,
#             )
#             with open(output_path, "wb") as f:
#                 f.write(response.content)
#             print(f"    [✓ OpenAI fallback] voice={voice}")
#             return True
#         except Exception as e:
#             print(f"    [✗ OpenAI fallback] {e}")
#             return False

#     # ------------------------------------------------------------------
#     # Internal: transcript parser
#     # ------------------------------------------------------------------

#     def _parse_transcript_segments(self, transcript: str) -> List[Dict]:
#         segments = []
#         raw = transcript.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
#         pattern = re.compile(
#             r'^\**\s*(?:User\s+)?([AB])\**\s*:\s*\**\s*(.+?)\s*\**$',
#             re.IGNORECASE,
#         )
#         for line in raw.splitlines():
#             line = line.strip()
#             if not line:
#                 continue
#             m = pattern.match(line)
#             if not m:
#                 continue
#             text = self._clean_text(m.group(2))
#             if text:
#                 segments.append({"speaker": m.group(1).upper(), "text": text})

#         if not segments:
#             preview = raw[:300].replace("\n", "\\n")
#             print("[AudioService] WARNING: no segments matched. Preview: %r" % preview)
#         return segments

#     def _clean_text(self, text: str) -> str:
#         text = re.sub(r'\*{1,3}', '', text)
#         text = re.sub(r'_{1,2}', '', text)
#         text = re.sub(r'`+', '', text)
#         text = re.sub(r'(?m)^#+\s*', '', text)
#         text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
#         text = re.sub(r'\s+', ' ', text)
#         return text.strip()

#     # ------------------------------------------------------------------
#     # Public utilities / legacy compatibility
#     # ------------------------------------------------------------------

#     def convert_to_mp3(self, input_path: str, output_path: str) -> str:
#         try:
#             AudioSegment.from_file(input_path).export(output_path, format="mp3", bitrate="192k")
#             return output_path
#         except Exception as e:
#             raise Exception(f"Audio conversion error: {e}")

#     def generate_audio_from_text(self, text: str, output_path: str, lang: str = "en") -> str:
#         os.makedirs(os.path.dirname(output_path), exist_ok=True)
#         ok, quota_hit = self._elevenlabs_tts(text, _VOICE_MALE, output_path)
#         if not ok:
#             if quota_hit:
#                 self._el_quota_exhausted.set()
#             ok, quota_hit = self._elevenlabs_tts(text, _VOICE_MALE_BACKUP, output_path)
#             if not ok:
#                 self._openai_tts(text, "onyx", output_path)
#         return output_path

#     def generate_audio_from_pdf_content(self, content: dict, output_path: str) -> str:
#         return self.generate_audio_from_text(self._create_audio_script(content), output_path)

#     def _create_audio_script(self, content: dict) -> str:
#         parts = []
#         if "title" in content:
#             parts.append(f"{content['title']}.")
#         if "introduction" in content:
#             parts.append(content["introduction"])
#         for sec in content.get("sections", []):
#             parts.append(f"{sec['heading']}.")
#             parts.append(sec["content"])
#             for pt in sec.get("points", []):
#                 parts.append(pt)
#         if "summary" in content:
#             parts.append(f"Summary. {content['summary']}")
#         if "key_takeaways" in content:
#             parts.append("Yaad Rakho. Key Takeaways.")
#             for i, t in enumerate(content["key_takeaways"], 1):
#                 parts.append(f"Point number {i}. {t}")
#         return " ".join(parts)

"""
audio_service.py — ElevenLabs TTS with Indian accent voices
============================================================
Uses direct httpx calls (no SDK) so the API key is always
explicitly passed — no risk of None slipping through.

Male Instructor  → ElevenLabs "Raj"    (Indian English male)
Female Student   → ElevenLabs "Sana"   (Indian English female)

Returns:
    (output_path: str, segment_durations: List[float], transcript_used: str)

EFFICIENCY / LENGTH-CONTROL NOTES (2026-07 rewrite, updated 2026-07)
----------------------------------------------------------------------
1. Sticky quota skip: previously, once ElevenLabs quota ran out, EVERY
   remaining segment still tried primary ElevenLabs (fail) → backup
   ElevenLabs (fail) → OpenAI (success) — two guaranteed wasted network
   round-trips per segment for the rest of the job. Now, the first
   "quota_exceeded" response sets a sticky flag and all later segments
   skip straight to OpenAI TTS.

2. Concurrent generation: segments are independent TTS calls (network-
   bound), so they're now generated in parallel via a thread pool instead
   of one-at-a-time. Final audio is still assembled in the original
   dialogue order.

3. NO DURATION CAP: audio is no longer trimmed to fit a fixed ceiling.
   llm_service now generates the transcript in page-batched chunks that
   cover the ENTIRE PDF with no content skipped, so the resulting video
   can legitimately run well past what any fixed cap would have allowed
   (e.g. 25-40+ min for a long PDF) — and that's expected/desired here.
   The full assembled audio is always exported as-is; nothing is cut.
   `transcript_used` is therefore always identical to the input transcript
   now (kept in the return signature for backward compatibility with
   callers that unpack it).
"""

import os
import re
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
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

_MAX_TTS_WORKERS       = 4          # concurrent TTS requests
# NOTE: the previous _MAX_VIDEO_SECONDS hard cap (18 min) has been removed.
# Long PDFs are expected to produce long videos now — see module docstring.


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

        # Sticky flag: once ElevenLabs reports quota_exceeded, stop trying it
        # for the rest of this job (both primary and backup voices share the
        # same account quota, so retrying either is pointless).
        self._el_quota_exhausted = threading.Event()

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
    ) -> Tuple[str, List[float], str]:
        """
        Generate dual-voice MP3 covering the FULL transcript — no trimming.
        Returns (output_path, segment_durations, transcript_used), where
        transcript_used is always the original transcript (kept for
        backward-compatible call signatures).
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="audio_seg_")
        self._el_quota_exhausted.clear()

        try:
            segments = self._parse_transcript_segments(transcript)
            if not segments:
                raise Exception("No dialogue segments found in transcript")

            n = len(segments)
            print(f"[AudioService] Found {n} dialogue segments "
                  f"(generating up to {_MAX_TTS_WORKERS} at a time, full transcript, no trimming)")

            seg_paths: List[str] = [
                os.path.join(temp_dir, f"seg_{i:04d}.mp3") for i in range(n)
            ]

            # ── Generate all segments concurrently (network-bound work) ──
            def _generate_one(i: int) -> None:
                seg      = segments[i]
                speaker  = seg["speaker"]
                text     = seg["text"]
                label    = "Instructor (Male)" if speaker == "A" else "Student (Female)"
                print(f"  [{i+1}/{n}] {label}: {text[:70]}...")

                success = self._tts_with_fallback(speaker, text, seg_paths[i])
                if not success:
                    words       = len(text.split())
                    duration_ms = max(1500, words * 400)
                    AudioSegment.silent(duration=duration_ms).export(seg_paths[i], format="mp3")
                    print(f"  ⚠️  Segment {i}: silence placeholder ({duration_ms}ms)")

            with ThreadPoolExecutor(max_workers=_MAX_TTS_WORKERS) as pool:
                list(pool.map(_generate_one, range(n)))

            # ── Assemble in original order — every segment included ──────
            final_audio     = AudioSegment.empty()
            silence_between = AudioSegment.silent(duration=_INTER_SEGMENT_PAUSE_MS)
            segment_durations: List[float] = []

            for i, seg_path in enumerate(seg_paths):
                try:
                    seg_audio = AudioSegment.from_mp3(seg_path)
                    combined  = seg_audio + silence_between
                    segment_durations.append(len(combined) / 1000.0)
                    final_audio += combined
                except Exception as load_err:
                    print(f"  ⚠️  Could not load segment {i}: {load_err}")
                    segment_durations.append(max(3.0, len(segments[i]["text"].split()) / 2.5))

            if len(final_audio) == 0:
                raise Exception("All audio segments failed to generate")

            total_s = len(final_audio) / 1000
            print(f"\n[AudioService] ✅ Total audio: {total_s:.1f}s "
                  f"({total_s/60:.1f} min) — full transcript, {n}/{n} segments, no cap applied")

            final_audio.export(output_path, format="mp3", bitrate="192k")
            return output_path, segment_durations, transcript

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

        if not self._el_quota_exhausted.is_set():
            ok, quota_hit = self._elevenlabs_tts(text, primary, seg_path)
            if ok:
                return True
            if quota_hit:
                self._el_quota_exhausted.set()
                print("  ↳ ElevenLabs quota exhausted — skipping ElevenLabs for rest of job")
            else:
                print("  ↳ Primary voice failed, trying backup…")
                ok, quota_hit = self._elevenlabs_tts(text, backup, seg_path)
                if ok:
                    return True
                if quota_hit:
                    self._el_quota_exhausted.set()
                    print("  ↳ ElevenLabs quota exhausted — skipping ElevenLabs for rest of job")
                else:
                    print("  ↳ Backup voice failed, trying OpenAI TTS…")

        return self._openai_tts(text, oai_voice, seg_path)

    # ------------------------------------------------------------------
    # Internal: ElevenLabs via direct httpx (no SDK)
    # ------------------------------------------------------------------

    def _elevenlabs_tts(self, text: str, voice_id: str, output_path: str) -> Tuple[bool, bool]:
        """
        Call ElevenLabs REST API directly — xi-api-key always explicitly set.
        Returns (success, quota_exceeded) so the caller can set the sticky
        skip flag on quota errors specifically (vs. other transient failures).
        """
        if not self._elevenlabs_available:
            return False, False

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
                body = resp.text[:300]
                quota_hit = "quota_exceeded" in body
                print(f"    [✗ ElevenLabs] HTTP {resp.status_code} voice={voice_id[:8]}…: {body[:200]}")
                return False, quota_hit

            with open(output_path, "wb") as f:
                f.write(resp.content)

            size = os.path.getsize(output_path)
            if size < 1000:
                print(f"    [✗ ElevenLabs] File too small ({size}B) voice={voice_id[:8]}…")
                return False, False

            print(f"    [✓ ElevenLabs] voice={voice_id[:8]}… ({size // 1024}KB)")
            return True, False

        except Exception as e:
            print(f"    [✗ ElevenLabs] voice={voice_id[:8]}… exception: {e}")
            return False, False

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
        ok, quota_hit = self._elevenlabs_tts(text, _VOICE_MALE, output_path)
        if not ok:
            if quota_hit:
                self._el_quota_exhausted.set()
            ok, quota_hit = self._elevenlabs_tts(text, _VOICE_MALE_BACKUP, output_path)
            if not ok:
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
