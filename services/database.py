# from pymongo import MongoClient
# from pymongo.errors import ServerSelectionTimeoutError, DuplicateKeyError
# from datetime import datetime
# from typing import Optional, Dict, Any
# from config import config
# from bson.objectid import ObjectId

# class DatabaseService:
#     def __init__(self):
#         try:
#             self.client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000)
#             self.client.admin.command('ping')
#             self.db = self.client[config.MONGODB_DB_NAME]
#             self.jobs_collection = self.db["processing_jobs"]
#             self._create_indexes()
#             print("✓ MongoDB connected successfully")
#         except ServerSelectionTimeoutError as e:
#             raise Exception(f"Failed to connect to MongoDB: {str(e)}")

#     def _create_indexes(self):
#         """Create necessary indexes for optimal query performance."""
#         try:
#             self.jobs_collection.create_index("job_id", unique=True)
#             self.jobs_collection.create_index("status")
#             self.jobs_collection.create_index("created_at", background=True)
#         except Exception as e:
#             print(f"Warning: Could not create indexes: {str(e)}")

#     def create_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
#         """Create a new processing job in the database."""
#         try:
#             # Add timestamps
#             job_data["created_at"] = datetime.utcnow()
#             job_data["updated_at"] = datetime.utcnow()

#             result = self.jobs_collection.insert_one(job_data)
#             job_data["_id"] = result.inserted_id

#             return job_data
#         except DuplicateKeyError:
#             raise Exception(f"Job with ID {job_data.get('job_id')} already exists")
#         except Exception as e:
#             raise Exception(f"Failed to create job: {str(e)}")

#     def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
#         """Get a job by its job_id."""
#         try:
#             job = self.jobs_collection.find_one({"job_id": job_id})

#             if job:
#                 job["_id"] = str(job["_id"])
#                 return job
#             return None
#         except Exception as e:
#             print(f"Error getting job: {str(e)}")
#             return None

#     def update_job(self, job_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
#         """Update a job's information."""
#         try:
#             updates["updated_at"] = datetime.utcnow()

#             result = self.jobs_collection.find_one_and_update(
#                 {"job_id": job_id},
#                 {"$set": updates},
#                 return_document=True
#             )

#             if result:
#                 result["_id"] = str(result["_id"])
#                 return result
#             return None
#         except Exception as e:
#             raise Exception(f"Failed to update job: {str(e)}")

#     def update_job_status(self, job_id: str, status: str, error: Optional[str] = None) -> Dict[str, Any]:
#         """Update job status and optionally set error message."""
#         updates = {"status": status}
#         if error:
#             updates["error"] = error
#         return self.update_job(job_id, updates)

#     def get_all_jobs(self, limit: int = 100) -> list:
#         """Get all jobs ordered by creation date."""
#         try:
#             jobs = list(
#                 self.jobs_collection.find()
#                 .sort("created_at", -1)
#                 .limit(limit)
#             )

#             for job in jobs:
#                 job["_id"] = str(job["_id"])

#             return jobs
#         except Exception as e:
#             print(f"Error getting jobs: {str(e)}")
#             return []

#     def get_jobs_by_status(self, status: str, limit: int = 50) -> list:
#         """Get jobs filtered by status."""
#         try:
#             jobs = list(
#                 self.jobs_collection.find({"status": status})
#                 .sort("created_at", -1)
#                 .limit(limit)
#             )

#             for job in jobs:
#                 job["_id"] = str(job["_id"])

#             return jobs
#         except Exception as e:
#             print(f"Error getting jobs by status: {str(e)}")
#             return []

#     def save_metadata(self, job_id: str, metadata: Dict[str, Any]) -> bool:
#         """Save additional metadata for a job."""
#         try:
#             self.update_job(job_id, {"metadata": metadata})
#             return True
#         except Exception as e:
#             print(f"Error saving metadata: {str(e)}")
#             return False

#     def get_metadata(self, job_id: str) -> Optional[Dict[str, Any]]:
#         """Get metadata for a job."""
#         try:
#             job = self.get_job(job_id)
#             return job.get("metadata") if job else None
#         except Exception as e:
#             print(f"Error getting metadata: {str(e)}")
#             return None

#     def delete_old_jobs(self, days: int = 30) -> int:
#         """Delete jobs older than specified days."""
#         try:
#             from datetime import timedelta

#             cutoff_date = datetime.utcnow() - timedelta(days=days)
#             result = self.jobs_collection.delete_many({"created_at": {"$lt": cutoff_date}})

#             return result.deleted_count
#         except Exception as e:
#             print(f"Error deleting old jobs: {str(e)}")
#             return 0

from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, DuplicateKeyError
from datetime import datetime
from typing import Optional, Dict, Any
from config import config
from bson.objectid import ObjectId

# ── Per-platform database names ───────────────────────────────────────────────
# Both platforms share the same Atlas cluster URI.
# CA jobs are stored in "ca_chatbot"; CS jobs are stored in "cs-chatbot".
_PLATFORM_DB: dict = {
    "ca": getattr(config, "MONGODB_DB_NAME",    "ca_chatbot"),
    "cs": getattr(config, "CS_MONGODB_DB_NAME", "cs-chatbot"),
}


class DatabaseService:
    def __init__(self, platform: str = "ca"):
        """
        Args:
            platform: "ca" or "cs".
                      - Selects the correct database  (ca_chatbot / cs-chatbot).
                      - Selects the correct dashboard collection (ca_dashboard / cs_dashboard).
                      - processing_jobs collection lives in whichever DB is selected.
        """
        try:
            self.platform = platform
            db_name       = _PLATFORM_DB.get(platform, config.MONGODB_DB_NAME)

            self.client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            self.db = self.client[db_name]

            self.jobs_collection      = self.db["processing_jobs"]
            self.dashboard_collection = self.db[f"{platform}_dashboard"]

            self._create_indexes()
            print(f"✓ MongoDB connected → db='{db_name}'")
            print(f"✓ Collections: processing_jobs, {platform}_dashboard")
        except ServerSelectionTimeoutError as e:
            raise Exception(f"Failed to connect to MongoDB: {str(e)}")

    def _create_indexes(self):
        """Create necessary indexes for optimal query performance."""
        try:
            # processing_jobs indexes
            self.jobs_collection.create_index("job_id", unique=True)
            self.jobs_collection.create_index("dashboard_id")
            self.jobs_collection.create_index("platform")      # useful for filtering by platform
            self.jobs_collection.create_index("status")
            self.jobs_collection.create_index("created_at", background=True)

            # platform_dashboard indexes
            self.dashboard_collection.create_index("_id")
            self.dashboard_collection.create_index("subject")
            # "chapter" used by CA; "module" used by CS — index both defensively
            self.dashboard_collection.create_index("chapter")
            self.dashboard_collection.create_index("module")
        except Exception as e:
            print(f"Warning: Could not create indexes: {str(e)}")

    # ========================================================================
    # PROCESSING_JOBS Collection Methods
    # ========================================================================

    def create_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new processing job in the database."""
        try:
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

    def get_jobs_by_dashboard_id(self, dashboard_id: str) -> list:
        """Get all processing jobs for a specific dashboard document."""
        try:
            jobs = list(
                self.jobs_collection.find({"dashboard_id": dashboard_id})
                .sort("created_at", -1)
            )

            for job in jobs:
                job["_id"] = str(job["_id"])

            return jobs
        except Exception as e:
            print(f"Error getting jobs by dashboard_id: {str(e)}")
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

    # ========================================================================
    # {platform}_DASHBOARD Collection Methods
    # ========================================================================

    def get_dashboard_document(self, dashboard_id: str) -> Optional[Dict[str, Any]]:
        """Get a document from {platform}_dashboard collection by _id."""
        try:
            if isinstance(dashboard_id, str):
                try:
                    obj_id = ObjectId(dashboard_id)
                except Exception:
                    obj_id = dashboard_id

            doc = self.dashboard_collection.find_one({"_id": obj_id})

            if doc:
                doc["_id"] = str(doc["_id"])
                return doc
            return None
        except Exception as e:
            print(f"Error getting dashboard document: {str(e)}")
            return None

    def update_dashboard_with_urls(
        self,
        dashboard_id: str,
        simplified_pdf_url: Optional[str] = None,
        audio_url: Optional[str] = None,
        video_url: Optional[str] = None,
        processing_status: str = "completed"
    ) -> bool:
        """
        Update {platform}_dashboard document with generated content URLs.

        Args:
            dashboard_id:       MongoDB _id from {platform}_dashboard
            simplified_pdf_url: URL of simplified PDF from S3
            audio_url:          URL of audio file from S3
            video_url:          URL of video file from S3
            processing_status:  Status of processing (completed, failed, processing, pending)

        Returns:
            True if successful, False otherwise
        """
        try:
            if isinstance(dashboard_id, str):
                try:
                    obj_id = ObjectId(dashboard_id)
                except Exception:
                    obj_id = dashboard_id

            updates = {
                "updated_at": datetime.utcnow(),
                "processing_status": processing_status
            }

            if simplified_pdf_url:
                updates["simplified_pdf_url"] = simplified_pdf_url

            if audio_url:
                updates["audio_url"] = audio_url

            if video_url:
                updates["video_url"] = video_url

            result = self.dashboard_collection.find_one_and_update(
                {"_id": obj_id},
                {"$set": updates},
                return_document=True
            )

            if result:
                print(f"✓ Updated {self.platform}_dashboard document {dashboard_id}")
                return True
            else:
                print(f"✗ Dashboard document {dashboard_id} not found in {self.platform}_dashboard")
                return False

        except Exception as e:
            print(f"Error updating dashboard document: {str(e)}")
            return False

    def get_dashboard_by_chapter(self, chapter: str) -> list:
        """Get all dashboard documents for a specific chapter (CA) or module (CS)."""
        try:
            # Support both "chapter" (CA) and "module" (CS) field names
            docs = list(
                self.dashboard_collection.find({
                    "$or": [
                        {"chapter": chapter},
                        {"module": chapter},
                    ]
                }).sort("created_at", -1)
            )

            for doc in docs:
                doc["_id"] = str(doc["_id"])

            return docs
        except Exception as e:
            print(f"Error getting dashboard documents by chapter: {str(e)}")
            return []

    def get_dashboard_by_subject(self, subject: str) -> list:
        """Get all dashboard documents for a specific subject."""
        try:
            docs = list(
                self.dashboard_collection.find({"subject": subject})
                .sort("created_at", -1)
            )

            for doc in docs:
                doc["_id"] = str(doc["_id"])

            return docs
        except Exception as e:
            print(f"Error getting dashboard documents by subject: {str(e)}")
            return []

    def get_dashboard_by_level(self, level: str) -> list:
        """Get all dashboard documents for a specific level."""
        try:
            docs = list(
                self.dashboard_collection.find({"level": level})
                .sort("created_at", -1)
            )

            for doc in docs:
                doc["_id"] = str(doc["_id"])

            return docs
        except Exception as e:
            print(f"Error getting dashboard documents by level: {str(e)}")
            return []

    def search_dashboard(self, query: str) -> list:
        """Search {platform}_dashboard documents by title, chapter, or module."""
        try:
            docs = list(
                self.dashboard_collection.find({
                    "$or": [
                        {"title":   {"$regex": query, "$options": "i"}},
                        {"chapter": {"$regex": query, "$options": "i"}},
                        {"module":  {"$regex": query, "$options": "i"}},
                    ]
                }).sort("created_at", -1)
            )

            for doc in docs:
                doc["_id"] = str(doc["_id"])

            return docs
        except Exception as e:
            print(f"Error searching dashboard: {str(e)}")
            return []
