import json
import datetime
from modules.scope_manager import ScopeManager

class ServiceLevelTransparencyController:
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
            "You are an ITSM Service Level intent analyzer. Read the conversation history and the user's latest query.\n"
            "Output a JSON object with the key 'intent' which MUST be one of the following:\n"
            "'avg_resolution_time' or 'unknown'."
        )
        
        try:
            raw_intent = self.get_ai_response(intent_prompt, chat_history, force_json=True)
            return json.loads(raw_intent)
        except Exception:
            return {"intent": "unknown"}

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
        
        alias_scope = self.scope_manager.get_scope_condition(user_id, table_alias="i")
        
        query = ""
        params = []
        results = []
        executed_queries = []

        if intent == "avg_resolution_time":
            # Calculates the average resolution time in hours grouped by priority and assignment group
            # FIXED: Removed redundant 'i.company_id = %s' bug
            query = f"""
                SELECT 
                    p.name AS priority, 
                    ag.group_name AS assignment_group, 
                    COUNT(i.incident_id) AS resolved_incidents,
                    ROUND(AVG(TIMESTAMPDIFF(HOUR, i.opened_at, i.resolved_at)), 2) AS avg_resolution_time_hours
                FROM incident i
                LEFT JOIN priority_master p ON i.priority_id = p.priority_id
                LEFT JOIN assignment_group ag ON i.assigned_group_id = ag.group_id
                WHERE i.state_id IN (5, 6) 
                  AND i.resolved_at IS NOT NULL 
                  AND {alias_scope}
                GROUP BY p.name, ag.group_name
                ORDER BY p.name, avg_resolution_time_hours DESC
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
            "You are a highly sarcastic, witty AI ITSM Analyst."
        ) if self.spicy else (
            "You are a professional IT Service Management Business Analyst."
        )

        generation_prompt = (
            f"{persona}\n\n"
            "You have just executed an analytical query in the ITSM database based on the user's input.\n"
            f"Database Results: {json_results}\n\n"
            "Instructions:\n"
            "- Answer the user's question directly using the database results provided. Summarize the data nicely.\n"
            "- If the result set is empty or zero, state that there is no resolved ticket data available for calculating average times.\n"
            "- Do not output JSON. Reply directly to the user."
        )

        final_text = self.get_ai_response(generation_prompt, chat_history)
        
        # Save updated thread context
        chat_history.append({"role": "assistant", "content": final_text})
        self.session_manager.save_history(user_id, chat_history)
        
        query_log = "\n".join(executed_queries)
        return final_text, results, query_log