# modules/op_resumeHold.py
import json
from datetime import datetime

class ResumeHoldController:
    def __init__(self, db_cursor, ai_client, db_connection, session_manager, spicy=False):
        self.cursor = db_cursor
        self.ai_client = ai_client
        self.db_connection = db_connection
        self.session_manager = session_manager
        self.spicy = spicy

    def handle_resume_intent(self, user_input, user_id):
        executed_queries = []
        
        prompt = (
            "You are an ITSM AI assistant. The agent wants to resume an incident or end its hold.\n"
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
            return "Please explicitly specify the incident number you wish to resume.", [], ""

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

        # --- 1. Validate Ownership and State ---
        if not target or target['assigned_user_id'] != user_id:
            return f"Action out of bounds. No incident {inc_id_str} found (or it is not assigned to you).", [], "\n".join(executed_queries)

        if target['state_id'] != 4:
            return f"Incident {target['incident_number']} is not currently on hold.", [], "\n".join(executed_queries)

        incident_id = target['incident_id']
        inc_number = target['incident_number']
        from_state_id = target['state_id'] # This is 4 (Hold)

        # --- 2. Check for Illegal Response SLA Pauses ---
        check_resp_sla = """
            SELECT si.status 
            FROM sla_instance si 
            JOIN sla_definition sd ON si.sla_def_id = sd.sla_definition_id 
            WHERE si.incident_id = %s AND sd.sla_type = 'response'
        """
        self.cursor.execute(check_resp_sla, (incident_id,))
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
        resp_slas = self.cursor.fetchall()
        
        if any(s['status'] == 'paused' for s in resp_slas):
            return "Action unknown, please contact admin. (Response SLA is illegally paused).", [], "\n".join(executed_queries)

        # --- 3. Update Incident Hold Event (Calculate Minutes) ---
        get_hold_event = """
            SELECT hold_event_id, TIMESTAMPDIFF(MINUTE, hold_start_at, NOW()) as mins 
            FROM incident_hold_event 
            WHERE incident_id = %s AND hold_end_at IS NULL 
            ORDER BY hold_start_at DESC LIMIT 1
        """
        self.cursor.execute(get_hold_event, (incident_id,))
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
        hold_event = self.cursor.fetchone()
        
        if not hold_event:
            return f"Error: Could not find an active hold tracking event for {inc_number}.", [], "\n".join(executed_queries)
            
        mins = hold_event['mins'] or 0
        hold_event_id = hold_event['hold_event_id']

        close_hold_event = """
            UPDATE incident_hold_event 
            SET hold_end_at = NOW(), hold_duration_minutes = %s, updated_at = NOW() 
            WHERE hold_event_id = %s
        """
        self.cursor.execute(close_hold_event, (mins, hold_event_id))
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))

        # --- 4. Update SLA Pause Events ---
        close_sla_pause = """
            UPDATE sla_pause_event spe
            JOIN sla_instance si ON spe.sla_instance_id = si.sla_instance_id
            SET spe.pause_end_at = NOW()
            WHERE si.incident_id = %s AND spe.pause_end_at IS NULL
        """
        self.cursor.execute(close_sla_pause, (incident_id,))
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))

        # --- 5. Update SLA Instance (Add duration to due_time) ---
        update_sla = """
            UPDATE sla_instance si
            JOIN sla_definition sd ON si.sla_def_id = sd.sla_definition_id
            SET si.due_time = si.due_time + INTERVAL %s MINUTE, 
                si.status = 'running', 
                si.last_updated_at = NOW(), 
                si.updated_at = NOW()
            WHERE si.incident_id = %s AND sd.sla_type = 'resolution' AND si.status = 'paused'
        """
        self.cursor.execute(update_sla, (mins, incident_id))
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))

        # --- 6. Update Incident State ---
        update_inc = """
            UPDATE incident 
            SET state_id = 3, hold_reason_id = NULL, updated_by = %s, updated_at = NOW() 
            WHERE incident_id = %s
        """
        self.cursor.execute(update_inc, (user_id, incident_id))
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
        self.db_connection.commit()
        
        # --- 7. TRACK STATE HISTORY (RESUME -> STATE 3) ---
        hist_q = """
            INSERT INTO incident_state_history 
            (incident_id, from_state_id, to_state_id, changed_by, changed_at, created_at, updated_at) 
            VALUES (%s, %s, 3, %s, NOW(), NOW(), NOW())
        """
        self.cursor.execute(hist_q, (incident_id, from_state_id, user_id))
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
        self.db_connection.commit()
        self.session_manager.log_db_modification(user_id, "incident_state_history", "INSERTED STATE CHANGE TO IN PROGRESS INTO")

        # Logging
        self.session_manager.log_db_modification(user_id, "incident", f"RESUMED HOLD ON INCIDENT {inc_number} IN")
        self.session_manager.log_db_modification(user_id, "sla_instance", f"EXTENDED DUE_TIME BY {mins} MINS IN")

        # --- TERMINAL OUTPUT ---
        print("\n--- MODIFIED COLUMNS IN INCIDENT TABLE ---")
        print(f"{'incident_id':<12} | {'state_id':<10} | {'hold_reason_id':<15} | {'updated_by'}")
        print(f"{incident_id:<12} | {3:<10} | {'NULL':<15} | {user_id}")
        print("------------------------------------------\n")
        
        print("\n--- MODIFIED COLUMNS IN INCIDENT_HOLD_EVENT TABLE ---")
        print(f"{'hold_event_id':<14} | {'hold_duration_minutes':<22} | {'hold_end_at'}")
        print(f"{hold_event_id:<14} | {mins:<22} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-----------------------------------------------------\n")

        msg = f"Success. Incident {inc_number} has been resumed and is back In Progress (State 3). The hold duration of {mins} minute(s) has been safely added to the Resolution SLA due time."
        
        if hasattr(self.session_manager, 'get_or_create_session'):
            session = self.session_manager.get_or_create_session(user_id, "Agent")
            chat_history = session.get("chat_history", [])
            chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
            self.session_manager.save_history(user_id, chat_history)

        return msg, [], "\n".join(executed_queries)