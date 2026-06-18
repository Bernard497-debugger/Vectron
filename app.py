import os
import re
import secrets
import platform
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, session, send_file, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename
import requests

app = Flask(__name__)
CORS(app)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
UNSPLASH_KEY = os.environ.get("UNSPLASH_KEY", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
DEFAULT_MODEL = "poolside/laguna-xs.2:free"
IMAGE_MODEL = "black-forest-labs/flux-schnell-free"
VIDEO_MODEL = "alibaba/wan-2.6"
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
SITE_URL = os.environ.get("SITE_URL", "https://vectron.onrender.com")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "receipts")
VIDEO_FOLDER = os.path.join(os.path.dirname(__file__), "videos")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(VIDEO_FOLDER, exist_ok=True)

# ─── PLANS ────────────────────────────────────────────────────────────────────
SUBSCRIPTION_PLANS = {
    "free": {
        "name": "Free", "price": 0, "price_id": "free",
        "max_tokens_per_day": 10000, "max_messages_per_day": 50,
        "features": ["Basic AI Chat", "Code Generation", "Calculator Tool", "System Info", "Image Generation (5/day via Unsplash)"],
        "models": ["poolside/laguna-xs.2:free"]
    },
    "pro": {
        "name": "Pro", "price": 12.00, "price_id": "pro_monthly",
        "max_tokens_per_day": 100000, "max_messages_per_day": 500,
        "features": ["Everything in Free", "Priority Response", "Longer Context", "AI Images (50/day)", "Video Generation (10/day)", "No Ads"],
        "models": ["poolside/laguna-xs.2:free"]
    },
    "enterprise": {
        "name": "Enterprise", "price": 49.99, "price_id": "enterprise_monthly",
        "max_tokens_per_day": 1000000, "max_messages_per_day": 5000,
        "features": ["Everything in Pro", "Dedicated Support", "Custom Training", "API Access", "Team Collaboration", "Video Generation (100/day)"],
        "models": ["poolside/laguna-xs.2:free"]
    }
}

ORANGE_MONEY_COUNTRIES = {
    "bw": {"name": "Botswana", "prefix": "267", "length": 8, "currency": "BWP", "mobile_money": "Orange Money"},
    "ci": {"name": "Côte d'Ivoire", "prefix": "225", "length": 10, "currency": "XOF", "mobile_money": "Orange Money"},
    "sn": {"name": "Senegal", "prefix": "221", "length": 9, "currency": "XOF", "mobile_money": "Orange Money"},
    "ml": {"name": "Mali", "prefix": "223", "length": 8, "currency": "XOF", "mobile_money": "Orange Money"},
    "bf": {"name": "Burkina Faso", "prefix": "226", "length": 8, "currency": "XOF", "mobile_money": "Orange Money"},
    "bj": {"name": "Benin", "prefix": "229", "length": 8, "currency": "XOF", "mobile_money": "Orange Money"},
    "cm": {"name": "Cameroon", "prefix": "237", "length": 9, "currency": "XAF", "mobile_money": "Orange Money"},
    "gn": {"name": "Guinea", "prefix": "224", "length": 9, "currency": "GNF", "mobile_money": "Orange Money"},
}

EXCHANGE_RATES = {"BWP": 13.5, "XOF": 600, "XAF": 600, "GNF": 8600}

PENDING_PAYMENTS = {}
VERIFIED_PAYMENTS = {}
SUBSCRIPTIONS = {}

AVAILABLE_MODELS = [
    {"id": "poolside/laguna-xs.2:free", "label": "Laguna XS", "plan": "free"},
]

SYSTEM_PROMPT = """You are Vectron, a powerful AI agent. You can:
- Answer questions clearly and concisely
- Help with coding, writing, analysis, and research
- Remember the conversation history within this session
- Generate clean, working code when asked

When writing code, always wrap it in triple backtick fences with the language name.
Be direct, smart, and actually useful."""

CODE_SYSTEM_PROMPT = """You are an expert code generator.
When given a description, respond with clean, working code only.
Include a brief comment at the top explaining what it does.
Always wrap code in triple backtick fences with the correct language.
No extra explanation outside the code block."""

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_user_plan():
    user_id = session.get("user_id")
    if not user_id:
        return "free"
    sub = SUBSCRIPTIONS.get(user_id, {})
    if sub.get("status") == "active" and sub.get("expires_at", datetime.now()) > datetime.now():
        return sub.get("plan", "free")
    return "free"

def check_rate_limit():
    plan = get_user_plan()
    limits = SUBSCRIPTION_PLANS[plan]
    today = datetime.now().date().isoformat()
    if "usage" not in session:
        session["usage"] = {}
    if today not in session["usage"]:
        session["usage"][today] = {"messages": 0, "tokens": 0, "images": 0, "videos": 0}
    usage = session["usage"][today]
    image_limit = 5 if plan == "free" else 50 if plan == "pro" else 500
    video_limit = 0 if plan == "free" else 10 if plan == "pro" else 100
    if usage.get("images", 0) >= image_limit:
        return False, f"Daily image limit reached ({image_limit}). Upgrade to Pro!"
    if video_limit > 0 and usage.get("videos", 0) >= video_limit:
        return False, f"Daily video limit reached ({video_limit})."
    if usage["messages"] >= limits["max_messages_per_day"]:
        return False, f"Daily message limit reached ({limits['max_messages_per_day']}). Upgrade to Pro!"
    if usage["tokens"] >= limits["max_tokens_per_day"]:
        return False, f"Daily token limit reached. Upgrade to Pro!"
    return True, None

def update_usage(messages_count=0, tokens_used=0, images_count=0, videos_count=0):
    today = datetime.now().date().isoformat()
    if "usage" not in session:
        session["usage"] = {}
    if today not in session["usage"]:
        session["usage"][today] = {"messages": 0, "tokens": 0, "images": 0, "videos": 0}
    session["usage"][today]["messages"] += messages_count
    session["usage"][today]["tokens"] += tokens_used
    session["usage"][today]["images"] += images_count
    session["usage"][today]["videos"] += videos_count

def openrouter_headers():
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": SITE_URL,
        "X-Title": "Vectron",
    }

def call_openrouter(messages, temperature=0.7, max_tokens=2048, model=None):
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set")
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(BASE_URL, headers=openrouter_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"], data.get("usage", {})

def generate_image(prompt, plan="free"):
    if plan == "free":
        if not UNSPLASH_KEY:
            raise ValueError("UNSPLASH_KEY not configured")
        u_res = requests.get(
            f"https://api.unsplash.com/photos/random?query={prompt}&client_id={UNSPLASH_KEY}",
            timeout=10
        )
        u_data = u_res.json()
        if "urls" not in u_data:
            raise ValueError(f"Unsplash error: {u_data.get('errors', u_data)}")
        return u_data["urls"]["regular"]
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set")
    payload = {
        "model": IMAGE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image"]
    }
    resp = requests.post(BASE_URL, headers=openrouter_headers(), json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if content.startswith("http"):
        return content
    url_match = re.search(r'!\[.*?\]\((.*?)\)', content)
    if url_match:
        return url_match.group(1)
    return content

def generate_video_wan26(prompt, duration=5, resolution="512x512"):
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set")
    payload = {
        "model": VIDEO_MODEL,
        "messages": [{"role": "user", "content": f"Generate a video: {prompt}"}],
        "modalities": ["video"],
        "video_config": {"duration": duration, "resolution": resolution, "fps": 24},
        "max_tokens": 1000
    }
    try:
        response = requests.post(BASE_URL, headers=openrouter_headers(), json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        video_url = None
        for pattern in [
            r'(https?://[^\s]+\.(mp4|webm|mov|avi))',
            r'!\[.*?\]\((https?://[^\)]+\.(mp4|webm))\)',
            r'"video_url":\s*"([^"]+)"',
        ]:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                video_url = match.group(1)
                break
        if not video_url and data.get("video_url"):
            video_url = data["video_url"]
        if video_url:
            video_filename = f"wan26_{secrets.token_hex(8)}_{int(time.time())}.mp4"
            video_path = os.path.join(VIDEO_FOLDER, video_filename)
            try:
                vr = requests.get(video_url, timeout=60)
                vr.raise_for_status()
                with open(video_path, "wb") as f:
                    f.write(vr.content)
                return {"success": True, "video_url": f"/video/{video_filename}"}
            except:
                return {"success": True, "video_url": video_url}
        return {"success": False, "message": "No video URL returned"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def calculate(expression):
    try:
        if all(c in "0123456789+-*/()., " for c in expression):
            return f"Result: {eval(expression)}"
        return "Error: unsafe expression"
    except Exception as e:
        return f"Error: {e}"

def get_system_info():
    return f"OS: {platform.system()} {platform.release()} | Python: {platform.python_version()}"

# ─── ADMIN AUTH ───────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-Admin-Key") or request.args.get("key")
        if not ADMIN_KEY or key != ADMIN_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "openrouter_key_set": bool(OPENROUTER_API_KEY),
        "unsplash_key_set": bool(UNSPLASH_KEY),
        "admin_key_set": bool(ADMIN_KEY),
    })

@app.route("/logo")
def logo():
    path = os.path.join(os.path.dirname(__file__), "logo.png")
    if os.path.exists(path):
        return send_file(path, mimetype="image/png")
    return "", 404

@app.route("/video/<filename>")
def serve_video(filename):
    if get_user_plan() == "free":
        return jsonify({"error": "Video access requires a Pro plan"}), 403
    path = os.path.join(VIDEO_FOLDER, filename)
    if os.path.exists(path):
        return send_file(path, mimetype="video/mp4")
    return jsonify({"error": "Video not found"}), 404

@app.route("/models")
def get_models():
    current = session.get("model", DEFAULT_MODEL)
    user_plan = get_user_plan()
    available = [m for m in AVAILABLE_MODELS if m["plan"] in ["free", user_plan]]
    return jsonify({"models": available, "current": current})

@app.route("/model", methods=["POST"])
def set_model():
    model_id = (request.json or {}).get("model", "").strip()
    if not model_id:
        return jsonify({"error": "No model provided"}), 400
    session["model"] = model_id
    return jsonify({"ok": True, "model": model_id})

@app.route("/plans")
def get_plans():
    user_plan = get_user_plan()
    today = datetime.now().date().isoformat()
    usage = session.get("usage", {}).get(today, {"messages": 0, "tokens": 0, "images": 0, "videos": 0})
    return jsonify({"plans": SUBSCRIPTION_PLANS, "current_plan": user_plan, "usage": usage})

@app.route("/orange-money-countries")
def get_orange_countries():
    return jsonify({"countries": ORANGE_MONEY_COUNTRIES})

@app.route("/initiate-payment", methods=["POST"])
def initiate_payment():
    data = request.json or {}
    plan_id = data.get("plan_id")
    phone_number = data.get("phone_number", "").strip()
    country_code = data.get("country_code", "bw")
    if plan_id not in SUBSCRIPTION_PLANS or plan_id == "free":
        return jsonify({"error": "Invalid plan"}), 400
    country_info = ORANGE_MONEY_COUNTRIES.get(country_code)
    if not country_info:
        return jsonify({"error": "Invalid country"}), 400
    phone_number = re.sub(r'[\s\+]', '', phone_number)
    if not phone_number.isdigit():
        return jsonify({"error": "Phone number must contain only digits"}), 400
    user_id = session.get("user_id")
    if not user_id:
        user_id = secrets.token_hex(16)
        session["user_id"] = user_id
    payment_id = secrets.token_hex(16)
    amount_usd = SUBSCRIPTION_PLANS[plan_id]["price"]
    currency = country_info.get("currency", "USD")
    amount_local = round(amount_usd * EXCHANGE_RATES.get(currency, 1), 2)
    PENDING_PAYMENTS[payment_id] = {
        "user_id": user_id, "plan_id": plan_id,
        "amount_usd": amount_usd, "amount_local": amount_local, "currency": currency,
        "phone_number": f"{country_info['prefix']}{phone_number}",
        "country": country_info["name"], "status": "pending",
        "created_at": datetime.now().isoformat(), "receipt_filename": None
    }
    instructions = f"""
    📱 {country_info['mobile_money']} Payment Instructions:

    1. Open your {country_info['mobile_money']} app or dial #144#
    2. Select 'Send Money'
    3. Enter our merchant number: **01 23 45 67 89**
    4. Enter amount: **{amount_local} {currency}** (${amount_usd} USD)
    5. Enter your PIN to confirm
    6. Screenshot the confirmation and upload below
    """
    return jsonify({
        "success": True, "payment_id": payment_id,
        "amount_usd": amount_usd, "amount_local": amount_local, "currency": currency,
        "plan_name": SUBSCRIPTION_PLANS[plan_id]["name"], "instructions": instructions,
    })

@app.route("/upload-receipt", methods=["POST"])
def upload_receipt():
    payment_id = request.form.get("payment_id")
    if not payment_id:
        return jsonify({"error": "Payment ID required"}), 400
    payment = PENDING_PAYMENTS.get(payment_id)
    if not payment or payment["status"] != "pending":
        return jsonify({"error": "Invalid payment session"}), 404
    if "receipt" not in request.files or request.files["receipt"].filename == "":
        return jsonify({"error": "No receipt file uploaded"}), 400
    receipt = request.files["receipt"]
    filename = f"{payment_id}_{secrets.token_hex(8)}_{secure_filename(receipt.filename)}"
    receipt.save(os.path.join(UPLOAD_FOLDER, filename))
    payment["status"] = "verified"
    payment["receipt_filename"] = filename
    payment["verified_at"] = datetime.now().isoformat()
    user_id = payment["user_id"]
    plan_id = payment["plan_id"]
    SUBSCRIPTIONS[user_id] = {
        "plan": plan_id, "status": "active",
        "started_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(days=30),
        "payment_method": "orange_money",
        "receipt_filename": filename,
        "country": payment["country"]
    }
    VERIFIED_PAYMENTS[payment_id] = payment
    return jsonify({
        "success": True,
        "message": f"Receipt uploaded! Your {SUBSCRIPTION_PLANS[plan_id]['name']} plan is now active."
    })

@app.route("/payment-status/<payment_id>")
def payment_status(payment_id):
    payment = PENDING_PAYMENTS.get(payment_id)
    if not payment:
        return jsonify({"error": "Payment not found"}), 404
    return jsonify({"status": payment["status"]})

@app.route("/")
def index():
    session.setdefault("history", [])
    session.setdefault("tokens", 0)
    session.setdefault("user_id", secrets.token_hex(16))
    return HTML

@app.route("/chat", methods=["POST"])
def chat():
    user_message = (request.json or {}).get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400
    allowed, error_msg = check_rate_limit()
    if not allowed:
        return jsonify({"error": error_msg}), 429
    history = session.get("history", [])
    history.append({"role": "user", "content": user_message})
    active_model = session.get("model", DEFAULT_MODEL)
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        reply, usage = call_openrouter(messages, model=active_model)
        tokens_used = usage.get("total_tokens", 0)
        history.append({"role": "assistant", "content": reply})
        session["history"] = history[-40:]
        session["tokens"] = session.get("tokens", 0) + tokens_used
        update_usage(messages_count=1, tokens_used=tokens_used)
        today = datetime.now().date().isoformat()
        remaining = SUBSCRIPTION_PLANS[get_user_plan()]["max_messages_per_day"] - session["usage"].get(today, {}).get("messages", 0)
        return jsonify({"reply": reply, "tokens": session["tokens"], "remaining_messages": remaining})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/generate-image", methods=["POST"])
def generate_image_route():
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400
    allowed, error_msg = check_rate_limit()
    if not allowed:
        return jsonify({"error": error_msg}), 429
    plan = get_user_plan()
    try:
        image_url = generate_image(prompt, plan=plan)
        update_usage(images_count=1)
        today = datetime.now().date().isoformat()
        usage = session.get("usage", {}).get(today, {"images": 0})
        image_limit = 5 if plan == "free" else 50 if plan == "pro" else 500
        remaining_images = image_limit - usage.get("images", 0)
        return jsonify({"success": True, "image_url": image_url, "prompt": prompt, "remaining_images": remaining_images})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/generate-video", methods=["POST"])
def generate_video_route():
    user_plan = get_user_plan()
    if user_plan == "free":
        return jsonify({"error": "PRO_FEATURE_LOCKED", "message": "Video Generation requires a Pro plan."}), 403
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400
    allowed, error_msg = check_rate_limit()
    if not allowed:
        return jsonify({"error": error_msg}), 429
    try:
        result = generate_video_wan26(prompt)
        if result.get("success"):
            update_usage(videos_count=1)
            today = datetime.now().date().isoformat()
            usage = session.get("usage", {}).get(today, {"videos": 0})
            remaining = (10 if user_plan == "pro" else 100) - usage.get("videos", 0)
            return jsonify({"success": True, "video_url": result.get("video_url"), "prompt": prompt, "remaining_videos": remaining})
        return jsonify({"success": False, "error": result.get("message", "Video generation failed")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/tool", methods=["POST"])
def tool():
    data = request.json or {}
    tool_name = data.get("tool", "").strip()
    args = data.get("args", "").strip()
    allowed, error_msg = check_rate_limit()
    if not allowed:
        return jsonify({"error": error_msg}), 429

    if tool_name == "calculate":
        if not args:
            return jsonify({"result": "Provide a math expression"})
        update_usage(messages_count=1)
        return jsonify({"result": calculate(args)})

    elif tool_name == "system_info":
        update_usage(messages_count=1)
        return jsonify({"result": get_system_info()})

    elif tool_name == "code":
        if not args:
            return jsonify({"result": "Describe the code you want"}), 400
        try:
            messages = [
                {"role": "system", "content": CODE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Generate code for: {args}"}
            ]
            reply, usage = call_openrouter(messages, temperature=0.3, model=session.get("model", DEFAULT_MODEL))
            update_usage(messages_count=1, tokens_used=usage.get("total_tokens", 0))
            return jsonify({"result": reply})
        except Exception as e:
            return jsonify({"result": f"Error: {e}"}), 500

    elif tool_name == "image":
        if not args:
            return jsonify({"result": "Describe the image you want"}), 400
        plan = get_user_plan()
        try:
            image_url = generate_image(args, plan=plan)
            update_usage(images_count=1)
            today = datetime.now().date().isoformat()
            usage = session.get("usage", {}).get(today, {"images": 0})
            image_limit = 5 if plan == "free" else 50 if plan == "pro" else 500
            remaining = image_limit - usage.get("images", 0)
            return jsonify({
                "result": f"\n\n![Generated Image]({image_url})\n\n",
                "image_url": image_url, "type": "image", "remaining_images": remaining
            })
        except Exception as e:
            return jsonify({"result": f"Error: {e}"}), 500

    elif tool_name == "video":
        user_plan = get_user_plan()
        if user_plan == "free":
            return jsonify({
                "result": "## 🔒 Video Generation is a Pro Feature!\n\nUpgrade to **Pro ($12/month)** to unlock AI Video Generation.\n\n📱 Pay with Orange Money — instant activation!",
                "type": "text", "requires_upgrade": True
            })
        if not args:
            return jsonify({"result": "Describe the video you want"}), 400
        try:
            result = generate_video_wan26(args)
            if result.get("success"):
                update_usage(videos_count=1)
                today = datetime.now().date().isoformat()
                usage = session.get("usage", {}).get(today, {"videos": 0})
                remaining = (10 if user_plan == "pro" else 100) - usage.get("videos", 0)
                return jsonify({
                    "result": f"🎬 Video generated!\n\nPrompt: {args}",
                    "video_url": result.get("video_url"), "type": "video", "remaining_videos": remaining
                })
            return jsonify({"result": f"Error: {result.get('message', 'Failed')}", "type": "text"})
        except Exception as e:
            return jsonify({"result": f"Error: {e}", "type": "text"}), 500

    return jsonify({"error": f"Unknown tool '{tool_name}'"}), 400

@app.route("/reset", methods=["POST"])
def reset():
    session["history"] = []
    session["tokens"] = 0
    return jsonify({"ok": True})

# ─── ADMIN ────────────────────────────────────────────────────────────────────

@app.route("/admin")
def admin_panel():
    key = request.args.get("key", "")
    if not ADMIN_KEY or key != ADMIN_KEY:
        return """
        <html><body style="font-family:monospace;background:#0a0a0b;color:#e8e8ed;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
        <div style="text-align:center;">
            <h2 style="margin-bottom:16px;">🔐 Vectron Admin</h2>
            <form onsubmit="location.href='/admin?key='+document.getElementById('k').value;return false;">
                <input id="k" type="password" placeholder="Admin key..." style="padding:10px;border-radius:8px;border:1px solid #333;background:#111;color:white;font-size:14px;margin-bottom:10px;display:block;width:250px;">
                <button type="submit" style="padding:10px 24px;background:#3b82f6;color:white;border:none;border-radius:8px;cursor:pointer;font-size:14px;width:250px;">Login</button>
            </form>
        </div></body></html>
        """, 200

    def fmt_date(val):
        if hasattr(val, 'strftime'):
            return val.strftime('%Y-%m-%d')
        return str(val)[:10] if val else '—'

    subs_rows = ""
    for uid, s in SUBSCRIPTIONS.items():
        subs_rows += f"""
        <tr>
            <td style="font-family:monospace;font-size:11px;">{uid[:16]}...</td>
            <td><span style="color:{'#fbbf24' if s['plan']=='pro' else '#8b5cf6' if s['plan']=='enterprise' else '#6b6b7a'};font-weight:600;">{s['plan'].upper()}</span></td>
            <td><span style="color:{'#4ade80' if s['status']=='active' else '#f87171'}">{s['status']}</span></td>
            <td>{fmt_date(s.get('started_at'))}</td>
            <td>{fmt_date(s.get('expires_at'))}</td>
            <td>{s.get('country','—')}</td>
            <td>{s.get('payment_method','—')}</td>
            <td>
                <button onclick="activate('{uid}','pro')" style="padding:3px 8px;background:#fbbf24;color:#000;border:none;border-radius:4px;cursor:pointer;font-size:11px;margin-right:3px;">Pro</button>
                <button onclick="activate('{uid}','enterprise')" style="padding:3px 8px;background:#8b5cf6;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;margin-right:3px;">Ent</button>
                <button onclick="revoke('{uid}')" style="padding:3px 8px;background:#f87171;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;">Revoke</button>
            </td>
        </tr>"""

    if not subs_rows:
        subs_rows = '<tr><td colspan="8" class="empty">No subscriptions yet</td></tr>'

    all_payments = {**PENDING_PAYMENTS, **VERIFIED_PAYMENTS}
    payments_rows = ""
    for pid, p in all_payments.items():
        receipt_link = ""
        if p.get('receipt_filename'):
            receipt_link = f'<a href="/admin/receipt/{p["receipt_filename"]}?key={key}" target="_blank" style="color:#3b82f6;">View</a>'
        else:
            receipt_link = '—'
        
        status_color = '#4ade80' if p['status'] == 'verified' else '#fbbf24'
        payments_rows += f"""
        <tr>
            <td style="font-family:monospace;font-size:11px;">{pid[:16]}...</td>
            <td>{p.get('plan_id','—')}</td>
            <td>${p.get('amount_usd','—')}</td>
            <td>{p.get('amount_local','—')} {p.get('currency','')}</td>
            <td>{p.get('country','—')}</td>
            <td><span style="color:{status_color}">{p['status']}</span></td>
            <td>{str(p.get('created_at',''))[:10]}</td>
            <td>{receipt_link}</td>
        </tr>"""

    if not payments_rows:
        payments_rows = '<tr><td colspan="8" class="empty">No payments yet</td></tr>'

    total_subs = len(SUBSCRIPTIONS)
    active_subs = sum(1 for s in SUBSCRIPTIONS.values() if s.get('status') == 'active')
    pro_subs = sum(1 for s in SUBSCRIPTIONS.values() if s.get('plan') == 'pro')
    ent_subs = sum(1 for s in SUBSCRIPTIONS.values() if s.get('plan') == 'enterprise')
    total_payments = len(all_payments)
    verified_payments = len(VERIFIED_PAYMENTS)
    pending_payments = len([p for p in PENDING_PAYMENTS.values() if p['status'] == 'pending'])

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Vectron Admin</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
        *{{margin:0;padding:0;box-sizing:border-box;}}
        body{{font-family:'Courier New',monospace;background:#0a0a0b;color:#e8e8ed;padding:20px;min-height:100vh;}}
        h1{{font-size:18px;margin-bottom:20px;color:#3b82f6;display:flex;align-items:center;gap:8px;}}
        h2{{font-size:12px;margin:24px 0 10px;color:#6b6b7a;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid #2a2a30;padding-bottom:8px;}}
        .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:24px;}}
        .stat{{background:#111;border:1px solid #2a2a30;border-radius:10px;padding:14px;text-align:center;}}
        .stat-num{{font-size:22px;font-weight:700;color:#3b82f6;}}
        .stat-label{{font-size:10px;color:#6b6b7a;margin-top:4px;text-transform:uppercase;letter-spacing:.5px;}}
        .table-wrap{{overflow-x:auto;margin-bottom:24px;}}
        table{{width:100%;border-collapse:collapse;font-size:12px;min-width:600px;}}
        th{{text-align:left;padding:8px 10px;border-bottom:1px solid #2a2a30;color:#6b6b7a;font-size:10px;text-transform:uppercase;letter-spacing:.5px;}}
        td{{padding:8px 10px;border-bottom:1px solid #1a1a1f;vertical-align:middle;}}
        tr:hover td{{background:#111;}}
        .manual-form{{background:#111;border:1px solid #2a2a30;border-radius:10px;padding:16px;margin-bottom:24px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}}
        .manual-form input,.manual-form select{{background:#1a1a1f;border:1px solid #2a2a30;color:white;padding:8px 12px;border-radius:6px;font-size:12px;font-family:monospace;}}
        .manual-form input{{width:300px;}}
        .btn{{padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-family:monospace;font-weight:600;}}
        .btn-blue{{background:#3b82f6;color:white;}}
        .btn-blue:hover{{background:#60a5fa;}}
        .empty{{color:#6b6b7a;text-align:center;padding:20px;font-size:12px;}}
    </style>
</head>
<body>
    <h1>⚡ Vectron Admin</h1>

    <div class="stats">
        <div class="stat"><div class="stat-num">{total_subs}</div><div class="stat-label">Users</div></div>
        <div class="stat"><div class="stat-num" style="color:#4ade80">{active_subs}</div><div class="stat-label">Active</div></div>
        <div class="stat"><div class="stat-num" style="color:#fbbf24">{pro_subs}</div><div class="stat-label">Pro</div></div>
        <div class="stat"><div class="stat-num" style="color:#8b5cf6">{ent_subs}</div><div class="stat-label">Enterprise</div></div>
        <div class="stat"><div class="stat-num">{verified_payments}</div><div class="stat-label">Verified</div></div>
        <div class="stat"><div class="stat-num" style="color:#fbbf24">{pending_payments}</div><div class="stat-label">Pending</div></div>
    </div>

    <h2>Manual Activation</h2>
    <div class="manual-form">
        <input type="text" id="manual-uid" placeholder="Full User ID">
        <select id="manual-plan">
            <option value="pro">Pro</option>
            <option value="enterprise">Enterprise</option>
            <option value="free">Free (revoke)</option>
        </select>
        <button class="btn btn-blue" onclick="manualActivate()">Activate / Revoke</button>
    </div>

    <h2>Subscriptions ({total_subs})</h2>
    <div class="table-wrap">
    <table>
        <thead><tr><th>User ID</th><th>Plan</th><th>Status</th><th>Started</th><th>Expires</th><th>Country</th><th>Method</th><th>Actions</th></tr></thead>
        <tbody>{subs_rows}</tbody>
    </table>
    </div>

    <h2>Payments ({total_payments})</h2>
    <div class="table-wrap">
    <table>
        <thead><tr><th>Payment ID</th><th>Plan</th><th>USD</th><th>Local</th><th>Country</th><th>Status</th><th>Date</th><th>Receipt</th></tr></thead>
        <tbody>{payments_rows}</tbody>
    </table>
    </div>

    <script>
        const KEY = '{key}';

        async function activate(uid, plan) {{
            const res = await fetch('/admin/activate', {{
                method: 'POST',
                headers: {{'Content-Type':'application/json','X-Admin-Key':KEY}},
                body: JSON.stringify({{user_id:uid, plan}})
            }});
            const data = await res.json();
            alert(data.message || data.error);
            location.reload();
        }}

        async function revoke(uid) {{
            if (!confirm('Revoke subscription for user ' + uid.slice(0,12) + '...?')) return;
            const res = await fetch('/admin/revoke', {{
                method: 'POST',
                headers: {{'Content-Type':'application/json','X-Admin-Key':KEY}},
                body: JSON.stringify({{user_id:uid}})
            }});
            const data = await res.json();
            alert(data.message || data.error);
            location.reload();
        }}

        async function manualActivate() {{
            const uid = document.getElementById('manual-uid').value.trim();
            const plan = document.getElementById('manual-plan').value;
            if (!uid) {{ alert('Enter a user ID'); return; }}
            if (plan === 'free') {{
                if (!confirm('Revoke subscription for ' + uid.slice(0,12) + '...?')) return;
                await revoke(uid);
            }} else {{
                await activate(uid, plan);
            }}
        }}
    </script>
</body>
</html>"""

@app.route("/admin/activate", methods=["POST"])
@admin_required
def admin_activate():
    data = request.json or {}
    user_id = data.get("user_id", "").strip()
    plan = data.get("plan", "pro")
    if not user_id:
        return jsonify({"error": "User ID required"}), 400
    if plan == "free":
        SUBSCRIPTIONS.pop(user_id, None)
        return jsonify({"message": f"Subscription revoked for {user_id[:12]}..."})
    SUBSCRIPTIONS[user_id] = {
        "plan": plan, "status": "active",
        "started_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(days=30),
        "payment_method": "manual_admin",
        "country": "Admin"
    }
    return jsonify({"message": f"{plan.upper()} activated for {user_id[:12]}..."})

@app.route("/admin/revoke", methods=["POST"])
@admin_required
def admin_revoke():
    data = request.json or {}
    user_id = data.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "User ID required"}), 400
    SUBSCRIPTIONS.pop(user_id, None)
    return jsonify({"message": f"Revoked for {user_id[:12]}..."})

@app.route("/admin/receipt/<filename>")
def admin_receipt(filename):
    key = request.args.get("key")
    if not ADMIN_KEY or key != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(path):
        return send_file(path)
    return jsonify({"error": "File not found"}), 404

@app.route("/admin/stats")
@admin_required
def admin_stats():
    return jsonify({
        "total_subscriptions": len(SUBSCRIPTIONS),
        "active_subscriptions": sum(1 for s in SUBSCRIPTIONS.values() if s.get("status") == "active"),
        "plans": {p: sum(1 for s in SUBSCRIPTIONS.values() if s.get("plan") == p) for p in ["free","pro","enterprise"]},
        "total_payments": len(PENDING_PAYMENTS) + len(VERIFIED_PAYMENTS),
        "verified_payments": len(VERIFIED_PAYMENTS),
        "pending_payments": sum(1 for p in PENDING_PAYMENTS.values() if p["status"] == "pending"),
    })

# ─── HTML ─────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0a0a0b">
<title>Vectron AI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#0a0a0b;--surface:#111113;--surface2:#18181b;--surface3:#222227;
  --border:#2a2a30;--text:#e8e8ed;--text-muted:#6b6b7a;--text-dim:#3d3d4a;
  --accent:#3b82f6;--accent2:#60a5fa;--user-bg:#1a1a20;--code-bg:#0d1117;
  --radius:12px;--radius-sm:8px;--nav-h:64px;--orange:#ff6600;
}
body{font-family:'Sora',sans-serif;background:var(--bg);color:var(--text);height:100dvh;display:flex;flex-direction:column;overflow:hidden;}
header{display:flex;align-items:center;justify-content:space-between;padding:12px 20px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0;z-index:10;}
.logo{display:flex;align-items:center;gap:10px;}
.logo img{height:30px;width:auto;}
.logo-text{font-size:15px;font-weight:600;letter-spacing:-0.3px;}
.header-right{display:flex;align-items:center;gap:8px;}
.token-badge{font-size:11px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;background:var(--surface2);border:1px solid var(--border);padding:4px 10px;border-radius:20px;}
.plan-badge{font-size:11px;font-weight:600;background:linear-gradient(135deg,var(--accent),var(--accent2));padding:4px 12px;border-radius:20px;cursor:pointer;transition:opacity .15s;color:white;}
.plan-badge.pro{background:linear-gradient(135deg,#fbbf24,#f59e0b);}
.plan-badge.enterprise{background:linear-gradient(135deg,#8b5cf6,#6d28d9);}
.plan-badge:hover{opacity:.8;}
.icon-btn{width:32px;height:32px;border:1px solid var(--border);background:var(--surface2);color:var(--text-muted);border-radius:var(--radius-sm);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px;transition:all .15s;}
.icon-btn:hover{background:var(--surface3);color:var(--text);}
.main{display:flex;flex:1;overflow:hidden;}
.sidebar{width:240px;border-right:1px solid var(--border);background:var(--surface);padding:16px 12px;display:flex;flex-direction:column;gap:4px;flex-shrink:0;overflow-y:auto;}
.sidebar-label{font-size:10px;font-weight:600;color:var(--text-dim);letter-spacing:1px;text-transform:uppercase;padding:8px 8px 4px;}
.tool-btn{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:var(--radius-sm);border:none;background:transparent;color:var(--text-muted);font-family:'Sora',sans-serif;font-size:13px;cursor:pointer;transition:all .15s;text-align:left;width:100%;}
.tool-btn:hover{background:var(--surface2);color:var(--text);}
.tool-btn .tool-icon{width:28px;height:28px;background:var(--surface3);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0;}
.tool-btn.locked{opacity:0.6;}
.tool-btn.locked:hover{opacity:0.8;}
.tool-btn.locked .pro-lock{margin-left:auto;font-size:10px;background:linear-gradient(135deg,var(--orange),#ff8800);padding:2px 6px;border-radius:10px;color:white;}
.sidebar-divider{height:1px;background:var(--border);margin:8px 0;}
.sidebar-model select{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-family:'Sora',sans-serif;font-size:12px;padding:8px 10px;outline:none;cursor:pointer;appearance:none;-webkit-appearance:none;}
.upgrade-btn{background:linear-gradient(135deg,var(--orange),#ff8800);color:white;font-weight:600;border:none;margin-top:12px;}
.upgrade-btn:hover{background:linear-gradient(135deg,#ff5500,#ff7700);color:white;}
.chat-area{flex:1;display:flex;flex-direction:column;overflow:hidden;}
#messages{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:18px;scroll-behavior:smooth;}
#messages::-webkit-scrollbar{width:4px;}
#messages::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px;}
.msg{display:flex;gap:10px;animation:fadeUp .2s ease;max-width:800px;width:100%;margin:0 auto;}
.msg.user{flex-direction:row-reverse;}
@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.avatar{width:30px;height:30px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0;font-weight:600;}
.msg.agent .avatar{background:linear-gradient(135deg,var(--accent),var(--accent2));color:white;}
.msg.user .avatar{background:var(--surface3);color:var(--text-muted);font-size:11px;}
.bubble{flex:1;max-width:calc(100% - 40px);}
.bubble-inner{padding:11px 15px;border-radius:var(--radius);font-size:14px;line-height:1.7;}
.msg.agent .bubble-inner{background:transparent;color:var(--text);}
.msg.user .bubble-inner{background:var(--user-bg);border:1px solid var(--border);color:var(--text);border-radius:var(--radius) var(--radius) 4px var(--radius);}
.generated-image{max-width:100%;border-radius:var(--radius);margin:10px 0;border:1px solid var(--border);}
.image-container,.video-container{text-align:center;margin:10px 0;}
.generated-video{max-width:100%;max-height:400px;border-radius:var(--radius);border:1px solid var(--border);background:var(--bg);}
.bubble-inner p{margin-bottom:10px;}
.bubble-inner p:last-child{margin-bottom:0;}
.bubble-inner ul,.bubble-inner ol{padding-left:20px;margin-bottom:10px;}
.bubble-inner li{margin-bottom:4px;}
.bubble-inner strong{color:var(--text);font-weight:600;}
.bubble-inner h1,.bubble-inner h2,.bubble-inner h3{font-weight:600;margin:14px 0 6px;}
.bubble-inner h1:first-child,.bubble-inner h2:first-child,.bubble-inner h3:first-child{margin-top:0;}
.bubble-inner a{color:var(--accent);}
.bubble-inner blockquote{border-left:3px solid var(--accent);padding-left:12px;color:var(--text-muted);margin:10px 0;}
.bubble-inner code:not(pre code){font-family:'JetBrains Mono',monospace;font-size:12px;background:var(--surface3);border:1px solid var(--border);padding:2px 6px;border-radius:4px;color:var(--accent2);}
.code-block{margin:12px 0;border-radius:var(--radius-sm);overflow:hidden;border:1px solid var(--border);background:var(--code-bg);}
.code-header{display:flex;align-items:center;justify-content:space-between;padding:7px 12px;background:#161b22;border-bottom:1px solid var(--border);}
.code-lang{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;}
.copy-btn{display:flex;align-items:center;gap:4px;padding:4px 10px;border-radius:5px;border:1px solid var(--border);background:var(--surface3);color:var(--text-muted);font-family:'Sora',sans-serif;font-size:11px;cursor:pointer;transition:all .15s;}
.copy-btn:hover{background:var(--surface2);color:var(--text);}
.copy-btn.copied{color:#4ade80;border-color:#4ade80;}
.code-block pre{margin:0;padding:14px 16px;overflow-x:auto;font-size:13px;line-height:1.6;}
.code-block pre code{font-family:'JetBrains Mono',monospace;background:transparent!important;padding:0!important;border:none!important;}
.tool-result{background:var(--surface2);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:var(--radius-sm);padding:12px 14px;font-size:13px;line-height:1.6;max-width:800px;margin:0 auto;animation:fadeUp .2s ease;}
.tool-result-header{font-size:11px;color:var(--accent);font-weight:600;letter-spacing:.5px;text-transform:uppercase;margin-bottom:8px;}
.thinking{display:flex;gap:10px;max-width:800px;margin:0 auto;align-items:center;}
.thinking-dots{display:flex;gap:4px;padding:12px 0;}
.thinking-dots span{width:6px;height:6px;background:var(--text-muted);border-radius:50%;animation:pulse 1.2s infinite;}
.thinking-dots span:nth-child(2){animation-delay:.2s;}
.thinking-dots span:nth-child(3){animation-delay:.4s;}
@keyframes pulse{0%,80%,100%{opacity:.2;transform:scale(.8)}40%{opacity:1;transform:scale(1)}}
.input-area{padding:14px 20px 18px;border-top:1px solid var(--border);background:var(--surface);flex-shrink:0;}
.input-wrap{max-width:800px;margin:0 auto;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);display:flex;align-items:flex-end;gap:8px;padding:10px 12px;transition:border-color .15s;}
.input-wrap:focus-within{border-color:var(--text-dim);}
#user-input{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-family:'Sora',sans-serif;font-size:14px;resize:none;max-height:140px;line-height:1.5;padding:2px 0;}
#user-input::placeholder{color:var(--text-dim);}
.send-btn{width:34px;height:34px;background:var(--accent);border:none;border-radius:8px;color:white;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:15px;transition:all .15s;flex-shrink:0;}
.send-btn:hover{background:var(--accent2);}
.send-btn:disabled{opacity:.4;cursor:not-allowed;}
.input-hint{max-width:800px;margin:7px auto 0;font-size:11px;color:var(--text-dim);text-align:center;}
.remaining-badge{font-size:10px;color:var(--text-muted);margin-top:4px;text-align:center;}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.8);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:100;}
.modal-overlay.open{display:flex;}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px;width:560px;max-width:92vw;animation:fadeUp .2s ease;max-height:90vh;overflow-y:auto;}
.modal h3{font-size:15px;font-weight:600;margin-bottom:4px;}
.modal p{font-size:13px;color:var(--text-muted);margin-bottom:16px;}
.modal textarea{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-family:'Sora',sans-serif;font-size:13px;padding:10px 12px;outline:none;margin-bottom:12px;resize:vertical;transition:border-color .15s;}
.modal textarea:focus{border-color:var(--text-dim);}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;}
.btn{padding:8px 16px;border-radius:var(--radius-sm);font-family:'Sora',sans-serif;font-size:13px;font-weight:500;cursor:pointer;transition:all .15s;border:none;}
.btn-primary{background:var(--accent);color:white;}
.btn-primary:hover{background:var(--accent2);}
.btn-ghost{background:var(--surface2);color:var(--text-muted);border:1px solid var(--border);}
.btn-ghost:hover{color:var(--text);background:var(--surface3);}
.btn-upgrade{background:linear-gradient(135deg,var(--orange),#ff8800);color:white;}
.btn-upgrade:hover{background:linear-gradient(135deg,#ff5500,#ff7700);}
.btn-orange{background:linear-gradient(135deg,var(--orange),#ff8800);color:white;}
.plan-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px;}
.plan-card{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:14px;text-align:center;transition:all .15s;cursor:pointer;}
.plan-card.selected{border-color:var(--orange);background:var(--surface3);}
.plan-card:hover{border-color:var(--orange);}
.plan-name{font-size:14px;font-weight:600;margin-bottom:4px;}
.plan-price{font-size:18px;font-weight:700;color:var(--orange);margin-bottom:6px;}
.plan-price small{font-size:10px;font-weight:400;color:var(--text-muted);}
.plan-features{list-style:none;margin-top:8px;font-size:11px;color:var(--text-muted);}
.plan-features li{padding:2px 0;}
.payment-instructions{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;margin:16px 0;white-space:pre-wrap;font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--text-muted);}
.payment-field{margin-bottom:16px;}
.payment-field label{display:block;font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text-muted);}
.payment-field input,.payment-field select{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-family:'Sora',sans-serif;font-size:13px;padding:10px 12px;outline:none;transition:border-color .15s;}
.payment-field input:focus,.payment-field select:focus{border-color:var(--orange);}
.receipt-preview{margin-top:8px;padding:8px;background:var(--surface3);border-radius:var(--radius-sm);font-size:11px;color:var(--text-muted);text-align:center;}
.empty-state{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--text-muted);padding:40px;text-align:center;}
.empty-icon{width:52px;height:52px;background:linear-gradient(135deg,var(--accent),var(--accent2));border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:24px;margin-bottom:4px;}
.empty-state h2{font-size:17px;font-weight:600;color:var(--text);}
.empty-state p{font-size:13px;max-width:320px;line-height:1.6;}
.suggestion-chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:8px;}
.chip{padding:7px 14px;border:1px solid var(--border);border-radius:20px;font-size:12px;color:var(--text-muted);background:var(--surface2);cursor:pointer;transition:all .15s;}
.chip:hover{border-color:var(--accent);color:var(--text);}
.chip.pro-chip{background:linear-gradient(135deg,rgba(255,102,0,0.2),rgba(255,136,0,0.1));border-color:var(--orange);color:var(--orange);}
.mobile-nav{display:none;}
.mobile-nav-btn{display:flex;flex-direction:column;align-items:center;gap:3px;padding:8px 16px;border:none;background:transparent;color:var(--text-muted);font-family:'Sora',sans-serif;font-size:10px;cursor:pointer;border-radius:10px;}
.mobile-nav-btn .nav-icon{font-size:20px;}
.sheet-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:60;backdrop-filter:blur(4px);}
.sheet-overlay.open{display:block;}
.sheet{position:fixed;bottom:0;left:0;right:0;background:var(--surface);border-top:1px solid var(--border);border-radius:20px 20px 0 0;padding:16px 20px calc(32px + env(safe-area-inset-bottom));z-index:70;transform:translateY(100%);transition:transform .3s ease;}
.sheet.open{transform:translateY(0);}
.sheet-handle{width:36px;height:4px;background:var(--border);border-radius:4px;margin:0 auto 20px;}
.sheet-title{font-size:12px;font-weight:600;color:var(--text-muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;}
.sheet-tools{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px;}
.sheet-tool-btn{display:flex;align-items:center;gap:10px;padding:12px 14px;background:var(--surface2);border:1px solid var(--border);border-radius:12px;color:var(--text);font-family:'Sora',sans-serif;font-size:13px;cursor:pointer;}
.sheet-model-select{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:10px;color:var(--text);font-family:'Sora',sans-serif;font-size:13px;padding:12px 14px;outline:none;}
@media(max-width:640px){
  .sidebar{display:none!important;}
  .mobile-nav{display:flex;position:fixed;bottom:0;left:0;right:0;background:var(--surface);border-top:1px solid var(--border);padding:8px 16px calc(8px + env(safe-area-inset-bottom));justify-content:space-around;}
  #messages{padding:12px 14px;padding-bottom:calc(var(--nav-h) + 100px);}
  .input-area{padding:10px 12px;padding-bottom:calc(var(--nav-h) + 10px + env(safe-area-inset-bottom));}
  header{padding:10px 14px;}
  .token-badge{display:none;}
  .avatar{width:26px;height:26px;font-size:11px;}
  .bubble-inner{font-size:14px;padding:10px 12px;}
  .modal{width:95vw;}
  .input-hint{display:none;}
}
</style>
</head>
<body>

<div class="sheet-overlay" id="sheet-overlay" onclick="closeSheet()"></div>
<div class="sheet" id="sheet">
  <div class="sheet-handle"></div>
  <div id="sheet-tools-section">
    <div class="sheet-title">Tools</div>
    <div class="sheet-tools">
      <button class="sheet-tool-btn" onclick="sheetTool('code')">⌨ Code</button>
      <button class="sheet-tool-btn" onclick="sheetTool('calculate')">∑ Calculator</button>
      <button class="sheet-tool-btn" onclick="sheetTool('system_info')">⚙ System Info</button>
      <button class="sheet-tool-btn" onclick="sheetTool('image')">🎨 Image</button>
      <button class="sheet-tool-btn" onclick="handleVideoToolClick()">🎬 Video <span style="margin-left:auto;font-size:10px;color:var(--orange);">🔒 PRO</span></button>
    </div>
  </div>
  <div id="sheet-model-section">
    <div class="sheet-title">Model</div>
    <select id="model-select-mobile" onchange="switchModel(this.value)" class="sheet-model-select">
      <option>Loading...</option>
    </select>
  </div>
</div>

<header>
  <div class="logo">
    <img src="/logo" alt="Vectron" onerror="this.style.display='none'">
    <div class="logo-text">Vectron AI</div>
  </div>
  <div class="header-right">
    <div class="token-badge" id="token-count">0 tokens</div>
    <div class="plan-badge" id="plan-badge" onclick="openUpgradeModal()">Free</div>
    <button class="icon-btn" onclick="resetChat()" title="New chat">↺</button>
  </div>
</header>

<div class="main">
  <aside class="sidebar">
    <div class="sidebar-label">Plan</div>
    <button class="tool-btn upgrade-btn" onclick="openUpgradeModal()" id="upgrade-sidebar-btn">
      <div class="tool-icon">📱</div> Upgrade ($12/mo)
    </button>
    <div class="sidebar-divider"></div>
    <div class="sidebar-label">Model</div>
    <div class="sidebar-model">
      <select id="model-select" onchange="switchModel(this.value)">
        <option>Loading...</option>
      </select>
    </div>
    <div class="sidebar-divider"></div>
    <div class="sidebar-label">Tools</div>
    <button class="tool-btn" onclick="openTool('code')"><div class="tool-icon">⌨</div> Generate Code</button>
    <button class="tool-btn" onclick="openTool('calculate')"><div class="tool-icon">∑</div> Calculator</button>
    <button class="tool-btn" onclick="openTool('system_info')"><div class="tool-icon">⚙</div> System Info</button>
    <button class="tool-btn" onclick="openTool('image')"><div class="tool-icon">🎨</div> Generate Image</button>
    <button class="tool-btn locked" id="video-tool-btn" onclick="handleVideoToolClick()">
      <div class="tool-icon">🎬</div> Generate Video
      <span class="pro-lock" id="video-lock-label">🔒 PRO</span>
    </button>
    <div class="sidebar-divider"></div>
    <div class="sidebar-label">Session</div>
    <button class="tool-btn" onclick="resetChat()"><div class="tool-icon">↺</div> New Chat</button>
  </aside>

  <div class="chat-area">
    <div id="messages">
      <div class="empty-state" id="empty-state">
        <div class="empty-icon">⚡</div>
        <h2>What can I help with?</h2>
        <p>Ask anything, generate code, or use the tools.</p>
        <div class="suggestion-chips">
          <div class="chip" onclick="sendSuggestion('Write a Python Flask REST API with JWT auth')">Flask REST API</div>
          <div class="chip" onclick="sendSuggestion('Explain async/await in Python')">Async/Await</div>
          <div class="chip" onclick="sendSuggestion('Write a web scraper with requests and BeautifulSoup')">Web scraper</div>
          <div class="chip" onclick="openTool('image')">🎨 Generate Image</div>
          <div class="chip pro-chip" onclick="openUpgradeModal()">🎬 Generate Video (PRO)</div>
        </div>
      </div>
    </div>
    <div class="input-area">
      <div class="input-wrap">
        <textarea id="user-input" rows="1" placeholder="Message Vectron..." onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
        <button class="send-btn" id="send-btn" onclick="sendMessage()">↑</button>
      </div>
      <div class="input-hint">Enter to send · Shift+Enter for new line</div>
      <div class="remaining-badge" id="remaining-badge"></div>
    </div>
  </div>
</div>

<div class="mobile-nav">
  <div style="display:flex;justify-content:space-around;width:100%;">
    <button class="mobile-nav-btn" onclick="openSheet('tools')"><span class="nav-icon">⚙</span><span>Tools</span></button>
    <button class="mobile-nav-btn" onclick="resetChat()"><span class="nav-icon">↺</span><span>New</span></button>
    <button class="mobile-nav-btn" onclick="openSheet('model')"><span class="nav-icon">🤖</span><span>Model</span></button>
    <button class="mobile-nav-btn" onclick="openUpgradeModal()"><span class="nav-icon">📱</span><span>Upgrade</span></button>
  </div>
</div>

<!-- Tool Modal -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <h3 id="modal-title"></h3>
    <p id="modal-desc"></p>
    <textarea id="modal-input" rows="3"></textarea>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="runTool()">Run</button>
    </div>
  </div>
</div>

<!-- Upgrade Modal -->
<div class="modal-overlay" id="upgrade-modal">
  <div class="modal">
    <h3>📱 Upgrade with Orange Money</h3>
    <p>Get Pro features for just <strong>$12/month</strong></p>
    <div class="plan-cards" id="plan-cards"></div>
    <div id="payment-form" style="display:none;">
      <div class="payment-field">
        <label>📍 Country</label>
        <select id="payment-country" onchange="updatePhonePrefix()">
          <option value="">Select your country</option>
        </select>
      </div>
      <div class="payment-field">
        <label>📞 Orange Money Phone Number</label>
        <input type="tel" id="payment-phone" placeholder="e.g., 71234567">
        <small style="color:var(--text-muted);font-size:10px;">Enter without country code</small>
      </div>
      <div class="payment-instructions" id="payment-instructions"></div>
      <div class="payment-field" id="receipt-field" style="display:none;">
        <label>📎 Upload Payment Receipt</label>
        <input type="file" id="receipt-file" accept="image/*,.pdf">
        <div class="receipt-preview" id="receipt-preview"></div>
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeUpgradeModal()">Cancel</button>
      <button class="btn btn-upgrade" id="subscribe-btn" onclick="processUpgrade()">Continue with Orange Money</button>
    </div>
    <p style="font-size:10px;color:var(--text-dim);margin-top:12px;text-align:center;">🔒 Secure · Upload receipt for instant activation</p>
  </div>
</div>

<script>
marked.setOptions({breaks:true,gfm:true});
let currentTool=null,selectedPlan='pro',currentUserPlan='free',currentPaymentId=null,paymentStep='select',countriesData={};

const toolConfig={
  code:       {title:'Generate Code',  desc:'Describe what you want and AI will write it.',                       placeholder:'e.g. Flask REST API with JWT auth'},
  calculate:  {title:'Calculator',     desc:'Evaluate a math expression.',                                        placeholder:'e.g. (100 * 1.15) + 50'},
  system_info:{title:'System Info',    desc:'Show current system info.',                                          placeholder:''},
  image:      {title:'Generate Image', desc:'Free: Unsplash photo · Pro: AI-generated image',                    placeholder:'e.g. A futuristic city at sunset, cyberpunk style'},
  video:      {title:'Generate Video', desc:'Describe the video you want to generate. (Pro only)',                placeholder:'e.g. A drone shot over a misty mountain at sunrise'}
};

function handleVideoToolClick(){
  if(currentUserPlan==='free') openUpgradeModal();
  else openTool('video');
}

window.copyCode=function(btn,b64){
  try{
    const code=atob(b64);
    navigator.clipboard.writeText(code).then(()=>{
      const orig=btn.innerHTML;
      btn.classList.add('copied');
      btn.innerHTML='<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg> Copied!';
      setTimeout(()=>{btn.classList.remove('copied');btn.innerHTML=orig;},2000);
    }).catch(()=>prompt('Copy manually:',code));
  }catch(e){btn.innerHTML='❌ Failed!';setTimeout(()=>{btn.innerHTML='Copy';},2000);}
};

function renderMarkdown(text){
  const w=document.createElement('div');
  w.innerHTML=marked.parse(text);
  w.querySelectorAll('pre').forEach(pre=>{
    const code=pre.querySelector('code');
    if(!code)return;
    let lang='text';
    if(code.className){const m=code.className.match(/language-(\w+)/);if(m)lang=m[1];}
    const b64=btoa(unescape(encodeURIComponent(code.textContent)));
    const block=document.createElement('div');
    block.className='code-block';
    block.innerHTML=`<div class="code-header"><span class="code-lang">${lang}</span><button class="copy-btn" onclick="copyCode(this,'${b64}')"><svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg> Copy</button></div><pre><code class="language-${lang}">${escapeHtml(code.textContent)}</code></pre>`;
    pre.replaceWith(block);
  });
  w.querySelectorAll('pre code').forEach(el=>hljs.highlightElement(el));
  return w.innerHTML;
}

function escapeHtml(t){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function scrollBottom(){const m=document.getElementById('messages');m.scrollTop=m.scrollHeight;}
function hideEmpty(){const e=document.getElementById('empty-state');if(e)e.remove();}

function appendUserMsg(text){
  hideEmpty();
  const d=document.createElement('div');
  d.className='msg user';
  d.innerHTML=`<div class="avatar">U</div><div class="bubble"><div class="bubble-inner">${escapeHtml(text)}</div></div>`;
  document.getElementById('messages').appendChild(d);scrollBottom();
}

function appendAgentMsg(text){
  const d=document.createElement('div');
  d.className='msg agent';
  d.innerHTML=`<div class="avatar">V</div><div class="bubble"><div class="bubble-inner">${renderMarkdown(text)}</div></div>`;
  document.getElementById('messages').appendChild(d);scrollBottom();
}

function appendToolResult(name,result,saved,type,extra){
  hideEmpty();
  let content='';
  if(type==='image'&&extra&&extra.image_url){
    content=`<div class="image-container"><img src="${extra.image_url}" alt="Generated" class="generated-image" onerror="this.src='https://placehold.co/512x512?text=Failed'"></div>`;
    if(extra.prompt) content+=`<div style="margin-top:8px;font-size:12px;color:var(--text-muted);">Prompt: ${escapeHtml(extra.prompt)}</div>`;
    if(extra.remaining!==undefined) content+=`<div style="margin-top:6px;font-size:11px;color:var(--orange);">🎨 ${extra.remaining} images remaining today</div>`;
  } else if(type==='video'&&extra&&extra.video_url){
    content=`<div class="video-container"><video controls class="generated-video"><source src="${extra.video_url}" type="video/mp4">Your browser does not support video.</video></div>`;
    if(extra.prompt) content+=`<div style="margin-top:8px;font-size:12px;color:var(--text-muted);">Prompt: ${escapeHtml(extra.prompt)}</div>`;
    content+=`<div style="margin-top:8px;display:flex;align-items:center;gap:12px;"><a href="${extra.video_url}" download style="font-size:12px;color:var(--accent);">📥 Download</a>${extra.remaining!==undefined?`<span style="font-size:11px;color:var(--orange);">🎬 ${extra.remaining} videos remaining</span>`:''}`;
  } else if(extra&&extra.requires_upgrade){
    content=renderMarkdown(result);
    content+=`<div style="margin-top:12px;"><button class="btn btn-upgrade" onclick="openUpgradeModal()" style="font-size:12px;padding:8px 16px;">📱 Upgrade with Orange Money</button></div>`;
  } else {
    content=renderMarkdown(result);
  }
  const d=document.createElement('div');
  d.className='tool-result';
  d.innerHTML=`<div class="tool-result-header">⚙ ${name}</div>${content}${saved?`<div style="margin-top:8px;font-size:11px;color:var(--text-muted);">💾 ${saved}</div>`:''}`;
  document.getElementById('messages').appendChild(d);scrollBottom();
}

function showThinking(){
  hideEmpty();
  const d=document.createElement('div');
  d.className='thinking';d.id='thinking';
  d.innerHTML=`<div class="avatar" style="background:linear-gradient(135deg,var(--accent),var(--accent2));color:white">V</div><div class="thinking-dots"><span></span><span></span><span></span></div>`;
  document.getElementById('messages').appendChild(d);scrollBottom();
}
function removeThinking(){const t=document.getElementById('thinking');if(t)t.remove();}

async function sendMessage(){
  const input=document.getElementById('user-input');
  const text=input.value.trim();
  if(!text)return;
  input.value='';autoResize(input);setLoading(true);
  appendUserMsg(text);showThinking();
  try{
    const res=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    const data=await res.json();
    removeThinking();
    if(data.error){
      if(res.status===429){appendAgentMsg(`**⚠️ Limit Reached**\n\n${data.error}`);openUpgradeModal();}
      else appendAgentMsg(`**Error:** ${data.error}`);
    } else {
      appendAgentMsg(data.reply);
      document.getElementById('token-count').textContent=`${data.tokens.toLocaleString()} tokens`;
      if(data.remaining_messages!==undefined)
        document.getElementById('remaining-badge').textContent=`✨ ${data.remaining_messages} messages remaining today`;
    }
  }catch(e){removeThinking();appendAgentMsg(`**Error:** ${e.message}`);}
  finally{setLoading(false);}
}

function sendSuggestion(text){document.getElementById('user-input').value=text;sendMessage();}
function handleKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}}
function autoResize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,140)+'px';}
function setLoading(on){document.getElementById('send-btn').disabled=on;}

function openTool(name){
  currentTool=name;
  const cfg=toolConfig[name];
  document.getElementById('modal-title').textContent=cfg.title;
  document.getElementById('modal-desc').textContent=cfg.desc;
  const inp=document.getElementById('modal-input');
  inp.placeholder=cfg.placeholder;inp.value='';
  inp.style.display=name==='system_info'?'none':'block';
  document.getElementById('modal').classList.add('open');
  if(name!=='system_info')setTimeout(()=>inp.focus(),50);
}
function closeModal(){document.getElementById('modal').classList.remove('open');currentTool=null;}
document.getElementById('modal').addEventListener('click',function(e){if(e.target===this)closeModal();});

async function runTool(){
  const args=document.getElementById('modal-input').value.trim();
  const tool=currentTool;
  closeModal();setLoading(true);

  if(tool==='image'){
    if(!args){appendToolResult('Generate Image','Please describe the image you want.',null,'text');setLoading(false);return;}
    showThinking();
    try{
      const res=await fetch('/generate-image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:args})});
      const data=await res.json();
      removeThinking();
      if(data.error){
        if(res.status===429){appendToolResult('Rate Limit',data.error,null,'text');openUpgradeModal();}
        else appendToolResult('Error',data.error,null,'text');
      } else {
        appendToolResult('Generated Image','',null,'image',{image_url:data.image_url,prompt:data.prompt,remaining:data.remaining_images});
        if(data.remaining_images!==undefined)
          document.getElementById('remaining-badge').textContent=`🎨 ${data.remaining_images} images remaining today`;
      }
    }catch(e){removeThinking();appendToolResult('Error',e.message,null,'text');}
    finally{setLoading(false);}
    return;
  }

  showThinking();
  try{
    const res=await fetch('/tool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool:tool||'system_info',args})});
    const data=await res.json();
    removeThinking();
    if(data.error){
      if(res.status===429){appendToolResult('Rate Limit',data.error,null,'text');openUpgradeModal();}
      else appendToolResult('Error',data.error,null,'text');
    } else if(data.type==='video'){
      appendToolResult(toolConfig[tool]?.title||tool,data.result,null,'video',{video_url:data.video_url,prompt:args,remaining:data.remaining_videos});
      if(data.requires_upgrade)openUpgradeModal();
    } else {
      appendToolResult(toolConfig[tool]?.title||tool,data.result,data.saved,data.type||'text',data.requires_upgrade?{requires_upgrade:true}:null);
      if(data.requires_upgrade)openUpgradeModal();
    }
  }catch(e){removeThinking();appendToolResult('Error',e.message,null,'text');}
  finally{setLoading(false);}
}

async function loadCountries(){
  try{
    const res=await fetch('/orange-money-countries');
    const data=await res.json();
    countriesData=data.countries;
    document.getElementById('payment-country').innerHTML='<option value="">Select your country</option>'+
      Object.entries(countriesData).map(([code,c])=>`<option value="${code}">${c.name} (+${c.prefix}) - ${c.mobile_money}</option>`).join('');
  }catch(e){console.error(e);}
}

function updatePhonePrefix(){
  const cc=document.getElementById('payment-country').value;
  document.getElementById('payment-phone').placeholder=(cc&&countriesData[cc])?`e.g., ${'7'.repeat(countriesData[cc].length)}`:'e.g., 71234567';
}

async function loadPlans(){
  try{
    const res=await fetch('/plans');
    const data=await res.json();
    currentUserPlan=data.current_plan;
    const badge=document.getElementById('plan-badge');
    const upgradeBtn=document.getElementById('upgrade-sidebar-btn');
    if(currentUserPlan==='free'){
      badge.textContent='Free';badge.className='plan-badge';
      if(upgradeBtn)upgradeBtn.style.display='flex';
    } else {
      badge.textContent=currentUserPlan.toUpperCase();
      badge.className=`plan-badge ${currentUserPlan}`;
      if(upgradeBtn)upgradeBtn.style.display='none';
      const vb=document.getElementById('video-tool-btn');
      const vl=document.getElementById('video-lock-label');
      if(vb)vb.classList.remove('locked');
      if(vl)vl.style.display='none';
    }
    const plansDiv=document.getElementById('plan-cards');
    if(plansDiv&&data.plans){
      plansDiv.innerHTML=Object.entries(data.plans)
        .filter(([id])=>id!==currentUserPlan&&id!=='free')
        .map(([id,plan])=>`
          <div class="plan-card ${selectedPlan===id?'selected':''}" onclick="selectPlan('${id}')">
            <div class="plan-name">${plan.name}</div>
            <div class="plan-price">$${plan.price}<small>/mo</small></div>
            <ul class="plan-features">${plan.features.slice(0,3).map(f=>`<li>✓ ${f}</li>`).join('')}${plan.features.length>3?`<li>+${plan.features.length-3} more</li>`:''}</ul>
          </div>`).join('');
    }
    if(data.usage){
      const limits=data.plans[currentUserPlan];
      const remMsgs=limits.max_messages_per_day-(data.usage.messages||0);
      const imgLimit=currentUserPlan==='free'?5:currentUserPlan==='pro'?50:500;
      const remImgs=imgLimit-(data.usage.images||0);
      document.getElementById('remaining-badge').textContent=`💬 ${remMsgs} msgs · 🎨 ${remImgs} images left today`;
    }
  }catch(e){console.error(e);}
}

function selectPlan(planId){
  selectedPlan=planId;
  document.querySelectorAll('.plan-card').forEach(c=>{
    c.classList.toggle('selected',c.querySelector('.plan-name')?.innerText.toLowerCase()===planId);
  });
  resetPaymentForm();
}

function resetPaymentForm(){
  paymentStep='select';currentPaymentId=null;
  document.getElementById('payment-form').style.display='none';
  document.getElementById('payment-instructions').innerHTML='';
  document.getElementById('receipt-field').style.display='none';
  document.getElementById('receipt-file').value='';
  document.getElementById('receipt-preview').innerHTML='';
  document.getElementById('subscribe-btn').textContent='Continue with Orange Money';
  document.getElementById('subscribe-btn').className='btn btn-upgrade';
}

function openUpgradeModal(){
  selectedPlan='pro';resetPaymentForm();
  loadPlans();loadCountries();
  document.getElementById('upgrade-modal').classList.add('open');
}
function closeUpgradeModal(){document.getElementById('upgrade-modal').classList.remove('open');resetPaymentForm();}

async function processUpgrade(){
  const btn=document.getElementById('subscribe-btn');
  if(paymentStep==='select'){
    document.getElementById('payment-form').style.display='block';
    btn.textContent='Continue to Payment';btn.className='btn btn-orange';
    paymentStep='payment';return;
  }
  if(paymentStep==='payment'){
    const cc=document.getElementById('payment-country').value;
    const ph=document.getElementById('payment-phone').value.trim();
    if(!cc){alert('Please select your country');return;}
    if(!ph){alert('Please enter your phone number');return;}
    btn.textContent='Processing...';btn.disabled=true;
    try{
      const res=await fetch('/initiate-payment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan_id:selectedPlan,country_code:cc,phone_number:ph})});
      const data=await res.json();
      if(data.success){
        currentPaymentId=data.payment_id;
        document.getElementById('payment-instructions').innerHTML=`<strong>📱 Payment Required</strong>\n\n${data.instructions}`;
        document.getElementById('receipt-field').style.display='block';
        document.getElementById('receipt-file').onchange=function(){
          document.getElementById('receipt-preview').innerHTML=this.files[0]?`📎 ${this.files[0].name}`:'';
        };
        paymentStep='receipt';btn.textContent='Upload Receipt & Activate';btn.disabled=false;
      } else {alert('Error: '+(data.error||'Failed'));btn.disabled=false;btn.textContent='Continue to Payment';}
    }catch(e){alert('Error: '+e.message);btn.disabled=false;btn.textContent='Continue to Payment';}
    return;
  }
  if(paymentStep==='receipt'){
    const file=document.getElementById('receipt-file').files[0];
    if(!file){alert('Please upload your payment receipt');return;}
    if(!currentPaymentId){alert('Session expired. Please restart.');resetPaymentForm();return;}
    btn.textContent='Uploading...';btn.disabled=true;
    const fd=new FormData();
    fd.append('payment_id',currentPaymentId);fd.append('receipt',file);
    try{
      const res=await fetch('/upload-receipt',{method:'POST',body:fd});
      const data=await res.json();
      if(data.success){alert(data.message);closeUpgradeModal();location.reload();}
      else{alert('Failed: '+(data.error||'Try again'));btn.disabled=false;btn.textContent='Upload Receipt & Activate';}
    }catch(e){alert('Error: '+e.message);btn.disabled=false;btn.textContent='Upload Receipt & Activate';}
  }
}

async function loadModels(){
  const res=await fetch('/models');
  const data=await res.json();
  const opts=data.models.map(m=>`<option value="${m.id}"${m.id===data.current?' selected':''}>${m.label}</option>`).join('');
  ['model-select','model-select-mobile'].forEach(id=>{const el=document.getElementById(id);if(el)el.innerHTML=opts;});
}

async function switchModel(id){
  if(!id)return;
  try{await fetch('/model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:id})});}
  catch(e){console.error(e);}
  closeSheet();
}

function openSheet(section){
  document.getElementById('sheet-tools-section').style.display=section==='model'?'none':'block';
  document.getElementById('sheet-model-section').style.display=section==='model'?'block':'none';
  document.getElementById('sheet').classList.add('open');
  document.getElementById('sheet-overlay').classList.add('open');
}
function closeSheet(){
  document.getElementById('sheet').classList.remove('open');
  document.getElementById('sheet-overlay').classList.remove('open');
}
function sheetTool(name){closeSheet();openTool(name);}

async function resetChat(){
  await fetch('/reset',{method:'POST'});
  document.getElementById('messages').innerHTML=`
    <div class="empty-state" id="empty-state">
      <div class="empty-icon">⚡</div>
      <h2>What can I help with?</h2>
      <p>Ask anything, generate code, or use the tools.</p>
      <div class="suggestion-chips">
        <div class="chip" onclick="sendSuggestion('Write a Python Flask REST API with JWT auth')">Flask REST API</div>
        <div class="chip" onclick="sendSuggestion('Explain async/await in Python')">Async/Await</div>
        <div class="chip" onclick="sendSuggestion('Write a web scraper with requests and BeautifulSoup')">Web scraper</div>
        <div class="chip" onclick="openTool('image')">🎨 Generate Image</div>
        <div class="chip pro-chip" onclick="openUpgradeModal()">🎬 Generate Video (PRO)</div>
      </div>
    </div>`;
  document.getElementById('token-count').textContent='0 tokens';
  loadPlans();
}

loadModels();
loadPlans();
loadCountries();
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
