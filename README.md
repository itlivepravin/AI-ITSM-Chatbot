# AI ITSM Chatbot

An AI-powered **IT Service Management (ITSM)** assistant that lets users talk in natural language to create, assign, track, and close incidents. The bot routes each message with OpenAI, queries a MySQL database with role-based access, and can run as a Flask API, a terminal chat, or a Tkinter dashboard.

---

## What type of project is this?

This is a **Python backend + HTML frontend ITSM chatbot**.

It is not a full ServiceNow-style product. It is a local demo that shows how an IT helpdesk bot can:

- Understand a user’s intent (create a ticket, check status, assign work, comment for SLA, and so on)
- Enforce **RBAC** (role-based access control) so Callers, Agents, Managers, and Admins only see the data they should see
- Drive **multi-turn conversations** (for example: create an incident, then ask for impact and urgency)
- Persist tickets, SLA timers, comments, and history in **MySQL**
- Expose a **REST API** that a simple web chat UI can call

Typical use: local development, training, or a proof of concept for an AI helpdesk.

---

## What has been used

| Layer | Technology | Why it is used |
| --- | --- | --- |
| Language | Python 3 | Main application and business logic |
| Web API | Flask | HTTP server for the chatbot (`POST /api/chat`) |
| CORS | Flask-CORS | Lets the HTML frontend call the API from the browser |
| AI / NLP | OpenAI Python SDK (`gpt-3.5-turbo`) | Intent routing, SQL-oriented answers, and conversational steps |
| Config | python-dotenv | Loads `API_Key` from a `.env` file |
| Database | MySQL | Stores users, roles, incidents, SLA, comments, history |
| MySQL driver | mysql-connector-python | Connects Python to MySQL |
| Optional DB driver | psycopg2-binary | Listed as a dependency (this project uses MySQL) |
| Tables in terminal | tabulate | Pretty-prints table data in `db_test.py` |
| Desktop UI | Tkinter | Optional incident dashboard and database viewer |
| Frontend | HTML, CSS, JavaScript | Static chat UI in `htmlChatbot/` |
| Sessions | `user_sessions.json` | Per-user chat history and multi-turn state |
| Audit | `audit.log` | Login and session audit trail |

### Project layout

```text
ITSM/
├── main.py                 # Bot, Flask API, optional CLI + Tkinter GUI
├── db_test.py              # Database viewer (terminal or Tkinter)
├── requirements.txt        # Python packages
├── seed.sql                # Schema + base seed data
├── seed_more.sql           # Extra sample incidents and SLA rows
├── .env                    # OpenAI API key (create this locally)
├── user_sessions.json      # Saved chat sessions (created at runtime)
├── audit.log               # Audit log (created at runtime)
├── htmlChatbot/            # Browser chat UI
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── modules/
    ├── session_manager.py              # Session + chat history
    ├── scope_manager.py                # Role and data-scope rules
    ├── operational_control.py          # Status / ticket queries
    ├── process_quality.py              # Quality analytics
    ├── service_level_transparency.py   # SLA / resolution-time queries
    ├── op_createIncident.py            # Create incident (Caller)
    ├── op_assignIncident.py            # Assign incident (Agent)
    ├── op_slaComment.py                # SLA comment (Agent)
    ├── op_holdIncident.py              # Put on hold (Agent)
    ├── op_resumeHold.py                # Resume from hold (Agent)
    └── op_closeIncident.py             # Close resolved incident (Agent)
```

### Roles in the seed data

| User ID | Username | Role | What they can do |
| --- | --- | --- | --- |
| 1 | admin | Admin | Company-wide queries (admin features are still limited) |
| 2 | manager | Manager | Company-wide queries (manager features are still limited) |
| 3 | agent | Agent | Assign, SLA comment, hold, resume, close, view assigned / group tickets |
| 4 | caller | Caller | Create incidents and view their own tickets |

---

## Prerequisites

Before you set up the project, install:

1. **Python 3.10+** (3.11 or 3.12 is fine)
2. **MySQL Server** (or MariaDB) running locally
3. An **OpenAI API key**
4. A terminal (PowerShell on Windows is fine)

This project’s code connects to MySQL on **port 3307** by default (not 3306). If your MySQL listens on 3306, change the port in `main.py` and `db_test.py`.

---

## How to set up

Follow these steps on a machine that already has Python and MySQL.

### Step 1 — Get the project files

Copy or clone the project into a folder, then open that folder in a terminal:

```powershell
cd C:\WorkSpace\Auto3-Pravin_1\ai\ITSM
```

### Step 2 — Create a Python virtual environment

This keeps the project packages separate from the rest of your system.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

After activation, your prompt should show `(.venv)`.

### Step 3 — Install Python packages

```powershell
pip install -r requirements.txt
```

This installs Flask, Flask-CORS, openai, python-dotenv, mysql-connector-python, psycopg2-binary, and tabulate.

Tkinter ships with most Python installers on Windows. On Linux you may also need:

```bash
sudo apt-get install python3-tk
```

### Step 4 — Create the `.env` file

In the project root, create a file named `.env` with your OpenAI key:

```env
API_Key=sk-your-openai-api-key
```

The code reads `API_Key` (not `OPENAI_API_KEY`). Do not commit this file to git.

### Step 5 — Create the MySQL database and seed data

Start MySQL, then run the SQL files. Example with the MySQL client:

```powershell
mysql -u root -p -P 3307 < seed.sql
mysql -u root -p -P 3307 < seed_more.sql
```

Or open MySQL Workbench / another SQL client and execute:

1. `seed.sql` — creates database `cerebree_itsmdb`, tables, roles, users, and one starter incident
2. `seed_more.sql` — extra incidents and SLA rows for testing

Default connection used in `main.py`:

| Setting | Default value |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `3307` |
| Database | `cerebree_itsmdb` |
| User | `root` |
| Password | `admin123` |

If your MySQL user or password is different, edit the `AITSM_Bot(...)` call at the bottom of `main.py` (and the matching block in `db_test.py`).

### Step 6 — Confirm the database connection (optional)

```powershell
python db_test.py
```

With `TEST_GUI = True` (the default), a Tkinter window opens and shows tables. With `TEST_GUI = False`, it prints the top 5 rows of every table in the terminal.

---

## How to create this project step by step

Use this section if you want to rebuild the same kind of app from scratch, not only run the existing code.

### Step 1 — Define the ITSM domain

Decide the core objects first:

- **Users and roles** (Admin, Manager, Agent, Caller)
- **Incidents** (number, caller, assignee, group, category, priority, state)
- **SLA** (response / resolution targets and running timers)
- **Comments, hold reasons, closure codes, history tables**

That mapping is what `seed.sql` implements.

### Step 2 — Design the database

Create a MySQL database and tables for:

- `role`, `company`, `user`, `user_role`
- `assignment_group`, `group_member`
- `state_master`, `priority_master`, `priority_matrix`
- `category`, `subcategory`
- `incident` plus comment / history tables
- `sla_definition`, `sla_instance`, hold/pause events

Insert seed users so you can log in as User IDs `1`–`4`.

### Step 3 — Add role-based scope

Write a scope helper (`modules/scope_manager.py`) that, given a `user_id`, returns a SQL `WHERE` clause:

- **Admin / Manager:** only their company
- **Agent:** tickets assigned to them, their groups, or unassigned tickets in their group’s categories
- **Caller:** only tickets they opened

Every query must use this scope so one user cannot see another company’s or another caller’s tickets.

### Step 4 — Add session state

ITSM actions are multi-step. Store per-user state in JSON (`user_sessions.json`):

- chat history
- creation draft (impact, urgency, description)
- assign / hold / close / SLA-comment state machines

Without sessions, the bot would forget “which incident were we talking about?” after the next message.

### Step 5 — Build the AI router

In `main.py`, send the latest user message plus recent history to `gpt-3.5-turbo` and force a **single-word route**, for example:

- Caller: `create_incident`, `operational`, `quality`, `service_level`, `greeting`, `unknown`
- Agent: `assign_incident`, `sla_comment`, `hold_incident`, `resume_hold`, `close_incident`, plus the query routes

Then call the matching controller. After three unknown intents, tell the user to contact IT admin.

### Step 6 — Implement controllers as state machines

Each action module asks follow-up questions until it has enough data, then writes to MySQL.

Examples:

- **Create:** description → category → impact → urgency → insert incident
- **Assign:** pick incident → optional comment → assign to the agent
- **Hold:** pick incident → hold reason → pause SLA
- **Close:** only resolved incidents → closure code → close notes

### Step 7 — Expose a Flask API

Wrap the bot in Flask:

- `POST /api/chat` with `{ "user_id": 4, "query": "My laptop is broken" }`
- `POST /api/check_pending` with `{ "user_id": 4 }` to resume an abandoned draft or SLA reminder

Enable CORS so a browser UI can call the API.

### Step 8 — Add a simple frontend

The `htmlChatbot/` folder is a static page: user ID + message box. JavaScript `fetch`es `/api/chat` and shows the bot reply.

### Step 9 — Optional desktop GUI

Tkinter in `main.py` shows query result tables and can export CSV. `db_test.py` is a separate viewer for browsing and editing tables during development.

---

## How to run and use

The app has **three run modes**, controlled by flags at the bottom of `main.py`:

```python
SHOW_GUI_FLAG = True    # Tkinter results grid (CLI mode only)
SPICY_FLAG = False      # Witty vs professional replies
USE_API_FLAG = True     # True = Flask API, False = terminal chat
```

### Mode A — Flask API + web chat (default)

**What this does:** Starts the bot as an HTTP server on port 5000. The HTML page talks to it.

1. Activate the virtual environment (see setup Step 2).
2. Make sure MySQL is running and `cerebree_itsmdb` exists.
3. Confirm `USE_API_FLAG = True` in `main.py`.
4. Start the server:

```powershell
python main.py
```

You should see:

```text
--- Starting Flask API Server ---
Endpoint: POST http://127.0.0.1:5000/api/chat
```

5. Open `htmlChatbot/index.html` in a browser (double-click the file, or drag it into Chrome/Edge).
6. Leave **API Base** as `http://127.0.0.1:5000`.
7. Set **User ID** to a seeded user (`1`, `2`, `3`, or `4`).
8. Type a message and click **Send**.

**Try these examples:**

| User ID | Role | Example message |
| --- | --- | --- |
| 4 | Caller | `My VPN is not connecting` |
| 4 | Caller | `Show my open tickets` |
| 4 | Caller | `What is the status of INC00001?` |
| 3 | Agent | `Show incidents assigned to me` |
| 3 | Agent | `Assign INC00001 to me` |
| 3 | Agent | `Put INC00003 on hold` |
| 3 | Agent | `Add an SLA comment to INC00003` |
| 1 | Admin | `Show open tickets` |

You can also call the API with curl or Postman:

```powershell
curl -X POST http://127.0.0.1:5000/api/chat -H "Content-Type: application/json" -d "{\"user_id\": 4, \"query\": \"Show me my tickets\"}"
```

Expected JSON fields:

- `response` — bot text to show the user
- `data` — table rows (if the query returned records)
- `sql_query` — SQL that was executed (useful for debugging)
- `routed_to` — which controller handled the message

Pending-draft check:

```powershell
curl -X POST http://127.0.0.1:5000/api/check_pending -H "Content-Type: application/json" -d "{\"user_id\": 4}"
```

### Mode B — Terminal chat (optional Tkinter grid)

**What this does:** You type in the console. If the GUI flag is on, a window shows result tables and can save CSV.

1. In `main.py`, set:

```python
USE_API_FLAG = False
SHOW_GUI_FLAG = True   # or False for chat-only
```

2. Run:

```powershell
python main.py
```

3. Enter a numeric User ID when prompted (for example `4`).
4. Chat in the terminal. Type `bye`, `exit`, or `quit` to stop.

If the GUI is enabled, query results appear in the grid. **Save Table** writes a CSV under `saved_tables/<timestamp>/`.

### Mode C — Database viewer

**What this does:** Browse MySQL tables without using the chatbot.

```powershell
python db_test.py
```

- Double-click a table name to open its rows
- Add / delete rows, then **Save Changes** to commit
- **Save Table to CSV** exports the current table

Set `TEST_GUI = False` in `db_test.py` if you only want terminal output.

---

## How a typical conversation works

1. The user sends a message with a `user_id`.
2. The bot loads that user’s role and session.
3. If a multi-turn flow is already active (create, assign, hold, close, SLA comment), that flow continues.
4. Otherwise OpenAI classifies the intent into one route.
5. The matching module builds SQL, applying the user’s data scope.
6. Results are turned into a natural-language answer (and optional table data).
7. Chat history is saved to `user_sessions.json`.

**Three-strike rule:** if the bot cannot understand the intent three times in a row, it tells the user to contact IT admin and resets the counter.

**SLA reminder:** when an Agent starts a session, the bot can warn if a response SLA is due within 30 minutes and offer to add a comment.

**Abandoned create:** if a Caller left a ticket half-created, the next login asks whether to continue the draft.

---

## Incident states

Seeded states in `state_master`:

1. New  
2. Assigned  
3. In Progress  
4. On Hold  
5. Resolved  
6. Closed  

Agents close only **Resolved** incidents. Callers cannot assign, hold, resume, close, or post SLA comments.

---

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `WARNING: API_Key not found in .env file` | Create `.env` in the project root with `API_Key=...` |
| `Error connecting to MySQL` | MySQL is running; host/port/user/password in `main.py` match your server |
| Unknown database `cerebree_itsmdb` | Run `seed.sql` |
| Chat UI shows a network / CORS error | Flask is running on port 5000; API Base in the UI is `http://127.0.0.1:5000` |
| Bot always says it does not understand | OpenAI key is valid and has access to `gpt-3.5-turbo` |
| Agent cannot see tickets | User `3` must be in `group_member` (seed puts them in Service Desk) |
| User ID not found / wrong scope | Use seeded IDs 1–4, or insert matching rows in `user` and `user_role` |

---

## Notes

- Database host, port, and password are hardcoded in `main.py` and `db_test.py` for local demo use. Change them for your environment.
- Admin and Manager roles can query company data; specialized admin workflows are still marked as in progress in the CLI greeting.
- Keep `.env`, `user_sessions.json`, and `audit.log` out of public repositories.
