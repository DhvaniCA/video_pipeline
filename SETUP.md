# Setup Guide - CA Content Processor

## Quick Start

### 1. Prerequisites

Ensure you have the following installed:
- Python 3.9 or higher
- pip (Python package manager)
- MongoDB (locally or MongoDB Atlas cloud instance)
- AWS account with S3 access
- OpenAI API key
- Google Cloud account with Gemini API access

### 2. Environment Setup

#### Step 1: Create Environment File

```bash
cp .env.example .env
```

#### Step 2: Configure Credentials

Open `.env` and add your credentials:

```env
# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your_aws_access_key_here
AWS_SECRET_ACCESS_KEY=your_aws_secret_key_here
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket-name

# MongoDB Configuration
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=ca_content_processor

# OpenAI Configuration
OPENAI_API_KEY=sk-your-openai-key-here

# Google Gemini Configuration
GOOGLE_API_KEY=your-google-api-key-here
```

For MongoDB Atlas (cloud):
```env
MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/
MONGODB_DB_NAME=ca_content_processor
```

### 3. Install Dependencies

#### Option A: Using the run script (Recommended)

```bash
chmod +x run.sh
./run.sh
```

This will:
- Create a virtual environment
- Install all dependencies
- Start the server

#### Option B: Manual installation

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the server
python main.py
```

### 4. Verify Installation

Once the server starts, visit:
- API: http://localhost:8000
- Interactive API docs: http://localhost:8000/docs
- Alternative API docs: http://localhost:8000/redoc

## MongoDB Setup

### Option 1: Local MongoDB (Recommended for Development)

1. **Install MongoDB Community Edition**
   - macOS: `brew install mongodb-community`
   - Ubuntu: `sudo apt-get install -y mongodb`
   - Windows: Download from [mongodb.com](https://www.mongodb.com/try/download/community)

2. **Start MongoDB**
   ```bash
   # macOS
   brew services start mongodb-community

   # Ubuntu
   sudo systemctl start mongod

   # Windows (if installed as service)
   # MongoDB will start automatically
   ```

3. **Verify Connection**
   ```bash
   mongosh  # or mongo on older versions
   ```

4. **Configure .env**
   ```env
   MONGODB_URI=mongodb://localhost:27017
   MONGODB_DB_NAME=ca_content_processor
   ```

### Option 2: MongoDB Atlas (Cloud)

1. Go to [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
2. Create a free account
3. Create a new cluster
4. Get your connection string
5. Configure .env with your connection string:
   ```env
   MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/
   MONGODB_DB_NAME=ca_content_processor
   ```

## Getting API Keys

### AWS S3

1. Go to [AWS Console](https://console.aws.amazon.com/)
2. Navigate to IAM → Users → Create User
3. Attach policy: `AmazonS3FullAccess`
4. Create access key
5. Create an S3 bucket in your preferred region

### OpenAI

1. Go to [OpenAI Platform](https://platform.openai.com/)
2. Sign up or log in
3. Navigate to API Keys
4. Create a new secret key
5. Note: Requires billing setup

### Google Gemini

1. Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Sign in with Google account
3. Click "Create API Key"
4. Copy the generated key

## Database

MongoDB will automatically create the database and collection on first run. The `processing_jobs` collection includes:
- Job tracking and status
- URLs for all generated files
- Error logging
- Metadata storage (flexible BSON format)

## Testing the API

### Using the test script:

```bash
python test_api.py
```

### Using curl:

```bash
# Test root endpoint
curl http://localhost:8000/

# Start processing a PDF
curl -X POST http://localhost:8000/api/process \
  -H "Content-Type: application/json" \
  -d '{
    "pdf_s3_url": "https://your-bucket.s3.amazonaws.com/sample.pdf",
    "use_gemini": true,
    "use_openai": true
  }'

# Check job status
curl http://localhost:8000/api/status/{job_id}
```

### Using the interactive docs:

Visit http://localhost:8000/docs and use the built-in API testing interface.

## S3 Bucket Setup

1. Create a bucket in your AWS region
2. Configure bucket permissions:
   - Enable public access for generated files (or use signed URLs)
   - Add CORS configuration if accessing from web browser:

```json
[
    {
        "AllowedHeaders": ["*"],
        "AllowedMethods": ["GET", "HEAD"],
        "AllowedOrigins": ["*"],
        "ExposeHeaders": []
    }
]
```

## Troubleshooting

### Common Issues

#### 1. Module not found errors
```bash
# Ensure virtual environment is activated
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

#### 2. API key errors
- Verify all API keys are correctly set in `.env`
- Check for extra spaces or quotes
- Ensure API keys are active and have sufficient credits

#### 3. S3 upload failures
- Verify AWS credentials
- Check bucket name and region
- Ensure bucket allows uploads
- Verify IAM permissions

#### 4. Video generation errors
- Ensure ffmpeg is installed on your system
- moviepy requires ffmpeg for video encoding
- Install: `brew install ffmpeg` (Mac) or `apt-get install ffmpeg` (Linux)

#### 5. MongoDB connection errors
- Verify MongoDB is running
- Check MONGODB_URI and MONGODB_DB_NAME in .env
- For local: ensure `mongod` service is running
- For Atlas: check cluster is active and IP whitelisted
- Test with: `mongosh "mongodb://localhost:27017"`

## Production Deployment

### Recommended Setup:

1. **Use environment-specific .env files**
2. **Set up proper S3 bucket policies**
3. **Use service accounts for API access**
4. **Implement rate limiting**
5. **Add authentication to API endpoints**
6. **Set up monitoring and logging**
7. **Use a production ASGI server** (e.g., gunicorn with uvicorn workers)

### Example production start:

```bash
gunicorn main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

## Support

For issues or questions:
1. Check the README.md for detailed documentation
2. Review error logs in the console
3. Verify all environment variables are set correctly
4. Check API rate limits and quotas

## Next Steps

Once everything is set up:

1. Upload a sample PDF to your S3 bucket
2. Use the API to process it
3. Monitor the job status
4. Check the generated simplified PDF, audio, and video files in S3
5. Review the transcript in the database metadata

Happy processing!
