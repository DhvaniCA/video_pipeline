from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl
from typing import Optional
import uuid
from datetime import datetime

from services.database import DatabaseService
from services.orchestrator import ContentProcessingOrchestrator

app = FastAPI(
    title="CA Content Processor API",
    description="Backend system for processing CA educational content with AI",
    version="1.0.0"
)

try:
    db_service = DatabaseService()
except Exception as e:
    print(f"Warning: Database initialization failed: {str(e)}")
    print("Make sure MongoDB is running and accessible")
    db_service = None

class ProcessingRequest(BaseModel):
    pdf_s3_url: HttpUrl
    use_gemini: bool = True
    use_openai: bool = True

class ProcessingResponse(BaseModel):
    job_id: str
    status: str
    message: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    original_pdf_url: Optional[str]
    simplified_pdf_url: Optional[str]
    audio_url: Optional[str]
    video_url: Optional[str]
    error: Optional[str]
    created_at: str
    updated_at: str

@app.get("/")
async def root():
    return {
        "message": "CA Content Processor API",
        "version": "1.0.0",
        "endpoints": {
            "process": "/api/process",
            "status": "/api/status/{job_id}"
        }
    }

@app.post("/api/process", response_model=ProcessingResponse)
async def process_content(
    request: ProcessingRequest,
    background_tasks: BackgroundTasks
):
    """
    Start processing a PDF from S3 URL.
    Returns a job_id for tracking progress.
    """
    if not db_service:
        raise HTTPException(status_code=500, detail="Database service is not available. Check MongoDB connection.")

    try:
        # Create a new job in database
        job_id = str(uuid.uuid4())

        job_data = {
            "job_id": job_id,
            "status": "queued",
            "original_pdf_url": str(request.pdf_s3_url),
            "use_gemini": request.use_gemini,
            "use_openai": request.use_openai
        }

        db_service.create_job(job_data)

        # Start background processing
        orchestrator = ContentProcessingOrchestrator()
        background_tasks.add_task(
            orchestrator.process,
            job_id=job_id,
            pdf_url=str(request.pdf_s3_url),
            use_gemini=request.use_gemini,
            use_openai=request.use_openai
        )

        return ProcessingResponse(
            job_id=job_id,
            status="queued",
            message="Processing started. Use the job_id to check status."
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start processing: {str(e)}")

@app.get("/api/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """
    Get the status of a processing job.
    """
    if not db_service:
        raise HTTPException(status_code=500, detail="Database service is not available. Check MongoDB connection.")

    try:
        job = db_service.get_job(job_id)

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        return JobStatusResponse(
            job_id=job["job_id"],
            status=job["status"],
            original_pdf_url=job.get("original_pdf_url"),
            simplified_pdf_url=job.get("simplified_pdf_url"),
            audio_url=job.get("audio_url"),
            video_url=job.get("video_url"),
            error=job.get("error"),
            created_at=str(job["created_at"]),   # convert datetime → string
            updated_at=str(job["updated_at"])    # convert datetime → string
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get job status: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)