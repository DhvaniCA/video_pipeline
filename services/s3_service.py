import boto3
from botocore.exceptions import ClientError
from typing import Optional
import os
from config import config
from urllib.parse import urlparse, unquote
class S3Service:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION
        )
        self.bucket_name = config.S3_BUCKET_NAME

    def download_file(self, s3_url: str, local_path: str) -> bool:
        """Download a file from S3 URL to local path."""
        try:
            # Convert to plain string (handles Pydantic HttpUrl objects too)
            s3_url = str(s3_url)

            # Robustly extract the S3 key from the URL
            # URL format: https://bucket.s3.region.amazonaws.com/key/path/file.pdf
            from urllib.parse import urlparse
            parsed = urlparse(s3_url)
            # The path starts with '/', so strip the leading slash to get the key
            key = unquote(parsed.path.lstrip('/'))   # ✅ FIX

            print(f"  Bucket: {self.bucket_name}")
            print(f"  Key: {key}")

            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            self.s3_client.download_file(self.bucket_name, key, local_path)
            return True
        except ClientError as e:
            print(f"Error downloading file from S3: {e}")
            return False

    def upload_file(self, local_path: str, s3_key: str) -> Optional[str]:
        """Upload a file to S3 and return the S3 URL."""
        try:
            self.s3_client.upload_file(
                local_path,
                self.bucket_name,
                s3_key,
                ExtraArgs={'ACL': 'public-read'}
            )

            s3_url = f"https://{self.bucket_name}.s3.{config.AWS_REGION}.amazonaws.com/{s3_key}"
            return s3_url
        except ClientError as e:
            print(f"Error uploading file to S3: {e}")
            return None

    def generate_s3_key(self, job_id: str, file_type: str, extension: str) -> str:
        """Generate a unique S3 key for a file."""
        return f"ca-content/{job_id}/{file_type}.{extension}"
