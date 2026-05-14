import boto3
from botocore.exceptions import ClientError
from typing import Optional, Tuple
import os
from config import config
from urllib.parse import urlparse, unquote


class S3Service:
    def __init__(self, platform: str = "ca"):
        """
        Args:
            platform: "ca" or "cs" — used as the S3 key prefix folder.
                      "ca" → ca-content/..., "cs" → cs-content/...
        """
        self.platform = platform
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION
        )
        # Default bucket for uploads — derived from platform
        self.bucket_name = (
            getattr(config, "CS_S3_BUCKET_NAME", config.S3_BUCKET_NAME)
            if platform == "cs"
            else config.S3_BUCKET_NAME
        )

    @staticmethod
    def parse_s3_url(s3_url: str) -> Tuple[str, str]:
        """
        Parse any S3 URL and return (bucket, key).

        Handles both formats:
          Virtual-hosted: https://bucket-name.s3.region.amazonaws.com/key/file.pdf
          Path-style:     https://s3.region.amazonaws.com/bucket-name/key/file.pdf

        This is the ONLY place bucket+key are extracted — never use self.bucket_name
        for downloads, always parse from the URL itself.
        """
        s3_url = str(s3_url)
        parsed = urlparse(s3_url)
        host   = parsed.netloc  # e.g. "cs-chatbot-pdf-store.s3.ap-south-1.amazonaws.com"

        if ".s3." in host and host.endswith(".amazonaws.com"):
            # Virtual-hosted style — bucket is the subdomain before .s3.
            bucket = host.split(".s3.")[0]
            key    = unquote(parsed.path.lstrip("/"))
        elif host.startswith("s3.") and host.endswith(".amazonaws.com"):
            # Path style — bucket is first path segment
            parts  = unquote(parsed.path.lstrip("/")).split("/", 1)
            bucket = parts[0]
            key    = parts[1] if len(parts) > 1 else ""
        else:
            # Fallback — treat whole path as key, bucket unknown
            raise ValueError(f"Cannot parse S3 URL — unrecognised format: {s3_url}")

        return bucket, key

    def download_file(self, s3_url: str, local_path: str) -> bool:
        """
        Download a file from an S3 URL to a local path.

        Bucket is parsed FROM THE URL — never from self.bucket_name.
        This makes it work for both CA and CS regardless of which
        bucket the file was originally uploaded to.
        """
        try:
            bucket, key = self.parse_s3_url(s3_url)

            print(f"  Bucket: {bucket}")   # will correctly show cs-chatbot-pdf-store for CS
            print(f"  Key:    {key}")

            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            self.s3_client.download_file(bucket, key, local_path)
            return True

        except ValueError as e:
            print(f"Error parsing S3 URL: {e}")
            return False
        except ClientError as e:
            print(f"Error downloading file from S3: {e}")
            return False

    def upload_file(self, local_path: str, s3_key: str) -> Optional[str]:
        """
        Upload a file to S3 and return the public S3 URL.

        Always uploads to self.bucket_name (platform-specific bucket set in __init__).
        """
        try:
            self.s3_client.upload_file(
                local_path,
                self.bucket_name,
                s3_key,
                ExtraArgs={'ACL': 'public-read'}
            )
            s3_url = (
                f"https://{self.bucket_name}"
                f".s3.{config.AWS_REGION}.amazonaws.com/{s3_key}"
            )
            return s3_url

        except ClientError as e:
            print(f"Error uploading file to S3: {e}")
            return None

    def generate_s3_key(self, job_id: str, file_type: str, extension: str) -> str:
        """
        Generate a unique S3 key using the platform prefix.

        Examples:
            platform="ca" → "ca-content/{job_id}/{file_type}.{extension}"
            platform="cs" → "cs-content/{job_id}/{file_type}.{extension}"
        """
        return f"{self.platform}-content/{job_id}/{file_type}.{extension}"
