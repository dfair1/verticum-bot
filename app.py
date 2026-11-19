import os, json, requests, time, hashlib
from flask import Flask, request, jsonify, render_template_string, session, redirect

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "verticum-secret")

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "verticum-verify")
CLOUDINARY_CLOUD = os.environ.get("CLOUDINARY_CLOUD_NAME", "demo")
CLOUDINARY_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
CLOUDINARY_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")

def load_clients():
    try:
        with open("clients.json") as f:
            return json.load(f)
    except:
        return []

def save_clients(clients):
    with open("clients.json", "w") as f:
        json.dump(clients, f, indent=2)

CHATS = {}
USER_LANG = {}
ONBOARDING = {}
TAKEOVER = set()

def send_text(phone_id, to, text):
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    r = requests.post(url, headers=headers, json=payload)
    print("SEND TEXT:", r.text)
    return r

def send_image(phone_id, to, image_url, caption):
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"link": image_url, "caption": caption}}
    r = requests.post(url, headers=headers, json=payload)
    print("SEND IMAGE:", r.text)
    return r

# --- NEW: WhatsApp -> Cloudinary Auto Upload ---
def upload_to_cloudinary(image_bytes, business_name):
    """Upload image bytes to Cloudinary, return secure_url or None"""
    if not CLOUDINARY_KEY or not CLOUDINARY_SECRET:
        print("CLOUDINARY keys missing - skipping upload")
        return None
    try:
        timestamp = int(time.time())
        folder = f"verticum/{business_name.lower().replace(' ','-')}"
        # Signature for Cloudinary: folder + timestamp + secret
        to_sign = f"folder={folder}&timestamp={timestamp}{CLOUDINARY_SECRET}"
        signature = hashlib.sha1(to_sign.encode()).hexdigest()
        url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD}/image/upload"
        files = {"file": ("product.jpg", image_bytes, "image/jpeg")}
        data = {"api_key": CLOUDINARY_KEY, "timestamp": timestamp, "folder": folder, "signature": signature}
        r = requests.post(url, files=files, data=data, timeout=30)
        print("CLOUDINARY:", r.text[:500])
        if r.status_code == 200:
            return r.json().get("secure_url")
        return None
    except Exception as e:
        print("CLOUDINARY ERROR:", e)
        return None

def download_whatsapp_media_and_upload(media_id, business_name):
    """1. Get WhatsApp media URL, 2. Download bytes, 3. Upload to Cloudinary"""
    try:
        # Step 1: Get download URL
        meta_url = f"https://graph.facebook.com/v21.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        r1 = requests.get(meta_url, headers=headers, timeout=20)
        print("MEDIA META:", r1.text)
        download_url = r1.json().get("url")
        if not download_url:
            return None
        # Step 2: Download image bytes
        r2 = requests.get(download_url, headers=headers, timeout=30)
        if r2.status_code != 200:
            return None
        # Step 3: Upload to Cloudinary
        return upload_to_cloudinary(r2.content, business_name)
    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        return None

TEXTS = {
    "choose_lang": "Welcome to {biz}! 👋\nHow do you want me to talk to you?\nReply 1 for English\nReply 2 for Pidgin",
    "greeting_en": "Hello, welcome to {biz}! How can we help you today? Type PRICE for prices, PICTURES to see products, or HUMAN to talk to a person.",
    "greeting_pg": "Hello my customer, welcome to {biz}! How we fit help you today? Type PRICE for price, PICTURES to see products, or HUMAN to talk to person.",
}

def get_client_by_phone_id(phone_id):
    for c in load_clients():
        if str(c.get("phone_id")) == str(phone_id):
            return c
    return None

def handle_onboarding(client, customer_phone, msg):
    state = ONBOARDING.get(customer_phone, {"step": 0, "data": {}})
    lang = USER_LANG.get(customer_phone, "en")
    low = msg.lower().strip()
    pg = (lang == "pg")
    if state["step"] == 0:
        ONBOARDING[customer_phone] = {"step": 1, "data": {}}
        if pg:
            return "Welcome to Verticum! We dey set up Auto WhatsApp for businesses. Wetin be your business name?"
        return "Welcome to Verticum! We set up Auto WhatsApp reply for businesses. What is your business name?"
    if state["step"] == 1:
        state["data"]["business"] = msg
        state["step"] = 2
        ONBOARDING[customer_phone] = state
        return "What do you sell? (e.g. Wigs, Food, Clothes)" if not pg else "Wetin you dey sell? (e.g. Wigs, Food, Clothes)"
    if state["step"] == 2:
        state["data"]["products_text"] = msg
        state["step"] = 3
        ONBOARDING[customer_phone] = state
        return "What are your prices? (e.g. Frontal ₦85k)" if not pg else "How much be your prices? (e.g. Frontal ₦85k)"
    if state["step"] == 3:
        state["data"]["prices"] = msg
        state["step"] = 4
        ONBOARDING[customer_phone] = state
        return "Where are you located / Do you deliver?" if not pg else "Where you dey / You dey deliver?"
    if state["step"] == 4:
        state["data"]["location"] = msg
        state["step"] = 5
        ONBOARDING[customer_phone] = state
        biz = state["data"].get("business", "your business")
        if pg:
            return f"Thank you for {biz}! We fit set up your own Auto WhatsApp like this one for ₦50,000 one-time setup + ₦10,000/month. E go answer PRICE, PICTURES and HUMAN for you, for English and Pidgin, 24/7.\n\nYou want make we proceed? Reply YES"
        return f"Thank you for {biz}! We can set up your own Auto WhatsApp like this one for ₦50,000 one-time setup + ₦10,000/month. It will answer PRICE, PICTURES and HUMAN for you, in English and Pidgin, 24/7.\n\nDo you want us to proceed? Reply YES"
    if state["step"] == 5:
        if low in ["yes", "y", "yes please", "proceed", "ok", "okay"]:
            state["step"] = 6
            state["data"]["agreed_price"] = "₦50,000 setup + ₦10,000/month"
            state["data"]["pictures"] = []
            ONBOARDING[customer_phone] = state
            if pg:
                return "Great! Now send pictures of your products — as many as you sell. Send one by one. When you don finish, type DONE."
            return "Great! Now send pictures of your products — as many as you sell. Send them one by one. When you are done, type DONE."
        if low in ["no", "not now", "later"]:
            if pg:
                return "No wahala. When you ready, just type YES and we go continue. Or type HUMAN to talk to person."
            return "No problem. When you are ready, just type YES and we will continue. Or type HUMAN to talk to a person."
        if pg:
            return "Reply YES to proceed with ₦50,000 setup + ₦10,000/month, or type HUMAN to talk to person."
        return "Reply YES to proceed with ₦50,000 setup + ₦10,000/month, or type HUMAN to talk to a person."
    if state["step"] == 6:
        if low == "done":
            state["step"] = 7
            ONBOARDING[customer_phone] = state
            clients = load_clients()
            clients.append({"id": state["data"]["business"].lower().replace(" ",""), "business": state["data"]["business"], "mode": "client", "active": False, "status": "pending", "onboarding_data": state["data"], "phone_id": "", "products": []})
            save_clients(clients)
            count = len(state["data"].get("pictures", []))
            if pg:
                return f"Perfect! We don save {state['data']['business']} + {count} picture(s). Our team go contact you to connect your number and launch your bot. Thank you!"
            return f"Perfect! We have saved {state['data']['business']} + {count} picture(s). Our team will contact you to connect your number and launch your bot. Thank you!"
        if pg:
            return "Send your product pictures now, one by one. When you don finish, type DONE."
        return "Send your product pictures now, one by one. When you are done, type DONE."
    return "Type HELLO to start again."

def handle_client_bot(client, customer_phone, msg, phone_id):
    lang = USER_LANG.get(customer_phone, "en")
    low = msg.lower().strip()
    biz = client["business"]
    if low in ["price", "prices", "how much"]:
        lines = []
        for p in client.get("products", []):
            lines.append(f"{p['name']} - {p['price']}")
        if lang=="pg":
            return "Our prices be:\n" + "\n".join(lines) + "\nType PICTURES to see dem."
        return "Our prices are:\n" + "\n".join(lines) + "\nType PICTURES to see them."
    if low in ["pictures", "picture", "send pictures", "show me pictures", "images"]:
        for p in client.get("products", []):
            if p.get("image"):
                send_image(phone_id, customer_phone, p["image"], f"{p['name']} - {p['price']}")
        if lang=="pg":
            return "I don send the pictures above. Which one you want?"
        return "I have sent the pictures above. Which one would you like?"
    if low in ["human", "person", "agent", "talk to person"]:
        TAKEOVER.add((client["id"], customer_phone))
        return "A human will reply you shortly. Please wait." if lang=="en" else "Person go reply you soon. Abeg wait small."
    if low in ["hello", "hi", "hey"]:
        if lang=="pg":
            return client.get("greeting_pg", f"Hello, welcome to {biz}!")
        return client.get("greeting_en", f"Hello, welcome to {biz}!")
    if lang=="pg":
        return "I no understand. Type PRICE for prices, PICTURES to see products, HUMAN to talk to person."
    return "I didn't understand. Type PRICE for prices, PICTURES to see products, HUMAN to talk to a person."

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Invalid", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print(data)
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry:
            return "ok", 200
        msg_obj = entry["messages"][0]
        customer_phone = msg_obj["from"]
        phone_id = entry["metadata"]["phone_number_id"]
        client = get_client_by_phone_id(phone_id)
        if not client or not client.get("active", True):
            return "ok", 200
        CHATS.setdefault(client["id"], {}).setdefault(customer_phone, [])
        if msg_obj.get("type") == "image":
            media_id = msg_obj["image"]["id"]
            CHATS[client["id"]][customer_phone].append({"from": "customer", "type": "image", "media_id": media_id})
            if client.get("mode") == "onboarding" and ONBOARDING.get(customer_phone, {}).get("step") == 6:
                biz_name = ONBOARDING[customer_phone]["data"].get("business", "verticum")
                # NEW: Auto upload to Cloudinary
                cloud_url = download_whatsapp_media_and_upload(media_id, biz_name)
                if cloud_url:
                    ONBOARDING[customer_phone]["data"].setdefault("pictures", []).append(cloud_url)
                else:
                    ONBOARDING[customer_phone]["data"].setdefault("pictures", []).append(f"media_id:{media_id}")
                count = len(ONBOARDING[customer_phone]["data"]["pictures"])
                lang = USER_LANG.get(customer_phone, "en")
                if lang == "pg":
                    reply = f"Picture {count} saved to Cloudinary! Send another one, or type DONE if you don finish."
                else:
                    reply = f"Picture {count} saved to Cloudinary! Send another one, or type DONE if you are done."
                send_text(phone_id, customer_phone, reply)
                CHATS[client["id"]][customer_phone].append({"from": "bot", "text": reply})
            else:
                send_text(phone_id, customer_phone, "Thank you, we have received your picture. Our team will check it.")
            return "ok", 200
        msg_text = msg_obj.get("text", {}).get("body", "")
        CHATS[client["id"]][customer_phone].append({"from": "customer", "text": msg_text})
        if (client["id"], customer_phone) in TAKEOVER:
            return "ok", 200
        if customer_phone not in USER_LANG:
            if msg_text.strip() == "1":
                USER_LANG[customer_phone] = "en"
                if client.get("mode") == "onboarding":
                    ONBOARDING[customer_phone] = {"step": 1, "data": {}}
                    reply = "Welcome to Verticum! We set up Auto WhatsApp reply for businesses. What is your business name?"
                else:
                    reply = TEXTS["greeting_en"].format(biz=client["business"])
            elif msg_text.strip() == "2":
                USER_LANG[customer_phone] = "pg"
                if client.get("mode") == "onboarding":
                    ONBOARDING[customer_phone] = {"step": 1, "data": {}}
                    reply = "Welcome to Verticum! We dey set up Auto WhatsApp for businesses. Wetin be your business name?"
                else:
                    reply = TEXTS["greeting_pg"].format(biz=client["business"])
            else:
                reply = TEXTS["choose_lang"].format(biz=client["business"])
            send_text(phone_id, customer_phone, reply)
            CHATS[client["id"]][customer_phone].append({"from": "bot", "text": reply})
            return "ok", 200
        if client.get("mode") == "onboarding":
            reply = handle_onboarding(client, customer_phone, msg_text)
        else:
            reply = handle_client_bot(client, customer_phone, msg_text, phone_id)
        send_text(phone_id, customer_phone, reply)
        CHATS[client["id"]][customer_phone].append({"from": "bot", "text": reply})
    except Exception as e:
        print("ERROR:", e)
    return "ok", 200

DASHBOARD_HTML = """
<h2>Verticum Dashboard</h2>
<p><a href='/dashboard/add-client'>+ Add New Client</a></p>
<h3>All Clients</h3>
{% for c in clients %}
  <div style='border:1px solid #ccc;padding:10px;margin:10px'>
    <b>{{c.business}}</b> - {{c.mode}} - Active: {{c.active}} - Status: {{c.get('status','pending')}} - PhoneID: {{c.phone_id}}<br>
    <a href='/dashboard/client/{{c.id}}'>View Chats</a> |
    {% if c.active %}
      <a href='/dashboard/deactivate/{{c.id}}'>Deactivate</a> |
    {% else %}
      <a href='/dashboard/activate/{{c.id}}'>Activate</a> |
    {% endif %}
    <a href='/dashboard/delete/{{c.id}}' onclick="return confirm('Delete {{c.business}}? This cannot be undone.')">Delete</a>
  </div>
{% endfor %}
"""

@app.route("/dashboard")
def dashboard():
    return render_template_string(DASHBOARD_HTML, clients=load_clients())

@app.route("/dashboard/client/<client_id>")
def client_chats(client_id):
    chats = CHATS.get(client_id, {})
    html = f"<h2>{client_id} Chats</h2><a href='/dashboard'>Back</a><br><br>"
    for phone, msgs in chats.items():
        html += f"<h3>{phone} <a href='/dashboard/takeover/{client_id}/{phone}'>Take Over</a> | <a href='/dashboard/giveback/{client_id}/{phone}'>Give Back to Bot</a></h3>"
        for m in msgs:
            html += f"<p>{m}</p>"
    return html

@app.route("/dashboard/takeover/<client_id>/<phone>")
def takeover(client_id, phone):
    TAKEOVER.add((client_id, phone))
    return f"Taken over {phone}. Bot paused. <a href='/dashboard'>Back</a>"

@app.route("/dashboard/giveback/<client_id>/<phone>")
def giveback(client_id, phone):
    TAKEOVER.discard((client_id, phone))
    return f"Gave back {phone} to bot. <a href='/dashboard'>Back</a>"

@app.route("/dashboard/delete/<client_id>")
def delete_client(client_id):
    clients = load_clients()
    clients = [c for c in clients if c.get("id") != client_id]
    save_clients(clients)
    CHATS.pop(client_id, None)
    return redirect("/dashboard")

@app.route("/dashboard/activate/<client_id>")
def activate_client(client_id):
    clients = load_clients()
    for c in clients:
        if c.get("id") == client_id:
            c["active"] = True
            c["status"] = "active"
    save_clients(clients)
    return redirect("/dashboard")

@app.route("/dashboard/deactivate/<client_id>")
def deactivate_client(client_id):
    clients = load_clients()
    for c in clients:
        if c.get("id") == client_id:
            c["active"] = False
            c["status"] = "paused"
    save_clients(clients)
    return redirect("/dashboard")

@app.route("/dashboard/add-client", methods=["GET","POST"])
def add_client():
    if request.method=="POST":
        clients = load_clients()
        new = {"id": request.form["business"].lower().replace(" ",""), "business": request.form["business"], "phone_id": request.form["phone_id"], "mode": "client", "active": True, "status": "active", "products": [{"name": request.form["product_name"], "price": request.form["price"], "image": request.form["image"]}]}
        clients.append(new)
        save_clients(clients)
        return redirect("/dashboard")
    return """
    <h2>Add New Client - Fast Way</h2>
    <form method='POST'>
      Business Name: <input name='business'><br>
      Phone ID (from Meta): <input name='phone_id'><br>
      Product Name: <input name='product_name'><br>
      Price: <input name='price'><br>
      Cloudinary Image Link: <input name='image' placeholder='https://res.cloudinary.com/...'><br>
      <button>Add Client</button>
    </form>
    """

@app.route("/")
def home():
    return "Verticum Bot is Live"

if __name__ == "__main__":
    app.run(port=5000)
