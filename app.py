import os, json, requests
from flask import Flask, request, jsonify, render_template_string, session, redirect

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "verticum-secret")

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "verticum-verify")
CLOUDINARY_CLOUD = os.environ.get("CLOUDINARY_CLOUD_NAME", "demo")

# --- Load Clients ---
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

# --- WhatsApp Helpers ---
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
    payload = {
        "messaging_product": "whatsapp", "to": to, "type": "image",
        "image": {"link": image_url, "caption": caption}
    }
    r = requests.post(url, headers=headers, json=payload)
    print("SEND IMAGE:", r.text)
    return r

# --- Language Texts ---
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

# --- Onboarding Logic for Verticum ---
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
            clients.append({"id": state["data"]["business"].lower().replace(" ",""), "business": state["data"]["business"], "mode": "client", "active": False, "onboarding_data": state["data"], "phone_id": "", "products": []})
            save_clients(clients)
            count = len(state["data"].get("pictures", []))
            if pg:
                return f"Perfect! We don save {state['data']['business']} + {count} picture(s). Our team go contact you to connect your number and launch your bot. Thank you!"
            return f"Perfect! We have saved {state['data']['business']} + {count} picture(s). Our team will contact you to connect your number and launch your bot. Thank you!"
        if pg:
            return "Send your product pictures now, one by one. When you don finish, type DONE."
        return "Send your product pictures now, one by one. When you are done, type DONE."
    return "Type HELLO to start again."

# --- Client Bot Logic ---
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

# --- Webhook ---
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
        # Handle image from customer
        if msg_obj.get("type") == "image":
            media_id = msg_obj["image"]["id"]
            CHATS[client["id"]][customer_phone].append({"from": "customer", "type": "image", "media_id": media_id})
            # If in onboarding picture step, save it
            if client.get("mode") == "onboarding" and ONBOARDING.get(customer_phone,
