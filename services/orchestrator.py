import os
import tempfile
from typing import Dict, Any, Optional
from datetime import datetime

from services.database import DatabaseService
from services.s3_service import S3Service
from services.pdf_service import PDFService
from services.llm_service import LLMService
from services.audio_service import AudioService
from services.video_service import VideoService
from config import config


class ContentProcessingOrchestrator:
    def __init__(self, platform: str = "ca"):
        """
        Args:
            platform: "ca" or "cs" — passed to all platform-aware services.
        """
        self.platform = platform
        self.db    = DatabaseService(platform=platform)
        self.s3    = S3Service(platform=platform)
        self.pdf   = PDFService()
        self.llm   = LLMService(platform=platform)
        self.audio = AudioService()
        self.video = VideoService()

    async def process(
        self,
        job_id: str,
        dashboard_id: Optional[str] = None,
        pdf_url: str = None,
        use_gemini: bool = True,
        use_openai: bool = True,
    ):
        """
        Main orchestration method that processes the entire pipeline.

        NOTE: Simplified PDF is now uploaded by the client — this pipeline
        reads the existing simplified_pdf_url from the {platform}_dashboard document
        and uses its text as the source for transcript generation.
        If simplified_pdf_url is not yet stored, processing is aborted with
        a clear message asking the client to upload the PDF first.

        Args:
            job_id:       Unique job identifier (from processing_jobs)
            dashboard_id: MongoDB _id from {platform}_dashboard collection
            pdf_url:      S3 URL of the *original* PDF (kept for page-count metadata)
            use_gemini:   Use Gemini LLM for content simplification (fallback path)
            use_openai:   Use OpenAI LLM for content simplification (fallback path)
        """
        temp_dir = None

        try:
            # ----------------------------------------------------------------
            # Mark job as processing
            # ----------------------------------------------------------------
            self.db.update_job_status(job_id, "processing")

            if dashboard_id:
                self.db.update_dashboard_with_urls(
                    dashboard_id,
                    processing_status="processing",
                )

            # ----------------------------------------------------------------
            # Step 1: Read simplified_pdf_url from {platform}_dashboard
            #         The client must upload the simplified PDF before triggering
            #         this pipeline. If the URL is absent, abort early.
            # ----------------------------------------------------------------
            print(f"[Job {job_id}] Fetching {self.platform}_dashboard record {dashboard_id}...")
            dashboard_doc = self.db.get_dashboard_document(dashboard_id) if dashboard_id else None

            simplified_pdf_url = (dashboard_doc or {}).get("simplified_pdf_url", "").strip()

            if not simplified_pdf_url:
                raise Exception(
                    "simplified_pdf_url not found in the dashboard document. "
                    "Please upload the simplified PDF first, then trigger processing."
                )

            print(f"[Job {job_id}] ✓ simplified_pdf_url found: {simplified_pdf_url}")

            # ----------------------------------------------------------------
            # Step 2: Create temp directory
            # ----------------------------------------------------------------
            temp_dir = tempfile.mkdtemp(prefix=f"{self.platform}_job_{job_id}_")

            # ----------------------------------------------------------------
            # Step 3: Download simplified PDF and extract its text
            #         This is what the transcript will be based on — the
            #         client-provided simplified content, not the raw original.
            # ----------------------------------------------------------------
            print(f"[Job {job_id}] Downloading simplified PDF from S3...")
            simplified_pdf_local = os.path.join(temp_dir, "simplified.pdf")
            if not self.s3.download_file(simplified_pdf_url, simplified_pdf_local):
                raise Exception("Failed to download simplified PDF from S3")

            print(f"[Job {job_id}] Extracting text from simplified PDF...")
            simplified_extracted = self.pdf.extract_text_from_pdf(simplified_pdf_local)
            simplified_text = simplified_extracted["full_text"]

            # ----------------------------------------------------------------
            # Step 4 (optional): Also download original PDF for page-count
            #                    metadata only — skip if pdf_url not provided.
            # ----------------------------------------------------------------
            original_pages = 0
            if pdf_url:
                try:
                    print(f"[Job {job_id}] Downloading original PDF for metadata...")
                    original_pdf_path = os.path.join(temp_dir, "original.pdf")
                    if self.s3.download_file(pdf_url, original_pdf_path):
                        original_extracted = self.pdf.extract_text_from_pdf(original_pdf_path)
                        original_pages = original_extracted.get("total_pages", 0)
                except Exception as meta_err:
                    print(f"[Job {job_id}] ⚠️  Could not fetch original PDF metadata: {meta_err}")

            # ----------------------------------------------------------------
            # Step 5: Generate video transcript from simplified PDF text
            #         Length is now word-budgeted to target 10-18 minutes
            #         based on page count — see llm_service._target_minutes_for_pages
            # ----------------------------------------------------------------
            simplified_page_count = simplified_extracted.get("total_pages", 0)
            # Fall back to original PDF page count if simplified count not available
            if simplified_page_count <= 0:
                simplified_page_count = original_pages
            print(f"[Job {job_id}] Generating video transcript "
                  f"(pdf_pages={simplified_page_count})...")
            transcript = self.llm.generate_video_transcript_from_text(
                simplified_text,
                pdf_page_count=simplified_page_count,
            )

            # ----------------------------------------------------------------
            # Step 6: Generate audio from transcript
            #         audio_service enforces a hard 18-min cap independent of
            #         the transcript's word-budget target (real TTS pacing can
            #         vary), and returns transcript_for_video trimmed to match
            #         whatever actually made it into the final audio file.
            # ----------------------------------------------------------------
            print(f"[Job {job_id}] Generating audio from transcript...")
            audio_path = os.path.join(temp_dir, "audio.mp3")
            audio_path, seg_durations, transcript_for_video = (
                self.audio.generate_audio_from_transcript(transcript, audio_path)
            )
            total_audio_min = sum(seg_durations) / 60.0
            print(f"[Job {job_id}] ✓ Audio ready: {len(seg_durations)} segments, "
                  f"~{total_audio_min:.1f} min")

            # ----------------------------------------------------------------
            # Step 7: Upload audio to S3
            # ----------------------------------------------------------------
            print(f"[Job {job_id}] Uploading audio to S3...")
            audio_s3_key = self.s3.generate_s3_key(job_id, "audio", "mp3")
            audio_url = self.s3.upload_file(audio_path, audio_s3_key)

            if not audio_url:
                raise Exception("Failed to upload audio to S3")

            self.db.update_job(job_id, {"audio_url": audio_url})

            if dashboard_id:
                self.db.update_dashboard_with_urls(
                    dashboard_id,
                    audio_url=audio_url,
                    processing_status="processing",
                )

            # ----------------------------------------------------------------
            # Step 8: Create animated video
            #         Uses transcript_for_video (audio-aligned) rather than the
            #         raw generated transcript, so PDF-panel sync always
            #         matches what's actually audible in the final file.
            # ----------------------------------------------------------------
            print(f"[Job {job_id}] Creating animated video...")
            video_path = os.path.join(temp_dir, "video.mp4")
            self.video.create_animated_video_from_transcript(
                transcript        = transcript_for_video,
                output_path       = video_path,
                audio_path        = audio_path,
                segment_durations = seg_durations,
                pdf_path          = simplified_pdf_local,
                platform          = self.platform,
            )

            # ----------------------------------------------------------------
            # Step 9: Upload video to S3
            # ----------------------------------------------------------------
            print(f"[Job {job_id}] Uploading video to S3...")
            video_s3_key = self.s3.generate_s3_key(job_id, "video", "mp4")
            video_url = self.s3.upload_file(video_path, video_s3_key)

            if not video_url:
                raise Exception("Failed to upload video to S3")

            # ----------------------------------------------------------------
            # Step 10: Persist final results
            # ----------------------------------------------------------------
            self.db.update_job(job_id, {
                "video_url": video_url,
                "status": "completed",
                "metadata": {
                    "transcript": transcript_for_video,
                    "original_pages": original_pages,
                    "video_length_minutes": round(total_audio_min, 1),
                    "completed_at": datetime.utcnow().isoformat(),
                },
            })

            if dashboard_id:
                success = self.db.update_dashboard_with_urls(
                    dashboard_id,
                    audio_url=audio_url,
                    video_url=video_url,
                    processing_status="completed",
                )
                if success:
                    print(f"[Job {job_id}] ✓ {self.platform}_dashboard {dashboard_id} updated successfully")
                else:
                    print(f"[Job {job_id}] ✗ Failed to update {self.platform}_dashboard {dashboard_id}")

            print(f"[Job {job_id}] ✅ Processing completed successfully! "
                  f"(~{total_audio_min:.1f} min video)")

        except Exception as e:
            error_msg = str(e)
            print(f"[Job {job_id}] ❌ Error: {error_msg}")
            self.db.update_job_status(job_id, "failed", error=error_msg)

            if dashboard_id:
                self.db.update_dashboard_with_urls(
                    dashboard_id,
                    processing_status="failed",
                )

        finally:
            if temp_dir and os.path.exists(temp_dir):
                import shutil
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass
