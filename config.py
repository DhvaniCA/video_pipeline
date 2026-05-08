import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # AWS S3
    AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_REGION            = os.getenv("AWS_REGION", "us-east-1")
    S3_BUCKET_NAME        = os.getenv("S3_BUCKET_NAME")

    # MongoDB
    MONGODB_URI     = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ca_content_processor")

    # OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # Google Gemini
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    # ── ElevenLabs TTS — Indian accent voices ────────────────────────────────
    # Get your key at: https://elevenlabs.io/app/api-key
    # Recommended voices:
    #   Male   Instructor : "Rahul"  → voice_id TX3LPaxmHKxFdv7VOQHJ
    #   Female Student    : "Priya"  → configure in ElevenLabs dashboard
    #                                  or use voice_id EXAVITQu4vr4xnSDxMaL
    # Override voice IDs via env vars:
    #   ELEVENLABS_VOICE_MALE   = <voice_id>
    #   ELEVENLABS_VOICE_FEMALE = <voice_id>
    ELEVENLABS_API_KEY    = os.getenv("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_MALE   = os.getenv("ELEVENLABS_VOICE_MALE",   "29vD33N1CtxCmqQRPOHJ")
    ELEVENLABS_VOICE_FEMALE = os.getenv("ELEVENLABS_VOICE_FEMALE", "tKZQTIqwDrPzLv6MrPxF")

    # Processing settings
    MAX_VIDEO_DURATION = 420   # 7 minutes in seconds
    TEMP_DIR = "/tmp/ca_content_processor"

config = Config()
