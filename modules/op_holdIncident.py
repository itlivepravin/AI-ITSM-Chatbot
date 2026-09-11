import json
from datetime import datetime

class HoldIncidentController:
    def __init__(self, db_cursor, ai_client, db_connection, session_manager, spicy=False):
        self.cursor = db_cursor
        self.ai_client = ai_client
        self.db_connection = db_connection
        self.session_manager = session_manager
        self.spicy = spicy

    def get_active_hold_reasons(self):
        """Fetches active hold reasons from the DB to dynamically present to the user."""
        self.cursor.execute("SELECT hold_reason_id, reason_name FROM on_hold_reason WHERE is_active = 1")
        return self.cursor.fetchall()

    def handle_hold_intent(self, user_input, user_id):
        executed_queries = []
        
        prompt = (
            "You are an ITSM AI assistant. The agent wants to put an incident on hold.\n"
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
            return "Please explicitly specify the incident number you wish to put on hold.", [], ""

        inc_id_str = str(inc_id_raw).strip()
        
        if inc_id_str.isdigit():
            query = "SELECT incident_id, incident_number, assigned_user_id, state_id FROM incident WHERE incident_id = %s"
            params = (int(inc_id_str),)
        else:
            inc_id_str = inc_id_str.upper() if inc_id_str.upper().startswith("INC") else f"INC{inc_id_str.zfill(5)}"
            query = "SELECT incident_id, incident_number, assigned_user_id, state_id FROM incident WHERE incident_number = %s"
            params = (inc_id_str,)

        self.cursor.execute(query, params)
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
        target = self.cursor.fetchone()

        # --- 1. Enforce Ownership Validation ---
        if not target or target['assigned_user_id'] != user_id:
            return f"Action out of bounds. No incident {inc_id_str} found (or it is not assigned to you).", [], "\n".join(executed_queries)

        # --- 2. Enforce Strict State Logic (MUST be State 3) ---
        if target['state_id'] == 5:
            return f"Incident {target['incident_number']} is already resolved and cannot be put on hold.", [], "\n".join(executed_queries)
        elif target['state_id'] > 5:
            return f"Incident {target['incident_number']} is closed/completed and cannot be put on hold.", [], "\n".join(executed_queries)
        elif target['state_id'] == 4:
            return f"Incident {target['incident_number']} is already on hold.", [], "\n".join(executed_queries)
        elif target['state_id'] != 3:
            return f"Incident {target['incident_number']} must be 'In Progress' (State 3) before it can be put on hold. Please respond to the incident first to stop the Response SLA.", [], "\n".join(executed_queries)

        incident_id = target['incident_id']
        inc_number = target['incident_number']

        reasons = self.get_active_hold_reasons()
        reason_text = "\n".join([f"{r['hold_reason_id']} - {r['reason_name']}" for r in reasons])

        if hasattr(self.session_manager, 'get_or_create_session'):
            session = self.session_manager.get_or_create_session(user_id, "Agent")
            session['hold_state'] = 'AWAITING_HOLD_REASON'
            session['hold_incident_id'] = incident_id
            session['hold_inc_number'] = inc_number
            session['hold_retries'] = 0
            self.session_manager._save_sessions()
            
            msg = f"To put incident {inc_number} on hold, please provide the ID for the hold reason:\n{reason_text}"
            chat_history = session.get("chat_history", [])
            chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
            self.session_manager.save_history(user_id, chat_history)
            
            return msg, [], "\n".join(executed_queries)

    def process_step(self, user_input, user_id):
        executed_queries = []
        
        self.cursor.execute("SELECT r.role_name FROM user_role ur JOIN role r ON ur.role_id = r.role_id WHERE ur.user_id = %s", (user_id,))
        role_name = self.cursor.fetchone()['role_name']
        
        session = self.session_manager.get_or_create_session(user_id, role_name)
        state = session.get("hold_state")
        incident_id = session.get("hold_incident_id")
        inc_number = session.get("hold_inc_number")

        if state == "AWAITING_HOLD_REASON":
            val = user_input.strip()
            reasons = self.get_active_hold_reasons()
            valid_ids = [str(r['hold_reason_id']) for r in reasons]
            
            if val in valid_ids:
                session['hold_reason_id'] = int(val)
                session['hold_state'] = "AWAITING_HOLD_COMMENT"
                session['hold_retries'] = 0
                self.session_manager._save_sessions()
                
                msg = "Please enter a comment explaining why this incident is being put on hold."
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            else:
                retries = session.get('hold_retries', 0) + 1
                if retries >= 3:
                    session['hold_state'] = None
                    self.session_manager._save_sessions()
                    return "Action cancelled due to invalid responses. Please process the hold manually.", [], ""
                
                session['hold_retries'] = retries
                self.session_manager._save_sessions()
                reason_text = "\n".join([f"{r['hold_reason_id']} - {r['reason_name']}" for r in reasons])
                return f"I could not understand that. Please provide a valid value from the displayed options:\n{reason_text}", [], ""

        elif state == "AWAITING_HOLD_COMMENT":
            session['hold_comment_text'] = user_input
            session['hold_state'] = "AWAITING_HOLD_VISIBILITY"
            session['hold_retries'] = 0
            self.session_manager._save_sessions()
            
            msg = "Do you want to make this internal or external? (Type 'Internal' or 'External')"
            chat_history = session.get("chat_history", [])
            chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
            self.session_manager.save_history(user_id, chat_history)
            return msg, [], ""

        elif state == "AWAITING_HOLD_VISIBILITY":
            val = user_input.strip().lower()
            if val in ['internal', 'external']:
                is_internal = 1 if val == 'internal' else 0
                comment_text = session.get('hold_comment_text', '')
                hold_reason_id = session.get('hold_reason_id')
                
                # Fetch Company ID and Previous State
                self.cursor.execute("SELECT company_id, state_id FROM incident WHERE incident_id = %s", (incident_id,))
                res = self.cursor.fetchone()
                company_id = res['company_id']
                from_state_id = res['state_id']
                
                # --- 1. Insert into incident_comment ---
                insert_comm = """
                    INSERT INTO incident_comment 
                    (incident_id, user_id, comment_text, is_internal, created_at, updated_at) 
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                """
                self.cursor.execute(insert_comm, (incident_id, user_id, comment_text, is_internal))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.session_manager.log_db_modification(user_id, "incident_comment", "INSERTED HOLD COMMENT INTO")

                # --- 2. Insert into incident_hold_event ---
                insert_hold_event = """
                    INSERT INTO incident_hold_event 
                    (incident_id, company_id, hold_reason_id, hold_start_at, held_by, created_at, updated_at) 
                    VALUES (%s, %s, %s, NOW(), %s, NOW(), NOW())
                """
                self.cursor.execute(insert_hold_event, (incident_id, company_id, hold_reason_id, user_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.session_manager.log_db_modification(user_id, "incident_hold_event", "INSERTED NEW EVENT INTO")

                # --- 3. Update Running SLAs & Create Pause Events ---
                self.cursor.execute("SELECT sla_instance_id FROM sla_instance WHERE incident_id = %s AND status = 'running'", (incident_id,))
                running_slas = self.cursor.fetchall()
                paused_count = 0

                for sla in running_slas:
                    sla_id = sla['sla_instance_id']

                    # Update SLA Instance status to paused    
                    update_sla = """
                        UPDATE sla_instance
                        SET status = 'paused', last_updated_at = NOW(), updated_at = NOW()
                        WHERE sla_instance_id = %s
                    """
                    self.cursor.execute(update_sla, (sla_id,))
                    executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                    
                    # Create SLA Pause Event
                    insert_pause_event = """
                        INSERT INTO sla_pause_event 
                        (sla_instance_id, hold_reason_id, pause_start_at, created_by, created_at) 
                        VALUES (%s, %s, NOW(), %s, NOW())
                    """
                    self.cursor.execute(insert_pause_event, (sla_id, hold_reason_id, user_id))
                    executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                    
                    paused_count += 1
                    
                if paused_count > 0:
                    self.session_manager.log_db_modification(user_id, "sla_pause_event", f"INSERTED {paused_count} ROW(S) INTO")

                # --- 4. Update Incident State to 4 (On Hold) ---
                update_inc = """
                    UPDATE incident 
                    SET state_id = 4, hold_reason_id = %s, updated_at = NOW(), updated_by = %s 
                    WHERE incident_id = %s
                """
                self.cursor.execute(update_inc, (hold_reason_id, user_id, incident_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                self.session_manager.log_db_modification(user_id, "incident", f"PUT INCIDENT {inc_number} ON HOLD IN")

                # --- 5. TRACK STATE HISTORY (HOLD -> STATE 4) ---
                hist_q = """
                    INSERT INTO incident_state_history 
                    (incident_id, from_state_id, to_state_id, changed_by, hold_reason_id, changed_at, created_at, updated_at) 
                    VALUES (%s, %s, 4, %s, %s, NOW(), NOW(), NOW())
                """
                self.cursor.execute(hist_q, (incident_id, from_state_id, user_id, hold_reason_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                self.session_manager.log_db_modification(user_id, "incident_state_history", "INSERTED STATE CHANGE TO ON HOLD INTO")


                print("\n--- MODIFIED COLUMNS IN INCIDENT TABLE ---")
                print(f"{'incident_id':<12} | {'state_id':<10} | {'hold_reason_id':<15} | {'updated_by'}")
                print(f"{incident_id:<12} | {4:<10} | {hold_reason_id:<15} | {user_id}")
                print("------------------------------------------\n")
                
                if paused_count > 0:
                    print(f"--- STATUS UPDATED TO 'PAUSED' FOR {paused_count} RUNNING SLAs ---\n")

                # Clean Up State
                session['hold_state'] = None
                session['hold_incident_id'] = None
                session['hold_inc_number'] = None
                session['hold_reason_id'] = None
                session['hold_comment_text'] = None
                session['hold_retries'] = 0
                self.session_manager._save_sessions()

                msg = f"Success. Incident {inc_number} is now On Hold (State 4). {paused_count} running SLA(s) have been paused and logged."
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], "\n".join(executed_queries)
                
            else:
                retries = session.get('hold_retries', 0) + 1
                if retries >= 3:
                    session['hold_state'] = None
                    self.session_manager._save_sessions()
                    return "Action cancelled due to invalid responses. Hold unsuccessful.", [], ""
                session['hold_retries'] = retries
                self.session_manager._save_sessions()
                return "I could not understand that. Please answer in 'Internal' or 'External'.", [], ""