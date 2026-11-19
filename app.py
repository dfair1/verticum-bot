import os, json, requests, time, hashlib, re
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
        with open("clients.json") as f: return json.load(f)
    except: return []
def save_clients(clients):
    with open("clients.json", "w") as f: json.dump(clients, f, indent=2)

CHATS = {}
USER_LANG = {}
ONBOARDING = {}
TAKEOVER = set()

def send_text(phone_id, to, text):
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    r = requests.post(url, headers=headers, json=payload)
    print(f"SEND TEXT via {phone_id}:", r.text)
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

TEXTS = {
    "choose_lang": "Welcome to {biz}! 👋\nHow do you want me to talk to you?\nReply 1 for English\nReply 2 for Pidgin",
    "greeting_en": "Hello, welcome to {biz}! How can we help you today? Type PRICE for prices, PICTURES to see products, or HUMAN to talk to a person.",
    "greeting_pg": "Hello my customer, welcome to {biz}! How we fit help you today? Type PRICE for price, PICTURES to see products, or HUMAN to talk to person.",
}

def get_client_by_phone_id(phone_id):
    for c in load_clients():
        if str(c.get("phone_id")) == str(phone_id): return c
    return None

def handle_onboarding(client, customer_phone, msg):
    state = ONBOARDING.get(customer_phone, {"step": 0, "data": {}})
    lang = USER_LANG.get(customer_phone, "en")
    low = msg.lower().strip()
    pg = (lang == "pg")
    if state["step"] == 0:
        ONBOARDING[customer_phone] = {"step": 1, "data": {}}
        return "Welcome to Verticum! We dey set up Auto WhatsApp for businesses. Wetin be your business name?" if pg else "Welcome to Verticum! We set up Auto WhatsApp reply for businesses. What is your business name?"
    if state["step"] == 1:
        state["data"]["business"] = msg; state["step"] = 2; ONBOARDING[customer_phone]=state
        return "Wetin you dey sell? (e.g. Wigs, Food, Clothes) Or wetin be your service? (e.g. Makeup, Hair Braiding, Cleaning)" if pg else "What do you sell? (e.g. Wigs, Food, Clothes) Or what is your service? (e.g. Makeup, Hair Braiding, Cleaning)"
    if state["step"] == 2:
        state["data"]["products_text"] = msg; state["step"]=3; ONBOARDING[customer_phone]=state
        return "How much be your prices? (e.g. Frontal ₦85k, Makeup ₦30k)" if pg else "What are your prices? (e.g. Frontal ₦85k, Makeup ₦30k)"
    if state["step"] == 3:
        state["data"]["prices"]=msg; state["step"]=4; ONBOARDING[customer_phone]=state
        return "Where you dey / You dey deliver?" if pg else "Where are you located / Do you deliver?"
    if state["step"] == 4:
        state["data"]["location"]=msg; state["step"]=5; ONBOARDING[customer_phone]=state
        biz = state["data"].get("business","your business")
        if pg: return f"Thank you for {biz}! We fit set up your own Auto WhatsApp like this one for ₦50,000 one-time setup + ₦10,000/month. E go answer PRICE, PICTURES and HUMAN for you, for English and Pidgin, 24/7.\n\nYou want make we proceed? Reply YES"
        return f"Thank you for {biz}! We can set up your own Auto WhatsApp like this one for ₦50,000 one-time setup + ₦10,000/month. It will answer PRICE, PICTURES and HUMAN for you, in English and Pidgin, 24/7.\n\nDo you want us to proceed? Reply YES"
    if state["step"] == 5:
        if low in ["yes","y","yes please","proceed","ok","okay"]:
            state["step"]=6; state["data"]["agreed_price"]="
