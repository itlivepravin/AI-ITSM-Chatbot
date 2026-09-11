# modules/op_slaComment.py
import json
from datetime import datetime

class SLACommentController:
    def __init__(self, db_cursor, ai_client, db_connection, session_manager, spicy=False):
        self.cursor = db_cursor
        self.ai_client = ai_client
        self.db_connection = db_connection
        self.session_manager = session_manager
        self.spicy = spicy
        
        # Flag to control if incident_comment table is updated upon Resolution
        # --- FIXED: Set to False so resolution comments are only stored in the incident table ---
        self.UPDATE_INCIDENT_COMMENT_ON_RESOLUTION = False

    def handle_sla_intent(self, user_input, user_id):
        """Triggered when an agent explicitly asks to comment on an SLA."""
        executed_queries = []
        
        # 1. Enforce the keyword validation
        if "sla" not in user_input.lower():
            return "Please specify where to add the comment (e.g., 'I want to append a comment for SLA').", [], ""

        prompt = (
            "You are an ITSM AI assistant. The agent wants to add an SLA comment to an incident.\n"
            f"Agent input: '{user_input}'\n"
            "Extract the incident number (e.g., 'INC00015') or ID (e.g., 15) from the text.\n"
            "Output ONLY a JSON object with 'incident_identifier' (string or integer)."
        )
        
        try:
            response = self.ai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            ai_data = json.loads(response.choices[0].message.content)
            inc_id_raw = ai_data.get('incident_identifier')
        except Exception:
            return "Failed to parse the incident number from your request. Please try again.", [], ""

        if not inc_id_raw:
            return "Please explicitly specify the incident number.", [], ""

        inc_id_str = str(inc_id_raw).strip()
        
        if inc_id_str.isdigit():
            query = "SELECT incident_id, incident_number, assigned_user_id FROM incident WHERE incident_id = %s"
            params = (int(inc_id_str),)
        else:
            inc_id_str = inc_id_str.upper() if inc_id_str.upper().startswith("INC") else f"INC{inc_id_str.zfill(5)}"
            query = "SELECT incident_id, incident_number, assigned_user_id FROM incident WHERE incident_number = %s"
            params = (inc_id_str,)

        self.cursor.execute(query, params)
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
        target = self.cursor.fetchone()

        # 2. Enforce Ownership Validation
        if not target or target['assigned_user_id'] != user_id:
            return f"Action is out of bounds. No incident {inc_id_str} found for SLA (or it is not assigned to you).", [], "\n".join(executed_queries)

        incident_id = target['incident_id']
        inc_number = target['incident_number']

        # Enter State Machine
        if hasattr(self.session_manager, 'get_or_create_session'):
            session = self.session_manager.get_or_create_session(user_id, "Agent")
            session['sla_comment_state'] = 'AWAITING_SLA_COMMENT_TEXT'
            session['sla_incident_id'] = incident_id
            session['sla_inc_number'] = inc_number
            session['sla_retries'] = 0
            self.session_manager._save_sessions()
            
            msg = f"Please enter your comment for the SLA for incident {inc_number}."
            chat_history = session.get("chat_history", [])
            chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
            self.session_manager.save_history(user_id, chat_history)
            
            return msg, [], "\n".join(executed_queries)

    def process_step(self, user_input, user_id):
        """State machine handling Proactive Reminders, Response SLA, and Resolution SLA."""
        executed_queries = []
        
        self.cursor.execute("SELECT r.role_name FROM user_role ur JOIN role r ON ur.role_id = r.role_id WHERE ur.user_id = %s", (user_id,))
        role_name = self.cursor.fetchone()['role_name']
        
        session = self.session_manager.get_or_create_session(user_id, role_name)
        state = session.get("sla_comment_state")
        incident_id = session.get("sla_incident_id")
        inc_number = session.get("sla_inc_number")

        if state == "AWAITING_SLA_REMINDER_CONFIRM":
            val = user_input.strip().lower()
            if val in ['yes', 'y']:
                session['sla_comment_state'] = "AWAITING_SLA_COMMENT_TEXT"
                session['sla_retries'] = 0
                self.session_manager._save_sessions()
                
                msg = f"Please enter your comment for the SLA for incident {inc_number}."
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            elif val in ['no', 'n']:
                session['sla_comment_state'] = None
                self.session_manager._save_sessions()
                
                msg = "Got it. No comments added. How else can I assist you?"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            else:
                retries = session.get('sla_retries', 0) + 1
                if retries >= 3:
                    session['sla_comment_state'] = None
                    self.session_manager._save_sessions()
                    return "Action cancelled due to invalid responses.", [], ""
                
                session['sla_retries'] = retries
                self.session_manager._save_sessions()
                return "I could not understand that. Please answer in 'Yes' or 'No'. Would you like to add some comments to stop the SLA?", [], ""

        elif state == "AWAITING_SLA_COMMENT_TEXT":
            session['sla_comment_text'] = user_input
            session['sla_retries'] = 0
            
            # --- DYNAMICALLY DETERMINE IF THIS IS RESPONSE OR RESOLUTION ---
            check_q = """
                SELECT si.status, sd.sla_type 
                FROM sla_instance si 
                JOIN sla_definition sd ON si.sla_def_id = sd.sla_definition_id 
                WHERE si.incident_id = %s AND sd.sla_type = 'response'
            """
            self.cursor.execute(check_q, (incident_id,))
            resp_sla = self.cursor.fetchone()
            
            if resp_sla and resp_sla['status'] == 'running':
                # It's a Response SLA. Ask for visibility.
                session['sla_comment_state'] = "AWAITING_SLA_VISIBILITY"
                self.session_manager._save_sessions()
                
                msg = "Do you want to make this internal or external? (Type 'Internal' or 'External')"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            else:
                # --- RESOLUTION SLA BRANCH ---
                res_q = """
                    SELECT si.sla_instance_id, si.due_time 
                    FROM sla_instance si 
                    JOIN sla_definition sd ON si.sla_def_id = sd.sla_definition_id 
                    WHERE si.incident_id = %s AND sd.sla_type = 'resolution' AND si.status = 'running'
                """
                self.cursor.execute(res_q, (incident_id,))
                res_sla = self.cursor.fetchone()
                
                if not res_sla:
                    session['sla_comment_state'] = None
                    self.session_manager._save_sessions()
                    return "Action out of bounds. No running SLA (Response or Resolution) found for this incident.", [], ""
                    
                sla_id = res_sla['sla_instance_id']
                due_time = res_sla['due_time']
                
                if datetime.now() > due_time:
                    u_sla = "UPDATE sla_instance SET breached_flag = 1, breached_at = NOW(), status = 'breached', last_updated_at = NOW(), updated_at = NOW() WHERE sla_instance_id = %s"
                    sla_msg = "\n(Note: The Resolution SLA was breached.)"
                else:
                    u_sla = "UPDATE sla_instance SET stop_time = NOW(), status = 'completed', last_updated_at = NOW(), updated_at = NOW() WHERE sla_instance_id = %s"
                    sla_msg = "\n(Note: The Resolution SLA was completed on time.)"
                    
                self.cursor.execute(u_sla, (sla_id,))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                
                if self.UPDATE_INCIDENT_COMMENT_ON_RESOLUTION:
                    i_comm = "INSERT INTO incident_comment (incident_id, user_id, comment_text, is_internal, created_at, updated_at) VALUES (%s, %s, %s, 0, NOW(), NOW())"
                    self.cursor.execute(i_comm, (incident_id, user_id, user_input))
                    executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                    self.session_manager.log_db_modification(user_id, "incident_comment", "INSERTED NEW RESOLUTION ROW INTO")
                
                # --- FETCH PREVIOUS STATE BEFORE UPDATE ---
                self.cursor.execute("SELECT state_id FROM incident WHERE incident_id = %s", (incident_id,))
                from_state_id = self.cursor.fetchone()['state_id']
                
                u_inc = "UPDATE incident SET state_id = 5, resolution_summary = %s, resolved_at = NOW(), updated_at = NOW(), updated_by = %s WHERE incident_id = %s"
                self.cursor.execute(u_inc, (user_input, user_id, incident_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                self.session_manager.log_db_modification(user_id, "incident", f"RESOLVED INCIDENT {inc_number} IN")

                # --- TRACK STATE HISTORY (RESOLUTION -> STATE 5) ---
                hist_q = """
                    INSERT INTO incident_state_history 
                    (incident_id, from_state_id, to_state_id, changed_by, changed_at, created_at, updated_at) 
                    VALUES (%s, %s, 5, %s, NOW(), NOW(), NOW())
                """
                self.cursor.execute(hist_q, (incident_id, from_state_id, user_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                self.session_manager.log_db_modification(user_id, "incident_state_history", "INSERTED STATE CHANGE TO RESOLVED INTO")

                # --- NEW: Trigger Close Validation Workflow ---
                session['sla_comment_state'] = None
                session['close_state'] = 'AWAITING_CLOSE_CONFIRM'
                session['close_incident_id'] = incident_id
                session['close_inc_number'] = inc_number
                session['close_retries'] = 0
                self.session_manager._save_sessions()
                
                msg = f"Resolution comment added successfully and incident {inc_number} marked as Resolved (State 5).{sla_msg}\n\nWould you like to close the incident now? (Yes/No)"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], "\n".join(executed_queries)

        elif state == "AWAITING_SLA_VISIBILITY":
            val = user_input.strip().lower()
            if val in ['internal', 'external']:
                is_internal = 1 if val == 'internal' else 0
                comment_text = session.get('sla_comment_text', '')
                
                # 1. Insert into incident_comment
                insert_comm = """
                    INSERT INTO incident_comment 
                    (incident_id, user_id, comment_text, is_internal, created_at, updated_at) 
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                """
                self.cursor.execute(insert_comm, (incident_id, user_id, comment_text, is_internal))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                
                self.session_manager.log_db_modification(user_id, "incident_comment", "INSERTED NEW RESPONSE ROW INTO")

                # 2. Update SLA Response
                sla_query = """
                    SELECT si.sla_instance_id, si.due_time
                    FROM sla_instance si
                    JOIN sla_definition sd ON si.sla_def_id = sd.sla_definition_id
                    WHERE si.incident_id = %s AND sd.sla_type = 'response' AND si.status = 'running'
                """
                self.cursor.execute(sla_query, (incident_id,))
                sla_record = self.cursor.fetchone()
                
                sla_status_msg = ""
                if sla_record:
                    sla_id = sla_record['sla_instance_id']
                    due_time = sla_record['due_time']
                    
                    if datetime.now() > due_time:
                        update_sla = """
                            UPDATE sla_instance
                            SET breached_flag = 1, breached_at = NOW(), status = 'breached', last_updated_at = NOW(), updated_at = NOW()
                            WHERE sla_instance_id = %s
                        """
                        sla_status_msg = "\n(Note: The Response SLA was breached.)"
                    else:
                        update_sla = """
                            UPDATE sla_instance
                            SET stop_time = NOW(), status = 'completed', last_updated_at = NOW(), updated_at = NOW()
                            WHERE sla_instance_id = %s
                        """
                        sla_status_msg = "\n(Note: The Response SLA was completed on time.)"
                        
                    self.cursor.execute(update_sla, (sla_id,))
                    executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                    self.session_manager.log_db_modification(user_id, "sla_instance", "UPDATED RESPONSE ROW IN")
                
                # --- FETCH PREVIOUS STATE BEFORE UPDATE ---
                self.cursor.execute("SELECT state_id FROM incident WHERE incident_id = %s", (incident_id,))
                from_state_id = self.cursor.fetchone()['state_id']
                
                # 3. Update Incident State to 3 (In Progress)
                update_inc = "UPDATE incident SET state_id = 3, updated_at = NOW(), updated_by = %s WHERE incident_id = %s"
                self.cursor.execute(update_inc, (user_id, incident_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                self.session_manager.log_db_modification(user_id, "incident", f"MARKED INCIDENT {inc_number} IN PROGRESS IN")

                # --- TRACK STATE HISTORY (RESPONSE -> STATE 3) ---
                hist_q = """
                    INSERT INTO incident_state_history 
                    (incident_id, from_state_id, to_state_id, changed_by, changed_at, created_at, updated_at) 
                    VALUES (%s, %s, 3, %s, NOW(), NOW(), NOW())
                """
                self.cursor.execute(hist_q, (incident_id, from_state_id, user_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                self.session_manager.log_db_modification(user_id, "incident_state_history", "INSERTED STATE CHANGE TO IN PROGRESS INTO")

                session['sla_comment_state'] = None
                self.session_manager._save_sessions()

                msg = f"Response comment added successfully and incident {inc_number} moved to In Progress (State 3).{sla_status_msg}"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], "\n".join(executed_queries)
                
            else:
                retries = session.get('sla_retries', 0) + 1
                if retries >= 3:
                    session['sla_comment_state'] = None
                    self.session_manager._save_sessions()
                    return "Action cancelled due to invalid responses.", [], ""
                session['sla_retries'] = retries
                self.session_manager._save_sessions()
                return "I could not understand that. Please answer in 'Internal' or 'External'.", [], ""