import json
import os
import uuid
import logging
from datetime import datetime

# Set up the Audit Logger
audit_logger = logging.getLogger("ITSM_AuditLog")
audit_logger.setLevel(logging.INFO)
fh = logging.FileHandler("audit.log")
fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
if not audit_logger.handlers:
    audit_logger.addHandler(fh)

class SessionManager:
    def __init__(self, storage_file="user_sessions.json"):
        self.storage_file = storage_file
        self.sessions = self._load_sessions()
        self.active_logins = set() # Tracks who has already triggered a login log this server run

    def _load_sessions(self):
        if os.path.exists(self.storage_file):
            with open(self.storage_file, "r") as f:
                return json.load(f)
        return {}

    def _save_sessions(self):
        with open(self.storage_file, "w") as f:
            json.dump(self.sessions, f, indent=4)

    def get_or_create_session(self, user_id, role_name):
        uid_str = str(user_id)
        # Create a persistent thread for new users
        if uid_str not in self.sessions:
            self.sessions[uid_str] = {
                "thread_id": str(uuid.uuid4()), # Generate a unique thread ID
                "chat_history": []
            }
            self._save_sessions()
        
        # Log the login event if this is their first action during this server run
        if uid_str not in self.active_logins:
            thread_id = self.sessions[uid_str]["thread_id"]
            audit_logger.info(f"LOGIN: User ID {user_id} (Role: {role_name}) logged in. Thread ID: {thread_id}")
            self.active_logins.add(uid_str)
            
            # --- Pause abandoned creation states awaiting user confirmation ---
            state = self.sessions[uid_str].get("creation_state")
            if state in ["AWAITING_IMPACT", "AWAITING_URGENCY"]:
                self.sessions[uid_str]["previous_creation_state"] = state
                self.sessions[uid_str]["creation_state"] = "AWAITING_CONTINUE_CONFIRMATION"
                self._save_sessions()
                
            # --- Wipe abandoning assign/comment states to avoid getting stuck ---
            self.sessions[uid_str]["assign_state"] = None
            self.sessions[uid_str]["assign_incident_id"] = None
            self.sessions[uid_str]["assign_inc_number"] = None
            self.sessions[uid_str]["assign_comment"] = None
            self.sessions[uid_str]["assign_retries"] = 0
            
            # --- Wipe standalone SLA comment state to force a clean DB query on login ---
            self.sessions[uid_str]["sla_comment_state"] = None
            self.sessions[uid_str]["sla_incident_id"] = None
            self.sessions[uid_str]["sla_inc_number"] = None
            self.sessions[uid_str]["sla_comment_text"] = None
            self.sessions[uid_str]["sla_retries"] = 0
            self._save_sessions()

            # --- Wipe standalone Hold state on login ---
            self.sessions[uid_str]["hold_state"] = None
            self.sessions[uid_str]["hold_incident_id"] = None
            self.sessions[uid_str]["hold_inc_number"] = None
            self.sessions[uid_str]["hold_reason_id"] = None
            self.sessions[uid_str]["hold_comment_text"] = None
            self.sessions[uid_str]["hold_retries"] = 0

            # --- NEW: Wipe standalone Close state on login ---
            self.sessions[uid_str]["close_state"] = None
            self.sessions[uid_str]["close_incident_id"] = None
            self.sessions[uid_str]["close_inc_number"] = None
            self.sessions[uid_str]["closure_code_id"] = None
            self.sessions[uid_str]["close_retries"] = 0
            
            self._save_sessions()
            
        return self.sessions[uid_str]

    def save_history(self, user_id, history):
        uid_str = str(user_id)
        if uid_str in self.sessions:
            self.sessions[uid_str]["chat_history"] = history
            self._save_sessions()

    def log_db_modification(self, user_id, table_name, action="MODIFIED"):
        audit_logger.info(f"DB ACTION: User ID {user_id} {action} table '{table_name}'")