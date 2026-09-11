import json
import datetime
from modules.scope_manager import ScopeManager

class OperationalController:
    def __init__(self, db_cursor, ai_client, db_connection, session_manager, spicy=False):
        self.cursor = db_cursor
        self.ai_client = ai_client
        self.db_connection = db_connection
        self.session_manager = session_manager 
        self.spicy = spicy
        self.scope_manager = ScopeManager(self.cursor)

    def get_ai_response(self, system_prompt, messages, force_json=False):
        api_messages = [{"role": "system", "content": system_prompt}] + messages
        kwargs = {
            "model": "gpt-3.5-turbo",
            "messages": api_messages,
            "temperature": 0.1 if force_json else 0.7
        }
        if force_json:
            kwargs["response_format"] = {"type": "json_object"}
        
        response = self.ai_client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    def analyze_intent(self, user_input, chat_history):
        intent_prompt = (
            "You are an ITSM intent analyzer. Read the conversation history and the user's latest query.\n"
            "Output a JSON object with the key 'intent' which MUST be one of the following: "
            "'status', 'open_incidents', 'my_assigned_incidents', 'latest_incident', 'open_by_criteria', "
            "'new_in_timeframe', 'pending_incidents', 'sla_approaching', 'sla_breached', "
            "'unprocessed_high_priority', 'frequent_reassignments', 'forbidden_action'.\n"
            "CRITICAL: If the user says 'my incidents', 'incidents assigned to me', or 'show my tickets', set 'intent' to 'my_assigned_incidents'.\n"
            "CRITICAL: If the user simply types a number (e.g., '2', '02', '15') or asks for details on a specific incident, set 'intent' to 'status' and include a 'search_term' key with that number.\n"
            "If the user attempts to delete, modify, edit, or alter an incident's details or description in any way, set 'intent' to 'forbidden_action'.\n"
            "If the intent involves a timeframe (like 'recently'), include a 'days' key (integer)."
        )
        
        try:
            raw_intent = self.get_ai_response(intent_prompt, chat_history, force_json=True)
            return json.loads(raw_intent)
        except Exception:
            return {"action": "query", "intent": "unknown", "search_term": None}

    def handle_query(self, user_input, user_id, company_id=1):
        role_id, role_name = self.scope_manager.get_role_info(user_id)
        session = self.session_manager.get_or_create_session(user_id, role_name)
        chat_history = session["chat_history"]

        chat_history.append({"role": "user", "content": user_input})
        if len(chat_history) > 6:
            chat_history = chat_history[-6:]

        analysis = self.analyze_intent(user_input, chat_history)
        
        base_scope = self.scope_manager.get_scope_condition(user_id)
        alias_scope = self.scope_manager.get_scope_condition(user_id, table_alias="i")
        
        results = []
        executed_queries = []
        context_hint = "" 
        
        intent = analysis.get("intent")
        search_term = analysis.get("search_term")
        query = ""
        params = []

        if intent == "latest_incident":
            query = f"SELECT * FROM incident WHERE {base_scope} ORDER BY created_at DESC LIMIT 1"
            
        elif intent == "status" and search_term:
            search_str = str(search_term).strip()
            if search_str.isdigit():
                padded_inc = f"INC{search_str.zfill(5)}"
                query = f"""
                    SELECT i.incident_number, i.short_description, i.description, s.state_name as state, 
                           p.name as priority, c.category_name as category, i.opened_at, i.resolution_summary
                    FROM incident i
                    LEFT JOIN state_master s ON i.state_id = s.state_id
                    LEFT JOIN priority_master p ON i.priority_id = p.priority_id
                    LEFT JOIN category c ON i.category_id = c.category_id
                    WHERE (i.incident_id = %s OR i.incident_number = %s) AND {alias_scope}
                """
                params = [int(search_str), padded_inc]
            elif search_str.upper().startswith("INC"):
                query = f"SELECT * FROM incident WHERE incident_number = %s AND {base_scope}"
                params = [search_str.upper()]
            else:
                words = search_str.split()
                like_clauses = " AND ".join(["short_description LIKE %s" for _ in words])
                query = f"SELECT * FROM incident WHERE ({like_clauses}) AND {base_scope}"
                params = [f"%{w}%" for w in words]
        
        elif intent == "forbidden_action":
            final_text = "Action Out of Bounds or Not Possible. Incidents cannot be modified or deleted once created."
            chat_history.append({"role": "assistant", "content": final_text})
            self.session_manager.save_history(user_id, chat_history)
            return final_text, [], ""
            
        # --- Explicitly pulls tickets assigned to the user ---
        elif intent == "my_assigned_incidents":
            
            # --- FIXED: Split logic based on Agent vs Caller ---
            if role_id == 3: # Agent: Show tickets assigned to them to resolve
                query = f"""
                    SELECT i.incident_number, i.short_description, s.state_name as state, p.name as priority
                    FROM incident i
                    LEFT JOIN state_master s ON i.state_id = s.state_id
                    LEFT JOIN priority_master p ON i.priority_id = p.priority_id
                    WHERE i.assigned_user_id = %s AND {alias_scope}
                    ORDER BY i.priority_id ASC
                """
                params = [user_id]
            else: # Caller/Admin: Show tickets they reported
                query = f"""
                    SELECT i.incident_number, i.short_description, s.state_name as state, p.name as priority
                    FROM incident i
                    LEFT JOIN state_master s ON i.state_id = s.state_id
                    LEFT JOIN priority_master p ON i.priority_id = p.priority_id
                    WHERE i.caller_user_id = %s 
                    ORDER BY i.priority_id ASC
                """
                params = [user_id]
        
        elif intent == "open_incidents":
            if role_id == 3: 
                self.cursor.execute("SELECT company_id FROM user WHERE user_id = %s", (user_id,))
                agent_comp_res = self.cursor.fetchone()
                agent_comp = agent_comp_res['company_id'] if agent_comp_res else 1
                
                self.cursor.execute("SELECT group_id FROM group_member WHERE user_id = %s", (user_id,))
                groups = self.cursor.fetchall()
                
                # --- HARD RETURN 1: Explicitly handle Agents without a group bypassing AI ---
                if not groups:
                    final_text = "You are not assigned to any group, please ask your admin to assign you to a group."
                    chat_history.append({"role": "assistant", "content": final_text})
                    self.session_manager.save_history(user_id, chat_history)
                    return final_text, [], ""
                
                group_ids = [str(g['group_id']) for g in groups]
                group_list = ",".join(group_ids)
                
                domain_query = f"""
                    SELECT i.incident_number, i.short_description, s.state_name as state, p.name as priority
                    FROM incident i
                    LEFT JOIN state_master s ON i.state_id = s.state_id
                    LEFT JOIN priority_master p ON i.priority_id = p.priority_id
                    LEFT JOIN category c ON i.category_id = c.category_id
                    WHERE i.state_id NOT IN (5, 6) AND i.assigned_user_id IS NULL 
                      AND i.company_id = {agent_comp}
                      AND (i.assigned_group_id IN ({group_list}) OR (i.assigned_group_id IS NULL AND c.assignment_group_id IN ({group_list})))
                    ORDER BY i.priority_id ASC
                """
                self.cursor.execute(domain_query)
                executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
                results = self.cursor.fetchall()
                
                # --- HARD RETURN 2: Explicitly handle empty results bypassing AI ---
                if not results:
                    final_text = "There are no open incidents for you."
                    chat_history.append({"role": "assistant", "content": final_text})
                    self.session_manager.save_history(user_id, chat_history)
                    query_log = "\n".join(executed_queries)
                    return final_text, [], query_log
                    
                context_hint = "These are unassigned incidents mapped to the agent's specific groups. Present them nicely."
            else:
                query = f"""
                    SELECT i.incident_number, i.short_description, s.state_name as state, p.name as priority
                    FROM incident i
                    LEFT JOIN state_master s ON i.state_id = s.state_id
                    LEFT JOIN priority_master p ON i.priority_id = p.priority_id
                    WHERE i.state_id NOT IN (5, 6) AND {alias_scope}
                    ORDER BY i.priority_id ASC
                """
        
        elif intent == "open_by_criteria":
            query = f"""
                SELECT i.incident_number, p.name as priority, s.state_name as state, ag.group_name 
                FROM incident i 
                LEFT JOIN priority_master p ON i.priority_id = p.priority_id 
                LEFT JOIN state_master s ON i.state_id = s.state_id 
                LEFT JOIN assignment_group ag ON i.assigned_group_id = ag.group_id 
                WHERE i.state_id NOT IN (5, 6) AND {alias_scope}
            """
        
        elif intent == "new_in_timeframe":
            days = analysis.get("days", 1) 
            query = f"""
                SELECT incident_number, short_description, created_at 
                FROM incident 
                WHERE created_at >= NOW() - INTERVAL %s DAY AND {base_scope}
            """
            params = [days]
            
        elif intent == "pending_incidents":
            query = f"""
                SELECT i.incident_number, h.reason_name, i.short_description
                FROM incident i 
                JOIN on_hold_reason h ON i.hold_reason_id = h.hold_reason_id 
                WHERE i.state_id = 4 AND {alias_scope}
            """
            
        elif intent == "sla_approaching":
            query = f"""
                SELECT i.incident_number, si.due_time, p.name as priority
                FROM sla_instance si 
                JOIN incident i ON si.incident_id = i.incident_id 
                JOIN priority_master p ON i.priority_id = p.priority_id
                WHERE si.breached_flag = 0 AND si.status = 'running' 
                AND si.due_time BETWEEN NOW() AND NOW() + INTERVAL 2 HOUR 
                AND {alias_scope}
            """
            
        elif intent == "sla_breached":
            query = f"""
                SELECT i.incident_number, si.breached_at, p.name as priority
                FROM sla_instance si 
                JOIN incident i ON si.incident_id = i.incident_id 
                JOIN priority_master p ON i.priority_id = p.priority_id
                WHERE si.breached_flag = 1 AND {alias_scope}
            """
            
        elif intent == "unprocessed_high_priority":
            query = f"""
                SELECT i.incident_number, p.name as priority, s.state_name as state, i.opened_at 
                FROM incident i
                JOIN priority_master p ON i.priority_id = p.priority_id
                JOIN state_master s ON i.state_id = s.state_id
                WHERE i.priority_id IN (1, 2) AND i.state_id IN (1, 2) AND {alias_scope}
            """
            
        elif intent == "frequent_reassignments":
            query = f"""
                SELECT i.incident_number, COUNT(ah.assign_hist_id) as reassignments 
                FROM incident_assignment_hist ah 
                JOIN incident i ON ah.incident_id = i.incident_id 
                WHERE {alias_scope}
                GROUP BY ah.incident_id 
                HAVING reassignments > 1
            """
        
        if query:
            self.cursor.execute(query, tuple(params))
            executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
            results = self.cursor.fetchall()

        def date_converter(o):
            if isinstance(o, datetime.datetime):
                return o.strftime("%Y-%m-%d %H:%M:%S")

        if not results:
            final_text = "I cannot find any records for that request. It may not exist or you may not have permission to view it."
        else:
            preferred_keys = [
                "incident_number",
                "short_description",
                "state",
                "priority",
                "category",
                "opened_at",
                "resolution_summary",
                "due_time",
                "breached_at"
            ]

            def format_row(row):
                ordered_keys = [k for k in preferred_keys if k in row]
                ordered_keys += [k for k in row.keys() if k not in ordered_keys]
                parts = [f"{k.replace('_', ' ').title()}: {row.get(k)}" for k in ordered_keys]
                return "\n".join(parts)

            if len(results) == 1:
                final_text = "Here is the record I found:\n" + format_row(results[0])
            else:
                top_rows = results[:3]
                lines = [f"Found {len(results)} records. Showing the top {len(top_rows)}:"]
                for row in top_rows:
                    inc_number = row.get("incident_number")
                    short_desc = row.get("short_description")
                    state = row.get("state")
                    priority = row.get("priority")
                    summary_parts = []
                    if inc_number:
                        summary_parts.append(f"{inc_number}")
                    if short_desc:
                        summary_parts.append(short_desc)
                    if state:
                        summary_parts.append(f"State: {state}")
                    if priority:
                        summary_parts.append(f"Priority: {priority}")
                    if summary_parts:
                        lines.append("- " + " | ".join(summary_parts))
                    else:
                        lines.append("- " + format_row(row))
                lines.append("Would you like to know more about any particular incident?")
                final_text = "\n".join(lines)
        
        # Save updated thread context
        chat_history.append({"role": "assistant", "content": final_text})
        self.session_manager.save_history(user_id, chat_history)
        
        query_log = "\n".join(executed_queries)
        return final_text, results, query_log