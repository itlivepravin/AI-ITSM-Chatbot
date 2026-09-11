import os
import sys
import threading
import csv                        
from datetime import datetime     
import mysql.connector
from mysql.connector import Error
import tkinter as tk
from tkinter import ttk
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask, request, jsonify
from flask_cors import CORS 

from modules.operational_control import OperationalController
from modules.process_quality import ProcessQualityController
from modules.service_level_transparency import ServiceLevelTransparencyController
from modules.op_createIncident import CreateIncidentController 
from modules.op_assignIncident import AssignIncidentController
from modules.op_slaComment import SLACommentController
from modules.op_holdIncident import HoldIncidentController
from modules.op_resumeHold import ResumeHoldController
from modules.op_closeIncident import CloseIncidentController # <-- NEW IMPORT
from modules.session_manager import SessionManager

load_dotenv()

class AITSM_Bot:
    def __init__(self, host, port, database, db_user, password, show_gui=False, spicy=False):
        self.db_config = {
            'host': host,
            'port': port,
            'database': database,
            'user': db_user,
            'password': password
        }
        self.show_gui = show_gui
        self.spicy = spicy
        self.connection = None
        self.cursor = None
        self.user_id = None
        
        api_key = os.getenv("API_Key")
        if not api_key:
            print("WARNING: API_Key not found in .env file.")
        self.ai_client = OpenAI(api_key=api_key)
        self.op_control = None
        self.pq_control = None
        self.sl_control = None
        self.create_control = None 
        self.assign_control = None
        self.sla_comment_control = None 
        self.hold_control = None 
        self.resume_control = None 
        self.close_control = None # <-- INIT
        self.session_manager = SessionManager()

        self.current_records = []                 
        self.current_table_name = "query_results" 

    def connect(self):
        try:
            self.connection = mysql.connector.connect(**self.db_config)
            if self.connection.is_connected():
                self.cursor = self.connection.cursor(dictionary=True)
                self.op_control = OperationalController(self.cursor, self.ai_client, self.connection, self.session_manager, self.spicy)
                self.pq_control = ProcessQualityController(self.cursor, self.ai_client, self.session_manager, self.spicy)
                self.sl_control = ServiceLevelTransparencyController(self.cursor, self.ai_client, self.session_manager, self.spicy) 
                self.create_control = CreateIncidentController(self.cursor, self.ai_client, self.connection, self.session_manager, self.spicy) 
                self.assign_control = AssignIncidentController(self.cursor, self.ai_client, self.connection, self.session_manager, self.spicy)
                self.sla_comment_control = SLACommentController(self.cursor, self.ai_client, self.connection, self.session_manager, self.spicy)
                self.hold_control = HoldIncidentController(self.cursor, self.ai_client, self.connection, self.session_manager, self.spicy)
                self.resume_control = ResumeHoldController(self.cursor, self.ai_client, self.connection, self.session_manager, self.spicy) 
                self.close_control = CloseIncidentController(self.cursor, self.ai_client, self.connection, self.session_manager, self.spicy) # <-- INSTANTIATE
                return True
        except Error as e:
            print(f"Error connecting to MySQL: {e}")
            return False

    def check_approaching_slas(self, user_id):
        """Proactively checks for running Response SLAs assigned to the agent that are about to breach."""

        role_id, role_name = self.op_control.scope_manager.get_role_info(user_id)
        if role_id != 3: 
            return None                     # Only for agents
            
        session = self.session_manager.get_or_create_session(user_id, role_name)
        
        if session.get("sla_comment_state") == 'AWAITING_SLA_REMINDER_CONFIRM':
            inc_number = session.get("sla_inc_number")
            return f"Hello! Incident {inc_number} is pending and its Response SLA is about to be breached. Would you like to add a comment to stop the SLA? (Yes/No)"
        
        # Otherwise, query the DB for approaching SLAs (within next 30 minutes)
        query = """
            SELECT i.incident_id, i.incident_number 
            FROM incident i
            JOIN sla_instance si ON i.incident_id = si.incident_id
            JOIN sla_definition sd ON si.sla_def_id = sd.sla_definition_id
            WHERE i.assigned_user_id = %s 
              AND sd.sla_type = 'response' 
              AND si.status = 'running' 
              AND si.due_time > NOW() 
              AND si.due_time <= NOW() + INTERVAL 30 MINUTE
            LIMIT 1
        """
        self.cursor.execute(query, (user_id,))
        res = self.cursor.fetchone()
        if res:
            session['sla_comment_state'] = 'AWAITING_SLA_REMINDER_CONFIRM'
            session['sla_incident_id'] = res['incident_id']
            session['sla_inc_number'] = res['incident_number']
            session['sla_retries'] = 0
            self.session_manager._save_sessions()
            return f"Hello! Incident {res['incident_number']} is pending and its Response SLA is about to be breached. Would you like to add a comment to stop the SLA? (Yes/No)"
            
        return None

    def check_pending_creation(self, user_id):
        """Proactively checks if the user abandoned an incident draft in a previous session."""
        
        # 1. Fetch session first to trigger login logic which pauses the draft
        role_id, role_name = self.op_control.scope_manager.get_role_info(user_id)
        session = self.session_manager.get_or_create_session(user_id, role_name)
        state = session.get("creation_state")

        # 2. Issue the strict Yes/No continuation prompt
        if state == 'AWAITING_CONTINUE_CONFIRMATION':
            draft = session.get("draft_incident", {})
            desc = draft.get("short_description", "Unknown Issue")
            return f"Hello! There is an uncreated incident regarding '{desc}'. Would you like to continue from where you left off? Answer Yes or No."
        return None
        
    def process_user_query(self, user_input, user_id):
        """Centralized routing and processing, implementing the 3-strike unknown rule."""
        
        # 1. Fetch Context History & Role
        role_id, role_name = self.op_control.scope_manager.get_role_info(user_id)
        session = self.session_manager.get_or_create_session(user_id, role_name)
        
        # Security: Forcefully clear creation state if an Agent somehow gets stuck in it
        if role_id == 3 and session.get("creation_state"):
            session["creation_state"] = None
            self.session_manager._save_sessions()

        # --- INTERCEPTS FOR MULTI-TURN FLOWS ---
        if session.get("creation_state"):
            resp, data, sql_q = self.create_control.process_step(user_input, user_id)
            return resp, data, "create_incident", sql_q
            
        # INTERCEPT FOR ASSIGN/COMMENT STATE MACHINE
        if session.get("assign_state"):
            resp, data, sql_q = self.assign_control.process_step(user_input, user_id)
            return resp, data, "assign_incident", sql_q

        # --- INTERCEPT FOR HOLD STATE MACHINE --- 
        if session.get("sla_comment_state"):
            resp, data, sql_q = self.sla_comment_control.process_step(user_input, user_id)
            return resp, data, "sla_comment", sql_q
            
        if session.get("hold_state"):
            resp, data, sql_q = self.hold_control.process_step(user_input, user_id)
            return resp, data, "hold_incident", sql_q

        # --- INTERCEPT FOR CLOSE STATE MACHINE ---
        if session.get("close_state"):
            resp, data, sql_q = self.close_control.process_step(user_input, user_id)
            return resp, data, "close_incident", sql_q
            
        chat_history = session.get("chat_history", [])
        history_text = ""
        if chat_history:
            history_text = "\n".join([f"{m.get('role', '')}: {m.get('content', '')}" for m in chat_history[-4:]])
        
        # --- DYNAMIC RBAC ROUTER PROMPT ---
        if role_id == 3:                            # Agent specific routing
            prompt_options = (
                "You are interacting with an AGENT. Agents CANNOT create new incidents.\n"
                "1. 'assign_incident': An agent wanting to assign an open ticket/incident to themselves.\n"
                "2. 'sla_comment': An agent wanting to explicitly append a comment to a specific incident for SLA (User MUST mention 'SLA').\n"
                "3. 'hold_incident': An agent wanting to put a specific incident on hold.\n"
                "4. 'resume_hold': An agent wanting to end the hold or resume a specific incident that is currently on hold.\n"
                "5. 'close_incident': An agent wanting to close an incident that is currently resolved.\n"
                "6. 'operational': Day-to-day tickets, status checks, viewing tickets assigned to them, attempting to modify/delete a ticket, or querying specific numbers.\n"
                "7. 'quality': Process analytics, recurring incidents, reopen rates, first-time fix, handovers.\n"
                "8. 'service_level': Average resolution time per priority or group, service stability, business impact.\n"
                "9. 'greeting': User is just saying hello, thanks, goodbye, or casual pleasantries.\n"
                "10. 'unknown': If the intent is completely unintelligible or lacks context.\n"
                "Respond with exactly one word: 'assign_incident', 'sla_comment', 'hold_incident', 'resume_hold', 'close_incident', 'operational', 'quality', 'service_level', 'greeting', or 'unknown'."
            )
        else:                                       # Caller / Admin specific routing (Catches role 4 and 1)
            prompt_options = (
                "You are interacting with a CALLER. Callers CANNOT assign incidents, hold incidents, resume incidents, close incidents, or do SLA comments.\n"
                "1. 'create_incident': Reporting a new issue, creating a ticket, something is broken and needs to be fixed.\n"
                "2. 'operational': Day-to-day tickets, status checks, viewing their tickets, attempting to modify/delete a ticket, or querying specific numbers.\n"
                "3. 'quality': Process analytics, recurring incidents, reopen rates, first-time fix, handovers.\n"
                "4. 'service_level': Average resolution time per priority or group, service stability, business impact.\n"
                "5. 'greeting': User is just saying hello, thanks, goodbye, or casual pleasantries.\n"
                "6. 'unknown': If the intent is completely unintelligible or lacks context.\n"
                "Respond with exactly one word: 'create_incident', 'operational', 'quality', 'service_level', 'greeting', or 'unknown'."
            )

        prompt = (
            "You are an ITSM routing AI. Review the recent chat history and the user's latest query.\n"
            f"Chat History:\n{history_text}\n\n"
            f"Latest Query: '{user_input}'\n\n"
            "Decide the intent:\n"
            f"{prompt_options}"
        )
        
        try:
            response = self.ai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": prompt}],
                temperature=0.1
            )
            route = response.choices[0].message.content.strip().lower()
        except Exception as e:
            print(f"[Router Error]: {e}")
            route = "unknown"

        if "greeting" in route:
            ans = "Hello! I am your IT Support Assistant. How can I help you today?"
            if "thank" in user_input.lower():
                ans = "You're very welcome! Let me know if you need anything else."
            elif "bye" in user_input.lower():
                ans = "Goodbye! Have a great day."
                
            chat_history.append({"role": "user", "content": user_input})
            chat_history.append({"role": "assistant", "content": ans})
            self.session_manager.save_history(user_id, chat_history)
            return ans, [], "greeting", ""

        # 2. Check for the 3-Strike Unknown Rule
        elif "unknown" in route:
            uid_str = str(user_id)
            count = self.session_manager.sessions[uid_str].get("unknown_count", 0) + 1
            self.session_manager.sessions[uid_str]["unknown_count"] = count
            self.session_manager._save_sessions()               # Reset after Triggering
            
            if count >= 3:
                ans = "Please Contact IT Admin Manually. Sorry for the inconvinience caused."
                self.session_manager.sessions[uid_str]["unknown_count"] = 0 
                self.session_manager._save_sessions()
            else:
                ans = "Could you please reframe or rewrite your query? I couldn't quite understand the intent."
            
            # Save the failure to history so context flows
            chat_history.append({"role": "user", "content": user_input})
            chat_history.append({"role": "assistant", "content": ans})
            self.session_manager.save_history(user_id, chat_history)
            return ans, [], "unknown", ""

        # 3. Route Standard Queries & Reset unknown tracker
        else:
            uid_str = str(user_id)
            if self.session_manager.sessions[uid_str].get("unknown_count", 0) > 0:
                self.session_manager.sessions[uid_str]["unknown_count"] = 0
                self.session_manager._save_sessions()

            if "create_incident" in route:
                resp, data, sql_q = self.create_control.process_step(user_input, user_id)
                return resp, data, "create_incident", sql_q
            elif "assign_incident" in route:
                resp, data, sql_q = self.assign_control.handle_assign(user_input, user_id)
                return resp, data, "assign_incident", sql_q
            elif "sla_comment" in route:
                resp, data, sql_q = self.sla_comment_control.handle_sla_intent(user_input, user_id)
                return resp, data, "sla_comment", sql_q
            elif "hold_incident" in route:
                resp, data, sql_q = self.hold_control.handle_hold_intent(user_input, user_id)
                return resp, data, "hold_incident", sql_q
            elif "resume_hold" in route:
                resp, data, sql_q = self.resume_control.handle_resume_intent(user_input, user_id)
                return resp, data, "resume_hold", sql_q
            elif "close_incident" in route:
                resp, data, sql_q = self.close_control.handle_close_intent(user_input, user_id)
                return resp, data, "close_incident", sql_q
            elif "service_level" in route:
                resp, data, sql_q = self.sl_control.handle_query(user_input, user_id)
                return resp, data, "service_level", sql_q
            elif "quality" in route:
                resp, data, sql_q = self.pq_control.handle_query(user_input, user_id)
                return resp, data, "quality", sql_q
            else:
                resp, data, sql_q = self.op_control.handle_query(user_input, user_id)
                return resp, data, "operational", sql_q

    def chat_loop(self):
        print(f"\nLogged in as User ID: {self.user_id}")
        mode = "SPICY" if self.spicy else "PROFESSIONAL"
        print(f"Bot Personality: {mode}")
        
        # --- DEV IN PROGRESS MESSAGE FOR ADMINS/MANAGERS ---
        role_id, role_name = self.op_control.scope_manager.get_role_info(self.user_id)
        if role_id in [1, 2]:
            print(f"\nBot: Hello {role_name}. Please note that development is still in progress and a lot is yet to be appended. You have access to the full database, but your scope is strictly limited to your company's data. You cannot access external company data.")
        
        pending_create_msg = self.check_pending_creation(self.user_id)
        pending_sla_msg = self.check_approaching_slas(self.user_id)
        
        if pending_create_msg:
            print(f"\nBot: {pending_create_msg}")
        elif pending_sla_msg:
            print(f"\nBot: {pending_sla_msg}")
        else:
            print("\nAsk a question or report an issue. Type 'bye' to exit.")
        
        while True:
            user_input = input("\nYou: ")
            if user_input.lower() in ['bye', 'exit', 'quit']:
                print("Exiting chat. Closing application...")
                self.close_connection()
                os._exit(0) 

            response_text, raw_data_records, route, sql_query = self.process_user_query(user_input, self.user_id)
            
            if sql_query:
                print(f"\n---------- SQL QUERY ---------\n{sql_query}\n-------------------------------\n")
                
            print(f"Bot: {response_text}")

            if self.show_gui:
                self.root.after(0, self.update_gui_data, raw_data_records)

    def build_gui(self):
        self.root = tk.Tk()
        self.root.title(f"ITSM Dashboard - User Scope: {self.user_id}")
        self.root.geometry("1000x450")

        # --- Top frame for the Save button ---
        top_frame = tk.Frame(self.root)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        
        self.save_btn = tk.Button(top_frame, text="Save Table", command=self.save_table, state=tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT)

        self.tree = ttk.Treeview(self.root, show="headings", selectmode="browse")
        y_scrollbar = ttk.Scrollbar(self.root, orient=tk.VERTICAL, command=self.tree.yview)
        x_scrollbar = ttk.Scrollbar(self.root, orient=tk.HORIZONTAL, command=self.tree.xview)
        
        self.tree.configure(yscroll=y_scrollbar.set, xscroll=x_scrollbar.set)
        y_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        x_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)

        # Start with an empty GUI
        self.update_gui_data([])

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.mainloop()

    def update_gui_data(self, records):
        """Dynamically clears and rebuilds the grid based on active query."""

        self.current_records = records                      # Store records for saving
        
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        if not records:
            self.save_btn.config(state=tk.DISABLED)         # Disable if empty
            self.tree["columns"] = ("Message",)
            self.tree.heading("Message", text="Message")
            self.tree.insert("", tk.END, values=("Waiting for query or no records found.",))
            return

        self.save_btn.config(state=tk.NORMAL)               # Enable if populated 

        # Build columns based on the keys of the first dictionary
        headers = list(records[0].keys())
        self.tree["columns"] = headers
        for col in headers:
            self.tree.heading(col, text=col.replace('_', ' ').title())
            self.tree.column(col, width=150, anchor=tk.W)

        # Insert specific rows
        for row in records:
            str_row = [str(item) if item is not None else "None" for item in row.values()]
            self.tree.insert("", tk.END, values=str_row)

    # --- Save functionality ---
    def save_table(self):
        if not self.current_records:
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_path = os.path.join("saved_tables", timestamp)
        os.makedirs(folder_path, exist_ok=True)
        
        file_name = f"{self.current_table_name}.csv"
        file_path = os.path.join(folder_path, file_name)
        
        try:
            headers = list(self.current_records[0].keys())
            with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=headers)
                writer.writeheader()
                writer.writerows(self.current_records)
            print(f"\n[GUI] Successfully saved data to: {file_path}")
        except Exception as e:
            print(f"\n[GUI] Error saving file: {e}")

    def on_closing(self):
        self.close_connection()
        os._exit(0)

    def close_connection(self):
        if self.connection and self.connection.is_connected():
            self.cursor.close()
            self.connection.close()

if __name__ == '__main__':
    SHOW_GUI_FLAG = True                        # Set to True to enable the GUI, False for CLI-only mode
    SPICY_FLAG = False                          # Set to True for the witty persona
    USE_API_FLAG = True                        # Set to True for Flask, False for Terminal/GUI                 

    bot = AITSM_Bot(
        host='127.0.0.1', 
        port=3307,
        database='cerebree_itsmdb',
        db_user='root',       
        password='admin123', 
        show_gui=SHOW_GUI_FLAG,
        spicy=SPICY_FLAG
    )
    
    if not bot.connect():
        sys.exit()

    # --- FLASK API MODE ---
    if USE_API_FLAG:
        app = Flask(__name__)
        CORS(app) 

        @app.route('/api/chat', methods=['POST'])
        def chat_endpoint():
            data = request.json

            # Validate payload
            if not data or 'user_id' not in data or 'query' not in data:
                return jsonify({"error": "Missing 'user_id' or 'query' in request body."}), 400

            try:
                user_id = int(data['user_id'])
                query = data['query']

                # Uses the new centralized process_user_query function
                response_text, raw_data, route, sql_query = bot.process_user_query(query, user_id)

                return jsonify({
                    "response": response_text,
                    "data": raw_data,
                    "sql_query": sql_query,
                    "routed_to": route 
                }), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        # --- NEW ENDPOINT: For Web Frontends to check for abandoned drafts --- 
        @app.route('/api/check_pending', methods=['POST'])
        def check_pending_endpoint():
            data = request.json
            if not data or 'user_id' not in data:
                return jsonify({"error": "Missing 'user_id'."}), 400
            user_id = int(data['user_id'])
            pending_create = bot.check_pending_creation(user_id)
            pending_sla = bot.check_approaching_slas(user_id)
            pending_msg = pending_create if pending_create else pending_sla
            return jsonify({"pending_message": pending_msg}), 200

        print("\n--- Starting Flask API Server ---")
        print("Endpoint: POST http://127.0.0.1:5000/api/chat")
        print("Expected JSON: {\"user_id\": 1, \"query\": \"Show me open tickets\"}\n")
        
        # Run the server (use_reloader=False prevents Flask from launching the bot twice in some environments)
        app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)

    else:
        try:
            user_id_input = input("Enter your User ID to log in: ")
            bot.user_id = int(user_id_input)
        except ValueError:
            print("Invalid User ID. Must be a number.")
            sys.exit()

        chat_thread = threading.Thread(target=bot.chat_loop, daemon=True)
        chat_thread.start()

        if SHOW_GUI_FLAG:
            bot.build_gui()
        else:
            chat_thread.join()