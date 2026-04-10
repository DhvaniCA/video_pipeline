"""
Example usage of the CA Content Processor API

This demonstrates how to integrate the API into your application.
"""

import asyncio
import aiohttp
from typing import Dict, Optional

class CAContentProcessorClient:
    """Client for interacting with the CA Content Processor API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url

    async def process_pdf(
        self,
        pdf_s3_url: str,
        use_gemini: bool = True,
        use_openai: bool = True
    ) -> Optional[str]:
        """
        Start processing a PDF and return the job ID.

        Args:
            pdf_s3_url: S3 URL of the PDF to process
            use_gemini: Whether to use Google Gemini
            use_openai: Whether to use OpenAI

        Returns:
            Job ID if successful, None otherwise
        """
        async with aiohttp.ClientSession() as session:
            data = {
                "pdf_s3_url": pdf_s3_url,
                "use_gemini": use_gemini,
                "use_openai": use_openai
            }

            async with session.post(
                f"{self.base_url}/api/process",
                json=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("job_id")
                else:
                    print(f"Error: {await response.text()}")
                    return None

    async def get_job_status(self, job_id: str) -> Optional[Dict]:
        """
        Get the status of a processing job.

        Args:
            job_id: The job ID to check

        Returns:
            Job status dict if successful, None otherwise
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/api/status/{job_id}"
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"Error: {await response.text()}")
                    return None

    async def wait_for_completion(
        self,
        job_id: str,
        check_interval: int = 5,
        timeout: int = 600
    ) -> Optional[Dict]:
        """
        Wait for a job to complete.

        Args:
            job_id: The job ID to monitor
            check_interval: Seconds between status checks
            timeout: Maximum time to wait in seconds

        Returns:
            Final job status if completed, None if timeout
        """
        elapsed = 0

        while elapsed < timeout:
            status = await self.get_job_status(job_id)

            if status and status["status"] in ["completed", "failed"]:
                return status

            await asyncio.sleep(check_interval)
            elapsed += check_interval
            print(f"Still processing... ({elapsed}s elapsed)")

        print("Timeout reached!")
        return None


async def example_basic_usage():
    """Basic usage example."""
    print("=== Basic Usage Example ===\n")

    client = CAContentProcessorClient()

    # Replace with your actual S3 URL
    pdf_url = "https://your-bucket.s3.amazonaws.com/sample-ca-document.pdf"

    # Start processing
    print(f"Starting processing for: {pdf_url}")
    job_id = await client.process_pdf(pdf_url)

    if job_id:
        print(f"✓ Processing started! Job ID: {job_id}\n")

        # Wait for completion
        print("Waiting for processing to complete...")
        result = await client.wait_for_completion(job_id)

        if result:
            print("\n=== Processing Complete ===")
            print(f"Status: {result['status']}")

            if result['status'] == 'completed':
                print(f"\nGenerated Files:")
                print(f"  • Simplified PDF: {result['simplified_pdf_url']}")
                print(f"  • Audio: {result['audio_url']}")
                print(f"  • Video: {result['video_url']}")
            elif result['status'] == 'failed':
                print(f"\nError: {result['error']}")
    else:
        print("✗ Failed to start processing")


async def example_batch_processing():
    """Example of processing multiple PDFs."""
    print("=== Batch Processing Example ===\n")

    client = CAContentProcessorClient()

    # List of PDFs to process
    pdf_urls = [
        "https://your-bucket.s3.amazonaws.com/accounting-basics.pdf",
        "https://your-bucket.s3.amazonaws.com/taxation-guide.pdf",
        "https://your-bucket.s3.amazonaws.com/audit-procedures.pdf",
    ]

    jobs = []

    # Start all jobs
    for url in pdf_urls:
        print(f"Starting: {url}")
        job_id = await client.process_pdf(url)
        if job_id:
            jobs.append(job_id)
            print(f"  ✓ Job ID: {job_id}")
        else:
            print(f"  ✗ Failed to start")

    print(f"\n{len(jobs)} jobs started. Monitoring progress...\n")

    # Monitor all jobs
    completed = 0
    failed = 0

    for job_id in jobs:
        result = await client.wait_for_completion(job_id, timeout=300)
        if result:
            if result['status'] == 'completed':
                completed += 1
                print(f"✓ Job {job_id}: Completed")
            else:
                failed += 1
                print(f"✗ Job {job_id}: Failed - {result.get('error')}")

    print(f"\nResults: {completed} completed, {failed} failed")


async def example_error_handling():
    """Example with proper error handling."""
    print("=== Error Handling Example ===\n")

    client = CAContentProcessorClient()

    try:
        # Invalid URL example
        job_id = await client.process_pdf("invalid-url")

        if not job_id:
            print("Failed to start processing - check your PDF URL")
            return

        # Monitor with timeout
        result = await client.wait_for_completion(job_id, timeout=60)

        if not result:
            print("Processing timed out - job may still be running")
            # Could implement retry logic here
            return

        if result['status'] == 'failed':
            print(f"Processing failed: {result['error']}")
            # Could implement error notification here
            return

        print("Processing completed successfully!")

    except Exception as e:
        print(f"Unexpected error: {e}")
        # Could implement logging or alerting here


async def main():
    """Run examples."""
    print("CA Content Processor - API Examples\n")
    print("Note: Replace PDF URLs with your actual S3 URLs\n")
    print("-" * 60 + "\n")

    # Run basic example
    await example_basic_usage()

    # Uncomment to run other examples:
    # await example_batch_processing()
    # await example_error_handling()


if __name__ == "__main__":
    asyncio.run(main())
