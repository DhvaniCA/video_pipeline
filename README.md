# CA Content Processor Backend

A comprehensive backend system for processing CA (Chartered Accountant) educational content using AI.

## Features

1. **PDF Processing**: Extracts content from PDF files stored in AWS S3
2. **AI Simplification**: Uses OpenAI GPT-4 and Google Gemini to simplify complex CA content for students
3. **Automated PDF Generation**: Creates clean, easy-to-read PDF documents
4. **Audio Generation**: Converts simplified content to audio using text-to-speech
5. **Video Creation**: Generates animated educational videos with dialogue between two users
6. **Cloud Storage**: Automatically uploads all generated content to AWS S3
7. **Job Tracking**: Uses MongoDB database to track processing jobs

## Architecture

```
Input: PDF URL (S3)
   ↓
Extract Text
   ↓
Simplify with LLM (OpenAI/Gemini)
   ↓
├─→ Generate Simplified PDF → Upload to S3
├─→ Generate Audio (TTS) → Upload to S3
└─→ Generate Video Transcript → Create Video → Upload to S3
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required environment variables:
- `AWS_ACCESS_KEY_ID`: Your AWS access key
- `AWS_SECRET_ACCESS_KEY`: Your AWS secret key
- `AWS_REGION`: AWS region (default: us-east-1)
- `S3_BUCKET_NAME`: Your S3 bucket name
- `MONGODB_URI`: Your MongoDB connection string (default: mongodb://localhost:27017)
- `MONGODB_DB_NAME`: Database name (default: ca_content_processor)
- `OPENAI_API_KEY`: Your OpenAI API key
- `GOOGLE_API_KEY`: Your Google Gemini API key

### 3. MongoDB Setup

MongoDB will automatically create the database and collection on first use. The `processing_jobs` collection tracks:
- Job status and progress
- URLs for all generated files
- Error messages if processing fails
- Metadata including transcripts

## Usage

### Start the API Server

```bash
python main.py
```

The API will be available at `http://localhost:8000`

### API Endpoints

#### 1. Process Content

```http
POST /api/process
Content-Type: application/json

{
  "pdf_s3_url": "https://your-bucket.s3.amazonaws.com/path/to/file.pdf",
  "use_gemini": true,
  "use_openai": true
}
```

Response:
```json
{
  "job_id": "uuid-here",
  "status": "queued",
  "message": "Processing started. Use the job_id to check status."
}
```

#### 2. Check Job Status

```http
GET /api/status/{job_id}
```

Response:
```json
{
  "job_id": "uuid-here",
  "status": "completed",
  "original_pdf_url": "https://...",
  "simplified_pdf_url": "https://...",
  "audio_url": "https://...",
  "video_url": "https://...",
  "error": null,
  "created_at": "2024-01-01T00:00:00",
  "updated_at": "2024-01-01T00:05:00"
}
```

## Processing Pipeline

### 1. PDF Extraction
- Downloads PDF from S3
- Extracts text content using pdfplumber
- Maintains page structure

### 2. Content Simplification
- Sends content to LLM (OpenAI GPT-4 or Gemini)
- AI creates structured, simplified version
- Formats content for CA students

### 3. PDF Generation
- Creates professional PDF with ReportLab
- Includes sections, bullet points, summaries
- Optimized for readability

### 4. Audio Generation
- Converts simplified text to speech
- Uses Google Text-to-Speech (gTTS)
- Creates MP3 audio file

### 5. Video Creation
- Generates conversational transcript with LLM
- Creates animated video with User A & User B dialogue
- Duration: 5-7 minutes
- Includes text overlay and speaker identification

### 6. Cloud Upload
- All generated files uploaded to S3
- URLs stored in database
- Files organized by job ID

## File Structure

```
project/
├── main.py                 # FastAPI application
├── config.py              # Configuration management
├── requirements.txt       # Python dependencies
├── .env.example          # Environment variables template
├── services/
│   ├── database.py       # Supabase integration
│   ├── s3_service.py     # AWS S3 operations
│   ├── pdf_service.py    # PDF processing
│   ├── llm_service.py    # AI/LLM integration
│   ├── audio_service.py  # Audio generation
│   ├── video_service.py  # Video generation
│   └── orchestrator.py   # Main processing orchestration
```

## Video Format

The generated videos feature:
- Alternating colored backgrounds for speakers
- User A (Blue): Asks questions
- User B (Green): Provides explanations
- Clear text display with speaker labels
- Synchronized with generated audio
- 1280x720 resolution
- MP4 format with H.264 codec

## Database Schema

### processing_jobs Collection (MongoDB)

| Field | Type | Description |
|-------|------|-------------|
| _id | ObjectId | MongoDB object ID |
| job_id | string | Unique job identifier |
| status | string | queued, processing, completed, failed |
| original_pdf_url | string | Input PDF URL |
| simplified_pdf_url | string | Output simplified PDF URL |
| audio_url | string | Generated audio URL |
| video_url | string | Generated video URL |
| use_gemini | boolean | Whether Gemini was used |
| use_openai | boolean | Whether OpenAI was used |
| error | string | Error message if failed |
| metadata | object | Additional data (transcript, etc.) |
| created_at | date | Creation timestamp |
| updated_at | date | Last update timestamp |

Indexes created automatically:
- `job_id` (unique)
- `status`
- `created_at`

## Error Handling

All errors are:
- Logged to console
- Stored in database with job
- Returned via API status endpoint
- Include descriptive error messages

## Performance Considerations

- Background processing prevents API blocking
- Temporary files cleaned after processing
- Database indexed on job_id and status
- S3 uploads use public-read ACL

## Future Enhancements

- Support for additional TTS providers
- Custom video templates
- Multi-language support
- Batch processing
- Progress percentage tracking
- Webhook notifications

## License

MIT
