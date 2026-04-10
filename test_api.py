"""
Simple test script to verify the API endpoints.
This script demonstrates how to use the CA Content Processor API.
"""

import requests
import time
import json

# API base URL
BASE_URL = "http://localhost:8000"

def test_root():
    """Test the root endpoint."""
    print("Testing root endpoint...")
    response = requests.get(f"{BASE_URL}/")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}\n")

def test_process_content(pdf_url):
    """Test content processing."""
    print("Testing content processing...")

    data = {
        "pdf_s3_url": pdf_url,
        "use_gemini": True,
        "use_openai": True
    }

    response = requests.post(f"{BASE_URL}/api/process", json=data)
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        print(f"Job ID: {result['job_id']}")
        print(f"Status: {result['status']}")
        print(f"Message: {result['message']}\n")
        return result['job_id']
    else:
        print(f"Error: {response.text}\n")
        return None

def test_job_status(job_id):
    """Test job status endpoint."""
    print(f"Checking status for job: {job_id}")

    response = requests.get(f"{BASE_URL}/api/status/{job_id}")
    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        print(f"Job Status: {result['status']}")
        print(f"Original PDF: {result['original_pdf_url']}")

        if result['simplified_pdf_url']:
            print(f"Simplified PDF: {result['simplified_pdf_url']}")
        if result['audio_url']:
            print(f"Audio: {result['audio_url']}")
        if result['video_url']:
            print(f"Video: {result['video_url']}")
        if result['error']:
            print(f"Error: {result['error']}")

        print(f"Created: {result['created_at']}")
        print(f"Updated: {result['updated_at']}\n")
        return result['status']
    else:
        print(f"Error: {response.text}\n")
        return None

def monitor_job(job_id, max_wait_time=600):
    """Monitor a job until completion or timeout."""
    print(f"Monitoring job: {job_id}")
    print(f"Max wait time: {max_wait_time} seconds\n")

    start_time = time.time()

    while True:
        elapsed = time.time() - start_time

        if elapsed > max_wait_time:
            print("Timeout reached!")
            break

        status = test_job_status(job_id)

        if status in ['completed', 'failed']:
            print(f"Job finished with status: {status}")
            break

        print(f"Still processing... (elapsed: {int(elapsed)}s)")
        time.sleep(10)  # Check every 10 seconds

if __name__ == "__main__":
    print("=" * 60)
    print("CA Content Processor API Test")
    print("=" * 60 + "\n")

    # Test root endpoint
    test_root()

    # Example: Test with a sample PDF URL (replace with your actual S3 URL)
    sample_pdf_url = "https://your-bucket.s3.amazonaws.com/sample.pdf"

    print("\nTo test the processing endpoint, provide a valid S3 PDF URL:")
    print(f"Example: python test_api.py")
    print("\nOr modify the sample_pdf_url in this script.\n")

    # Uncomment below to test processing:
    # job_id = test_process_content(sample_pdf_url)
    # if job_id:
    #     monitor_job(job_id)
