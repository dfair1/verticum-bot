import os, json, requests, time, hashlib, re
from flask import Flask, request, jsonify, render_template_string, session, redirect
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "verticum-secret")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "verticum-verify")
CLOUDINARY_CLOUD = os.environ.get("CLOUDINARY_CLOUD_NAME", "demo")
CLOUDINARY_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
CLOUDINARY_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")
OWNER_PHONE = os.environ.get("OWNER_PHONE", "2348039113437")
ADMIN_USER = os.environ.get("ADMIN_USER", "verticum")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "verticum123")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ============ POSTGRES STORAGE (FIX FOR DISAPPEARING CLIENTS) ============
try:
    import psycopg2
    import psycopg2.extras
    HAS_PG = True if DATABASE_URL else False
except Exception as e:
    print("PSYCOPG2 NOT INSTALLED:", e)
    HAS_PG = False

def get_db():
    # Render gives postgres:// but psycopg2 needs postgresql://
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url, sslmode="require")

def init_db():
    if not HAS_PG:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                data JSONB NOT NULL
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                client_id TEXT NOT NULL,
                phone TEXT NOT NULL,
                messages JSONB NOT NULL,
                PRIMARY KEY (client_id, phone)
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("POSTGRES TABLES READY")
    except Exception as e:
        print("INIT DB ERROR:", e)

init_db()

def load_clients():
    # Postgres version
    if HAS_PG:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT data FROM clients ORDER BY data->>'business' ASC")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            clients = [r[0] for r in rows]
            # Auto-import from old json file once, if DB is empty
            if not clients:
                try:
                    with open("clients.json") as f:
                        old = json.load(f)
                    if old:
                        save_clients(old)
                        print(f"IMPORTED {len(old)} clients from clients.json to Postgres")
                        return old
                except:
                    pass
            return clients
        except Exception as e:
            print("LOAD CLIENTS PG ERROR:", e)
            return []
    # Fallback for local testing without DATABASE_URL
    try:
        with open("clients.json") as f: return json.load(f)
    except: return []

def save_clients(clients):
    if HAS_PG:
        try:
            conn = get_db()
            cur = conn.cursor()
            # Get existing ids to delete removed clients
            cur.execute("SELECT id FROM clients")
            existing = set([r[0] for r in cur.fetchall()])
            new_ids = set()
            for c in clients:
                cid = str(c.get("id", ""))
                if not cid:
                    continue
                new_ids.add(cid)
                cur.execute(
                    "INSERT INTO clients (id, data) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                    (cid, json.dumps(c))
                )
            # Delete clients that were removed
            for old_id in (existing - new_ids):
                cur.execute("DELETE FROM clients WHERE id = %s", (old_id,))
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception as e:
            print("SAVE CLIENTS PG ERROR:", e)
            return
    with open("clients.json", "w") as f: json.dump(clients, f, indent=2)

def load_chats():
    if HAS_PG:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT client_id, phone, messages FROM chats")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            chats = {}
            for client_id, phone, msgs in rows:
                if client_id not in chats:
                    chats[client_id] = {}
                chats[client_id][phone] = msgs if isinstance(msgs, list) else []
            # Auto-import old chats.json once if DB empty
            if not chats:
                try:
                    with open("chats.json") as f:
                        old = json.load(f)
                    if old:
                        save_chats(old)
                        return old
                except:
                    pass
            return chats
        except Exception as e:
            print("LOAD CHATS PG ERROR:", e)
            return {}
    try:
        with open("chats.json") as f: return json.load(f)
    except: return {}

def save_chats(chats):
    if HAS_PG:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT client_id, phone FROM chats")
            existing = set([(r[0], r[1]) for r in cur.fetchall()])
            new_keys = set()
            for client_id, phones in chats.items():
                for phone, msgs in phones.items():
                    new_keys.add((str(client_id), str(phone)))
                    cur.execute(
                        "INSERT INTO chats (client_id, phone, messages) VALUES (%s, %s, %s) ON CONFLICT (client_id, phone) DO UPDATE SET messages = EXCLUDED.messages",
                        (str(client_id), str(phone), json.dumps(msgs))
                    )
            for old_key in (existing - new_keys):
                cur.execute("DELETE FROM chats WHERE client_id = %s AND phone = %s", (old_key[0], old_key[1]))
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception as e:
            print("SAVE CHATS PG ERROR:", e)
            return
    with open("chats.json", "w") as f: json.dump(chats, f, indent=2)

CHATS = load_chats()
USER_LANG = {}
ONBOARDING = {}
TAKEOVER = set()
SEEN_PRODUCTS = set()
HUMAN_ATTEMPT_ONBOARDING = {}

def add_chat(client_id, customer_phone, msg_obj):
    if client_id not in CHATS: CHATS[client_id] = {}
    if customer_phone not in CHATS[client_id]: CHATS[client_id][customer_phone] = []
    CHATS[client_id][customer_phone].append(msg_obj)
    save_chats(CHATS)

def send_text(phone_id, to, text):
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    r = requests.post(url, headers=headers, json=payload)
    print(f"SEND TEXT via {phone_id} to {to}:", r.text)
    return r

def send_image(phone_id, to, image_url, caption):
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"link": image_url, "caption": caption}}
    r = requests.post(url, headers=headers, json=payload)
    print(f"SEND IMAGE via {phone_id}:", r.text)
    return r

def upload_to_cloudinary(image_bytes, business_name):
    if not CLOUDINARY_KEY or not CLOUDINARY_SECRET: return None
    try:
        timestamp = int(time.time())
        folder = f"verticum/{business_name.lower().replace(' ','-')}"
        to_sign = f"folder={folder}&timestamp={timestamp}{CLOUDINARY_SECRET}"
        signature = hashlib.sha1(to_sign.encode()).hexdigest()
        url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD}/image/upload"
        files = {"file": ("product.jpg", image_bytes, "image/jpeg")}
        data = {"api_key": CLOUDINARY_KEY, "timestamp": timestamp, "folder": folder, "signature": signature}
        r = requests.post(url, files=files, data=data, timeout=30)
        return r.json().get("secure_url") if r.status_code==200 else None
    except Exception as e:
        print("CLOUDINARY ERROR:", e)
        return None

def download_whatsapp_media_and_upload(media_id, business_name):
    try:
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        r1 = requests.get(f"https://graph.facebook.com/v21.0/{media_id}", headers=headers, timeout=20)
        download_url = r1.json().get("url")
        if not download_url: return None
        r2 = requests.get(download_url, headers=headers, timeout=30)
        return upload_to_cloudinary(r2.content, business_name) if r2.status_code==200 else None
    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None

def parse_price_text(text):
    text = text.strip()
    m = re.search(r'(₦?\s*\d[\d,]*\s*k?)', text, re.IGNORECASE)
    if m:
        price_raw = m.group(1).replace(" ", "")
        if "₦" not in price_raw: price_raw = "₦" + price_raw
        name = text[:m.start()].strip() or "Product"
        return name, price_raw
    return text, ""

def normalize_phone(raw):
    p = re.sub(r'\D', '', raw or '')
    if p.startswith('0') and len(p)==11: p = '234' + p[1:]
    if p.startswith('234') and len(p)==13: return p
    return p

TEXTS = {
    "choose_lang": "Welcome to {biz}! 👋\nHow do you want me to talk to you?\nReply 1 for English\nReply 2 for Pidgin",
    "greeting_en": "Hello, welcome to {biz}! Type PRICE to see prices, PICTURES to see products.",
    "greeting_pg": "Hello my customer, welcome to {biz}! Type PRICE for price, PICTURES to see products.",
}

def get_client_by_phone_id(phone_id):
    for c in load_clients():
        if str(c.get("phone_id")) == str(phone_id): return c
    return None

def get_owner_phone_for_client(client):
    if client.get("mode")=="onboarding":
        return OWNER_PHONE
    owner = client.get("owner_phone","").strip()
    if owner:
        return normalize_phone(owner)
    return OWNER_PHONE

def send_owner_alert(phone_id, client, customer_phone, customer_msg):
    try:
        biz = client.get("business", "Unknown")
        target = get_owner_phone_for_client(client)
        alert = f"🔔 HUMAN ALERT - {biz}\n\nCustomer: {customer_phone}\nWants to talk to a human.\nLast message: {customer_msg}\n\nBot has paused for this customer. Go to Dashboard to reply, or reply here on WhatsApp.\nTo give back to bot, go Dashboard > Give Back to Bot."
        send_text(phone_id, target, alert)
        print(f"Owner alert sent to {target} for {biz}")
    except Exception as e:
        print("OWNER ALERT ERROR:", e)

def is_admin(): return session.get("user_type")=="admin"
def is_client(): return session.get("user_type")=="client"
def current_client_id(): return session.get("client_id")
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_type" not in session: return redirect("/dashboard/login")
        return f(*args, **kwargs)
    return decorated
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("user_type")!="admin": return "You do not have permission to do this. Only Verticum Admin can do it. <a href='/dashboard'>Back</a>", 403
        return f(*args, **kwargs)
    return decorated
def can_access_client(client_id):
    if is_admin(): return True
    if is_client() and current_client_id()==client_id: return True
    return False

def handle_onboarding(client, customer_phone, msg):
    state = ONBOARDING.get(customer_phone, {"step": 0, "data": {}})
    lang = USER_LANG.get(customer_phone, "en")
    low = msg.lower().strip()
    pg = (lang == "pg")
    HUMAN_HINT = ""
    if state["step"] == 0:
        ONBOARDING[customer_phone] = {"step": 1, "data": {}}
        base = "Welcome to Verticum! We dey set up Auto WhatsApp for businesses. Wetin be your business name?" if pg else "Welcome to Verticum! We set up Auto WhatsApp reply for businesses. What is your business name?"
        return base + HUMAN_HINT
    if state["step"] == 1:
        state["data"]["business"] = msg; state["step"] = 2; ONBOARDING[customer_phone]=state
        base = "Wetin you dey sell? (e.g. Wigs, Food, Clothes) Or wetin be your service? (e.g. Makeup, Hair Braiding, Cleaning)" if pg else "What do you sell? (e.g. Wigs, Food, Clothes) Or what is your service? (e.g. Makeup, Hair Braiding, Cleaning)"
        return base + HUMAN_HINT
    if state["step"] == 2:
        state["data"]["products_text"] = msg; state["step"]=3; ONBOARDING[customer_phone]=state
        base = "Where you dey / You dey deliver?" if pg else "Where are you located / Do you deliver?"
        return base + HUMAN_HINT
    if state["step"] == 3:
        state["data"]["location"]=msg; state["step"]=4; ONBOARDING[customer_phone]=state
        biz = state["data"].get("business","your business")
        if pg: base = f"Thank you for {biz}! We fit set up your own Auto WhatsApp like this one for ₦50,000 one-time setup + ₦10,000/month. E go answer PRICE, PICTURES and HUMAN for you, for English and Pidgin, 24/7.\n\nYou want make we proceed? Reply YES"
        else: base = f"Thank you for {biz}! We can set up your own Auto WhatsApp like this one for ₦50,000 one-time setup + ₦10,000/month. It will answer PRICE, PICTURES and HUMAN for you, in English and Pidgin, 24/7.\n\nDo you want us to proceed? Reply YES"
        return base + HUMAN_HINT
    if state["step"] == 4:
        if low in ["yes","y","yes please","proceed","ok","okay"]:
            state["step"]=5; state["data"]["agreed_price"]="₦50,000 setup + ₦10,000/month"; state["data"]["pictures"]=[]; state["data"]["products"]=[]; state["data"]["pending_image"]=None; ONBOARDING[customer_phone]=state
            base = "Great! Now send pictures of your products — one by one. After each picture, I go ask you for its name and price. When you don finish, type DONE. If you offer SERVICES and you no get pictures, type SKIP." if pg else "Great! Now send pictures of your products — one by one. After each picture, I will ask you for its name and price. When you are done, type DONE. If you offer SERVICES and have no pictures, type SKIP."
            return base + HUMAN_HINT
        return "Reply YES to proceed with ₦50,000 setup + ₦10,000/month." if pg else "Reply YES to proceed with ₦50,000 setup + ₦10,000/month."
    if state["step"] == 5:
        if low in ["skip", "no pictures", "no picture", "i have no pictures", "services", "service", "no pics"]:
            state["step"] = 5.5; ONBOARDING[customer_phone] = state
            if pg: return "No wahala! Type your services one by one like this:\n\nAdult Hair Cut 3k\n\nSend one by one. When you don finish, type DONE." + HUMAN_HINT
            return "No problem! Type your services one by one like this:\n\nAdult Hair Cut 3k\n\nSend one by one. When you are done, type DONE." + HUMAN_HINT
        if state["data"].get("pending_image"):
            if low=="done": return "You never give me price for last picture. Wetin be name and price for am? e.g. Frontal 65k" if pg else "You haven't given me the price for the last picture yet. What is the name and price for it? e.g. Frontal 65k"
            name, price = parse_price_text(msg)
            prod = {"name": name, "price": price, "image": state["data"]["pending_image"]}
            state["data"]["products"].append(prod); state["data"]["pictures"].append(state["data"]["pending_image"]); state["data"]["pending_image"]=None; ONBOARDING[customer_phone]=state
            count=len(state["data"]["products"])
            return f"Saved! {name} - {price} + picture {count} don join. Send next picture, or type DONE if you don finish." if pg else f"Saved! {name} - {price} + picture {count} paired. Send next picture, or type DONE if you are done."
        if low=="done":
            state["step"]=6; state["data"]["faqs"]=[]; ONBOARDING[customer_phone]=state
            if pg: return "Products don finish! Now, wetin customers dey always ask you? Type question + answer like this:\n\nQ: Do you deliver to Lagos?\nA: Yes, we deliver to Lagos for ₦3,000\n\nSend one by one. When you don finish, type DONE." + HUMAN_HINT
            return "Products done! Now, what do customers always ask you? Type question + answer like this:\n\nQ: Do you deliver to Lagos?\nA: Yes, we deliver to Lagos for ₦3,000\n\nSend one by one. When you are done, type DONE." + HUMAN_HINT
        return "Send your product picture now. After you send am, I go ask for its name and price. If you offer SERVICES and you no get pictures, type SKIP." if pg else "Send your product picture now. After you send it, I will ask for its name and price. If you offer SERVICES and have no pictures, type SKIP."
    if state["step"] == 5.5:
        if low=="done":
            state["step"]=6; state["data"]["faqs"]=[]; ONBOARDING[customer_phone]=state
            if pg: return "Services don finish! Now, wetin customers dey always ask you? Type question + answer like this:\n\nQ: Do you deliver to Lagos?\nA: Yes, we deliver to Lagos for ₦3,000\n\nSend one by one. When you don finish, type DONE." + HUMAN_HINT
            return "Services done! Now, what do customers always ask you? Type question + answer like this:\n\nQ: Do you deliver to Lagos?\nA: Yes, we deliver to Lagos for ₦3,000\n\nSend one by one. When you are done, type DONE." + HUMAN_HINT
        name, price = parse_price_text(msg)
        prod = {"name": name, "price": price, "image": ""}
        state["data"]["products"].append(prod); ONBOARDING[customer_phone]=state
        count=len(state["data"]["products"])
        return f"Saved service! {name} - {price}. Send next service, or type DONE if you don finish." if pg else f"Saved service! {name} - {price}. Send next service, or type DONE if you are done."
    if state["step"] == 6:
        if low=="done":
            state["step"]=6.5; ONBOARDING[customer_phone]=state
            if pg: return "FAQs don finish! Last question:\n\nWetin be your PERSONAL WhatsApp number wey we go alert when your customer type HUMAN?\n\nNo be your business number o, na your private number wey customers no go see. E.g. 08039113437"
            return "FAQs done! Last question:\n\nWhat is your PERSONAL WhatsApp number we should alert when your customer types HUMAN?\n\nNot your business number, your private number that customers will not see. E.g. 08039113437"
        faq_text = msg
        state["data"]["faqs"].append({"q": faq_text, "a": faq_text})
        if "a:" in msg.lower():
            parts = re.split(r'a:', msg, flags=re.IGNORECASE)
            q = parts[0].replace("Q:","").replace("q:","").strip()
            a = parts[1].strip() if len(parts)>1 else faq_text
            state["data"]["faqs"][-1] = {"q": q, "a": a}
        ONBOARDING[customer_phone]=state
        return f"Saved FAQ {len(state['data']['faqs'])}! Send next question + answer, or type DONE if you don finish." if pg else f"Saved FAQ {len(state['data']['faqs'])}! Send next question + answer, or type DONE if you are done."
    if state["step"] == 6.5:
        personal = normalize_phone(msg)
        state["data"]["owner_phone"] = personal
        biz_clean = state["data"]["business"].lower().replace(" ","")
        state["data"]["login_username"] = biz_clean
        state["data"]["login_password"] = biz_clean + "123"
        state["step"]=7; ONBOARDING[customer_phone]=state
        clients=load_clients()
        new_id = state["data"]["business"].lower().replace(" ","").replace(".","")
        clients.append({"id": new_id, "business": state["data"]["business"], "mode":"client", "active":False, "status":"pending", "onboarding_data": state["data"], "phone_id":"", "owner_phone": personal, "login_username": biz_clean, "login_password": biz_clean+"123", "products": state["data"].get("products",[]), "faqs": state["data"].get("faqs",[])})
        save_clients(clients)
        if client["id"] in CHATS and customer_phone in CHATS[client["id"]]:
            if new_id not in CHATS: CHATS[new_id] = {}
            CHATS[new_id][customer_phone] = CHATS[client["id"]][customer_phone].copy()
            save_chats(CHATS)
        count=len(state["data"].get("products",[])); fcount=len(state["data"].get("faqs",[]))
        if pg: return f"Perfect! We don save {state['data']['business']} + {count} product(s)/service(s) + {fcount} FAQ(s) + your personal alert number {personal}.\n\nYour Dashboard Login:\nUsername: {biz_clean}\nPassword: {biz_clean}123\n\nOur team go contact you to connect your BUSINESS number (the one customers go chat) and launch your bot. If you still wan talk to person, type HUMAN now. Thank you!"
        return f"Perfect! We have saved {state['data']['business']} + {count} product(s)/service(s) + {fcount} FAQ(s) + your personal alert number {personal}.\n\nYour Dashboard Login:\nUsername: {biz_clean}\nPassword: {biz_clean}123\n\nOur team will contact you to connect your BUSINESS number (the one customers will chat) and launch your bot. If you still want to talk to a person, type HUMAN now. Thank you!"
    return "Type HELLO to start again."

def handle_client_bot(client, customer_phone, msg, phone_id):
    lang = USER_LANG.get(customer_phone, "en")
    low = msg.lower().strip()
    biz = client["business"]
    if low in ["english", "change to english"]:
        USER_LANG[customer_phone]="en"; return "Language changed to English. Type PRICE to see prices, PICTURES to see products."
    if low in ["pidgin", "change to pidgin"]:
        USER_LANG[customer_phone]="pg"; return "Language don change to Pidgin. Type PRICE for price, PICTURES to see products."
    for faq in client.get("faqs", []):
        q = faq.get("q","").lower()
        keywords = [w for w in re.findall(r'\w+', q) if len(w)>3]
        matches = sum(1 for k in keywords if k in low)
        if matches >= 2 or (keywords and all(k in low for k in keywords[:2])):
            return faq.get("a", "")
        if any(word in low and word in q for word in ["deliver","delivery","location","where","payment","pay","installment","return","exchange","available","stock","how long","last"]):
            if matches >= 1:
                return faq.get("a", "")
    if low in ["price","prices","how much"]:
        SEEN_PRODUCTS.add((client["id"], customer_phone))
        lines=[]
        for p in client.get("products",[]):
            lines.append(f"{p['name']} - {p['price']}")
        if not lines: return "Our prices go soon dey here." if lang=="pg" else "Our prices will be here soon."
        if lang=="pg": return "Our prices be:\n"+"\n".join(lines)+"\nType PICTURES to see dem.\n\nIf you wan talk to person, type HUMAN."
        return "Our prices are:\n"+"\n".join(lines)+"\nType PICTURES to see them.\n\nIf you want to talk to a person, type HUMAN."
    if low in ["pictures","picture","send pictures","show me pictures","images","services","service list","menu"]:
        SEEN_PRODUCTS.add((client["id"], customer_phone))
        has_images = False
        for p in client.get("products",[]):
            if p.get("image"):
                has_images = True
                send_image(phone_id, customer_phone, p["image"], f"{p['name']} - {p['price']}")
        if has_images:
            return "I don send the pictures above. Which one you want?\n\nIf you wan talk to person, type HUMAN." if lang=="pg" else "I have sent the pictures above. Which one would you like?\n\nIf you want to talk to a person, type HUMAN."
        lines=[]
        for p in client.get("products",[]):
            lines.append(f"{p['name']} - {p['price']}")
        if lines:
            if lang=="pg": return "We offer services, we no get pictures yet:\n"+"\n".join(lines)+"\nWhich one you want?\n\nIf you wan talk to person, type HUMAN."
            return "We offer these services (no pictures yet):\n"+"\n".join(lines)+"\nWhich one would you like?\n\nIf you want to talk to a person, type HUMAN."
        return "We no get pictures yet. Type PRICE for our services." if lang=="pg" else "We don't have pictures yet. Type PRICE for our services."
    if low in ["hello","hi","hey"]:
        return client.get("greeting_pg", f"Hello, welcome to {biz}! Type PRICE for price, PICTURES to see products.") if lang=="pg" else client.get("greeting_en", f"Hello, welcome to {biz}! Type PRICE to see prices, PICTURES to see products.")
    return "I no understand. Type PRICE for prices, PICTURES to see products." if lang=="pg" else "I didn't understand. Type PRICE for prices, PICTURES to see products."

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token")==VERIFY_TOKEN: return request.args.get("hub.challenge")
    return "Invalid",403

@app.route("/webhook", methods=["POST"])
def webhook():
    data=request.get_json()
    print(data)
    try:
        entry=data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry: return "ok",200
        msg_obj=entry["messages"][0]
        customer_phone=msg_obj["from"]
        phone_id=entry["metadata"]["phone_number_id"]
        client=get_client_by_phone_id(phone_id)
        if not client:
            print(f"No client found for phone_id {phone_id}")
            return "ok",200
        if not client.get("active",True):
            print(f"Client {client['business']} is inactive")
            return "ok",200
        if msg_obj.get("type")=="image":
            media_id=msg_obj["image"]["id"]
            add_chat(client["id"], customer_phone, {"from":"customer","type":"image","media_id":media_id})
            if client.get("mode")=="onboarding" and ONBOARDING.get(customer_phone,{}).get("step")==5:
                if ONBOARDING[customer_phone]["data"].get("pending_image"):
                    lang=USER_LANG.get(customer_phone,"en")
                    reply="You never give me price for last picture yet. Wetin be name and price for am? e.g. Frontal 65k" if lang=="pg" else "You haven't given me the price for the last picture yet. What is the name and price for it? e.g. Frontal 65k"
                    send_text(phone_id,customer_phone,reply); return "ok",200
                biz_name=ONBOARDING[customer_phone]["data"].get("business","verticum")
                cloud_url=download_whatsapp_media_and_upload(media_id,biz_name)
                if not cloud_url: cloud_url=f"media_id:{media_id}"
                ONBOARDING[customer_phone]["data"]["pending_image"]=cloud_url
                lang=USER_LANG.get(customer_phone,"en")
                reply="Picture saved to Cloudinary! Wetin be name and price for this picture? e.g. Frontal 65k" if lang=="pg" else "Picture saved to Cloudinary! What is the name and price for this picture? e.g. Frontal 65k"
                send_text(phone_id,customer_phone,reply); add_chat(client["id"], customer_phone, {"from":"bot","text":reply})
            else:
                send_text(phone_id,customer_phone,"Thank you, we have received your picture. Our team will check it.")
            return "ok",200
        msg_text=msg_obj.get("text",{}).get("body","")
        add_chat(client["id"], customer_phone, {"from":"customer","text":msg_text})
        low_text = msg_text.lower().strip()
        if low_text in ["human","person","agent","talk to person","talk to human","i want human","speak to human"]:
            cust_lang = USER_LANG.get(customer_phone, "en")
            if client.get("mode")=="onboarding":
                if ONBOARDING.get(customer_phone, {}).get("step", 0) >= 7:
                    TAKEOVER.add((client["id"], customer_phone))
                    reply = "Person go reply you soon. Abeg wait small." if cust_lang=="pg" else "A human will reply you shortly. Please wait."
                    send_text(phone_id, customer_phone, reply)
                    add_chat(client["id"], customer_phone, {"from":"bot","text":reply})
                    send_owner_alert(phone_id, client, customer_phone, msg_text)
                    return "ok",200
                count = HUMAN_ATTEMPT_ONBOARDING.get(customer_phone, 0) + 1
                HUMAN_ATTEMPT_ONBOARDING[customer_phone] = count
                if count == 1:
                    if cust_lang=="pg": reply = "I fit help you finish this sharp sharp, na 2 minutes remain. Make we continue — wetin be the next thing? Type DONE if you don finish this step."
                    else: reply = "I can help you finish this quickly, only 2 minutes left. Let's continue — what is the next thing? Type DONE if you have finished this step."
                    send_text(phone_id, customer_phone, reply)
                    add_chat(client["id"], customer_phone, {"from":"bot","text":reply})
                    return "ok",200
                TAKEOVER.add((client["id"], customer_phone))
                reply = "Person go reply you soon. Abeg wait small." if cust_lang=="pg" else "A human will reply you shortly. Please wait."
                send_text(phone_id, customer_phone, reply)
                add_chat(client["id"], customer_phone, {"from":"bot","text":reply})
                send_owner_alert(phone_id, client, customer_phone, msg_text)
                return "ok",200
            if (client["id"], customer_phone) not in SEEN_PRODUCTS:
                lines=[]
                for p in client.get("products",[]):
                    lines.append(f"{p['name']} - {p['price']}")
                SEEN_PRODUCTS.add((client["id"], customer_phone))
                if lines:
                    if cust_lang=="pg": reply = "Make you first see wetin we get:\n"+"\n".join(lines)+"\n\nIf you still wan talk to person after you see am, type HUMAN again."
                    else: reply = "Let me first show you what we have:\n"+"\n".join(lines)+"\n\nIf you still want to talk to a person after seeing this, type HUMAN again."
                else:
                    reply = "Make you first tell me wetin you want. Type PRICE or PICTURES." if cust_lang=="pg" else "Let me first show you what we have. Type PRICE or PICTURES."
                send_text(phone_id, customer_phone, reply)
                add_chat(client["id"], customer_phone, {"from":"bot","text":reply})
                return "ok",200
            TAKEOVER.add((client["id"], customer_phone))
            reply = "Person go reply you soon. Abeg wait small." if cust_lang=="pg" else "A human will reply you shortly. Please wait. A person has been alerted."
            send_text(phone_id, customer_phone, reply)
            add_chat(client["id"], customer_phone, {"from":"bot","text":reply})
            send_owner_alert(phone_id, client, customer_phone, msg_text)
            return "ok",200
        if (client["id"],customer_phone) in TAKEOVER:
            try:
                target = get_owner_phone_for_client(client)
                send_text(phone_id, target, f"💬 New message from {customer_phone} ({client.get('business')}) while waiting for human:\n\n{msg_text}")
            except: pass
            return "ok",200
        if customer_phone not in USER_LANG:
            if msg_text.strip()=="1":
                USER_LANG[customer_phone]="en"
                if client.get("mode")=="onboarding": ONBOARDING[customer_phone]={"step":1,"data":{}}; reply="Welcome to Verticum! We set up Auto WhatsApp reply for businesses. What is your business name?"
                else: reply=TEXTS["greeting_en"].format(biz=client["business"])
            elif msg_text.strip()=="2":
                USER_LANG[customer_phone]="pg"
                if client.get("mode")=="onboarding": ONBOARDING[customer_phone]={"step":1,"data":{}}; reply="Welcome to Verticum! We dey set up Auto WhatsApp for businesses. Wetin be your business name?"
                else: reply=TEXTS["greeting_pg"].format(biz=client["business"])
            else: reply=TEXTS["choose_lang"].format(biz=client["business"])
            send_text(phone_id,customer_phone,reply); add_chat(client["id"], customer_phone, {"from":"bot","text":reply}); return "ok",200
        if client.get("mode")=="onboarding": reply=handle_onboarding(client,customer_phone,msg_text)
        else: reply=handle_client_bot(client,customer_phone,msg_text,phone_id)
        send_text(phone_id,customer_phone,reply); add_chat(client["id"], customer_phone, {"from":"bot","text":reply})
    except Exception as e: print("ERROR:",e)
    return "ok",200

LOGIN_HTML = """
<h2>Verticum Dashboard - Login</h2>
{% if error %}<p style='color:red'>{{error}}</p>{% endif %}
<form method='POST'>
  Username: <input name='username' required><br><br>
  Password: <input type='password' name='password' required><br><br>
  <button>Login</button>
</form>
<p>Admin: use Verticum login. Client: use username/password wey Verticum give you.</p>
"""
@app.route("/dashboard/login", methods=["GET","POST"])
def dashboard_login():
    if request.method=="POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","").strip()
        if u==ADMIN_USER and p==ADMIN_PASS:
            session["user_type"]="admin"; session["username"]=u; session.pop("client_id", None); return redirect("/dashboard")
        for c in load_clients():
            if c.get("login_username")==u and c.get("login_password")==p:
                session["user_type"]="client"; session["username"]=u; session["client_id"]=c.get("id"); session["business"]=c.get("business"); return redirect("/dashboard")
        return render_template_string(LOGIN_HTML, error="Wrong username or password. Check am again.")
    return render_template_string(LOGIN_HTML, error=None)
@app.route("/dashboard/logout")
def dashboard_logout():
    session.clear(); return redirect("/dashboard/login")

DASHBOARD_ADMIN_HTML = """
<h2>Verticum Dashboard - ADMIN ({{session.username}})</h2>
<p><a href='/dashboard/logout'>Logout</a> | <a href='/dashboard/add-client'>+ Add New Client</a> | <a href='/dashboard/all-chats'>View ALL Chats</a></p>
<p><b>Verticum Owner alerts go to:</b> 08039113437 | <b>Each client alert goes to their own Personal Number</b></p>
<h3>All Clients</h3>
{% for c in clients %}
  <div style='border:1px solid #ccc;padding:12px;margin:10px'>
    <b>{{c.business}}</b> - Mode: {{c.mode}} - Active: {{c.active}} - Status: {{c.get('status','pending')}}<br>
    <b>Business Phone ID (Meta - customers chat this):</b> {{c.phone_id or '(Not set yet)'}}<br>
    <b>Personal Alert Number (private):</b> {{c.get('owner_phone','(Not set)')}}<br>
    <b>Dashboard Login:</b> Username: <b>{{c.get('login_username','(not set)')}}</b> / Password: <b>{{c.get('login_password','(not set)')}}</b><br>
    <form method='POST' action='/dashboard/update-client/{{c.id}}' style='margin:6px 0'>
      Business Phone ID: <input name='phone_id' value='{{c.phone_id}}' style='width:200px'>
      Personal Alert: <input name='owner_phone' value='{{c.get('owner_phone','')}}' style='width:150px' placeholder='0803...'>
      <br>Login Username: <input name='login_username' value='{{c.get('login_username','')}}' style='width:150px'>
      Login Password: <input name='login_password' value='{{c.get('login_password','')}}' style='width:150px'>
      <button>Save All</button>
    </form>
    Products: {{c.get('products', [])}}<br>
    FAQs: {{c.get('faqs', [])}}<br><br>
    <a href='/dashboard/client/{{c.id}}'>View Chats + Reply</a> |
    {% if c.active %}<a href='/dashboard/deactivate/{{c.id}}'>Deactivate</a> |{% else %}<a href='/dashboard/activate/{{c.id}}'>Activate</a> |{% endif %}
    <a href='/dashboard/delete/{{c.id}}' onclick="return confirm('Delete {{c.business}}?')">Delete</a>
  </div>
{% endfor %}
"""
DASHBOARD_CLIENT_HTML = """
<h2>{{client.business}} Dashboard - Welcome {{session.username}}</h2>
<p><a href='/dashboard/logout'>Logout</a></p>
<p>Your Business Phone ID (Meta): {{client.phone_id or '(Verticum go set am)'}}<br>
Your Personal Alert Number: {{client.get('owner_phone','(Not set - tell Verticum)')}}<br>
Status: {{client.get('status','pending')}} - Active: {{client.active}}</p>
<p>Your Products: {{client.get('products', [])}}<br>Your FAQs: {{client.get('faqs', [])}}</p>
<p><a href='/dashboard/client/{{client.id}}'>View My Chats + Reply to Customers</a></p>
<p><b>Note:</b> You only see your own business. You no fit see other Verticum clients.</p>
"""
@app.route("/dashboard")
@login_required
def dashboard():
    if is_admin(): return render_template_string(DASHBOARD_ADMIN_HTML, clients=load_clients())
    else:
        clients = load_clients()
        for c in clients:
            if c.get("id")==current_client_id(): return render_template_string(DASHBOARD_CLIENT_HTML, client=c)
        return "Client not found. <a href='/dashboard/logout'>Logout</a>"
@app.route("/dashboard/all-chats")
@login_required
@admin_required
def all_chats():
    chats = load_chats()
    html = "<h2>ALL Chats (Admin Only)</h2><a href='/dashboard'>Back</a><br><br>"
    for client_id, phones in chats.items():
        html += f"<h3>Client ID: {client_id}</h3>"
        for phone, msgs in phones.items():
            html += f"<h4>{phone} <a href='/dashboard/client/{client_id}'>View + Reply</a></h4>"
            for m in msgs:
                if m.get("type")=="image": html += f"<p><b>{m.get('from')}:</b> [IMAGE sent]</p>"
                else: html += f"<p><b>{m.get('from')}:</b> {m.get('text')}</p>"
    if not chats: html += "<p>No chats saved yet.</p>"
    return html
@app.route("/dashboard/client/<client_id>")
@login_required
def client_chats(client_id):
    if not can_access_client(client_id): return "You no fit see this client chats. You only fit see your own. <a href='/dashboard'>Back to Your Dashboard</a>", 403
    chats = load_chats(); clients = load_clients(); biz_name = client_id
    for c in clients:
        if c.get("id")==client_id: biz_name = c.get("business", client_id)
    html=f"<h2>{biz_name} Chats - Reply from here</h2><a href='/dashboard'>Back</a><br><br>"
    if client_id in chats:
        for phone,msgs in chats[client_id].items():
            is_paused = (client_id, phone) in TAKEOVER
            status = "⏸️ BOT PAUSED - Human in charge" if is_paused else "🤖 Bot Active"
            html+=f"<div style='border:1px solid #999;padding:10px;margin:10px'><h3>{phone} - {status}</h3>"
            html+=f"<a href='/dashboard/takeover/{client_id}/{phone}'>Take Over (Pause Bot)</a> | <a href='/dashboard/giveback/{client_id}/{phone}'>Give Back to Bot (Resume)</a><br><br>"
            for m in msgs:
                if m.get("type")=="image": html+=f"<p><b>{m.get('from')}:</b> [IMAGE]</p>"
                else: html+=f"<p><b>{m.get('from')}:</b> {m.get('text')}</p>"
            html+=f"""
            <form method='POST' action='/dashboard/reply/{client_id}/{phone}' style='margin-top:10px'>
              <input name='message' placeholder='Type your reply to {phone}...' style='width:70%;padding:8px' required>
              <button style='padding:8px'>Send Reply to WhatsApp</button>
            </form>
            </div>
            """
    else: html+=f"<p>No chats yet for {biz_name}.</p>"
    return html
@app.route("/dashboard/reply/<client_id>/<phone>", methods=["POST"])
@login_required
def dashboard_reply(client_id, phone):
    if not can_access_client(client_id): return "You no fit reply this client. <a href='/dashboard'>Back</a>", 403
    msg = request.form.get("message","").strip()
    if not msg: return redirect(f"/dashboard/client/{client_id}")
    clients = load_clients(); phone_id = None
    for c in clients:
        if c.get("id")==client_id: phone_id = c.get("phone_id")
    if not phone_id:
        for c in clients:
            if c.get("mode")=="onboarding": phone_id = c.get("phone_id")
    if phone_id:
        send_text(phone_id, phone, msg)
        add_chat(client_id, phone, {"from":"you (owner)","text":msg})
    else: return f"No Phone ID found for {client_id}. Set Phone ID first in dashboard. <a href='/dashboard'>Back</a>"
    return redirect(f"/dashboard/client/{client_id}")
@app.route("/dashboard/update-client/<client_id>", methods=["POST"])
@login_required
@admin_required
def update_client(client_id):
    clients=load_clients()
    for c in clients:
        if c.get("id")==client_id:
            c["phone_id"]=request.form.get("phone_id","").strip()
            c["owner_phone"]=normalize_phone(request.form.get("owner_phone","").strip())
            c["login_username"]=request.form.get("login_username","").strip()
            c["login_password"]=request.form.get("login_password","").strip()
    save_clients(clients); return redirect("/dashboard")
@app.route("/dashboard/update-phone/<client_id>", methods=["POST"])
@login_required
@admin_required
def update_phone(client_id): return update_client(client_id)
@app.route("/dashboard/takeover/<client_id>/<phone>")
@login_required
def takeover(client_id,phone):
    if not can_access_client(client_id): return "No permission", 403
    TAKEOVER.add((client_id,phone)); return f"Taken over {phone}. Bot paused. You go reply am now. <a href='/dashboard/client/{client_id}'>Back to Chats + Reply</a>"
@app.route("/dashboard/giveback/<client_id>/<phone>")
@login_required
def giveback(client_id,phone):
    if not can_access_client(client_id): return "No permission", 403
    TAKEOVER.discard((client_id,phone)); return f"Gave back {phone} to bot. Bot go reply am again. <a href='/dashboard/client/{client_id}'>Back to Chats</a>"
@app.route("/dashboard/delete/<client_id>")
@login_required
@admin_required
def delete_client(client_id):
    clients=load_clients(); clients=[c for c in clients if c.get("id")!=client_id]; save_clients(clients)
    chats=load_chats(); chats.pop(client_id,None); save_chats(chats); return redirect("/dashboard")
@app.route("/dashboard/activate/<client_id>")
@login_required
@admin_required
def activate_client(client_id):
    clients=load_clients()
    for c in clients:
        if c.get("id")==client_id: c["active"]=True; c["status"]="active"
    save_clients(clients); return redirect("/dashboard")
@app.route("/dashboard/deactivate/<client_id>")
@login_required
@admin_required
def deactivate_client(client_id):
    clients=load_clients()
    for c in clients:
        if c.get("id")==client_id: c["active"]=False; c["status"]="paused"
    save_clients(clients); return redirect("/dashboard")
@app.route("/dashboard/add-client", methods=["GET","POST"])
@login_required
@admin_required
def add_client():
    if request.method=="POST":
        clients=load_clients(); biz = request.form["business"]; biz_clean = biz.lower().replace(" ","")
        new={"id": biz.lower().replace(" ","").replace(".",""), "business": biz, "phone_id": request.form["phone_id"], "owner_phone": normalize_phone(request.form.get("owner_phone","")), "login_username": request.form.get("login_username") or biz_clean, "login_password": request.form.get("login_password") or (biz_clean+"123"), "mode":"client", "active":True, "status":"active", "products":[{"name": request.form["product_name"], "price": request.form["price"], "image": request.form["image"]}], "faqs":[]}
        clients.append(new); save_clients(clients); return redirect("/dashboard")
    return """
    <h2>Add New Client - Fast Way (Admin Only)</h2>
    <form method='POST'>
      Business Name: <input name='business'><br>
      Business Phone ID (from Meta): <input name='phone_id'><br>
      Personal Alert Number: <input name='owner_phone' placeholder='e.g. 08039113437'><br>
      Dashboard Username: <input name='login_username' placeholder='e.g. sandra'><br>
      Dashboard Password: <input name='login_password' placeholder='e.g. sandra123'><br>
      Product / Service Name: <input name='product_name'><br>
      Price: <input name='price'><br>
      Cloudinary Image Link: <input name='image' placeholder='https://res.cloudinary.com/...'><br>
      <button>Add Client</button>
    </form>
    """
@app.route("/")
def home(): return "Verticum Bot is Live"
if __name__=="__main__": app.run(port=5000)
