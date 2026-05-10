import json
from functools import lru_cache
import firebase_admin
from firebase_admin import auth, credentials, db as firebase_db
import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)
from config import get_settings

_app: firebase_admin.App | None = None


def _init_firebase() -> firebase_admin.App:
    global _app
    if _app:
        return _app
    s = get_settings()
    cred = credentials.Certificate(s.firebase_service_account_json)
    _app = firebase_admin.initialize_app(
        cred,
        {"databaseURL": s.firebase_database_url},
    )
    return _app


class FirebaseAuthService:
    def __init__(self) -> None:
        _init_firebase()

    def verify_token(self, id_token: str) -> dict:
        """
        Verifies a Firebase ID token from the Android app.
        Returns the decoded claims dict which includes uid, email, etc.
        Raises firebase_admin.auth.InvalidIdTokenError on failure.
        """
        decoded = auth.verify_id_token(id_token)
        return decoded

    def get_uid(self, id_token: str) -> str:
        return self.verify_token(id_token)["uid"]

    def notify_job_done(self, officer_id: str, job_id: str, payload: dict) -> None:
        """
        Writes to Firebase Realtime DB so the Android app's listener fires.
        Path: /officers/{officer_id}/jobs/{job_id}
        """
        ref = firebase_db.reference(f"officers/{officer_id}/jobs/{job_id}")
        ref.set(payload)

    def update_kill_list_status(
        self, officer_id: str, status: str, job_id: str
    ) -> None:
        ref = firebase_db.reference(f"officers/{officer_id}/kill_list_status")
        ref.set({"status": status, "job_id": job_id})