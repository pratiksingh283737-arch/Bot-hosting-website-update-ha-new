import os
import requests
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
import logging
from datetime import datetime
import atexit
from waitress import serve
from threading import Lock
import time
import json
import uuid

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

app = Flask(__name__)
lock = Lock()

# --- SECURITY & CONFIG ---
# Security key zaroori hai login session ke liye
app.secret_key = os.environ.get("ytryhde6ugf", "change_this_to_random_string")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "pratik12345) 

DATA_FILE = 'customers.json'
STATUS_FILE = 'ping_statuses.json'

# --- TELEGRAM SETTINGS (Render Environment Variables) ---
TG_BOT_TOKEN = 8390715031:AAHk8gik0anp4iKyl42o_lHlLJJ482-L8R0 
TG_CHAT_ID = os.environ.get("8541572102")      

ALL_CUSTOMERS_BOTS = {}

def load_data():
    global ALL_CUSTOMERS_BOTS
    try:
        if not os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'w') as f: json.dump({}, f)
            ALL_CUSTOMERS_BOTS = {}
        else:
            with open(DATA_FILE, 'r') as f: ALL_CUSTOMERS_BOTS = json.load(f)
    except: ALL_CUSTOMERS_BOTS = {}

def save_data(data):
    try:
        with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=4)
        global ALL_CUSTOMERS_BOTS
        ALL_CUSTOMERS_BOTS = data
    except Exception as e: logging.error(f"Error saving data: {e}")

def read_statuses():
    try:
        with open(STATUS_FILE, 'r') as f: return json.load(f)
    except: return {}

def write_statuses(statuses):
    with open(STATUS_FILE, 'w') as f: json.dump(statuses, f)

def send_telegram_msg(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID: return 
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
    try: requests.post(url, json=payload, timeout=10)
    except: pass

def ping_all_services():
    load_data()
    if not ALL_CUSTOMERS_BOTS: return
    
    all_bots_to_ping = []
    
    # Sirf Verified Bots ko hi Ping list mein daalo
    for customer_id, data in ALL_CUSTOMERS_BOTS.items():
        for name, info in data.items():
            if isinstance(info, dict):
                # Check Approval: Sirf agar verified=True hai tabhi ping karo
                if info.get('verified', False) == True:
                    all_bots_to_ping.append(info.get('url'))
            else:
                # Old format fallback (admin bots wagera ke liye)
                all_bots_to_ping.append(info) 

    if not lock.acquire(blocking=False): return
    try:
        current_statuses = read_statuses()
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        # Self Ping to keep Pinger alive
        pinger_url = os.environ.get('RENDER_EXTERNAL_URL')
        if pinger_url: all_bots_to_ping.append(pinger_url)

        # Unique URLs only
        all_bots_to_ping = list(set(filter(None, all_bots_to_ping)))
        
        logging.info(f"--- Ping cycle started for {len(all_bots_to_ping)} bots ---")

        for url in all_bots_to_ping:
            previous_status = current_statuses.get(url, {}).get('status', 'waiting')
            try:
                response = requests.get(url, timeout=30)
                if response.ok:
                    new_status = 'live'
                    if previous_status == 'down': new_status = 'recovered'
                    current_statuses[url] = {'status': new_status, 'code': response.status_code, 'error': None, 'timestamp': timestamp}
                else:
                    current_statuses[url] = {'status': 'down', 'code': response.status_code, 'error': f"HTTP {response.status_code}", 'timestamp': timestamp}
            except requests.RequestException as e:
                current_statuses[url] = {'status': 'down', 'code': None, 'error': str(e.__class__.__name__), 'timestamp': timestamp}
            time.sleep(2)
        write_statuses(current_statuses)
        logging.info("--- Ping Cycle Finished ---")
    finally:
        lock.release()

# --- ROUTES ---

@app.route('/')
def landing_page():
    # Demo ke liye sirf admin bots dikhao
    admin_bots = ALL_CUSTOMERS_BOTS.get("admin", {})
    return render_template('index.html', bots_for_demo=admin_bots)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect('/admin')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/')

@app.route('/add-bot')
def add_bot_page():
    return render_template('add_bot.html')

@app.route('/add-bot-api', methods=['POST'])
def add_bot_api():
    data = request.json
    name = data.get('name')
    url = data.get('url')
    txid = data.get('txid')
    plan = data.get('plan', 'Starter')

    if not name or not url or not txid:
        return jsonify({'message': 'Please fill all fields'}), 400

    customer_id = str(uuid.uuid4())[:8]
    load_data()
    
    if customer_id not in ALL_CUSTOMERS_BOTS:
        ALL_CUSTOMERS_BOTS[customer_id] = {}
    
    # Save bot data (Verified = False by default)
    ALL_CUSTOMERS_BOTS[customer_id][name] = {
        'url': url,
        'plan': plan,
        'txid': txid,
        'verified': False,
        'added_at': datetime.utcnow().isoformat()
    }
    save_data(ALL_CUSTOMERS_BOTS)

    # Generate Approval Link for Admin
    host_url = os.environ.get('RENDER_EXTERNAL_URL', request.host_url)
    approve_link = f"{host_url}admin/approve/{customer_id}/{name}"

    # Telegram Alert to Admin
    msg = f"🔔 <b>New Order Received!</b>\n\n<b>Plan:</b> {plan}\n<b>TxID:</b> {txid}\n<b>Bot:</b> {name}\n<b>URL:</b> {url}\n\n👇 <b>Click below to Verify & Start:</b>\n{approve_link}"
    send_telegram_msg(msg)

    return jsonify({'message': 'Order Placed! Waiting for Approval.', 'dashboard_url': f"/{customer_id}"})

# --- ADMIN APPROVAL ROUTE ---
@app.route('/admin/approve/<customer_id>/<bot_name>')
def approve_bot(customer_id, bot_name):
    # Check if admin is logged in
    if not session.get('logged_in'):
        # Save where they wanted to go and redirect to login
        return redirect(f'/login')

    load_data()
    if customer_id in ALL_CUSTOMERS_BOTS and bot_name in ALL_CUSTOMERS_BOTS[customer_id]:
        # Bot ko verify kar do
        ALL_CUSTOMERS_BOTS[customer_id][bot_name]['verified'] = True
        save_data(ALL_CUSTOMERS_BOTS)
        
        return f"""
        <div style='font-family:sans-serif; text-align:center; margin-top:50px;'>
            <h1 style='color:green'>Successfully Approved!</h1>
            <p>Bot <b>{bot_name}</b> is now Live and being pinged.</p>
            <a href='/admin' style='padding:10px 20px; background:blue; color:white; text-decoration:none; border-radius:5px;'>Go to Dashboard</a>
        </div>
        """
    
    return "Bot not found or Error."

@app.route('/admin')
def admin_dashboard():
    if not session.get('logged_in'): return redirect('/login')
    
    # Flatten data for dashboard view
    all_bots_flat = {}
    for uid, bots in ALL_CUSTOMERS_BOTS.items():
        for name, data in bots.items():
            if isinstance(data, dict):
                # Admin ko dikhao ki bot verified hai ya pending
                status_icon = "✅" if data.get('verified') else "❌ [PENDING]"
                display_name = f"{status_icon} {name} ({data.get('plan')}) - {data.get('txid')}"
                all_bots_flat[display_name] = data['url']
            else:
                all_bots_flat[name] = data 

    return render_template('dashboard.html', bots_for_this_page=all_bots_flat, is_admin=True)

@app.route('/<customer_name>')
def customer_dashboard(customer_name):
    customer_data = ALL_CUSTOMERS_BOTS.get(customer_name)
    if customer_data is None: return "<h2>Dashboard Not Found!</h2>", 404
    
    bots_flat = {}
    bot_statuses = {} # To pass verification status to HTML
    
    for name, data in customer_data.items():
        if isinstance(data, dict):
            bots_flat[name] = data['url']
            bot_statuses[data['url']] = data.get('verified', False)
        else:
            bots_flat[name] = data
            bot_statuses[data] = True

    return render_template('dashboard.html', bots_for_this_page=bots_flat, verification_map=bot_statuses)

@app.route('/status')
def get_status():
    return jsonify({'statuses': read_statuses()})
    
scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
scheduler.add_job(ping_all_services, 'interval', minutes=2)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    load_data()
    serve(app, host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
