import json
from datetime import datetime

class AssignIncidentController:
    def __init__(self, db_cursor, ai_client, db_connection, session_manager, spicy=False):
        self.cursor = db_cursor
        self.ai_client = ai_client
        self.db_connection = db_connection
        self.session_manager = session_manager
        self.spicy = spicy

    def get_agent_details(self, user_id):
        # Fetch the agent's company_id
        self.cursor.execute("SELECT company_id FROM user WHERE user_id = %s", (user_id,))
        res = self.cursor.fetchone()
        company_id = res['company_id'] if res else None

        # Fetch the agent's group_member
        self.cursor.execute("SELECT group_id FROM group_member WHERE user_id = %s", (user_id,))
        groups = self.cursor.fetchall()
        group_ids = [g['group_id'] for g in groups] if groups else []

        return company_id, group_ids
    
    def process_step(self, user_input, user_id):
        """State machine handling multi-turn incident commenting and SLA updating."""
        executed_queries = []
        
        self.cursor.execute("SELECT r.role_name FROM user_role ur JOIN role r ON ur.role_id = r.role_id WHERE ur.user_id = %s", (user_id,))
        role_res = self.cursor.fetchone()
        role_name = role_res['role_name'] if role_res else 'Agent'
        
        session = self.session_manager.get_or_create_session(user_id, role_name)
        state = session.get("assign_state")
        incident_id = session.get("assign_incident_id")
        inc_number = session.get("assign_inc_number")

        if state == "AWAITING_COMMENT_CONFIRM":
            val = user_input.strip().lower()
            if val in ['yes', 'y']:
                session['assign_state'] = "AWAITING_COMMENT_TEXT"
                session['assign_retries'] = 0
                self.session_manager._save_sessions()
                
                msg = "Please comment your response."
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            elif val in ['no', 'n']:
                session['assign_state'] = None
                self.session_manager._save_sessions()
                
                msg = "Got it. No comments added. How else can I assist you?"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
                
            else:
                retries = session.get('assign_retries', 0) + 1
                if retries >= 3:
                    session['assign_state'] = None
                    self.session_manager._save_sessions()
                    return "Action cancelled due to invalid responses. Comment addition unsuccessful.", [], ""
                
                session['assign_retries'] = retries
                self.session_manager._save_sessions()
                return "I could not understand that. Please answer in 'Yes' or 'No'. Do you want to add some comments as a response to it?", [], ""

        elif state == "AWAITING_COMMENT_TEXT":
            session['assign_comment'] = user_input
            session['assign_state'] = "AWAITING_VISIBILITY"
            session['assign_retries'] = 0
            self.session_manager._save_sessions()
            
            msg = "Do you want to make this internal or external? (Type 'Internal' or 'External')"
            chat_history = session.get("chat_history", [])
            chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
            self.session_manager.save_history(user_id, chat_history)
            return msg, [], ""

        elif state == "AWAITING_VISIBILITY":
            val = user_input.strip().lower()
            if val in ['internal', 'external']:
                is_internal = 1 if val == 'internal' else 0
                comment_text = session.get('assign_comment', '')
                
                # 1. Insert into incident_comment
                insert_comm = """
                    INSERT INTO incident_comment 
                    (incident_id, user_id, comment_text, is_internal, created_at, updated_at) 
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                """
                self.cursor.execute(insert_comm, (incident_id, user_id, comment_text, is_internal))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                comment_id = self.cursor.lastrowid
                
                self.session_manager.log_db_modification(user_id, "incident_comment", "INSERTED NEW ROW INTO")

                # 2. Update SLA Response
                sla_query = """
                    SELECT si.sla_instance_id, si.due_time
                    FROM sla_instance si
                    JOIN sla_definition sd ON si.sla_def_id = sd.sla_definition_id
                    WHERE si.incident_id = %s AND sd.sla_type = 'response' AND si.status = 'running'
                """
                self.cursor.execute(sla_query, (incident_id,))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
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
                    self.db_connection.commit()
                    self.session_manager.log_db_modification(user_id, "sla_instance", "UPDATED ROW IN")

                # --- 3. FETCH PREVIOUS STATE BEFORE UPDATE ---
                self.cursor.execute("SELECT state_id FROM incident WHERE incident_id = %s", (incident_id,))
                from_state_id = self.cursor.fetchone()['state_id']

                # --- 4. Update Incident State to 3 (In Progress) ---
                update_inc = "UPDATE incident SET state_id = 3, updated_at = NOW(), updated_by = %s WHERE incident_id = %s"
                self.cursor.execute(update_inc, (user_id, incident_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                self.session_manager.log_db_modification(user_id, "incident", f"MARKED INCIDENT {inc_number} IN PROGRESS IN")

                # --- 5. TRACK STATE HISTORY (1 -> 3) ---
                hist_q = """
                    INSERT INTO incident_state_history 
                    (incident_id, from_state_id, to_state_id, changed_by, changed_at, created_at, updated_at) 
                    VALUES (%s, %s, 3, %s, NOW(), NOW(), NOW())
                """
                self.cursor.execute(hist_q, (incident_id, from_state_id, user_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()
                self.session_manager.log_db_modification(user_id, "incident_state_history", "INSERTED STATE CHANGE TO IN PROGRESS INTO")

                print("\n--- MODIFIED COLUMNS IN INCIDENT_COMMENT TABLE ---")
                print(f"{'comment_id':<12} | {'incident_id':<12} | {'user_id':<10} | {'is_internal':<12} | {'comment_text'}")
                print(f"{comment_id:<12} | {incident_id:<12} | {user_id:<10} | {is_internal:<12} | {comment_text}")
                print("--------------------------------------------------\n")

                if sla_record:
                    print(f"--- SLA INSTANCE {sla_id} UPDATED: {sla_status_msg.strip()} ---\n")

                # Clean Up State
                session['assign_state'] = None
                session['assign_incident_id'] = None
                session['assign_inc_number'] = None
                session['assign_comment'] = None
                session['assign_retries'] = 0
                self.session_manager._save_sessions()

                msg = f"Comment added successfully and incident moved to In Progress (State 3).{sla_status_msg}"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], "\n".join(executed_queries)
                
            else:
                retries = session.get('assign_retries', 0) + 1
                if retries >= 3:
                    session['assign_state'] = None
                    self.session_manager._save_sessions()
                    return "Action cancelled due to invalid responses. Comment addition unsuccessful.", [], ""
                session['assign_retries'] = retries
                self.session_manager._save_sessions()
                return "I could not understand that. Please answer in 'Internal' or 'External'.", [], ""


    def handle_assign(self, user_input, user_id):
        company_id, group_ids = self.get_agent_details(user_id)
        executed_queries = []
        
        # --- Explicit handling for Agents without groups ---
        if not group_ids:
            return "You are not assigned to any group, please ask your admin to assign you to a group.", [], ""
        
        if not company_id:
            return "Assignment failed: You are not associated with any company.", [], ""

        # 1. Use AI to parse out which incident the agent wants to assign to themselves. Strict Check for incidents within the agent's company to prevent cross-company assignments.
        prompt = (
            "You are an ITSM AI assistant. The agent wants to assign an incident to themselves.\n"
            f"Agent input: '{user_input}'\n"
            "Extract the incident number (e.g., 'INC00030') or ID (e.g., 30) from the text.\n"
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
            return "Please explicitly specify the incident number you wish to assign.", [], ""

        inc_id_str = str(inc_id_raw).strip()
        
        # 2. Check if incident exists strictly within the Agent's company
        if inc_id_str.isdigit():
            query = """
                SELECT i.incident_id, i.incident_number, c.assignment_group_id 
                FROM incident i
                LEFT JOIN category c ON i.category_id = c.category_id
                WHERE i.incident_id = %s AND i.company_id = %s
            """
            params = (int(inc_id_str), company_id)
        else:
            inc_id_str = inc_id_str.upper() if inc_id_str.upper().startswith("INC") else f"INC{inc_id_str.zfill(5)}"
            query = """
                SELECT i.incident_id, i.incident_number, c.assignment_group_id 
                FROM incident i
                LEFT JOIN category c ON i.category_id = c.category_id
                WHERE i.incident_number = %s AND i.company_id = %s
            """
            params = (inc_id_str, company_id)

        self.cursor.execute(query, params)
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
        target = self.cursor.fetchone()

        if not target:
            return "Incident not found, or it is outside of your company scope.", [], "\n".join(executed_queries)

        # 3. Check if the incident's category assignment group matches any of the agent's groups
        incident_id = target['incident_id']
        inc_number = target['incident_number']
        target_group_id = group_ids[0]

        # 4. Process the Database Update        Also, State ID is deliberately left as 1 (New) instead of 2 (Assigned) ---
        update_query = """
            UPDATE incident 
            SET assigned_group_id = %s, 
                assigned_user_id = %s, 
                updated_by = %s, 
                updated_at = NOW()
            WHERE incident_id = %s
        """
        self.cursor.execute(update_query, (target_group_id, user_id, user_id, incident_id))
        executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
        self.db_connection.commit()

        self.session_manager.log_db_modification(user_id, "incident", f"ASSIGNED THEMSELVES TO {inc_number} IN")

        # --- TERMINAL OUTPUT REQUIREMENT ---
        print("\n--- MODIFIED COLUMNS IN INCIDENT TABLE ---")
        print(f"{'incident_id':<12} | {'incident_number':<15} | {'assigned_group_id':<18} | {'assigned_user_id':<16} | {'updated_by':<10}")
        print(f"{incident_id:<12} | {inc_number:<15} | {target_group_id:<18} | {user_id:<16} | {user_id:<10}")
        print("------------------------------------------\n")

        self.cursor.execute("SELECT * FROM incident WHERE incident_id = %s", (incident_id,))
        results = self.cursor.fetchall()

        final_text = f"Success! {inc_number} has been assigned to you under Group ID {target_group_id}.\nDo you want to add some comments as a response to it? (Yes/No)"

        # Save updated thread context to memory
        if hasattr(self.session_manager, 'get_or_create_session'):
            session = self.session_manager.get_or_create_session(user_id, "Agent")
            
            # --- START MULTI-TURN STATE MACHINE ---
            session['assign_state'] = 'AWAITING_COMMENT_CONFIRM'
            session['assign_incident_id'] = incident_id
            session['assign_inc_number'] = inc_number
            session['assign_retries'] = 0
            self.session_manager._save_sessions()
            
            chat_history = session.get("chat_history", [])
            chat_history.append({"role": "user", "content": user_input})
            chat_history.append({"role": "assistant", "content": final_text})
            if len(chat_history) > 6:
                chat_history = chat_history[-6:]
            self.session_manager.save_history(user_id, chat_history)
            
        query_log = "\n".join(executed_queries)
        return final_text, results, query_log