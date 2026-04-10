import os
import tempfile
from typing import Dict, Any
from datetime import datetime

from services.database import DatabaseService
from services.s3_service import S3Service
from services.pdf_service import PDFService
from services.llm_service import LLMService
from services.audio_service import AudioService
from services.video_service import VideoService
from config import config

class ContentProcessingOrchestrator:
    def __init__(self):
        self.db = DatabaseService()
        self.s3 = S3Service()
        self.pdf = PDFService()
        self.llm = LLMService()
        self.audio = AudioService()
        self.video = VideoService()

    async def process(
        self,
        job_id: str,
        pdf_url: str,
        use_gemini: bool = True,
        use_openai: bool = True
    ):
        """
        Main orchestration method that processes the entire pipeline.
        """
        temp_dir = None

        try:
            # Update status to processing
            self.db.update_job_status(job_id, "processing")

            # Create temporary directory for processing
            temp_dir = tempfile.mkdtemp(prefix=f"ca_job_{job_id}_")

            # Step 1: Download original PDF from S3
            print(f"[Job {job_id}] Downloading PDF from S3...")
            original_pdf_path = os.path.join(temp_dir, "original.pdf")
            if not self.s3.download_file(pdf_url, original_pdf_path):
                raise Exception("Failed to download PDF from S3")

            # Step 2: Extract text from PDF
            print(f"[Job {job_id}] Extracting text from PDF...")
            extracted_data = self.pdf.extract_text_from_pdf(original_pdf_path)
            full_text = extracted_data["full_text"]

            # Step 3: Simplify content using LLM
            print(f"[Job {job_id}] Simplifying content with LLM...")
            if use_openai:
                simplified_content = self.llm.simplify_content_with_openai(full_text)
            elif use_gemini:
                simplified_content = self.llm.simplify_content_with_gemini(full_text)
            else:
                raise Exception("At least one LLM (OpenAI or Gemini) must be enabled")

            # Step 4: Create simplified PDF
            print(f"[Job {job_id}] Creating simplified PDF...")
            simplified_pdf_path = os.path.join(temp_dir, "simplified.pdf")
            self.pdf.create_simplified_pdf(simplified_content, simplified_pdf_path)

            # Step 5: Upload simplified PDF to S3
            print(f"[Job {job_id}] Uploading simplified PDF to S3...")
            simplified_pdf_s3_key = self.s3.generate_s3_key(job_id, "simplified_pdf", "pdf")
            simplified_pdf_url = self.s3.upload_file(simplified_pdf_path, simplified_pdf_s3_key)

            if not simplified_pdf_url:
                raise Exception("Failed to upload simplified PDF to S3")

            self.db.update_job(job_id, {"simplified_pdf_url": simplified_pdf_url})

            # Step 6: Generate video transcript FIRST
            print(f"[Job {job_id}] Generating video transcript...")
            transcript = self.llm.generate_video_transcript(
                simplified_content,
                use_openai=use_openai
            )

            # Step 7: Generate audio from the SAME transcript used in video
            print(f"[Job {job_id}] Generating audio from transcript...")
            audio_path = os.path.join(temp_dir, "audio.mp3")
            self.audio.generate_audio_from_transcript(transcript, audio_path)

            # Step 8: Upload audio to S3
            print(f"[Job {job_id}] Uploading audio to S3...")
            audio_s3_key = self.s3.generate_s3_key(job_id, "audio", "mp3")
            audio_url = self.s3.upload_file(audio_path, audio_s3_key)

            if not audio_url:
                raise Exception("Failed to upload audio to S3")

            self.db.update_job(job_id, {"audio_url": audio_url})

            # Step 9: Create animated video
            print(f"[Job {job_id}] Creating animated video...")
            video_path = os.path.join(temp_dir, "video.mp4")
            self.video.create_animated_video_from_transcript(
                transcript,
                video_path,
                audio_path=audio_path
            )

            # Step 10: Upload video to S3
            print(f"[Job {job_id}] Uploading video to S3...")
            video_s3_key = self.s3.generate_s3_key(job_id, "video", "mp4")
            video_url = self.s3.upload_file(video_path, video_s3_key)

            if not video_url:
                raise Exception("Failed to upload video to S3")

            # Step 11: Update job with final results
            self.db.update_job(job_id, {
                "video_url": video_url,
                "status": "completed",
                "metadata": {
                    "transcript": transcript,
                    "original_pages": extracted_data["total_pages"],
                    "completed_at": datetime.utcnow().isoformat()
                }
            })

            print(f"[Job {job_id}] Processing completed successfully!")

        except Exception as e:
            error_msg = str(e)
            print(f"[Job {job_id}] Error: {error_msg}")
            self.db.update_job_status(job_id, "failed", error=error_msg)

        finally:
            # Clean up temporary files
            if temp_dir and os.path.exists(temp_dir):
                import shutil
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass