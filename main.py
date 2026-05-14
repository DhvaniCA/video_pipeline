from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from typing import Optional
import uuid
from datetime import datetime
from bson.objectid import ObjectId

from services.database import DatabaseService
from services.orchestrator import ContentProcessingOrchestrator

app = FastAPI(
    title="CA Content Processor API",
    description="Backend system for processing CA/CS educational content with AI",
    version="1.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["http://localhost:5173"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    db_service = DatabaseService()
except Exception as e:
    print(f"Warning: Database initialization failed: {str(e)}")
    print("Make sure MongoDB is running and accessible")
    db_service = None


class ProcessingRequest(BaseModel):
    pdf_s3_url: HttpUrl
    dashboard_id: str       # MongoDB _id from {platform}_dashboard collection
    platform: str = "ca"   # "ca" or "cs"
    use_gemini: bool = True
    use_openai: bool = True


class ProcessingResponse(BaseModel):
    job_id: str
    dashboard_id: str
    platform: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    dashboard_id: str
    platform: str
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
        "message": "CA/CS Content Processor API",
        "version": "1.0.0",
        "endpoints": {
            "process": "/api/process",
            "status": "/api/status/{job_id}",
            "dashboard_status": "/api/dashboard/{dashboard_id}?platform=ca"
        }
    }


@app.post("/api/process", response_model=ProcessingResponse)
async def process_content(
    request: ProcessingRequest,
    background_tasks: BackgroundTasks
):
    """
    Start processing a PDF from S3 URL and link to {platform}_dashboard document.

    Args:
        request.pdf_s3_url:   S3 URL of the original PDF
        request.dashboard_id: MongoDB _id from {platform}_dashboard collection
        request.platform:     "ca" or "cs" (default: "ca")
        request.use_gemini:   Use Gemini LLM (default: True)
        request.use_openai:   Use OpenAI LLM (default: True)

    Returns:
        job_id:       Unique job ID for tracking
        dashboard_id: The dashboard document being updated
        platform:     Platform this job belongs to
        status:       Current job status (queued)
    """
    # Validate platform
    if request.platform not in ("ca", "cs"):
        raise HTTPException(status_code=400, detail="platform must be 'ca' or 'cs'")

    if not db_service:
        raise HTTPException(status_code=500, detail="Database service is not available. Check MongoDB connection.")

    try:
        # Validate dashboard_id exists in the right platform collection
        platform_db = DatabaseService(platform=request.platform)
        dashboard_doc = platform_db.get_dashboard_document(request.dashboard_id)
        if not dashboard_doc:
            raise HTTPException(
                status_code=404,
                detail=f"Dashboard document with ID {request.dashboard_id} not found in {request.platform}_dashboard"
            )

        # Create a new job in processing_jobs collection
        job_id = str(uuid.uuid4())

        job_data = {
            "job_id": job_id,
            "dashboard_id": request.dashboard_id,
            "platform": request.platform,
            "status": "queued",
            "original_pdf_url": str(request.pdf_s3_url),
            "use_gemini": request.use_gemini,
            "use_openai": request.use_openai
        }

        db_service.create_job(job_data)

        # Start background processing with platform-aware orchestrator
        orchestrator = ContentProcessingOrchestrator(platform=request.platform)
        background_tasks.add_task(
            orchestrator.process,
            job_id=job_id,
            dashboard_id=request.dashboard_id,
            pdf_url=str(request.pdf_s3_url),
            use_gemini=request.use_gemini,
            use_openai=request.use_openai
        )

        return ProcessingResponse(
            job_id=job_id,
            dashboard_id=request.dashboard_id,
            platform=request.platform,
            status="queued",
            message="Processing started. Use the job_id to check status."
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start processing: {str(e)}")


@app.get("/api/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """
    Get the status of a processing job by job_id.
    """
    if not db_service:
        raise HTTPException(status_code=500, detail="Database service is not available. Check MongoDB connection.")

    try:
        job = db_service.get_job(job_id)

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        return JobStatusResponse(
            job_id=job["job_id"],
            dashboard_id=job.get("dashboard_id", ""),
            platform=job.get("platform", "ca"),
            status=job["status"],
            original_pdf_url=job.get("original_pdf_url"),
            simplified_pdf_url=job.get("simplified_pdf_url"),
            audio_url=job.get("audio_url"),
            video_url=job.get("video_url"),
            error=job.get("error"),
            created_at=str(job["created_at"]),
            updated_at=str(job["updated_at"])
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get job status: {str(e)}")


@app.get("/api/dashboard/{dashboard_id}")
async def get_dashboard_status(dashboard_id: str, platform: str = "ca"):
    """
    Get the processing status and generated URLs from {platform}_dashboard document.
    Shows: simplified_pdf_url, audio_url, video_url if available.

    Query param:
        platform: "ca" or "cs" (default: "ca")
    """
    if platform not in ("ca", "cs"):
        raise HTTPException(status_code=400, detail="platform must be 'ca' or 'cs'")

    if not db_service:
        raise HTTPException(status_code=500, detail="Database service is not available. Check MongoDB connection.")

    try:
        platform_db = DatabaseService(platform=platform)
        dashboard_doc = platform_db.get_dashboard_document(dashboard_id)

        if not dashboard_doc:
            raise HTTPException(status_code=404, detail="Dashboard document not found")

        return {
            "dashboard_id": dashboard_id,
            "platform": platform,
            "title": dashboard_doc.get("title"),
            # Support both "chapter" (CA) and "module" (CS) field names
            "chapter": dashboard_doc.get("chapter") or dashboard_doc.get("module"),
            "original_pdf_url": dashboard_doc.get("pdf_url"),
            "simplified_pdf_url": dashboard_doc.get("simplified_pdf_url"),
            "audio_url": dashboard_doc.get("audio_url"),
            "video_url": dashboard_doc.get("video_url"),
            "processing_status": dashboard_doc.get("processing_status", "pending"),
            "created_at": str(dashboard_doc.get("created_at")),
            "updated_at": str(dashboard_doc.get("updated_at"))
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard status: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
