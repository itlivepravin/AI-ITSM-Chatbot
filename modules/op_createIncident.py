import json
from datetime import datetime

class CreateIncidentController:
    def __init__(self, db_cursor, ai_client, db_connection, session_manager, spicy=False):
        self.cursor = db_cursor
        self.ai_client = ai_client
        self.db_connection = db_connection
        self.session_manager = session_manager
        self.spicy = spicy

    def get_company_id(self, user_id):
        self.cursor.execute("SELECT company_id FROM user WHERE user_id = %s", (user_id,))
        res = self.cursor.fetchone()
        return res['company_id'] if res else 1

    def fetch_category_mapping(self, company_id):
        query = """
            SELECT c.category_id, c.category_name, s.subcategory_id, s.subcategory_name
            FROM category c
            LEFT JOIN subcategory s ON c.category_id = s.category_id
            WHERE c.company_id = %s AND c.is_active = 1
        """
        self.cursor.execute(query, (company_id,))
        rows = self.cursor.fetchall()

        mapping = {}
        for r in rows:
            cat_key = f"{r['category_name']} (ID: {r['category_id']})"
            if cat_key not in mapping:
                mapping[cat_key] = []
            if r['subcategory_id']:
                mapping[cat_key].append({
                    "name": r['subcategory_name'],
                    "id": r['subcategory_id']
                })
        return mapping

    def extract_rating(self, user_input):
        """Uses a tiny AI call to safely extract a number from the user's reply."""

        prompt = (
            "Extract the rating number from the user's input.\n"
            f"User input: '{user_input}'\n"
            "Return ONLY a JSON object with the key 'value' containing the integer (e.g. 1, 2, or 3). If no clear number is found, return null."
        )
        try:
            res = self.ai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            data = json.loads(res.choices[0].message.content)
            return data.get('value')
        except:
            return None

    def process_step(self, user_input, user_id):
        """State machine handling multi-turn incident creation."""

        executed_queries = []
        
        self.cursor.execute("SELECT r.role_name, r.role_id FROM user_role ur JOIN role r ON ur.role_id = r.role_id WHERE ur.user_id = %s", (user_id,))
        role_res = self.cursor.fetchone()
        role_name = role_res['role_name'] if role_res else 'Caller'
        role_id = role_res['role_id'] if role_res else 4
        
        # --- RBAC SAFETY BLOCK ---
        if role_id == 3:
            return "Action Out of Bounds. Agents are not permitted to create new incidents.", [], ""

        session = self.session_manager.get_or_create_session(user_id, role_name)
        state = session.get("creation_state")

        # --- STEP 0: HANDLE CONTINUATION CONFIRMATION ---
        if state == 'AWAITING_CONTINUE_CONFIRMATION':
            user_input_clean = user_input.strip().lower()
            
            if user_input_clean in ['yes', 'y']:
                # Resume where they left off
                prev_state = session.get('previous_creation_state')
                session['creation_state'] = prev_state
                session['previous_creation_state'] = None
                self.session_manager._save_sessions()

                if prev_state == 'AWAITING_IMPACT':
                    msg = "Welcome back. On a scale of 1-3, please select the impact for the incident you mentioned (1 = High, 2 = Medium, 3 = Low)."
                elif prev_state == 'AWAITING_URGENCY':
                    msg = "Welcome back. On a rating of 1-3, what is the urgency for this issue (1 = High, 2 = Medium, 3 = Low)?"
                else:
                    msg = "Welcome back. Please provide the required information to continue."
                
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""

            else:
                # Discard entirely if they say anything else
                session['creation_state'] = None
                session['draft_incident'] = None
                session['previous_creation_state'] = None
                session['creation_retries'] = 0
                self.session_manager._save_sessions()
                
                msg = "The incomplete incident has been discarded. How can I help you today?"
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""

        # --- STEP 1: INITIAL INCIDENT REPORT ---
        elif not state:
            company_id = self.get_company_id(user_id)
            mapping = self.fetch_category_mapping(company_id)
            
            prompt = (
                "You are an ITSM AI assistant responsible for classifying new incidents.\n"
                f"User issue description: '{user_input}'\n\n"
                f"Available Categories and Subcategories for this company:\n{json.dumps(mapping, indent=2)}\n\n"
                "Analyze the user's issue and select the most appropriate category_id and subcategory_id from the JSON provided.\n"
                "Also generate a concise 'short_description' and a detailed 'description'.\n"
                "CRITICAL: If the user input does not look like a valid IT issue (e.g., just numbers, gibberish, or lacks context), set 'is_valid' to false. Otherwise, set it to true.\n"
                "Output ONLY a valid JSON object with the keys: 'is_valid' (boolean), 'category_id' (int), 'subcategory_id' (int or null), 'short_description' (string), and 'description' (string)."
            )

            try:
                response = self.ai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "system", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )

                # If JSON fails, safely reject rather than hallucinate
                ai_data = json.loads(response.choices[0].message.content)
            except Exception:
                ai_data = {"is_valid": False} 

            # --- Validation Check ---
            if ai_data.get('is_valid') is False:
                msg = "This doesn't look like a valid issue description. Please briefly describe the IT issue you are facing, or log the incident manually."
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""

            # Save draft to session memory instead of DB
            session['draft_incident'] = {
                'category_id': ai_data.get('category_id', 9),
                'subcategory_id': ai_data.get('subcategory_id', None),
                'short_description': ai_data.get('short_description', 'New Incident'),
                'description': ai_data.get('description', user_input),
                'company_id': company_id
            }
            session['creation_state'] = 'AWAITING_IMPACT'
            session['creation_retries'] = 0
            self.session_manager._save_sessions()

            if self.spicy:
                msg = f"Got it. I've noted: '{session['draft_incident']['short_description']}'. Before I log this, how much of a disaster is it? On a scale of 1-3 (1 being 'the building is on fire' and 3 being 'take your time'), what's the impact?"
            else:
                msg = f"I have noted your issue: '{session['draft_incident']['short_description']}'. On a scale of 1-3, please select the impact for the incident you just mentioned (1 = High, 2 = Medium, 3 = Low)."
            
            chat_history = session.get("chat_history", [])
            chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
            self.session_manager.save_history(user_id, chat_history)
            return msg, [], ""

        # --- STEP 2: GATHER IMPACT ---
        elif state == 'AWAITING_IMPACT':
            val = self.extract_rating(user_input)
            
            # --- FIXED: Cap at 3 to match the DB priority_matrix limitations smoothly ---
            if val in [1, 2, 3]:
                session['draft_incident']['impact_id'] = val
                session['creation_state'] = 'AWAITING_URGENCY'
                session['creation_retries'] = 0
                self.session_manager._save_sessions()

                if self.spicy:
                    msg = "Alright, noted. Now, on a rating of 1-3, how fast do I realistically need to fix this?"
                else:
                    msg = "Thank you. On a rating of 1-3, what is the urgency for this issue (1 = High, 2 = Medium, 3 = Low)?"
                
                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                return msg, [], ""
            else:
                retries = session.get('creation_retries', 0) + 1
                if retries >= 3:
                    session['creation_state'] = None
                    session['draft_incident'] = None
                    self.session_manager._save_sessions()
                    return "Sorry, I am unable to process your request at this moment, please log your incident manually.", [], ""
                
                session['creation_retries'] = retries
                self.session_manager._save_sessions()
                return "I didn't quite catch that. Please provide a valid number between 1 and 3 for the impact.", [], ""

        # --- STEP 3: GATHER URGENCY & COMMIT ---
        elif state == 'AWAITING_URGENCY':
            val = self.extract_rating(user_input)
            
            # --- FIXED: Adjusted to 1-3 Scale ---
            if val in [1, 2, 3]:
                draft = session['draft_incident']
                impact_id = draft['impact_id']
                urgency_id = val

                # 1. Get Priority
                self.cursor.execute("SELECT priority_id FROM priority_matrix WHERE impact_id = %s AND urgency_id = %s", (impact_id, urgency_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                p_res = self.cursor.fetchone()
                priority_id = p_res['priority_id'] if p_res else 3

                # 2. Check for matching SLA Definitions BEFORE creating the incident
                sla_check_query = """
                    SELECT sla_definition_id, sla_type, target_minutes 
                    FROM sla_definition 
                    WHERE company_id = %s AND priority_id = %s AND is_active = 1 
                    AND sla_type IN ('response', 'resolution')
                """
                self.cursor.execute(sla_check_query, (draft['company_id'], priority_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                sla_defs = self.cursor.fetchall()

                # --- Check if BOTH Response and Resolution SLAs exist and are active ---
                found_sla_types = [sla['sla_type'] for sla in sla_defs]
                
                if 'response' not in found_sla_types or 'resolution' not in found_sla_types:
                    msg = f"Incident creation unsuccessful: Missing active SLA definitions (both 'response' and 'resolution' are required) for Company {draft['company_id']} with Priority {priority_id}. Please contact your administrator."
                    
                    # Clean Up State so the user isn't permanently stuck
                    session['creation_state'] = None
                    session['draft_incident'] = None
                    session['creation_retries'] = 0
                    self.session_manager._save_sessions()

                    chat_history = session.get("chat_history", [])
                    chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                    self.session_manager.save_history(user_id, chat_history)
                    
                    query_log = "\n".join(executed_queries)
                    return msg, [], query_log

                # 3. Create Incident
                insert_query = """
                    INSERT INTO incident 
                    (company_id, caller_user_id, category_id, subcategory_id, impact_id, urgency_id, priority_id, state_id, short_description, description, opened_at, created_at, created_by) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, NOW(), NOW(), %s)
                """
                self.cursor.execute(insert_query, (draft['company_id'], user_id, draft['category_id'], draft['subcategory_id'], impact_id, urgency_id, priority_id, draft['short_description'], draft['description'], user_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()

                new_id = self.cursor.lastrowid
                inc_number = f"INC{str(new_id).zfill(5)}"
                self.cursor.execute("UPDATE incident SET incident_number = %s WHERE incident_id = %s", (inc_number, new_id))
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                self.db_connection.commit()

                # 4. Create SLA Instances
                for sla in sla_defs:
                    sla_insert = """
                        INSERT INTO sla_instance 
                        (incident_id, company_id, sla_def_id, start_time, due_time, breached_flag, status, cycle_no, last_updated_at, created_at, updated_at) 
                        VALUES (%s, %s, %s, NOW(), NOW() + INTERVAL %s MINUTE, 0, 'running', 1, NOW(), NOW(), NOW())
                    """
                    self.cursor.execute(sla_insert, (new_id, draft['company_id'], sla['sla_definition_id'], sla['target_minutes']))
                    executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                
                self.db_connection.commit()

                self.session_manager.log_db_modification(user_id, "incident", "INSERTED NEW ROW INTO")
                self.session_manager.log_db_modification(user_id, "sla_instance", f"INSERTED {len(sla_defs)} NEW ROWS INTO")

                # Terminal Outputs
                print("\n--- MODIFIED COLUMNS IN INCIDENT TABLE ---")
                print(f"{'incident_id':<12} | {'incident_number':<15} | {'company_id':<10} | {'caller_user_id':<14} | {'category_id':<11} | {'subcategory_id':<14} | {'short_description'}")
                print(f"{new_id:<12} | {inc_number:<15} | {draft['company_id']:<10} | {user_id:<14} | {draft['category_id']:<11} | {str(draft['subcategory_id']):<14} | {draft['short_description']}")
                print("------------------------------------------\n")
                
                print("\n--- MODIFIED COLUMNS IN SLA_INSTANCE TABLE ---")
                print(f"{'incident_id':<12} | {'company_id':<10} | {'sla_def_id':<10} | {'sla_type':<12} | {'target_minutes'}")
                for sla in sla_defs:
                    print(f"{new_id:<12} | {draft['company_id']:<10} | {sla['sla_definition_id']:<10} | {sla['sla_type']:<12} | {sla['target_minutes']}")
                print("----------------------------------------------\n")

                self.cursor.execute("SELECT * FROM incident WHERE incident_id = %s", (new_id,))
                results = self.cursor.fetchall()
                
                # Fetch Names
                if draft['subcategory_id']:
                    self.cursor.execute("SELECT c.category_name, s.subcategory_name FROM category c JOIN subcategory s ON s.subcategory_id = %s WHERE c.category_id = %s", (draft['subcategory_id'], draft['category_id']))
                else:
                    self.cursor.execute("SELECT category_name, NULL as subcategory_name FROM category WHERE category_id = %s", (draft['category_id'],))
                
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                
                names = self.cursor.fetchone()
                cat_name = names['category_name'] if names and names['category_name'] else "Unknown"
                subcat_name = names['subcategory_name'] if names and names['subcategory_name'] else "None"
                creation_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                msg = f"Successfully created incident {inc_number}: '{draft['short_description']}'.\nIt has been routed to (Category {draft['category_id']}: {cat_name}, Subcategory {str(draft['subcategory_id'])}: {subcat_name}).\nCreated on {creation_time}."

                # Clean Up State
                session['creation_state'] = None
                session['draft_incident'] = None
                session['creation_retries'] = 0
                self.session_manager._save_sessions()

                chat_history = session.get("chat_history", [])
                chat_history.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": msg}])
                self.session_manager.save_history(user_id, chat_history)
                
                query_log = "\n".join(executed_queries)
                return msg, results, query_log
            
            else:
                retries = session.get('creation_retries', 0) + 1
                if retries >= 3:
                    session['creation_state'] = None
                    session['draft_incident'] = None
                    self.session_manager._save_sessions()
                    return "Sorry, I am unable to process your request at this moment. Please log your incident manually.", [], ""
                
                session['creation_retries'] = retries
                self.session_manager._save_sessions()
                return "I didn't quite catch that. Please provide a valid number between 1 and 3 for the urgency.", [], ""