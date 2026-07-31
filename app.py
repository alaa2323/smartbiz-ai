from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import sqlite3
import os
import uuid
import requests
import json
from dotenv import load_dotenv
from pathlib import Path

# Always look for .env right next to this file, no matter which folder
# `python app.py` was launched from - this is the #1 cause of "API key not
# configured" errors when the key is actually correct.
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / '.env')

app = Flask(__name__, static_folder='static')
app.config['JWT_SECRET_KEY'] = 'smartbiz-ai-secret-key-2026-hackathon'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=7)

CORS(app, resources={
    r"/api/*": {
        "origins": ["http://localhost:5000", "http://127.0.0.1:5000", "*"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})
jwt = JWTManager(app)

# ========== DEMO MODE (task-based, not message-based) ==========
# A "task" is one full job the user is working on with a tool (which may take
# several back-and-forth messages). The demo quota only decreases when a NEW
# task starts - continuing an existing task with more messages is always free
# until the task is marked finished or abandoned.
DEMO_REQUESTS_LIMIT = 5
DEMO_HISTORY_LIMIT = 12  # messages kept per demo task (context window)
demo_state = {}  # ip -> {"count": int, "tasks": {task_id: {"tool_type", "history", "closed"}}}


def get_demo_state(ip):
    return demo_state.setdefault(ip, {"count": 0, "tasks": {}})


# ========== DATABASE SETUP ==========
DATABASE = 'smartbiz.db'


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tool_type TEXT NOT NULL,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES chats (id)
            );

            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tool_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
        """)
        conn.commit()


init_db()

# ========== GROQ AI CONFIGURATION ==========
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '').strip().strip('"').strip("'")
GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'

SYSTEM_PROMPTS = {
    'email': 'You are a professional business email assistant. Write professional, polite, and effective emails in the requested language. Format: Subject line first, then the email body. Use appropriate business tone.',

    'quote': 'You are a professional quote/proposal generator. Create detailed, professional business quotes with: Company info placeholder, Itemized list with prices, Terms and conditions, Validity period, Payment terms. Format as a professional document.',

    'meeting': 'You are a meeting summarizer. Extract and organize: 1. Meeting title and date, 2. Attendees, 3. Key discussion points, 4. Action items (with owners if mentioned), 5. Decisions made. Format clearly with bullet points.',

    'social': 'You are a social media marketing expert. Create engaging posts with: Catchy headline, Engaging body text, Relevant hashtags, Call to action. Adapt tone for the requested platform (LinkedIn, Instagram, Facebook, Twitter/X).',

    'tasks': 'You are a smart task manager. Analyze tasks and provide: 1. Priority ranking (High/Medium/Low), 2. Suggested deadlines, 3. Task breakdown into subtasks, 4. Time estimates. Format as an organized task list.'
}

# Domain/industry context added on top of the base tool prompt so replies are
# tailored to the kind of business the user runs, instead of one-size-fits-all.
DOMAIN_CONTEXTS = {
    'general': '',
    'restaurant': 'The business is a restaurant / cafe / food service. Use relevant vocabulary: menu, reservations, dine-in, delivery, suppliers, service hours.',
    'retail': 'The business is a retail store or e-commerce shop. Use relevant vocabulary: products, inventory, stock, orders, customers, shipping.',
    'consulting': 'The business offers consulting or professional services. Use relevant vocabulary: clients, engagements, deliverables, scope of work, retainers.',
    'tech': 'The business is a tech/software company or startup. Use relevant vocabulary: features, releases, sprints, subscriptions, support tickets.',
    'freelance': 'The user is an independent freelancer. Keep the tone personal and direct, referencing clients and individual projects rather than departments.',
    'health': 'The business is in healthcare or wellness. Use a professional, caring tone and vocabulary like patients, appointments, treatments, follow-ups.',
    'construction': 'The business is in construction or contracting. Use relevant vocabulary: projects, sites, materials, subcontractors, timelines, permits.',
    'education': 'The business is educational/training. Use relevant vocabulary: students, courses, sessions, schedules, enrollment.'
}


def build_system_prompt(tool_type, domain=None):
    base = SYSTEM_PROMPTS.get(tool_type, SYSTEM_PROMPTS['email'])
    extra = DOMAIN_CONTEXTS.get(domain, '') if domain else ''
    if extra:
        return f"{base}\n\nBusiness context: {extra}"
    return base


def call_groq(messages, model='llama-3.3-70b-versatile'):
    """Call Groq API for AI responses"""
    if not GROQ_API_KEY or GROQ_API_KEY == 'gsk_your_groq_api_key_here':
        return {"error": "GROQ_API_KEY not configured. Please add your API key to .env file. Get free key at https://console.groq.com"}

    headers = {
        'Authorization': f'Bearer {GROQ_API_KEY}',
        'Content-Type': 'application/json'
    }

    data = {
        'model': model,
        'messages': messages,
        'temperature': 0.7,
        'max_tokens': 2048
    }

    try:
        response = requests.post(GROQ_API_URL, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"API Error: {str(e)}. Please check your GROQ_API_KEY."}


# ========== DOMAINS ROUTE ==========
@app.route('/api/domains', methods=['GET'])
def get_domains():
    return jsonify({'domains': list(DOMAIN_CONTEXTS.keys())})


# ========== DEMO ROUTES (task-based limiting) ==========
@app.route('/api/demo/status', methods=['GET'])
def demo_status():
    """Check remaining demo tasks for this IP"""
    client_ip = request.remote_addr
    state = get_demo_state(client_ip)
    remaining = max(0, DEMO_REQUESTS_LIMIT - state['count'])
    return jsonify({
        'remaining': remaining,
        'limit': DEMO_REQUESTS_LIMIT,
        'is_demo': True
    })


@app.route('/api/demo/chat', methods=['POST', 'OPTIONS'])
def demo_chat():
    if request.method == 'OPTIONS':
        return jsonify({'status': 'ok'}), 200

    """Free demo without login - limited by TASKS, not messages.
    Send task_id back on every follow-up message of the same task so it
    keeps going without using up another one of the 5 free tasks."""
    client_ip = request.remote_addr
    state = get_demo_state(client_ip)

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON data. Please send valid JSON.'}), 400

    message = data.get('message')
    tool_type = data.get('tool_type', 'email')
    domain = data.get('domain')
    task_id = data.get('task_id')

    if not message:
        return jsonify({'error': 'Message is required'}), 400

    task = state['tasks'].get(task_id) if task_id else None

    if task and not task.get('closed'):
        # Continuing an existing, still-open task: free of charge.
        pass
    else:
        # Starting a brand new task - this is what counts against the limit.
        if state['count'] >= DEMO_REQUESTS_LIMIT:
            return jsonify({
                'error': 'Demo limit reached! Create a free account to continue using all features.',
                'limit_reached': True,
                'remaining': 0
            }), 429
        task_id = str(uuid.uuid4())
        task = {'tool_type': tool_type, 'history': [], 'closed': False}
        state['tasks'][task_id] = task
        state['count'] += 1

    task['history'].append({'role': 'user', 'content': message})

    messages = [{'role': 'system', 'content': build_system_prompt(tool_type, domain)}]
    messages.extend(task['history'][-DEMO_HISTORY_LIMIT:])

    response = call_groq(messages)

    if 'error' in response:
        return jsonify({'error': response['error']}), 500

    ai_message = response['choices'][0]['message']['content']
    task['history'].append({'role': 'assistant', 'content': ai_message})

    remaining = max(0, DEMO_REQUESTS_LIMIT - state['count'])

    return jsonify({
        'task_id': task_id,
        'message': ai_message,
        'tool_type': tool_type,
        'remaining_requests': remaining,
        'is_demo': True
    })


@app.route('/api/demo/task/<task_id>/finish', methods=['POST'])
def demo_finish_task(task_id):
    """Mark a demo task as finished/abandoned so a later message with the
    same tool would start (and count as) a new task."""
    client_ip = request.remote_addr
    state = get_demo_state(client_ip)
    task = state['tasks'].get(task_id)
    if task:
        task['closed'] = True
    return jsonify({'message': 'Task finished'})


# ========== DEBUG ROUTE ==========
@app.route('/api/debug/token', methods=['GET'])
def debug_token():
    auth_header = request.headers.get('Authorization', '')
    return jsonify({
        'auth_header': auth_header[:50] + '...' if len(auth_header) > 50 else auth_header,
        'secret_key': app.config['JWT_SECRET_KEY'][:20] + '...'
    })


@app.route('/api/debug/groq', methods=['GET'])
def debug_groq():
    """Quick sanity check: is the Groq key actually loaded, without exposing it."""
    configured = bool(GROQ_API_KEY) and GROQ_API_KEY != 'gsk_your_groq_api_key_here'
    return jsonify({
        'configured': configured,
        'key_preview': (GROQ_API_KEY[:8] + '...') if configured else None,
        'env_path_checked': str(BASE_DIR / '.env')
    })


# ========== AUTH ROUTES ==========
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON data'}), 400

    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if not all([username, email, password]):
        return jsonify({'error': 'All fields are required'}), 400

    password_hash = generate_password_hash(password)

    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                (username, email, password_hash)
            )
            conn.commit()
        return jsonify({'message': 'User registered successfully'}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Username or email already exists'}), 409


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON data'}), 400

    username = data.get('username')
    password = data.get('password')

    with get_db() as conn:
        user = conn.execute(
            'SELECT * FROM users WHERE username = ?', (username,)
        ).fetchone()

    if user and check_password_hash(user['password_hash'], password):
        access_token = create_access_token(identity=user['id'])
        return jsonify({
            'access_token': access_token,
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email']
            }
        })

    return jsonify({'error': 'Invalid credentials'}), 401


# ========== CHAT ROUTES ==========
@app.route('/api/chat', methods=['POST'])
@jwt_required()
def chat():
    user_id = get_jwt_identity()

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON data - please check request body'}), 400

    message = data.get('message')
    tool_type = data.get('tool_type', 'email')
    chat_id = data.get('chat_id')
    domain = data.get('domain')

    if not message:
        return jsonify({'error': 'Message is required'}), 400

    if not chat_id:
        with get_db() as conn:
            cursor = conn.execute(
                'INSERT INTO chats (user_id, tool_type, title) VALUES (?, ?, ?)',
                (user_id, tool_type, message[:50])
            )
            chat_id = cursor.lastrowid
            conn.commit()

    # Get existing history (without the new message)
    with get_db() as conn:
        history = conn.execute(
            'SELECT role, content FROM messages WHERE chat_id = ? ORDER BY created_at',
            (chat_id,)
        ).fetchall()

    # Build messages for AI: system + history + current message
    messages = [{'role': 'system', 'content': build_system_prompt(tool_type, domain)}]
    for msg in history[-9:]:  # Last 9 messages from history
        messages.append({'role': msg['role'], 'content': msg['content']})
    messages.append({'role': 'user', 'content': message})  # Add current message

    # Save user message to DB
    with get_db() as conn:
        conn.execute(
            'INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)',
            (chat_id, 'user', message)
        )
        conn.commit()

    response = call_groq(messages)

    if 'error' in response:
        return jsonify({'error': response['error']}), 500

    ai_message = response['choices'][0]['message']['content']

    with get_db() as conn:
        conn.execute(
            'INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)',
            (chat_id, 'assistant', ai_message)
        )
        conn.commit()

    return jsonify({
        'chat_id': chat_id,
        'message': ai_message,
        'tool_type': tool_type
    })


@app.route('/api/chat/<int:chat_id>/messages', methods=['GET'])
@jwt_required()
def get_chat_messages(chat_id):
    user_id = get_jwt_identity()

    with get_db() as conn:
        chat = conn.execute(
            'SELECT * FROM chats WHERE id = ? AND user_id = ?',
            (chat_id, user_id)
        ).fetchone()

        if not chat:
            return jsonify({'error': 'Chat not found'}), 404

        messages = conn.execute(
            'SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at',
            (chat_id,)
        ).fetchall()

    return jsonify({
        'chat': dict(chat),
        'messages': [dict(m) for m in messages]
    })


@app.route('/api/chats', methods=['GET'])
@jwt_required()
def get_chats():
    user_id = get_jwt_identity()

    with get_db() as conn:
        chats = conn.execute(
            "SELECT c.*, (SELECT content FROM messages WHERE chat_id = c.id AND role = 'assistant' ORDER BY created_at DESC LIMIT 1) as last_message FROM chats c WHERE c.user_id = ? ORDER BY c.created_at DESC",
            (user_id,)
        ).fetchall()

    return jsonify({'chats': [dict(c) for c in chats]})


@app.route('/api/chat/tool/<tool_type>/today', methods=['GET'])
@jwt_required()
def get_today_tool_chat(tool_type):
    """Return today's ongoing chat for a given tool (if any) so opening the
    tool from the sidebar resumes the conversation instead of wiping it."""
    user_id = get_jwt_identity()
    today = datetime.utcnow().strftime('%Y-%m-%d')

    with get_db() as conn:
        chat = conn.execute(
            "SELECT * FROM chats WHERE user_id = ? AND tool_type = ? AND DATE(created_at) = ? ORDER BY created_at DESC LIMIT 1",
            (user_id, tool_type, today)
        ).fetchone()

        if not chat:
            return jsonify({'chat_id': None, 'messages': []})

        messages = conn.execute(
            'SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at',
            (chat['id'],)
        ).fetchall()

    return jsonify({
        'chat_id': chat['id'],
        'messages': [dict(m) for m in messages]
    })


# ========== HISTORY BY DATE ==========
@app.route('/api/history/dates', methods=['GET'])
@jwt_required()
def history_dates():
    user_id = get_jwt_identity()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DATE(created_at) as day, COUNT(*) as chat_count FROM chats WHERE user_id = ? GROUP BY day ORDER BY day DESC",
            (user_id,)
        ).fetchall()
    return jsonify({'dates': [{'day': r['day'], 'chat_count': r['chat_count']} for r in rows]})


@app.route('/api/history/day/<day>', methods=['GET'])
@jwt_required()
def history_day(day):
    user_id = get_jwt_identity()
    with get_db() as conn:
        chats = conn.execute(
            "SELECT * FROM chats WHERE user_id = ? AND DATE(created_at) = ? ORDER BY created_at",
            (user_id, day)
        ).fetchall()

        result = []
        for c in chats:
            msgs = conn.execute(
                'SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at',
                (c['id'],)
            ).fetchall()
            result.append({'chat': dict(c), 'messages': [dict(m) for m in msgs]})

    return jsonify({'day': day, 'chats': result})


# ========== TOOL ROUTES ==========
@app.route('/api/tools/<tool_type>', methods=['POST'])
@jwt_required()
def use_tool(tool_type):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON data'}), 400

    prompt = data.get('prompt', '')
    domain = data.get('domain')

    if tool_type not in SYSTEM_PROMPTS:
        return jsonify({'error': 'Invalid tool type'}), 400

    messages = [
        {'role': 'system', 'content': build_system_prompt(tool_type, domain)},
        {'role': 'user', 'content': prompt}
    ]

    response = call_groq(messages)

    if 'error' in response:
        return jsonify({'error': response['error']}), 500

    return jsonify({
        'result': response['choices'][0]['message']['content'],
        'tool_type': tool_type
    })


# ========== EMAIL SENDING ==========
# No server-side SMTP is used (no mail credentials stored anywhere). The
# frontend instead builds a `mailto:` link, which opens the user's own
# default email app (Gmail, Outlook, Mail, etc.) with the to/subject/body
# pre-filled, and the user hits send from their own account. See static/index.html.


# ========== TEMPLATES ROUTES ==========
@app.route('/api/templates', methods=['GET', 'POST'])
@jwt_required()
def templates():
    user_id = get_jwt_identity()

    if request.method == 'GET':
        tool_type = request.args.get('tool_type')
        with get_db() as conn:
            if tool_type:
                templates = conn.execute(
                    'SELECT * FROM templates WHERE user_id = ? AND tool_type = ?',
                    (user_id, tool_type)
                ).fetchall()
            else:
                templates = conn.execute(
                    'SELECT * FROM templates WHERE user_id = ?',
                    (user_id,)
                ).fetchall()
        return jsonify({'templates': [dict(t) for t in templates]})

    elif request.method == 'POST':
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid JSON data'}), 400

        with get_db() as conn:
            conn.execute(
                'INSERT INTO templates (user_id, tool_type, name, content) VALUES (?, ?, ?, ?)',
                (user_id, data['tool_type'], data['name'], data['content'])
            )
            conn.commit()
        return jsonify({'message': 'Template saved'}), 201


# ========== STATIC FILES ==========
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)


# ========== ERROR HANDLERS ==========
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Internal server error'}), 500


# JWT Error Handlers
@jwt.invalid_token_loader
def invalid_token_callback(error):
    return jsonify({'error': 'Invalid token, please login again'}), 401


@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    return jsonify({'error': 'Token expired, please login again'}), 401


@jwt.unauthorized_loader
def unauthorized_callback(error):
    return jsonify({'error': 'Missing token, please login'}), 401


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 SmartBiz AI Server Starting...")
    print("=" * 60)
    print("📍 Local:   http://localhost:5000")
    print("📍 Network: http://0.0.0.0:5000")
    print("=" * 60)
    if GROQ_API_KEY and GROQ_API_KEY != 'gsk_your_groq_api_key_here':
        print(f"✅ GROQ_API_KEY loaded (starts with {GROQ_API_KEY[:8]}...)")
    else:
        print("❌ GROQ_API_KEY NOT found or still the placeholder value.")
        print(f"   Checked for .env at: {BASE_DIR / '.env'}")
        print("   Make sure that exact file exists, is named '.env' (not '.env.txt'),")
        print("   and contains a line like: GROQ_API_KEY=gsk_xxxxxxxxxxxx")
        print("   Get a free key at: https://console.groq.com")
    print("=" * 60)
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() in ('1', 'true', 'yes')
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
