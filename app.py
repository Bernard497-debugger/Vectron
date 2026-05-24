from flask import Flask, request, jsonify, session, send_file, render_template_string
import requests
import os
import re
import platform
import secrets
import json
from datetime import datetime, timedelta
from functools import wraps
import base64
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DEFAULT_MODEL = "laguna-llama-3.2-3b-instruct:free"
IMAGE_MODEL = "black-forest-labs/flux-schnell-free"  # Free image model
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
SITE_URL = os.environ.get("SITE_URL", "https://vectron.onrender.com")

# Configure upload folder for receipts (in production, use cloud storage)
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "receipts")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── PAID PLANS CONFIGURATION ─────────────────────────────────────────────────
SUBSCRIPTION_PLANS = {
    "free": {
        "name": "Free",
        "price": 0,
        "price_id": "free",
        "max_tokens_per_day": 10000,
        "max_messages_per_day": 50,
        "features": ["Basic AI Chat", "Code Generation", "Calculator Tool", "System Info", "Image Generation (5/day)"],
        "models": ["laguna-llama-3.2-3b-instruct:free"]
    },
    "pro": {
        "name": "Pro",
        "price": 12.00,
        "price_id": "pro_monthly",
        "max_tokens_per_day": 100000,
        "max_messages_per_day": 500,
        "features": ["Everything in Free", "Priority Response", "Longer Context (128k)", "Advanced Models", "More Images (50/day)", "No Ads"],
        "models": ["laguna-llama-3.2-3b-instruct:free"]
    },
    "enterprise": {
        "name": "Enterprise",
        "price": 49.99,
        "price_id": "enterprise_monthly",
        "max_tokens_per_day": 1000000,
        "max_messages_per_day": 5000,
        "features": ["Everything in Pro", "Dedicated Support", "Custom AI Training", "API Access", "Team Collaboration", "SLA Guarantee"],
        "models": ["laguna-llama-3.2-3b-instruct:free"]
    }
}

# Orange Money phone number prefix for different countries (including Botswana)
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

# Store pending payments (in production, use a database)
PENDING_PAYMENTS = {}
VERIFIED_PAYMENTS = {}

# Simulated user database (in production, use a real database)
USERS = {}
SUBSCRIPTIONS = {}

# Track image generation counts per user
IMAGE_GENERATION_COUNTS = {}

# Exchange rates (approximate - in production, use a real API)
EXCHANGE_RATES = {
    "BWP": 13.5,  # 1 USD ≈ 13.5 BWP
    "XOF": 600,   # 1 USD ≈ 600 XOF
    "XAF": 600,   # 1 USD ≈ 600 XAF
    "GNF": 8600,  # 1 USD ≈ 8600 GNF
}

AVAILABLE_MODELS = [
    {"id": "laguna-llama-3.2-3b-instruct:free", "label": "Laguna 3B", "plan": "free"},
]

SYSTEM_PROMPT = """You are Vectron, a powerful AI agent. You can:
- Answer questions clearly and concisely
- Help with coding, writing, analysis, and research
- Remember the conversation history within this session
- Generate clean, working code when asked
- Generate images when asked (use the image generation tool)

When writing code, always wrap it in triple backtick fences with the language name.
Be direct, smart, and actually useful."""

CODE_SYSTEM_PROMPT = """You are an expert code generator.
When given a description, respond with clean, working code only.
Include a brief comment at the top explaining what it does.
Always wrap code in triple backtick fences with the correct language.
No extra explanation outside the code block."""

# ─── AUTH & SUBSCRIPTION HELPERS ──────────────────────────────────────────────

def get_user_plan():
    """Get the current user's subscription plan"""
    user_id = session.get("user_id")
    if not user_id:
        return "free"
    sub = SUBSCRIPTIONS.get(user_id, {})
    if sub.get("status") == "active" and sub.get("expires_at", datetime.now()) > datetime.now():
        return sub.get("plan", "free")
    return "free"

def check_rate_limit():
    """Check if user has exceeded their plan's limits"""
    user_id = session.get("user_id", "anonymous")
    plan = get_user_plan()
    limits = SUBSCRIPTION_PLANS[plan]
    
    today = datetime.now().date().isoformat()
    
    # Initialize usage tracking
    if "usage" not in session:
        session["usage"] = {}
    if today not in session["usage"]:
        session["usage"][today] = {"messages": 0, "tokens": 0, "images": 0}
    
    usage = session["usage"][today]
    
    # Check image limits for free tier (5 per day)
    image_limit = 5 if plan == "free" else 50 if plan == "pro" else 500
    if usage.get("images", 0) >= image_limit:
        return False, f"You've reached your daily image generation limit ({image_limit} images). Please upgrade to Pro for just $12/month!"
    
    if usage["messages"] >= limits["max_messages_per_day"]:
        return False, f"You've reached your daily message limit ({limits['max_messages_per_day']} messages). Please upgrade to Pro for just $12/month!"
    if usage["tokens"] >= limits["max_tokens_per_day"]:
        return False, f"You've reached your daily token limit ({limits['max_tokens_per_day']} tokens). Please upgrade to Pro for just $12/month!"
    
    return True, None

def update_usage(messages_count=1, tokens_used=0, images_count=0):
    """Update user's usage statistics"""
    today = datetime.now().date().isoformat()
    if "usage" not in session:
        session["usage"] = {}
    if today not in session["usage"]:
        session["usage"][today] = {"messages": 0, "tokens": 0, "images": 0}
    
    session["usage"][today]["messages"] += messages_count
    session["usage"][today]["tokens"] += tokens_used
    session["usage"][today]["images"] += images_count

def require_subscription(required_plan):
    """Decorator to check if user has required subscription"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_plan = get_user_plan()
            plan_levels = {"free": 0, "pro": 1, "enterprise": 2}
            if plan_levels.get(user_plan, 0) < plan_levels.get(required_plan, 0):
                return jsonify({"error": f"This feature requires {SUBSCRIPTION_PLANS[required_plan]['name']} plan. Please upgrade!"}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ─── API HELPERS ──────────────────────────────────────────────────────────────

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
    
    # Check if user has access to the requested model
    user_plan = get_user_plan()
    model_info = next((m for m in AVAILABLE_MODELS if m["id"] == (model or DEFAULT_MODEL)), None)
    if model_info and model_info.get("plan") not in ["free", user_plan]:
        raise PermissionError(f"Model {model_info['label']} requires {SUBSCRIPTION_PLANS[model_info['plan']]['name']} plan")
    
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

def generate_image(prompt):
    """Generate an image from text prompt using OpenRouter"""
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
    
    # Extract image URL from response
    content = data["choices"][0]["message"]["content"]
    
    # If content is a URL, return it directly
    if content.startswith("http"):
        return content
    
    # Try to extract markdown image URL
    import re
    url_match = re.search(r'!\[.*?\]\((.*?)\)', content)
    if url_match:
        return url_match.group(1)
    
    return content

def calculate(expression):
    try:
        allowed = set("0123456789+-*/()., ")
        if all(c in allowed for c in expression):
            return f"Result: {eval(expression)}"
        return "Error: unsafe expression"
    except Exception as e:
        return f"Error: {e}"

def get_system_info():
    return f"OS: {platform.system()} {platform.release()} | Python: {platform.python_version()}"

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "key_set": bool(OPENROUTER_API_KEY)})

@app.route("/logo")
def logo():
    logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    if os.path.exists(logo_path):
        return send_file(logo_path, mimetype="image/png")
    return "", 404

@app.route("/models")
def get_models():
    current = session.get("model", DEFAULT_MODEL)
    user_plan = get_user_plan()
    
    # Filter models based on user's plan
    available = [m for m in AVAILABLE_MODELS if m["plan"] in ["free", user_plan]]
    return jsonify({"models": available, "current": current})

@app.route("/model", methods=["POST"])
def set_model():
    model_id = (request.json or {}).get("model", "").strip()
    if not model_id:
        return jsonify({"error": "No model provided"}), 400
    
    # Verify user has access to this model
    model_info = next((m for m in AVAILABLE_MODELS if m["id"] == model_id), None)
    if model_info:
        user_plan = get_user_plan()
        if model_info["plan"] not in ["free", user_plan]:
            return jsonify({"error": f"Model {model_info['label']} requires {SUBSCRIPTION_PLANS[model_info['plan']]['name']} plan"}), 403
    
    session["model"] = model_id
    return jsonify({"ok": True, "model": model_id})

@app.route("/plans", methods=["GET"])
def get_plans():
    """Get available subscription plans"""
    user_plan = get_user_plan()
    today = datetime.now().date().isoformat()
    usage = session.get("usage", {}).get(today, {"messages": 0, "tokens": 0, "images": 0})
    return jsonify({
        "plans": SUBSCRIPTION_PLANS,
        "current_plan": user_plan,
        "usage": usage
    })

@app.route("/orange-money-countries", methods=["GET"])
def get_orange_countries():
    """Get list of Orange Money supported countries"""
    return jsonify({"countries": ORANGE_MONEY_COUNTRIES})

@app.route("/initiate-payment", methods=["POST"])
def initiate_payment():
    """Initiate an Orange Money payment with receipt upload"""
    data = request.json or {}
    plan_id = data.get("plan_id")
    phone_number = data.get("phone_number", "").strip()
    country_code = data.get("country_code", "bw")
    
    if plan_id not in SUBSCRIPTION_PLANS:
        return jsonify({"error": "Invalid plan"}), 400
    
    if plan_id == "free":
        return jsonify({"error": "Free plan doesn't require payment"}), 400
    
    # Validate phone number format
    country_info = ORANGE_MONEY_COUNTRIES.get(country_code)
    if not country_info:
        return jsonify({"error": "Invalid country selection"}), 400
    
    # Basic phone number validation (remove spaces and +)
    phone_number = re.sub(r'[\s\+]', '', phone_number)
    if not phone_number.isdigit():
        return jsonify({"error": "Phone number must contain only digits"}), 400
    
    # Create payment record
    user_id = session.get("user_id")
    if not user_id:
        user_id = secrets.token_hex(16)
        session["user_id"] = user_id
    
    payment_id = secrets.token_hex(16)
    amount_usd = SUBSCRIPTION_PLANS[plan_id]["price"]
    
    # Calculate local currency amount
    currency = country_info.get("currency", "USD")
    exchange_rate = EXCHANGE_RATES.get(currency, 1)
    amount_local = round(amount_usd * exchange_rate, 2)
    
    PENDING_PAYMENTS[payment_id] = {
        "user_id": user_id,
        "plan_id": plan_id,
        "amount_usd": amount_usd,
        "amount_local": amount_local,
        "currency": currency,
        "phone_number": f"{country_info['prefix']}{phone_number}",
        "country": country_info["name"],
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "receipt_filename": None
    }
    
    # Generate payment instructions
    instructions = f"""
    📱 {country_info['mobile_money']} Payment Instructions:
    
    1. Open your {country_info['mobile_money']} app or dial #144#
    2. Select 'Send Money'
    3. Enter our merchant number: **01 23 45 67 89**
    4. Enter amount: **{amount_local} {currency}** (${amount_usd} USD)
    5. Enter your PIN to confirm
    6. Take a screenshot of the confirmation receipt
    7. Upload the receipt using the form below
    
    💡 Tip: Make sure the receipt shows the transaction ID and amount clearly.
    """
    
    return jsonify({
        "success": True,
        "payment_id": payment_id,
        "amount_usd": amount_usd,
        "amount_local": amount_local,
        "currency": currency,
        "plan_name": SUBSCRIPTION_PLANS[plan_id]["name"],
        "phone_number_display": f"{country_info['prefix']}{phone_number}",
        "instructions": instructions,
        "message": f"Please send {amount_local} {currency} via {country_info['mobile_money']} to complete your subscription. After payment, upload your receipt."
    })

@app.route("/upload-receipt", methods=["POST"])
def upload_receipt():
    """Upload receipt for payment verification"""
    payment_id = request.form.get("payment_id")
    
    if not payment_id:
        return jsonify({"error": "Payment ID is required"}), 400
    
    payment = PENDING_PAYMENTS.get(payment_id)
    if not payment:
        return jsonify({"error": "Invalid payment session"}), 404
    
    if payment["status"] != "pending":
        return jsonify({"error": f"Payment already {payment['status']}"}), 400
    
    if "receipt" not in request.files:
        return jsonify({"error": "No receipt file uploaded"}), 400
    
    receipt = request.files["receipt"]
    if receipt.filename == "":
        return jsonify({"error": "No file selected"}), 400
    
    # Save receipt
    filename = f"{payment_id}_{secrets.token_hex(8)}_{secure_filename(receipt.filename)}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    receipt.save(filepath)
    
    # Update payment record
    payment["status"] = "pending_review"
    payment["receipt_filename"] = filename
    payment["receipt_uploaded_at"] = datetime.now().isoformat()
    
    # For demo purposes, auto-verify after receipt upload
    # In production, this would go to an admin queue for manual verification
    payment["status"] = "verified"
    payment["verified_at"] = datetime.now().isoformat()
    
    # Activate the subscription
    user_id = payment["user_id"]
    plan_id = payment["plan_id"]
    
    SUBSCRIPTIONS[user_id] = {
        "plan": plan_id,
        "status": "active",
        "started_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(days=30),
        "payment_method": "orange_money",
        "receipt_filename": filename,
        "country": payment["country"]
    }
    
    VERIFIED_PAYMENTS[payment_id] = payment
    
    return jsonify({
        "success": True,
        "message": f"Receipt uploaded! Your {SUBSCRIPTION_PLANS[plan_id]['name']} plan is now active.",
        "plan": SUBSCRIPTION_PLANS[plan_id]
    })

@app.route("/payment-status/<payment_id>", methods=["GET"])
def payment_status(payment_id):
    """Check payment status"""
    payment = PENDING_PAYMENTS.get(payment_id)
    if not payment:
        return jsonify({"error": "Payment not found"}), 404
    
    return jsonify({
        "status": payment["status"],
        "plan_id": payment["plan_id"],
        "amount_usd": payment["amount_usd"],
        "amount_local": payment.get("amount_local"),
        "currency": payment.get("currency")
    })

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
    
    # Check rate limits
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
        
        # Keep last 40 messages to avoid token bloat
        session["history"] = history[-40:]
        session["tokens"] = session.get("tokens", 0) + tokens_used
        
        # Update usage tracking
        update_usage(messages_count=1, tokens_used=tokens_used)
        
        today = datetime.now().date().isoformat()
        remaining_messages = SUBSCRIPTION_PLANS[get_user_plan()]["max_messages_per_day"] - session["usage"].get(today, {}).get("messages", 0)
        
        return jsonify({
            "reply": reply,
            "tokens": session["tokens"],
            "remaining_messages": remaining_messages
        })
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"HTTP {e.response.status_code}: {e.response.text}"}), 502
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/generate-image", methods=["POST"])
def generate_image_route():
    """Generate an image from text prompt"""
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    
    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400
    
    # Check rate limits
    allowed, error_msg = check_rate_limit()
    if not allowed:
        return jsonify({"error": error_msg}), 429
    
    try:
        image_url = generate_image(prompt)
        update_usage(images_count=1)
        
        # Get remaining image count
        today = datetime.now().date().isoformat()
        usage = session.get("usage", {}).get(today, {"images": 0})
        plan = get_user_plan()
        image_limit = 5 if plan == "free" else 50 if plan == "pro" else 500
        remaining_images = image_limit - usage.get("images", 0)
        
        return jsonify({
            "success": True,
            "image_url": image_url,
            "prompt": prompt,
            "remaining_images": remaining_images
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/tool", methods=["POST"])
def tool():
    data = request.json or {}
    tool_name = data.get("tool", "").strip()
    args = data.get("args", "").strip()

    # Check rate limits
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
            active_model = session.get("model", DEFAULT_MODEL)
            reply, usage = call_openrouter(messages, temperature=0.3, model=active_model)
            update_usage(messages_count=1, tokens_used=usage.get("total_tokens", 0))
            return jsonify({"result": reply})
        except Exception as e:
            return jsonify({"result": f"Error: {e}"}), 500
    
    elif tool_name == "image":
        if not args:
            return jsonify({"result": "Describe the image you want"}), 400
        try:
            image_url = generate_image(args)
            update_usage(images_count=1)
            return jsonify({
                "result": f"![Generated Image]({image_url})",
                "image_url": image_url,
                "type": "image"
            })
        except Exception as e:
            return jsonify({"result": f"Error generating image: {e}"}), 500

    return jsonify({"error": f"Unknown tool '{tool_name}'"}), 400

@app.route("/reset", methods=["POST"])
def reset():
    session["history"] = []
    session["tokens"] = 0
    return jsonify({"ok": True})

# ─── HTML ─────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0a0a0b">
<meta name="description" content="Vectron — AI Agent powered by OpenRouter">
<title>Vectron AI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}

:root{
  --bg:#0a0a0b;
  --surface:#111113;
  --surface2:#18181b;
  --surface3:#222227;
  --border:#2a2a30;
  --text:#e8e8ed;
  --text-muted:#6b6b7a;
  --text-dim:#3d3d4a;
  --accent:#3b82f6;
  --accent2:#60a5fa;
  --accent-glow:rgba(59,130,246,0.15);
  --user-bg:#1a1a20;
  --code-bg:#0d1117;
  --radius:12px;
  --radius-sm:8px;
  --nav-h:64px;
  --pro-gold:#fbbf24;
  --orange:#ff6600;
}

body{
  font-family:'Sora',sans-serif;
  background:var(--bg);
  color:var(--text);
  height:100dvh;
  display:flex;
  flex-direction:column;
  overflow:hidden;
}

/* ── HEADER ── */
header{
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:12px 20px;
  border-bottom:1px solid var(--border);
  background:var(--surface);
  flex-shrink:0;
  z-index:10;
}

.logo{display:flex;align-items:center;gap:10px;}
.logo img{height:30px;width:auto;}
.logo-text{font-size:15px;font-weight:600;letter-spacing:-0.3px;}

.header-right{display:flex;align-items:center;gap:8px;}
.token-badge{
  font-size:11px;color:var(--text-muted);
  font-family:'JetBrains Mono',monospace;
  background:var(--surface2);border:1px solid var(--border);
  padding:4px 10px;border-radius:20px;
}
.plan-badge{
  font-size:11px;font-weight:600;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  padding:4px 12px;border-radius:20px;cursor:pointer;
  transition:opacity .15s;
}
.plan-badge.pro{background:linear-gradient(135deg,#fbbf24,#f59e0b);}
.plan-badge.enterprise{background:linear-gradient(135deg,#8b5cf6,#6d28d9);}
.plan-badge:hover{opacity:.8;}

.icon-btn{
  width:32px;height:32px;border:1px solid var(--border);
  background:var(--surface2);color:var(--text-muted);
  border-radius:var(--radius-sm);cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  font-size:14px;transition:all .15s;
}
.icon-btn:hover{background:var(--surface3);color:var(--text);}

/* ── LAYOUT ── */
.main{display:flex;flex:1;overflow:hidden;}

/* ── SIDEBAR ── */
.sidebar{
  width:240px;border-right:1px solid var(--border);
  background:var(--surface);padding:16px 12px;
  display:flex;flex-direction:column;gap:4px;flex-shrink:0;overflow-y:auto;
}
.sidebar-label{
  font-size:10px;font-weight:600;color:var(--text-dim);
  letter-spacing:1px;text-transform:uppercase;padding:8px 8px 4px;
}
.tool-btn{
  display:flex;align-items:center;gap:10px;padding:9px 10px;
  border-radius:var(--radius-sm);border:none;background:transparent;
  color:var(--text-muted);font-family:'Sora',sans-serif;font-size:13px;
  cursor:pointer;transition:all .15s;text-align:left;width:100%;
}
.tool-btn:hover{background:var(--surface2);color:var(--text);}
.tool-btn .tool-icon{
  width:28px;height:28px;background:var(--surface3);
  border-radius:6px;display:flex;align-items:center;justify-content:center;
  font-size:13px;flex-shrink:0;
}
.sidebar-divider{height:1px;background:var(--border);margin:8px 0;}
.sidebar-model select{
  width:100%;background:var(--surface2);border:1px solid var(--border);
  border-radius:var(--radius-sm);color:var(--text);
  font-family:'Sora',sans-serif;font-size:12px;
  padding:8px 10px;outline:none;cursor:pointer;
  appearance:none;-webkit-appearance:none;
}
.upgrade-btn{
  background:linear-gradient(135deg,var(--orange),#ff8800);
  color:white;font-weight:600;border:none;
  margin-top:12px;
}
.upgrade-btn:hover{background:linear-gradient(135deg,#ff5500,#ff7700);color:white;}

/* ── CHAT AREA ── */
.chat-area{flex:1;display:flex;flex-direction:column;overflow:hidden;}

#messages{
  flex:1;overflow-y:auto;padding:20px;
  display:flex;flex-direction:column;gap:18px;scroll-behavior:smooth;
}
#messages::-webkit-scrollbar{width:4px;}
#messages::-webkit-scrollbar-track{background:transparent;}
#messages::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px;}

/* ── MESSAGES ── */
.msg{
  display:flex;gap:10px;animation:fadeUp .2s ease;
  max-width:800px;width:100%;margin:0 auto;
}
.msg.user{flex-direction:row-reverse;}
@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}

.avatar{
  width:30px;height:30px;border-radius:7px;
  display:flex;align-items:center;justify-content:center;
  font-size:13px;flex-shrink:0;font-weight:600;
}
.msg.agent .avatar{background:linear-gradient(135deg,var(--accent),var(--accent2));color:white;}
.msg.user .avatar{background:var(--surface3);color:var(--text-muted);font-size:11px;}

.bubble{flex:1;max-width:calc(100% - 40px);}
.bubble-inner{padding:11px 15px;border-radius:var(--radius);font-size:14px;line-height:1.7;}
.msg.agent .bubble-inner{background:transparent;color:var(--text);}
.msg.user .bubble-inner{
  background:var(--user-bg);border:1px solid var(--border);
  color:var(--text);border-radius:var(--radius) var(--radius) 4px var(--radius);
}

/* Image styling */
.generated-image {
  max-width: 100%;
  border-radius: var(--radius);
  margin: 10px 0;
  border: 1px solid var(--border);
}
.image-container {
  text-align: center;
  margin: 10px 0;
}

/* markdown */
.bubble-inner p{margin-bottom:10px;}
.bubble-inner p:last-child{margin-bottom:0;}
.bubble-inner ul,.bubble-inner ol{padding-left:20px;margin-bottom:10px;}
.bubble-inner li{margin-bottom:4px;}
.bubble-inner strong{color:var(--text);font-weight:600;}
.bubble-inner h1,.bubble-inner h2,.bubble-inner h3{font-weight:600;margin:14px 0 6px;color:var(--text);}
.bubble-inner h1:first-child,.bubble-inner h2:first-child,.bubble-inner h3:first-child{margin-top:0;}
.bubble-inner a{color:var(--accent);}
.bubble-inner blockquote{border-left:3px solid var(--accent);padding-left:12px;color:var(--text-muted);margin:10px 0;}
.bubble-inner code:not(pre code){
  font-family:'JetBrains Mono',monospace;font-size:12px;
  background:var(--surface3);border:1px solid var(--border);
  padding:2px 6px;border-radius:4px;color:var(--accent2);
}

/* code blocks */
.code-block{margin:12px 0;border-radius:var(--radius-sm);overflow:hidden;border:1px solid var(--border);background:var(--code-bg);}
.code-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:7px 12px;background:#161b22;border-bottom:1px solid var(--border);
}
.code-lang{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;}
.copy-btn{
  display:flex;align-items:center;gap:4px;padding:4px 10px;
  border-radius:5px;border:1px solid var(--border);background:var(--surface3);
  color:var(--text-muted);font-family:'Sora',sans-serif;font-size:11px;cursor:pointer;transition:all .15s;
}
.copy-btn:hover{background:var(--surface2);color:var(--text);}
.copy-btn.copied{color:#4ade80;border-color:#4ade80;}
.code-block pre{margin:0;padding:14px 16px;overflow-x:auto;font-size:13px;line-height:1.6;}
.code-block pre code{font-family:'JetBrains Mono',monospace;background:transparent!important;padding:0!important;border:none!important;}

/* tool result */
.tool-result{
  background:var(--surface2);border:1px solid var(--border);border-left:3px solid var(--accent);
  border-radius:var(--radius-sm);padding:12px 14px;font-size:13px;line-height:1.6;
  max-width:800px;margin:0 auto;animation:fadeUp .2s ease;
}
.tool-result-header{font-size:11px;color:var(--accent);font-weight:600;letter-spacing:.5px;text-transform:uppercase;margin-bottom:8px;}

/* thinking */
.thinking{display:flex;gap:10px;max-width:800px;margin:0 auto;align-items:center;}
.thinking-dots{display:flex;gap:4px;padding:12px 0;}
.thinking-dots span{width:6px;height:6px;background:var(--text-muted);border-radius:50%;animation:pulse 1.2s infinite;}
.thinking-dots span:nth-child(2){animation-delay:.2s;}
.thinking-dots span:nth-child(3){animation-delay:.4s;}
@keyframes pulse{0%,80%,100%{opacity:.2;transform:scale(.8)}40%{opacity:1;transform:scale(1)}}

/* ── INPUT AREA ── */
.input-area{
  padding:14px 20px 18px;border-top:1px solid var(--border);
  background:var(--surface);flex-shrink:0;
}
.input-wrap{
  max-width:800px;margin:0 auto;background:var(--surface2);
  border:1px solid var(--border);border-radius:var(--radius);
  display:flex;align-items:flex-end;gap:8px;padding:10px 12px;
  transition:border-color .15s;
}
.input-wrap:focus-within{border-color:var(--text-dim);}
#user-input{
  flex:1;background:transparent;border:none;outline:none;
  color:var(--text);font-family:'Sora',sans-serif;font-size:14px;
  resize:none;max-height:140px;line-height:1.5;padding:2px 0;
}
#user-input::placeholder{color:var(--text-dim);}
.send-btn{
  width:34px;height:34px;background:var(--accent);border:none;
  border-radius:8px;color:white;cursor:pointer;
  display:flex;align-items:center;justify-content:center;font-size:15px;
  transition:all .15s;flex-shrink:0;
}
.send-btn:hover{background:var(--accent2);}
.send-btn:disabled{opacity:.4;cursor:not-allowed;}
.input-hint{max-width:800px;margin:7px auto 0;font-size:11px;color:var(--text-dim);text-align:center;}
.remaining-badge{font-size:10px;color:var(--text-muted);margin-top:4px;text-align:center;}

/* ── MODAL ── */
.modal-overlay{
  position:fixed;inset:0;background:rgba(0,0,0,.8);
  backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:100;
}
.modal-overlay.open{display:flex;}
.modal{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:24px;width:560px;max-width:92vw;animation:fadeUp .2s ease;
  max-height:90vh;overflow-y:auto;
}
.modal h3{font-size:15px;font-weight:600;margin-bottom:4px;}
.modal p{font-size:13px;color:var(--text-muted);margin-bottom:16px;}
.modal textarea{
  width:100%;background:var(--surface2);border:1px solid var(--border);
  border-radius:var(--radius-sm);color:var(--text);font-family:'Sora',sans-serif;
  font-size:13px;padding:10px 12px;outline:none;margin-bottom:12px;resize:vertical;
  transition:border-color .15s;
}
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

/* ── PLAN CARDS ── */
.plan-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px;}
.plan-card{
  background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);
  padding:14px;text-align:center;transition:all .15s;cursor:pointer;
}
.plan-card.selected{border-color:var(--orange);background:var(--surface3);}
.plan-card:hover{border-color:var(--orange);}
.plan-name{font-size:14px;font-weight:600;margin-bottom:4px;}
.plan-price{font-size:18px;font-weight:700;color:var(--orange);margin-bottom:6px;}
.plan-price small{font-size:10px;font-weight:400;color:var(--text-muted);}
.plan-features{list-style:none;margin-top:8px;font-size:11px;color:var(--text-muted);}
.plan-features li{padding:2px 0;}

/* Orange Money Payment Form */
.payment-instructions{
  background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);
  padding:16px;margin:16px 0;white-space:pre-wrap;font-family:'JetBrains Mono',monospace;
  font-size:12px;color:var(--text-muted);
}
.payment-field{
  margin-bottom:16px;
}
.payment-field label{
  display:block;font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text-muted);
}
.payment-field input, .payment-field select{
  width:100%;background:var(--surface2);border:1px solid var(--border);
  border-radius:var(--radius-sm);color:var(--text);font-family:'Sora',sans-serif;
  font-size:13px;padding:10px 12px;outline:none;transition:border-color .15s;
}
.payment-field input:focus, .payment-field select:focus{border-color:var(--orange);}
.payment-field input:disabled{opacity:0.5;}
.receipt-preview{
  margin-top:8px;padding:8px;background:var(--surface3);border-radius:var(--radius-sm);
  font-size:11px;color:var(--text-muted);text-align:center;
}
.file-input{
  padding:8px !important;
}

/* ── EMPTY STATE ── */
.empty-state{
  flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:10px;color:var(--text-muted);padding:40px;text-align:center;
}
.empty-icon{
  width:52px;height:52px;background:linear-gradient(135deg,var(--accent),var(--accent2));
  border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:24px;margin-bottom:4px;
}
.empty-state h2{font-size:17px;font-weight:600;color:var(--text);}
.empty-state p{font-size:13px;max-width:320px;line-height:1.6;}
.suggestion-chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:8px;}
.chip{
  padding:7px 14px;border:1px solid var(--border);border-radius:20px;
  font-size:12px;color:var(--text-muted);background:var(--surface2);
  cursor:pointer;transition:all .15s;
}
.chip:hover{border-color:var(--accent);color:var(--text);}

/* ── MOBILE BOTTOM NAV ── */
.mobile-nav{
  display:none;position:fixed;bottom:0;left:0;right:0;
  background:var(--surface);border-top:1px solid var(--border);
  padding:8px 16px calc(8px + env(safe-area-inset-bottom));z-index:50;
}
.mobile-nav-btns{display:flex;justify-content:space-around;align-items:center;}
.mobile-nav-btn{
  display:flex;flex-direction:column;align-items:center;gap:3px;
  padding:8px 16px;border:none;background:transparent;
  color:var(--text-muted);font-family:'Sora',sans-serif;font-size:10px;
  cursor:pointer;border-radius:10px;transition:all .15s;
}
.mobile-nav-btn .nav-icon{font-size:20px;}
.mobile-nav-btn.active{color:var(--accent);}

/* ── BOTTOM SHEET ── */
.sheet-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:60;backdrop-filter:blur(4px);}
.sheet-overlay.open{display:block;}
.sheet{
  position:fixed;bottom:0;left:0;right:0;
  background:var(--surface);border-top:1px solid var(--border);border-radius:20px 20px 0 0;
  padding:16px 20px calc(32px + env(safe-area-inset-bottom));
  z-index:70;transform:translateY(100%);transition:transform .3s ease;
}
.sheet.open{transform:translateY(0);}
.sheet-handle{width:36px;height:4px;background:var(--border);border-radius:4px;margin:0 auto 20px;}
.sheet-title{font-size:12px;font-weight:600;color:var(--text-muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;}
.sheet-tools{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px;}
.sheet-tool-btn{
  display:flex;align-items:center;gap:10px;padding:12px 14px;
  background:var(--surface2);border:1px solid var(--border);border-radius:12px;
  color:var(--text);font-family:'Sora',sans-serif;font-size:13px;cursor:pointer;transition:all .15s;
}
.sheet-tool-btn:active{border-color:var(--accent);}
.sheet-model-select{
  width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:10px;
  color:var(--text);font-family:'Sora',sans-serif;font-size:13px;padding:12px 14px;outline:none;cursor:pointer;
}

/* ── RESPONSIVE ── */
@media(max-width:640px){
  .sidebar{display:none!important;}
  .mobile-nav{display:flex;flex-direction:column;}
  .chat-area{padding-bottom:0;}
  #messages{padding:12px 14px;padding-bottom:calc(var(--nav-h) + 100px);}
  .input-area{
    padding:10px 12px;
    padding-bottom:calc(var(--nav-h) + 10px + env(safe-area-inset-bottom));
    position:sticky;bottom:var(--nav-h);
  }
  header{padding:10px 14px;}
  .logo-text{font-size:14px;}
  .token-badge{display:none;}
  .avatar{width:26px;height:26px;font-size:11px;}
  .bubble-inner{font-size:14px;padding:10px 12px;}
  .modal{width:95vw;}
  .input-hint{display:none;}
}
</style>
</head>
<body>

<!-- MOBILE SHEET OVERLAY -->
<div class="sheet-overlay" id="sheet-overlay" onclick="closeSheet()"></div>

<!-- BOTTOM SHEET -->
<div class="sheet" id="sheet">
  <div class="sheet-handle"></div>
  <div id="sheet-tools-section">
    <div class="sheet-title">Tools</div>
    <div class="sheet-tools">
      <button class="sheet-tool-btn" onclick="sheetTool('code')">⌨ Code</button>
      <button class="sheet-tool-btn" onclick="sheetTool('calculate')">∑ Calculator</button>
      <button class="sheet-tool-btn" onclick="sheetTool('system_info')">⚙ System Info</button>
      <button class="sheet-tool-btn" onclick="sheetTool('image')">🎨 Generate Image</button>
    </div>
  </div>
  <div id="sheet-model-section">
    <div class="sheet-title">Model</div>
    <select id="model-select-mobile" onchange="switchModel(this.value)" class="sheet-model-select">
      <option>Loading...</option>
    </select>
  </div>
</div>

<!-- HEADER -->
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
  <!-- SIDEBAR -->
  <aside class="sidebar">
    <div class="sidebar-label">Plan</div>
    <button class="tool-btn upgrade-btn" onclick="openUpgradeModal()" id="upgrade-sidebar-btn">
      <div class="tool-icon">📱</div> Upgrade with Orange Money ($12/mo)
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
    <div class="sidebar-divider"></div>
    <div class="sidebar-label">Session</div>
    <button class="tool-btn" onclick="resetChat()"><div class="tool-icon">↺</div> New Chat</button>
  </aside>

  <!-- CHAT -->
  <div class="chat-area">
    <div id="messages">
      <div class="empty-state" id="empty-state">
        <div class="empty-icon">⚡</div>
        <h2>What can I help with?</h2>
        <p>Ask anything, generate code, create images, or use the tools.</p>
        <div class="suggestion-chips">
          <div class="chip" onclick="sendSuggestion('Write a Python Flask REST API with JWT auth')">Flask REST API</div>
          <div class="chip" onclick="sendSuggestion('Explain async/await in Python')">Async/Await</div>
          <div class="chip" onclick="sendSuggestion('Write a web scraper with requests and BeautifulSoup')">Web scraper</div>
          <div class="chip" onclick="sendSuggestion('Generate an image of a futuristic city')">🎨 Futuristic City</div>
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

<!-- MOBILE BOTTOM NAV -->
<div class="mobile-nav">
  <div class="mobile-nav-btns">
    <button class="mobile-nav-btn" onclick="openSheet('tools')"><span class="nav-icon">⚙</span><span>Tools</span></button>
    <button class="mobile-nav-btn" onclick="resetChat()"><span class="nav-icon">↺</span><span>New</span></button>
    <button class="mobile-nav-btn" onclick="openSheet('model')"><span class="nav-icon">🤖</span><span>Model</span></button>
    <button class="mobile-nav-btn" onclick="openUpgradeModal()"><span class="nav-icon">📱</span><span>Upgrade</span></button>
  </div>
</div>

<!-- TOOL MODAL -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <h3 id="modal-title"></h3>
    <p id="modal-desc"></p>
    <textarea id="modal-input" rows="3" placeholder=""></textarea>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="runTool()">Run</button>
    </div>
  </div>
</div>

<!-- UPGRADE MODAL -->
<div class="modal-overlay" id="upgrade-modal">
  <div class="modal" style="width:560px;">
    <h3>📱 Upgrade with Orange Money</h3>
    <p>Get Pro features for just <strong>$12/month</strong> - Pay with Orange Money</p>
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
        <input type="tel" id="payment-phone" placeholder="e.g., 71234567" autocomplete="off">
        <small style="color:var(--text-muted);font-size:10px;">Enter without country code</small>
      </div>
      <div class="payment-instructions" id="payment-instructions"></div>
      <div class="payment-field" id="receipt-field" style="display:none;">
        <label>📎 Upload Payment Receipt (Screenshot)</label>
        <input type="file" id="receipt-file" accept="image/*,.pdf" class="file-input">
        <div class="receipt-preview" id="receipt-preview"></div>
        <small style="color:var(--text-muted);font-size:10px;">Upload a screenshot of your Orange Money payment confirmation</small>
      </div>
    </div>
    
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeUpgradeModal()">Cancel</button>
      <button class="btn btn-upgrade" id="subscribe-btn" onclick="processUpgrade()">Continue with Orange Money</button>
    </div>
    <p style="font-size:10px;color:var(--text-dim);margin-top:12px;text-align:center;">🔒 Secure payment via Orange Money · Upload receipt for verification · Instant activation</p>
  </div>
</div>

<script>
marked.setOptions({breaks:true,gfm:true});
let currentTool=null;
let selectedPlan = "pro";
let currentUserPlan = "free";
let currentPaymentId = null;
let paymentStep = "select"; // select, payment, receipt

// ── FIXED COPY FUNCTION ───────────────────────────────────────────────────────────
window.copyCode = function(btn, codeBase64) {
  try {
    const code = atob(codeBase64);
    
    navigator.clipboard.writeText(code).then(() => {
      const originalHTML = btn.innerHTML;
      btn.classList.add('copied');
      btn.innerHTML = '<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg> Copied!';
      setTimeout(() => {
        btn.classList.remove('copied');
        btn.innerHTML = originalHTML;
      }, 2000);
    }).catch(err => {
      console.error('Clipboard write failed:', err);
      prompt('Copy manually (Ctrl+C):', code);
    });
  } catch(e) {
    console.error('Copy failed:', e);
    const originalHTML = btn.innerHTML;
    btn.innerHTML = '❌ Failed!';
    setTimeout(() => {
      btn.innerHTML = originalHTML;
    }, 2000);
  }
};

// ── IMPROVED MARKDOWN + CODE RENDERER ───────────────────────────────────────────
function renderMarkdown(text){
  const html = marked.parse(text);
  const w = document.createElement('div');
  w.innerHTML = html;
  
  w.querySelectorAll('pre').forEach((pre, idx) => {
    const code = pre.querySelector('code');
    if(!code) return;
    
    let lang = 'text';
    if (code.className) {
      const match = code.className.match(/language-(\w+)/);
      if (match) lang = match[1];
    }
    
    const rawCode = code.textContent;
    const base64Code = btoa(unescape(encodeURIComponent(rawCode)));
    
    const block = document.createElement('div');
    block.className = 'code-block';
    
    block.innerHTML = `
      <div class="code-header">
        <span class="code-lang">${lang}</span>
        <button class="copy-btn" onclick="copyCode(this, '${base64Code}')">
          <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
            <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"></path>
          </svg>
          Copy
        </button>
      </div>`;
    
    const newPre = document.createElement('pre');
    const newCode = document.createElement('code');
    newCode.textContent = rawCode;
    newCode.className = `language-${lang}`;
    newPre.appendChild(newCode);
    block.appendChild(newPre);
    
    pre.replaceWith(block);
  });
  
  w.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
  return w.innerHTML;
}

// ── MESSAGES ─────────────────────────────────────────────────────────────────
function hideEmpty(){const e=document.getElementById('empty-state');if(e)e.remove();}
function escapeHtml(t){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function scrollBottom(){const m=document.getElementById('messages');m.scrollTop=m.scrollHeight;}

function appendUserMsg(text){
  hideEmpty();
  const d=document.createElement('div');
  d.className='msg user';
  d.innerHTML=`<div class="avatar">U</div><div class="bubble"><div class="bubble-inner">${escapeHtml(text)}</div></div>`;
  document.getElementById('messages').appendChild(d);
  scrollBottom();
}

function appendAgentMsg(text){
  const d=document.createElement('div');
  d.className='msg agent';
  d.innerHTML=`<div class="avatar">V</div><div class="bubble"><div class="bubble-inner">${renderMarkdown(text)}</div></div>`;
  document.getElementById('messages').appendChild(d);
  scrollBottom();
}

function appendToolResult(name, result, saved, type) {
  hideEmpty();
  const savedHtml = saved ? `<div style="margin-top:8px;font-size:11px;color:var(--text-muted);font-family:'JetBrains Mono',monospace">💾 ${saved}</div>` : '';
  
  let contentHtml = '';
  if (type === 'image' && result.image_url) {
    contentHtml = `<div class="image-container"><img src="${result.image_url}" alt="Generated Image" class="generated-image" onerror="this.src='https://placehold.co/512x512?text=Image+Failed+to+Load'"></div>`;
    if (result.prompt) {
      contentHtml += `<div style="margin-top:8px;font-size:12px;color:var(--text-muted);">Prompt: ${escapeHtml(result.prompt)}</div>`;
    }
    if (result.remaining_images !== undefined) {
      contentHtml += `<div style="margin-top:8px;font-size:11px;color:var(--orange);">🎨 ${result.remaining_images} images remaining today</div>`;
    }
  } else {
    contentHtml = renderMarkdown(result);
  }
  
  const d = document.createElement('div');
  d.className = 'tool-result';
  d.innerHTML = `<div class="tool-result-header">⚙ ${name}</div>${contentHtml}${savedHtml}`;
  document.getElementById('messages').appendChild(d);
  scrollBottom();
}

function showThinking(){
  hideEmpty();
  const d=document.createElement('div');
  d.className='thinking';d.id='thinking';
  d.innerHTML=`<div class="avatar" style="background:linear-gradient(135deg,var(--accent),var(--accent2));color:white">V</div><div class="thinking-dots"><span></span><span></span><span></span></div>`;
  document.getElementById('messages').appendChild(d);
  scrollBottom();
}
function removeThinking(){const t=document.getElementById('thinking');if(t)t.remove();}

// ── SEND ──────────────────────────────────────────────────────────────────────
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
      if(res.status === 429){
        appendAgentMsg(`**⚠️ Rate Limit Exceeded**\n\n${data.error}\n\nUpgrade to Pro for just $12/month with Orange Money!`);
        openUpgradeModal();
      } else {
        appendAgentMsg(`**Error:** ${data.error}`);
      }
    } else {
      appendAgentMsg(data.reply);
      document.getElementById('token-count').textContent=`${data.tokens.toLocaleString()} tokens`;
      if(data.remaining_messages !== undefined){
        document.getElementById('remaining-badge').textContent = `✨ ${data.remaining_messages} messages remaining today`;
      }
    }
  }catch(e){removeThinking();appendAgentMsg(`**Error:** ${e.message}`);}
  finally{setLoading(false);}
}

function sendSuggestion(text){document.getElementById('user-input').value=text;sendMessage();}
function handleKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}}
function autoResize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,140)+'px';}
function setLoading(on){document.getElementById('send-btn').disabled=on;}

// ── TOOLS ─────────────────────────────────────────────────────────────────────
const toolConfig={
  code:{title:'Generate Code',desc:'Describe what you want and AI will generate it.',placeholder:'e.g. Flask REST API with JWT auth'},
  calculate:{title:'Calculator',desc:'Evaluate a math expression.',placeholder:'e.g. (100 * 1.15) + 50'},
  system_info:{title:'System Info',desc:'Show current system info.',placeholder:''},
  image:{title:'Generate Image',desc:'Describe the image you want to create.',placeholder:'e.g. A beautiful sunset over mountains with a lake, digital art style'}
};

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

async function runTool(){
  const args=document.getElementById('modal-input').value.trim();
  const tool=currentTool;
  closeModal();setLoading(true);
  
  if(tool === 'image') {
    if(!args) {
      appendToolResult('Generate Image', 'Please describe the image you want to create.', null, 'text');
      setLoading(false);
      return;
    }
    
    showThinking();
    try {
      const res = await fetch('/generate-image', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prompt: args})
      });
      const data = await res.json();
      removeThinking();
      
      if(data.error) {
        if(res.status === 429) {
          appendToolResult('Rate Limit', data.error, null, 'text');
          openUpgradeModal();
        } else {
          appendToolResult('Error', data.error, null, 'text');
        }
      } else {
        appendToolResult('Generated Image', {
          image_url: data.image_url,
          prompt: data.prompt,
          remaining_images: data.remaining_images
        }, null, 'image');
        
        // Update remaining badge
        if(data.remaining_images !== undefined) {
          document.getElementById('remaining-badge').textContent = `✨ ${data.remaining_images} images remaining today`;
        }
      }
    } catch(e) {
      removeThinking();
      appendToolResult('Error', e.message, null, 'text');
    }
    finally {
      setLoading(false);
    }
    return;
  }
  
  try{
    const res=await fetch('/tool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool:tool||'system_info',args})});
    const data=await res.json();
    if(data.error){
      if(res.status === 429){
        appendToolResult('Rate Limit', data.error, null, 'text');
        openUpgradeModal();
      } else {
        appendToolResult('Error', data.error, null, 'text');
      }
    } else {
      appendToolResult(toolConfig[tool]?.title||tool, data.result, data.saved, data.type || 'text');
    }
  }catch(e){appendToolResult('Error',e.message, null, 'text');}
  finally{setLoading(false);}
}
document.getElementById('modal').addEventListener('click',function(e){if(e.target===this)closeModal();});

// ── UPGRADE SYSTEM WITH ORANGE MONEY & RECEIPT UPLOAD ────────────────────────────
let countriesData = {};

async function loadCountries() {
  try {
    const res = await fetch('/orange-money-countries');
    const data = await res.json();
    countriesData = data.countries;
    const select = document.getElementById('payment-country');
    select.innerHTML = '<option value="">Select your country</option>' + 
      Object.entries(countriesData).map(([code, country]) => 
        `<option value="${code}">${country.name} (+${country.prefix}) - ${country.mobile_money}</option>`
      ).join('');
  } catch(e) { console.error('Failed to load countries:', e); }
}

function updatePhonePrefix() {
  const countryCode = document.getElementById('payment-country').value;
  const phoneInput = document.getElementById('payment-phone');
  if(countryCode && countriesData[countryCode]) {
    phoneInput.placeholder = `e.g., ${'7'.repeat(countriesData[countryCode].length)}`;
  } else {
    phoneInput.placeholder = 'e.g., 71234567';
  }
}

async function loadPlans() {
  try {
    const res = await fetch('/plans');
    const data = await res.json();
    currentUserPlan = data.current_plan;
    
    const planBadge = document.getElementById('plan-badge');
    const upgradeBtn = document.getElementById('upgrade-sidebar-btn');
    
    if(currentUserPlan === 'free') {
      planBadge.textContent = 'Free';
      planBadge.className = 'plan-badge';
      if(upgradeBtn) upgradeBtn.style.display = 'flex';
    } else {
      planBadge.textContent = currentUserPlan.toUpperCase();
      planBadge.className = `plan-badge ${currentUserPlan}`;
      if(upgradeBtn) upgradeBtn.style.display = 'none';
    }
    
    const plansDiv = document.getElementById('plan-cards');
    if(plansDiv && data.plans) {
      plansDiv.innerHTML = Object.entries(data.plans).filter(([id]) => id !== currentUserPlan && id !== 'free').map(([id, plan]) => `
        <div class="plan-card ${selectedPlan === id ? 'selected' : ''}" onclick="selectPlan('${id}')">
          <div class="plan-name">${plan.name}</div>
          <div class="plan-price">$${plan.price}<small>/mo</small></div>
          <ul class="plan-features">
            ${plan.features.slice(0,3).map(f => `<li>✓ ${f}</li>`).join('')}
            ${plan.features.length > 3 ? `<li>+${plan.features.length-3} more</li>` : ''}
          </ul>
        </div>
      `).join('');
    }
    
    const today = new Date().toISOString().split('T')[0];
    if(data.usage) {
      const limits = data.plans[currentUserPlan];
      const remainingMessages = limits.max_messages_per_day - (data.usage.messages || 0);
      const remainingImages = (currentUserPlan === 'free' ? 5 : currentUserPlan === 'pro' ? 50 : 500) - (data.usage.images || 0);
      document.getElementById('remaining-badge').textContent = `💬 ${remainingMessages} msgs · 🎨 ${remainingImages} images left today`;
    }
  } catch(e) { console.error('Failed to load plans:', e); }
}

function selectPlan(planId) {
  selectedPlan = planId;
  document.querySelectorAll('.plan-card').forEach(card => {
    card.classList.remove('selected');
    if(card.querySelector('.plan-name')?.innerText.toLowerCase().includes(planId)) {
      card.classList.add('selected');
    }
  });
  resetPaymentForm();
}

function resetPaymentForm() {
  paymentStep = "select";
  currentPaymentId = null;
  document.getElementById('payment-form').style.display = 'none';
  document.getElementById('payment-instructions').innerHTML = '';
  document.getElementById('receipt-field').style.display = 'none';
  document.getElementById('receipt-file').value = '';
  document.getElementById('receipt-preview').innerHTML = '';
  document.getElementById('subscribe-btn').textContent = 'Continue with Orange Money';
  document.getElementById('subscribe-btn').classList.remove('btn-orange');
  document.getElementById('subscribe-btn').classList.add('btn-upgrade');
}

function openUpgradeModal() {
  selectedPlan = 'pro';
  paymentStep = "select";
  currentPaymentId = null;
  document.getElementById('payment-form').style.display = 'none';
  document.getElementById('receipt-field').style.display = 'none';
  document.getElementById('payment-instructions').innerHTML = '';
  document.getElementById('subscribe-btn').textContent = 'Continue with Orange Money';
  document.getElementById('subscribe-btn').classList.remove('btn-orange');
  document.getElementById('subscribe-btn').classList.add('btn-upgrade');
  loadPlans();
  loadCountries();
  document.getElementById('upgrade-modal').classList.add('open');
}

function closeUpgradeModal() {
  document.getElementById('upgrade-modal').classList.remove('open');
  resetPaymentForm();
}

async function processUpgrade() {
  const btn = document.getElementById('subscribe-btn');
  
  if(paymentStep === "select") {
    document.getElementById('payment-form').style.display = 'block';
    document.getElementById('subscribe-btn').textContent = 'Continue to Payment';
    document.getElementById('subscribe-btn').classList.remove('btn-upgrade');
    document.getElementById('subscribe-btn').classList.add('btn-orange');
    paymentStep = "payment";
    return;
  }
  
  if(paymentStep === "payment") {
    const countryCode = document.getElementById('payment-country').value;
    const phoneNumber = document.getElementById('payment-phone').value.trim();
    
    if(!countryCode) {
      alert('Please select your country');
      return;
    }
    if(!phoneNumber) {
      alert('Please enter your Orange Money phone number');
      return;
    }
    
    btn.textContent = 'Processing...';
    btn.disabled = true;
    
    try {
      const res = await fetch('/initiate-payment', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          plan_id: selectedPlan,
          country_code: countryCode,
          phone_number: phoneNumber
        })
      });
      const data = await res.json();
      
      if(data.success) {
        currentPaymentId = data.payment_id;
        document.getElementById('payment-instructions').innerHTML = `<strong>📱 Payment Required</strong>\n\n${data.instructions}`;
        document.getElementById('receipt-field').style.display = 'block';
        document.getElementById('subscribe-btn').textContent = 'Upload Receipt & Activate';
        paymentStep = "receipt";
        btn.disabled = false;
        btn.textContent = 'Upload Receipt & Activate';
        
        const fileInput = document.getElementById('receipt-file');
        fileInput.onchange = function(e) {
          const preview = document.getElementById('receipt-preview');
          if(fileInput.files && fileInput.files[0]) {
            preview.innerHTML = `📎 Selected: ${fileInput.files[0].name}`;
          } else {
            preview.innerHTML = '';
          }
        };
      } else {
        alert('Error: ' + (data.error || 'Failed to initiate payment'));
        btn.disabled = false;
        btn.textContent = 'Continue to Payment';
      }
    } catch(e) {
      alert('Error: ' + e.message);
      btn.disabled = false;
      btn.textContent = 'Continue to Payment';
    }
    return;
  }
  
  if(paymentStep === "receipt") {
    const receiptFile = document.getElementById('receipt-file').files[0];
    
    if(!receiptFile) {
      alert('Please upload your payment receipt/screenshot');
      return;
    }
    if(!currentPaymentId) {
      alert('Payment session expired. Please restart.');
      resetPaymentForm();
      return;
    }
    
    btn.textContent = 'Uploading & Verifying...';
    btn.disabled = true;
    
    const formData = new FormData();
    formData.append('payment_id', currentPaymentId);
    formData.append('receipt', receiptFile);
    
    try {
      const res = await fetch('/upload-receipt', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      
      if(data.success) {
        alert(data.message);
        closeUpgradeModal();
        location.reload();
      } else {
        alert('Verification failed: ' + (data.error || 'Please try again'));
        btn.disabled = false;
        btn.textContent = 'Upload Receipt & Activate';
      }
    } catch(e) {
      alert('Error: ' + e.message);
      btn.disabled = false;
      btn.textContent = 'Upload Receipt & Activate';
    }
  }
}

// ── MODEL SWITCHER ────────────────────────────────────────────────────────────
async function loadModels(){
  const res=await fetch('/models');
  const data=await res.json();
  const opts=data.models.map(m=>`<option value="${m.id}"${m.id===data.current?' selected':''}>${m.label}${m.plan !== 'free' ? ' ⭐' : ''}</option>`).join('');
  ['model-select','model-select-mobile'].forEach(id=>{const el=document.getElementById(id);if(el)el.innerHTML=opts;});
}

async function switchModel(id){
  if(!id)return;
  try {
    const res = await fetch('/model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:id})});
    const data = await res.json();
    if(data.error) {
      alert(data.error);
      loadModels();
    } else {
      ['model-select','model-select-mobile'].forEach(sid=>{const el=document.getElementById(sid);if(el)el.value=id;});
    }
  } catch(e) { console.error('Failed to switch model:', e); }
  closeSheet();
}

// ── BOTTOM SHEET ─────────────────────────────────────────────────────────────
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

// ── RESET ─────────────────────────────────────────────────────────────────────
async function resetChat(){
  await fetch('/reset',{method:'POST'});
  document.getElementById('messages').innerHTML=`
    <div class="empty-state" id="empty-state">
      <div class="empty-icon">⚡</div>
      <h2>What can I help with?</h2>
      <p>Ask anything, generate code, create images, or use the tools.</p>
      <div class="suggestion-chips">
        <div class="chip" onclick="sendSuggestion('Write a Python Flask REST API with JWT auth')">Flask REST API</div>
        <div class="chip" onclick="sendSuggestion('Explain async/await in Python')">Async/Await</div>
        <div class="chip" onclick="sendSuggestion('Write a web scraper with requests and BeautifulSoup')">Web scraper</div>
        <div class="chip" onclick="sendSuggestion('Generate an image of a futuristic city')">🎨 Futuristic City</div>
      </div>
    </div>`;
  document.getElementById('token-count').textContent='0 tokens';
  loadPlans(); // Refresh usage display
}

// ── INIT ─────────────────────────────────────────────────────────────────────
loadModels();
loadPlans();
loadCountries();
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
