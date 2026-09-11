import json
from datetime import datetime

class CloseIncidentController:
    def __init__(self, db_cursor, ai_client, db_connection, session_manager, spicy=False):
        self.cursor = db_cursor
        self.ai_client = ai_client
        self.db_connection = db_connection
        self.session_manager = session_manager
        self.spicy = spicy

    def get_active_closure_codes(self):
        self.cursor.execute("SELECT closure_code_id, code_name FROM closure_code_master WHERE is_active = 1")
        return self.cursor.fetchall()

    def handle_close_intent(self, user_input, user_id):
        executed_queries = []
        
        prompt = (
            "You are an ITSM AI assistant. The agent wants to close a resolved incident.\n"
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
            return "Please explicitly specify the incident number you wish to close.", [], ""

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

        if not target or target['assigned_user_id'] != user_id:
            return f"Action out of bounds. No incident {inc_id_str} found (or it is not assigned to you).", [], "\n".join(executed_queries)

        if target['state_id'] == 6:
            return f"Incident {target['incident_number']} is already closed.", [], "\n".join(executed_queries)
        elif target['state_id'] != 5:
            return f"Incident {target['incident_number']} must be 'Resolved' (State 5) before it can be closed. Currently in State {target['state_id']}.", [], "\n".join(executed_queries)

        incident_id = target['incident_id']
        inc_number = target['incident_number']

        codes = self.get_active_closure_codes()
        code_text = "\n".join([f"{c['closure_code_id']} - {c['code_name']}" for c in codes])

        if hasattr(self.session_manager, 'get_or_create_session'):
            session = self.session_manager.get_or_create_session(user_id, "Agent")
            session['close_state'] = 'AWAITING_CLOSURE_CODE'
            session['close_incident_id'] = incident_id
            session['close_inc_number'] = inc_number
            session['close_retries'] = 0
            self.session_manager._save_sessions()
            
            msg = f"To close incident {inc_number}, please provide the ID for the closure code:\n{code_text}"
            chat_history = session.get("chat_history", [])
            chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
            self.session_manager.save_history(user_id, chat_history)
            
            return msg, [], "\n".join(executed_queries)

    def process_step(self, user_input, user_id):
        executed_queries = []
        
        self.cursor.execute("SELECT r.role_name FROM user_role ur JOIN role r ON ur.role_id = r.role_id WHERE ur.user_id = %s", (user_id,))
        role_name = self.cursor.fetchone()['role_name']
        
        session = self.session_manager.get_or_create_session(user_id, role_name)
        state = session.get("close_state")
        incident_id = session.get("close_incident_id")
        inc_number = session.get("close_inc_number")

        # --- Triggered dynamically after SLA Resolution ---
        if state == "AWAITING_CLOSE_CONFIRM":
            val = user_input.strip().lower()
            if val in ['yes', 'y']:
                codes = self.get_active_closure_codes()
                code_text = "\n".join([f"{c['closure_code_id']} - {c['code_name']}" for c in codes])
                
                session['close_state'] = "AWAITING_CLOSURE_CODE"
                session['close_retries'] = 0
                self.session_manager._save_sessions()
                
                msg = f"Please provide the ID for the closure code:\n{code_text}"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            elif val in ['no', 'n']:
                session['close_state'] = None
                self.session_manager._save_sessions()
                
                msg = f"Got it. Incident {inc_number} will remain Resolved. How else can I assist you?"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            else:
                retries = session.get('close_retries', 0) + 1
                if retries >= 2: # Max ask 2 times
                    session['close_state'] = None
                    self.session_manager._save_sessions()
                    return "Action cancelled due to invalid responses. Incident not closed.", [], ""
                
                session['close_retries'] = retries
                self.session_manager._save_sessions()
                return "I could not understand that. Please answer 'Yes' or 'No'. Would you like to close the incident?", [], ""

        elif state == "AWAITING_CLOSURE_CODE":
            val = user_input.strip()
            codes = self.get_active_closure_codes()
            valid_ids = [str(c['closure_code_id']) for c in codes]
            
            if val in valid_ids:
                session['closure_code_id'] = int(val)
                session['close_state'] = "AWAITING_CLOSE_NOTES"
                session['close_retries'] = 0
                self.session_manager._save_sessions()
                
                msg = "Please enter your closing notes. If you want to keep it blank, please write 'None'."
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            else:
                retries = session.get('close_retries', 0) + 1
                if retries >= 2:
                    session['close_state'] = None
                    self.session_manager._save_sessions()
                    return "Action cancelled due to invalid responses. Incident not closed.", [], ""
                
                session['close_retries'] = retries
                self.session_manager._save_sessions()
                return "Please enter a valid response from the displayed ID options.", [], ""

        elif state == "AWAITING_CLOSE_NOTES":
            val = user_input.strip()
            if val.lower() in ['none', '', 'nothing']:
                close_notes = None
            else:
                close_notes = val
                
            closure_code_id = session.get('closure_code_id')

            # 1. Update Incident Table (State 6)
            update_inc = """
                UPDATE incident 
                SET state_id = 6, closure_code_id = %s, close_notes = %s, closed_at = NOW(), updated_at = NOW(), updated_by = %s 
                WHERE incident_id = %s
            """
            self.cursor.execute(update_inc, (closure_code_id, close_notes, user_id, incident_id))
            executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
            
            # 2. Track State History (5 -> 6)
            hist_q = """
                INSERT INTO incident_state_history 
                (incident_id, from_state_id, to_state_id, changed_by, closure_code_id, changed_at, created_at, updated_at) 
                VALUES (%s, 5, 6, %s, %s, NOW(), NOW(), NOW())
            """
            self.cursor.execute(hist_q, (incident_id, user_id, closure_code_id))
            executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
            
            self.db_connection.commit()
            self.session_manager.log_db_modification(user_id, "incident", f"CLOSED INCIDENT {inc_number} IN")
            self.session_manager.log_db_modification(user_id, "incident_state_history", "INSERTED STATE CHANGE TO CLOSED INTO")

            print("\n--- MODIFIED COLUMNS IN INCIDENT TABLE ---")
            print(f"{'incident_id':<12} | {'state_id':<10} | {'closure_code_id':<18} | {'close_notes'}")
            print(f"{incident_id:<12} | {6:<10} | {closure_code_id:<18} | {str(close_notes)}")
            print("------------------------------------------\n")

            # Clean Up State
            session['close_state'] = None
            session['close_incident_id'] = None
            session['close_inc_number'] = None
            session['closure_code_id'] = None
            session['close_retries'] = 0
            self.session_manager._save_sessions()

            msg = f"Success. Incident {inc_number} has been officially Closed (State 6)."
            chat_history = session.get("chat_history", [])
            chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
            self.session_manager.save_history(user_id, chat_history)
            
            return msg, [], "\n".join(executed_queries)