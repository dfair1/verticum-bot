import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from flask_sqlalchemy import SQLAlchemy
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "verticum-secret-change-this")

# --- DATABASE: Render Postgres if available, else SQLite for local test ---
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL or "sqlite:///verticum.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "verticum-verify")
PHONE_ID = os.environ.get("PHONE_ID", "")

# --- MODELS ---
class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_name = db.Column(db.String(100), nullable=False)
    whatsapp_number = db.Column(db.String(30), unique=True) # Business number
    login_email = db.Column(db.String(100), unique=True)
    login_password = db.Column(db.String(100), default="1234")
    language = db.Column(db.String(20), default="Pidgin") # Pidgin / English
    is_active = db.Column(db.Boolean, default=True)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    phone = db.Column(db.String(30))
    name = db.Column(db.String(100), default="Customer")
    mode = db.Column(db.String(20), default="bot") # bot / human
    human_until = db.Column(db.DateTime, nullable=True)
    support_access_until = db.Column(db.DateTime, nullable=True)
    last_message_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    sender = db.Column(db.String(20)) # customer / bot / human
    text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- HELPERS ---
def send_whatsapp(to, text):
    if not WHATSAPP_TOKEN or not PHONE_ID:
        print(f"[MOCK SEND] To {to}: {text}")
        return True
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    r = requests.post(url, headers=headers, json=payload)
    print(r.text)
    return r.status_code == 200

def bot_reply(client, customer, text):
    # Simple demo bot logic - you will replace with your real bot logic
    text_low = text.lower()
    if "price" in text_low or "how much" in text_low:
        if client.business_name == "Glow Hair":
            return "Our frontal wig na ₦85,000, bone straight na ₦120,000. Which one you want? I fit send you pictures."
        else:
            return "Our Ofada rice na ₦5,500 per plate with chicken. You want make we package am for you?"
    if "hello" in text_low or "hi" in text_low:
        if client.language == "Pidgin":
            return f"Hello my customer, welcome to {client.business_name}! How we fit help you today?"
        return f"Hello, welcome to {client.business_name}! How can we help you?"
    return "Thanks for your message. Our team go reply you soon. You fit also tell me which product you want?"

# --- WEBHOOK ---
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Invalid", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry:
            return "ok", 200
        msg = entry["messages"][0]
        from_phone = msg["from"]
        body = msg.get("text", {}).get("body", "")
        # Find which client this message is for - via metadata phone_number_id or to field
        # For demo: we map by checking which client is active - in production, use phone_number_id mapping
        # Here we use first active client for simplicity, you will map PHONE_ID -> Client
        client = Client.query.filter_by(is_active=True).first()
        if not client:
            return "ok", 200

        customer = Customer.query.filter_by(client_id=client.id, phone=from_phone).first()
        if not customer:
            customer = Customer(client_id=client.id, phone=from_phone, name=f"Customer {from_phone[-4:]}")
            db.session.add(customer)
            db.session.commit()

        # Save customer message
        db.session.add(Message(customer_id=customer.id, client_id=client.id, sender="customer", text=body))
        customer.last_message_at = datetime.utcnow()
        db.session.commit()

        # PRIVACY + TAKEOVER CHECK: If mode == human, DO NOT REPLY
        if customer.mode == "human":
            # Check auto-release after 30 mins
            if customer.human_until and datetime.utcnow() > customer.human_until:
                customer.mode = "bot"
                db.session.commit()
            else:
                print(f"Human mode - bot paused for {from_phone}")
                return "ok", 200

        # Bot replies
        reply = bot_reply(client, customer, body)
        send_whatsapp(from_phone, reply)
        db.session.add(Message(customer_id=customer.id, client_id=client.id, sender="bot", text=reply))
        db.session.commit()
    except Exception as e:
        print("Webhook error:", e)
    return "ok", 200

# --- DASHBOARD ---
LOGIN_HTML = """
<h2>Verticum Login</h2>
<form method="post">
Email: <input name="email"><br><br>
Password: <input name="password" type="password"><br><br>
<button>Login</button>
</form>
<p>Demo Admin: admin@verticum.com / admin123<br>Demo Glow Hair: glow@glow.com / 1234<br>Demo Mama Nkechi: nkechi@food.com / 1234</p>
"""

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        pwd = request.form.get("password")
        if email == "admin@verticum.com" and pwd == "admin123":
            session["role"] = "admin"
            session["client_id"] = None
            return redirect("/dashboard")
        c = Client.query.filter_by(login_email=email, login_password=pwd).first()
        if c:
            session["role"] = "client"
            session["client_id"] = c.id
            return redirect("/dashboard")
        return "Wrong login <a href='/login'>Try again</a>"
    return LOGIN_HTML

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/dashboard")
def dashboard():
    if "role" not in session:
        return redirect("/login")
    if session["role"] == "admin":
        clients = Client.query.all()
        html = "<h1>Verticum Admin - Overview (Privacy Mode)</h1><p>You see overview only. Full chats are hidden until client grants Support Access.</p><a href='/logout'>Logout</a><hr>"
        for c in clients:
            count = Customer.query.filter_by(client_id=c.id).count()
            msgs = Message.query.filter_by(client_id=c.id).count()
            html += f"<div><b>{c.business_name}</b> - {count} customers, {msgs} messages - <a href='/admin/client/{c.id}'>View Overview</a></div><br>"
        return html
    else:
        client = Client.query.get(session["client_id"])
        customers = Customer.query.filter_by(client_id=client.id).order_by(Customer.last_message_at.desc()).all()
        html = f"<h1>{client.business_name} - Inbox</h1><a href='/logout'>Logout</a><hr>"
        for cust in customers:
            badge = "🟢 BOT" if cust.mode=="bot" else "🔴 HUMAN TAKEN OVER"
            html += f"<div><b>{cust.name} ({cust.phone})</b> - {badge} - <a href='/chat/{cust.id}'>Open Chat</a></div><br>"
        return html

@app.route("/admin/client/<int:client_id>")
def admin_client(client_id):
    if session.get("role") != "admin":
        return redirect("/login")
    client = Client.query.get(client_id)
    customers = Customer.query.filter_by(client_id=client.id).all()
    html = f"<h2>{client.business_name} - Admin Overview</h2><a href='/dashboard'>Back</a><hr>"
    for cust in customers:
        # Privacy: show only metadata, not message content unless support access granted
        has_access = cust.support_access_until and cust.support_access_until > datetime.utcnow()
        if has_access:
            html += f"<div>{cust.name} - <a href='/chat/{cust.id}'>View Full Chat (Support Access Active)</a></div>"
        else:
            html += f"<div>{cust.name} - Messages hidden for privacy. Client must click 'Grant Support Access' to allow viewing. - Last active: {cust.last_message_at}</div>"
        html += "<br>"
    return html

@app.route("/chat/<int:customer_id>", methods=["GET"])
def chat_view(customer_id):
    if "role" not in session:
        return redirect("/login")
    cust = Customer.query.get(customer_id)
    # Client can only see own chats
    if session["role"] == "client" and session["client_id"] != cust.client_id:
        return "Not allowed"
    # Admin can only see if support access granted
    if session["role"] == "admin":
        if not cust.support_access_until or cust.support_access_until < datetime.utcnow():
            return "Privacy Protected: Client has not granted Support Access. Ask client to click 'Grant Support Access'."
    msgs = Message.query.filter_by(customer_id=cust.id).order_by(Message.created_at).all()
    client = Client.query.get(cust.client_id)
    html = f"<h2>Chat with {cust.name} - {client.business_name}</h2>"
    html += f"<p>Mode: <b>{cust.mode.upper()}</b></p>"
    html += f"<a href='/takeover/{cust.id}'>[ TAKE OVER CHAT ]</a> | <a href='/release/{cust.id}'>[ GIVE BACK TO BOT ]</a> | "
    if session["role"] == "client":
        html += f"<a href='/grant-support/{cust.id}'>[ Grant Support Access to Verticum - 1 Hour ]</a> | "
    html += f"<a href='/dashboard'>Back to Inbox</a><hr>"
    for m in msgs:
        color = "blue" if m.sender=="customer" else "green" if m.sender=="bot" else "red"
        html += f"<div style='color:{color}'><b>{m.sender}:</b> {m.text} <small>({m.created_at})</small></div><br>"
    html += f"""
    <form method="post" action="/send/{cust.id}">
    <input name="text" style="width:300px" placeholder="Type reply...">
    <button>Send</button>
    </form>
    """
    return html

@app.route("/takeover/<int:customer_id>")
def takeover(customer_id):
    cust = Customer.query.get(customer_id)
    cust.mode = "human"
    cust.human_until = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()
    return redirect(f"/chat/{customer_id}")

@app.route("/release/<int:customer_id>")
def release(customer_id):
    cust = Customer.query.get(customer_id)
    cust.mode = "bot"
    cust.human_until = None
    db.session.commit()
    return redirect(f"/chat/{customer_id}")

@app.route("/grant-support/<int:customer_id>")
def grant_support(customer_id):
    cust = Customer.query.get(customer_id)
    cust.support_access_until = datetime.utcnow() + timedelta(hours=1)
    db.session.commit()
    return f"Support Access granted for 1 hour. <a href='/chat/{customer_id}'>Back to chat</a>"

@app.route("/send/<int:customer_id>", methods=["POST"])
def send_msg(customer_id):
    cust = Customer.query.get(customer_id)
    text = request.form.get("text")
    # When human sends, ensure mode is human
    cust.mode = "human"
    cust.human_until = datetime.utcnow() + timedelta(minutes=30)
    db.session.add(Message(customer_id=cust.id, client_id=cust.client_id, sender="human", text=text))
    db.session.commit()
    send_whatsapp(cust.phone, text)
    return redirect(f"/chat/{customer_id}")

@app.route("/")
def home():
    return "Verticum is live. Go to /dashboard"

# Seed demo data
@app.before_first_request
def seed():
    db.create_all()
    if not Client.query.first():
        c1 = Client(business_name="Glow Hair", whatsapp_number="+2348000000001", login_email="glow@glow.com", login_password="1234", language="Pidgin")
        c2 = Client(business_name="Mama Nkechi Foods", whatsapp_number="+2348000000002", login_email="nkechi@food.com", login_password="1234", language="English")
        db.session.add_all([c1,c2])
        db.session.commit()
        cust1 = Customer(client_id=c1.id, phone="+2348011111111", name="Ada - Glow Customer")
        cust2 = Customer(client_id=c2.id, phone="+2348022222222", name="Chidi - Food Customer")
        db.session.add_all([cust1,cust2])
        db.session.commit()
        db.session.add_all([
            Message(customer_id=cust1.id, client_id=c1.id, sender="customer", text="Hello, how much is frontal wig?"),
            Message(customer_id=cust1.id, client_id=c1.id, sender="bot", text="Our frontal wig na ₦85,000. Which one you want?"),
            Message(customer_id=cust2.id, client_id=c2.id, sender="customer", text="Hello, how much is Ofada?"),
            Message(customer_id=cust2.id, client_id=c2.id, sender="bot", text="Our Ofada rice na ₦5,500 per plate with chicken."),
        ])
        db.session.commit()

if __name__ == "__main__":
    app.run(debug=True)
