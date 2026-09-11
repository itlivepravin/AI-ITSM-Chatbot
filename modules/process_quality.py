import json
import datetime
from modules.scope_manager import ScopeManager

class ProcessQualityController:
    def __init__(self, db_cursor, ai_client, session_manager, spicy=False):
        self.cursor = db_cursor
        self.ai_client = ai_client
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
            "You are an ITSM Process Quality intent analyzer. Read the conversation history and the user's latest query.\n"
            "Output a JSON object with the key 'intent' which MUST be one of the following:\n"
            "'recurring_incidents', 'frequently_reopened', 'reopen_rate', 'first_time_fix', "
            "'frequent_handovers', 'systematic_misclassification', or 'unknown'.\n"
            "If the intent involves a timeframe, extract the number of days as an integer in a 'days' key."
        )
        
        try:
            raw_intent = self.get_ai_response(intent_prompt, chat_history, force_json=True)
            return json.loads(raw_intent)
        except Exception:
            return {"intent": "unknown", "days": 30}

    def handle_query(self, user_input, user_id, company_id=1):
        # Fetch user's persistent session and history
        role_id, role_name = self.scope_manager.get_role_info(user_id)
        session = self.session_manager.get_or_create_session(user_id, role_name)
        chat_history = session["chat_history"]

        chat_history.append({"role": "user", "content": user_input})
        if len(chat_history) > 6:
            chat_history = chat_history[-6:]

        analysis = self.analyze_intent(user_input, chat_history)
        intent = analysis.get("intent")
        days = analysis.get("days", 30)
        
        alias_scope = self.scope_manager.get_scope_condition(user_id, table_alias="i")
        
        query = ""
        params = []
        results = []
        executed_queries = []

        # --- FIXED: Removed redundant 'i.company_id = %s' bug from all queries below ---
        if intent == "recurring_incidents":
            query = f"""
                SELECT c.category_name, i.short_description, COUNT(i.incident_id) as occurrence_count
                FROM incident i
                JOIN category c ON i.category_id = c.category_id
                WHERE {alias_scope}
                GROUP BY c.category_name, i.short_description
                HAVING occurrence_count > 1
                ORDER BY occurrence_count DESC
                LIMIT 10
            """

        elif intent == "frequently_reopened":
            query = f"""
                SELECT i.incident_number, i.short_description, COUNT(rh.reopen_id) as reopen_count
                FROM incident_reopen_hist rh
                JOIN incident i ON rh.incident_id = i.incident_id
                WHERE {alias_scope}
                GROUP BY i.incident_number, i.short_description
                ORDER BY reopen_count DESC
                LIMIT 10
            """

        elif intent == "reopen_rate":
            query = f"""
                SELECT 
                    COUNT(DISTINCT i.incident_id) as total_resolved_in_period,
                    COUNT(DISTINCT rh.incident_id) as total_reopened,
                    ROUND((COUNT(DISTINCT rh.incident_id) / NULLIF(COUNT(DISTINCT i.incident_id), 0)) * 100, 2) as reopen_rate_percentage
                FROM incident i
                LEFT JOIN incident_reopen_hist rh ON i.incident_id = rh.incident_id
                WHERE i.state_id IN (5, 6) AND {alias_scope}
                AND i.resolved_at >= NOW() - INTERVAL %s DAY
            """
            params = [days]

        elif intent == "first_time_fix":
            query = f"""
                SELECT
                    COUNT(DISTINCT i.incident_id) as total_resolved,
                    SUM(CASE WHEN ah.assign_hist_id IS NULL THEN 1 ELSE 0 END) as first_time_fixes,
                    ROUND((SUM(CASE WHEN ah.assign_hist_id IS NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(DISTINCT i.incident_id), 0)) * 100, 2) as ftf_rate_percentage
                FROM incident i
                LEFT JOIN incident_assignment_hist ah ON i.incident_id = ah.incident_id
                WHERE i.state_id IN (5, 6) AND {alias_scope}
                AND i.resolved_at >= NOW() - INTERVAL %s DAY
            """
            params = [days]

        elif intent == "frequent_handovers":
            query = f"""
                SELECT ag_old.group_name as from_group, ag_new.group_name as to_group, COUNT(ah.assign_hist_id) as handover_count
                FROM incident_assignment_hist ah
                JOIN incident i ON ah.incident_id = i.incident_id
                JOIN assignment_group ag_old ON ah.old_group_id = ag_old.group_id
                JOIN assignment_group ag_new ON ah.new_group_id = ag_new.group_id
                WHERE {alias_scope}
                GROUP BY from_group, to_group
                ORDER BY handover_count DESC
                LIMIT 10
            """

        elif intent == "systematic_misclassification":
            query = f"""
                SELECT c.category_name, COUNT(i.incident_id) as misclassified_count
                FROM incident i
                JOIN category c ON i.category_id = c.category_id
                JOIN closure_code_master cc ON i.closure_code_id = cc.closure_code_id
                WHERE cc.code_name = 'Not an Incident' AND {alias_scope}
                GROUP BY c.category_name
                ORDER BY misclassified_count DESC
            """

        if query:
            self.cursor.execute(query, tuple(params))
            executed_queries.append(self.cursor.statement.decode('utf-8') if isinstance(self.cursor.statement, bytearray) else str(self.cursor.statement))
            results = self.cursor.fetchall()

        def date_converter(o):
            if isinstance(o, datetime.datetime):
                return o.strftime("%Y-%m-%d %H:%M:%S")

        json_results = json.dumps(results, default=date_converter)

        persona = (
            "You are a highly sarcastic, witty AI Data Analyst."
        ) if self.spicy else (
            "You are a professional IT Service Management Data Analyst."
        )

        generation_prompt = (
            f"{persona}\n\n"
            "You have just executed an analytical query in the ITSM database based on the user's input.\n"
            f"Database Results: {json_results}\n\n"
            "Instructions:\n"
            "- Answer the user's question directly using the database results provided. Summarize the data.\n"
            "- If the result set is empty or zero, state that there is no data matching those criteria.\n"
            "- Do not output JSON. Reply directly to the user."
        )

        final_text = self.get_ai_response(generation_prompt, chat_history)
        
        # Save updated thread context
        chat_history.append({"role": "assistant", "content": final_text})
        self.session_manager.save_history(user_id, chat_history)
        
        query_log = "\n".join(executed_queries)
        return final_text, results, query_log