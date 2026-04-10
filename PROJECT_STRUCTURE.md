# CA Content Processor - Project Structure

## Overview

This backend system processes CA educational PDFs through an automated AI pipeline, generating simplified documents, audio narrations, and educational videos.

## Directory Structure

```
ca-content-processor/
│
├── main.py                     # FastAPI application entry point
├── config.py                   # Configuration and environment variables
├── requirements.txt            # Python dependencies
│
├── .env.example               # Environment variables template
├── .env                       # Your actual credentials (git-ignored)
├── .gitignore                 # Git ignore rules
│
├── run.sh                     # Quick start script
├── test_api.py               # API testing script
├── example_usage.py          # Client usage examples
│
├── README.md                 # Main documentation
├── SETUP.md                  # Setup instructions
├── PROJECT_STRUCTURE.md      # This file
│
└── services/                 # Service modules
    ├── __init__.py          # Package initialization
    ├── database.py          # Supabase database operations
    ├── s3_service.py        # AWS S3 file operations
    ├── pdf_service.py       # PDF extraction and generation
    ├── llm_service.py       # OpenAI and Gemini AI integration
    ├── audio_service.py     # Text-to-speech audio generation
    ├── video_service.py     # Video creation and animation
    └── orchestrator.py      # Main processing pipeline coordinator
```

## Core Components

### 1. Main Application (main.py)

FastAPI application providing REST API endpoints:
- `POST /api/process` - Start processing a PDF
- `GET /api/status/{job_id}` - Check processing status

### 2. Configuration (config.py)

Centralized configuration management loading from environment variables:
- AWS credentials
- MongoDB connection
- API keys (OpenAI, Gemini)
- Processing settings

### 3. Services Layer

#### Database Service (services/database.py)
- MongoDB integration (PyMongo)
- Job CRUD operations
- Status tracking
- Metadata storage
- Automatic index creation

#### S3 Service (services/s3_service.py)
- File upload/download
- URL generation
- S3 key management

#### PDF Service (services/pdf_service.py)
- Text extraction from PDFs
- Simplified PDF generation
- Formatting and styling

#### LLM Service (services/llm_service.py)
- OpenAI GPT-4 integration
- Google Gemini integration
- Content simplification
- Transcript generation

#### Audio Service (services/audio_service.py)
- Text-to-speech conversion
- Audio file generation
- Format conversion

#### Video Service (services/video_service.py)
- Animated video creation
- Dialogue visualization
- User A/B conversation format
- Audio synchronization

#### Orchestrator (services/orchestrator.py)
- Coordinates entire pipeline
- Error handling
- Progress tracking
- Temporary file management

## Data Flow

```
1. User uploads PDF to S3
   ↓
2. API receives S3 URL via POST /api/process
   ↓
3. Job created in Supabase database (status: queued)
   ↓
4. Orchestrator starts background processing
   ↓
5. Download PDF from S3
   ↓
6. Extract text with pdfplumber
   ↓
7. Simplify content with LLM (OpenAI/Gemini)
   ↓
8. Generate simplified PDF with ReportLab
   ↓
9. Upload simplified PDF to S3
   ↓
10. Generate audio with gTTS
   ↓
11. Upload audio to S3
   ↓
12. Generate video transcript with LLM
   ↓
13. Create animated video with moviepy
   ↓
14. Upload video to S3
   ↓
15. Update job status to 'completed'
   ↓
16. Return URLs via GET /api/status/{job_id}
```

## Database Schema

### processing_jobs Table

```sql
CREATE TABLE processing_jobs (
  id BIGSERIAL PRIMARY KEY,
  job_id UUID UNIQUE NOT NULL,
  status TEXT NOT NULL,                    -- queued, processing, completed, failed
  original_pdf_url TEXT NOT NULL,
  simplified_pdf_url TEXT,
  audio_url TEXT,
  video_url TEXT,
  use_gemini BOOLEAN DEFAULT true,
  use_openai BOOLEAN DEFAULT true,
  error TEXT,
  metadata JSONB,                          -- transcript, processing times, etc.
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

### Indexes
- `idx_processing_jobs_job_id` on job_id
- `idx_processing_jobs_status` on status
- `idx_processing_jobs_created_at` on created_at

## API Endpoints

### POST /api/process

Start processing a PDF.

**Request:**
```json
{
  "pdf_s3_url": "https://bucket.s3.amazonaws.com/file.pdf",
  "use_gemini": true,
  "use_openai": true
}
```

**Response:**
```json
{
  "job_id": "uuid-here",
  "status": "queued",
  "message": "Processing started"
}
```

### GET /api/status/{job_id}

Get job processing status.

**Response:**
```json
{
  "job_id": "uuid-here",
  "status": "completed",
  "original_pdf_url": "https://...",
  "simplified_pdf_url": "https://...",
  "audio_url": "https://...",
  "video_url": "https://...",
  "error": null,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:05:00Z"
}
```

## Environment Variables

### Required

- `AWS_ACCESS_KEY_ID` - AWS access key
- `AWS_SECRET_ACCESS_KEY` - AWS secret key
- `AWS_REGION` - AWS region (default: us-east-1)
- `S3_BUCKET_NAME` - S3 bucket name
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_KEY` - Supabase anon/service key
- `OPENAI_API_KEY` - OpenAI API key
- `GOOGLE_API_KEY` - Google Gemini API key

### Optional

- `TTS_API_KEY` - Custom TTS API key
- `TTS_API_URL` - Custom TTS API URL

## Dependencies

### Core Framework
- `fastapi` - Web framework
- `uvicorn` - ASGI server

### AWS Integration
- `boto3` - AWS SDK

### PDF Processing
- `PyPDF2` - PDF manipulation
- `pdfplumber` - Text extraction
- `reportlab` - PDF generation
- `pillow` - Image processing

### AI/LLM
- `openai` - OpenAI API client
- `google-generativeai` - Gemini API client

### Media Generation
- `gtts` - Google Text-to-Speech
- `pydub` - Audio processing
- `moviepy` - Video creation
- `opencv-python` - Video processing

### Database
- `supabase` - Supabase client

### Utilities
- `python-dotenv` - Environment variables
- `requests` - HTTP client
- `aiohttp` - Async HTTP client
- `pydantic` - Data validation

## Processing Pipeline Details

### 1. PDF Extraction
- Downloads PDF from S3 to temporary location
- Uses pdfplumber for accurate text extraction
- Maintains document structure and formatting
- Handles multi-page documents

### 2. Content Simplification
- Sends extracted text to LLM
- AI analyzes and simplifies content
- Structures into sections with key points
- Optimizes for CA student comprehension
- Returns JSON-formatted content structure

### 3. PDF Generation
- Creates professional layout with ReportLab
- Applies custom styling (fonts, colors, spacing)
- Includes title, introduction, sections, summary
- Adds bullet points and key takeaways
- Generates multi-page document

### 4. Audio Generation
- Converts text to natural speech
- Uses Google Text-to-Speech
- Creates clear narration of content
- Exports as MP3 format
- Optimized for learning

### 5. Video Transcript Creation
- LLM generates conversational dialogue
- User A (student) asks questions
- User B (teacher) provides explanations
- 5-7 minute duration (750-1050 words)
- Educational yet engaging

### 6. Video Generation
- Creates animated dialogue video
- Alternating colored backgrounds per speaker
- Text overlay for dialogue
- Speaker identification
- Synchronized with audio
- 1280x720 HD resolution
- H.264 codec, MP4 format

## Error Handling

### Processing Errors
- Caught at orchestrator level
- Logged to console
- Stored in database with job
- Status updated to 'failed'
- Error message saved for debugging

### API Errors
- HTTP error codes returned
- Descriptive error messages
- Validation errors for invalid input
- 404 for missing jobs
- 500 for server errors

### Cleanup
- Temporary files always deleted
- S3 uploads verified before cleanup
- Database updated even on failure
- No orphaned resources

## Performance Considerations

### Asynchronous Processing
- Background task execution
- Non-blocking API responses
- Multiple jobs can run simultaneously

### Resource Management
- Temporary files in /tmp
- Automatic cleanup after processing
- Efficient memory usage

### Database Optimization
- Indexed queries for fast lookups
- Efficient status tracking
- JSONB for flexible metadata

### S3 Integration
- Direct upload/download
- Public-read ACL for generated files
- Organized file structure by job_id

## Security Notes

### API Keys
- Never committed to git
- Stored in .env (git-ignored)
- Loaded at runtime only

### Database
- Row Level Security enabled
- Policies for access control
- Secure connection via Supabase

### S3 Access
- IAM-based authentication
- Bucket policies enforced
- CORS configured as needed

## Future Enhancements

### Planned Features
- [ ] Progress percentage tracking
- [ ] Webhook notifications on completion
- [ ] Batch processing endpoint
- [ ] Custom video templates
- [ ] Multi-language support
- [ ] Additional TTS providers
- [ ] Video quality options
- [ ] Caching for repeated content
- [ ] API authentication
- [ ] Rate limiting

### Scaling Considerations
- Message queue for job processing
- Worker pool for parallel processing
- CDN for S3 content delivery
- Database connection pooling
- Horizontal scaling with load balancer

## Development Workflow

### Setup
1. Clone repository
2. Copy .env.example to .env
3. Configure credentials
4. Run `./run.sh`

### Testing
1. Start server: `python main.py`
2. Run tests: `python test_api.py`
3. Check API docs: http://localhost:8000/docs

### Adding Features
1. Create service in `services/`
2. Import in orchestrator
3. Update API endpoints if needed
4. Update documentation

## Monitoring and Debugging

### Logs
- Console output for all operations
- Error messages include stack traces
- Job progress printed during processing

### Database Queries
- Check job status in Supabase dashboard
- View metadata for processing details
- Query by status for monitoring

### S3 Console
- Verify file uploads
- Check file structure
- Monitor storage usage

## Support and Maintenance

### Common Tasks

**View active jobs:**
```sql
SELECT job_id, status, created_at
FROM processing_jobs
WHERE status = 'processing'
ORDER BY created_at DESC;
```

**Check failed jobs:**
```sql
SELECT job_id, error, created_at
FROM processing_jobs
WHERE status = 'failed'
ORDER BY created_at DESC
LIMIT 10;
```

**Clean old jobs:**
```sql
DELETE FROM processing_jobs
WHERE created_at < NOW() - INTERVAL '30 days';
```

### Troubleshooting Guide

1. **API won't start**: Check .env file exists and has all required variables
2. **Processing fails**: Check API keys are valid and have credits
3. **S3 errors**: Verify AWS credentials and bucket permissions
4. **Database errors**: Check Supabase connection and schema
5. **Video errors**: Ensure ffmpeg is installed on system

---

For detailed setup instructions, see [SETUP.md](SETUP.md)

For usage examples, see [example_usage.py](example_usage.py)

For API documentation, visit http://localhost:8000/docs when running
