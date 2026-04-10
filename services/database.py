from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, DuplicateKeyError
from datetime import datetime
from typing import Optional, Dict, Any
from config import config
from bson.objectid import ObjectId

class DatabaseService:
    def __init__(self):
        try:
            self.client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            self.db = self.client[config.MONGODB_DB_NAME]
            self.jobs_collection = self.db["processing_jobs"]
            self._create_indexes()
            print("✓ MongoDB connected successfully")
        except ServerSelectionTimeoutError as e:
            raise Exception(f"Failed to connect to MongoDB: {str(e)}")

    def _create_indexes(self):
        """Create necessary indexes for optimal query performance."""
        try:
            self.jobs_collection.create_index("job_id", unique=True)
            self.jobs_collection.create_index("status")
            self.jobs_collection.create_index("created_at", background=True)
        except Exception as e:
            print(f"Warning: Could not create indexes: {str(e)}")

    def create_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new processing job in the database."""
        try:
            # Add timestamps
            job_data["created_at"] = datetime.utcnow()
            job_data["updated_at"] = datetime.utcnow()

            result = self.jobs_collection.insert_one(job_data)
            job_data["_id"] = result.inserted_id

            return job_data
        except DuplicateKeyError:
            raise Exception(f"Job with ID {job_data.get('job_id')} already exists")
        except Exception as e:
            raise Exception(f"Failed to create job: {str(e)}")

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get a job by its job_id."""
        try:
            job = self.jobs_collection.find_one({"job_id": job_id})

            if job:
                job["_id"] = str(job["_id"])
                return job
            return None
        except Exception as e:
            print(f"Error getting job: {str(e)}")
            return None

    def update_job(self, job_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update a job's information."""
        try:
            updates["updated_at"] = datetime.utcnow()

            result = self.jobs_collection.find_one_and_update(
                {"job_id": job_id},
                {"$set": updates},
                return_document=True
            )

            if result:
                result["_id"] = str(result["_id"])
                return result
            return None
        except Exception as e:
            raise Exception(f"Failed to update job: {str(e)}")

    def update_job_status(self, job_id: str, status: str, error: Optional[str] = None) -> Dict[str, Any]:
        """Update job status and optionally set error message."""
        updates = {"status": status}
        if error:
            updates["error"] = error
        return self.update_job(job_id, updates)

    def get_all_jobs(self, limit: int = 100) -> list:
        """Get all jobs ordered by creation date."""
        try:
            jobs = list(
                self.jobs_collection.find()
                .sort("created_at", -1)
                .limit(limit)
            )

            for job in jobs:
                job["_id"] = str(job["_id"])

            return jobs
        except Exception as e:
            print(f"Error getting jobs: {str(e)}")
            return []

    def get_jobs_by_status(self, status: str, limit: int = 50) -> list:
        """Get jobs filtered by status."""
        try:
            jobs = list(
                self.jobs_collection.find({"status": status})
                .sort("created_at", -1)
                .limit(limit)
            )

            for job in jobs:
                job["_id"] = str(job["_id"])

            return jobs
        except Exception as e:
            print(f"Error getting jobs by status: {str(e)}")
            return []

    def save_metadata(self, job_id: str, metadata: Dict[str, Any]) -> bool:
        """Save additional metadata for a job."""
        try:
            self.update_job(job_id, {"metadata": metadata})
            return True
        except Exception as e:
            print(f"Error saving metadata: {str(e)}")
            return False

    def get_metadata(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a job."""
        try:
            job = self.get_job(job_id)
            return job.get("metadata") if job else None
        except Exception as e:
            print(f"Error getting metadata: {str(e)}")
            return None

    def delete_old_jobs(self, days: int = 30) -> int:
        """Delete jobs older than specified days."""
        try:
            from datetime import timedelta

            cutoff_date = datetime.utcnow() - timedelta(days=days)
            result = self.jobs_collection.delete_many({"created_at": {"$lt": cutoff_date}})

            return result.deleted_count
        except Exception as e:
            print(f"Error deleting old jobs: {str(e)}")
            return 0