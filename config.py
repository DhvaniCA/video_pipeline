import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # AWS S3
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

    # MongoDB
    MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ca_content_processor")

    # OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # Google Gemini
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

    # TTS API
    TTS_API_KEY = os.getenv("TTS_API_KEY")
    TTS_API_URL = os.getenv("TTS_API_URL")

    # Processing settings
    MAX_VIDEO_DURATION = 420  # 7 minutes in seconds
    TEMP_DIR = "/tmp/ca_content_processor"

config = Config()
