from flask import Flask, request, jsonify, session, send_file
import requests
import os
import platform
import secrets

app = Flask(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "baidu/cobuddy:free")
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
SITE_URL = os.environ.get("SITE_URL", "https://vectron.onrender.com")

# ── PLANS ─────────────────────────────────────────────────────────────────────
PLANS = {
    "free":  {"name": "Free",    "price": 0,  "models": ["baidu/cobuddy:free", "poolside/laguna-xs.2:free", "meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-chat:free", "mistralai/mistral-small-3.1-24b-instruct:free"]},
    "basic": {"name": "Basic",   "price": 5,  "models": ["baidu/cobuddy:free", "poolside/laguna-xs.2:free", "meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-chat:free", "mistralai/mistral-small-3.1-24b-instruct:free", "x-ai/grok-3-mini-beta", "google/gemini-2.0-flash-001"]},
    "pro":   {"name": "Pro",     "price": 10, "models": ["baidu/cobuddy:free", "poolside/laguna-xs.2:free", "meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-chat:free", "mistralai/mistral-small-3.1-24b-instruct:free", "x-ai/grok-3-mini-beta", "google/gemini-2.0-flash-001", "anthropic/claude-sonnet-4-5", "google/gemini-2.5-pro", "openai/gpt-4o"]},
}

ALL_MODELS = [
    {"id": "baidu/cobuddy:free",                            "label": "CoBuddy",          "plan": "free"},
    {"id": "poolside/laguna-xs.2:free",                     "label": "Laguna XS.2",      "plan": "free"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free",        "label": "Llama 3.3 70B",    "plan": "free"},
    {"id": "deepseek/deepseek-chat:free",                   "label": "DeepSeek V3",      "plan": "free"},
    {"id": "mistralai/mistral-small-3.1-24b-instruct:free", "label": "Mistral Small",    "plan": "free"},
    {"id": "x-ai/grok-3-mini-beta",                         "label": "Grok 3 Mini",      "plan": "basic"},
    {"id": "google/gemini-2.0-flash-001",                   "label": "Gemini 2.0 Flash", "plan": "basic"},
    {"id": "anthropic/claude-sonnet-4-5",                   "label": "Claude Sonnet",    "plan": "pro"},
    {"id": "google/gemini-2.5-pro",                         "label": "Gemini 2.5 Pro",   "plan": "pro"},
    {"id": "openai/gpt-4o",                                 "label": "GPT-4o",           "plan": "pro"},
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
    payload = {"model": model or DEFAULT_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    resp = requests.post(BASE_URL, headers=openrouter_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"], data.get("usage", {})

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

def get_user_plan():
    return session.get("plan", "free")

def model_allowed(model_id):
    plan = get_user_plan()
    return model_id in PLANS[plan]["models"]

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
    plan = get_user_plan()
    allowed = PLANS[plan]["models"]
    models = [dict(m, locked=m["id"] not in allowed) for m in ALL_MODELS]
    current = session.get("model", DEFAULT_MODEL)
    return jsonify({"models": models, "current": current, "plan": plan})

@app.route("/model", methods=["POST"])
def set_model():
    model_id = (request.json or {}).get("model", "").strip()
    if not model_id:
        return jsonify({"error": "No model provided"}), 400
    if not model_allowed(model_id):
        return jsonify({"error": "upgrade_required", "plan": get_user_plan()}), 403
    session["model"] = model_id
    return jsonify({"ok": True, "model": model_id})

@app.route("/upgrade", methods=["POST"])
def upgrade():
    # In production replace this with real payment (Stripe etc.)
    # For now simulate upgrading plan
    plan = (request.json or {}).get("plan", "free")
    if plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400
    session["plan"] = plan
    return jsonify({"ok": True, "plan": plan, "name": PLANS[plan]["name"]})

@app.route("/plan")
def get_plan():
    plan = get_user_plan()
    return jsonify({"plan": plan, "name": PLANS[plan]["name"], "price": PLANS[plan]["price"]})

@app.route("/")
def index():
    session.setdefault("history", [])
    session.setdefault("tokens", 0)
    session.setdefault("plan", "free")
    return HTML

@app.route("/chat", methods=["POST"])
def chat():
    user_message = (request.json or {}).get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400
    history = session.get("history", [])
    history.append({"role": "user", "content": user_message})
    active_model = session.get("model", DEFAULT_MODEL)
    if not model_allowed(active_model):
        return jsonify({"error": "upgrade_required"}), 403
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        reply, usage = call_openrouter(messages, model=active_model)
        tokens = session.get("tokens", 0) + usage.get("total_tokens", 0)
        history.append({"role": "assistant", "content": reply})
        session["history"] = history[-40:]
        session["tokens"] = tokens
        return jsonify({"reply": reply, "tokens": tokens})
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"HTTP {e.response.status_code}: {e.response.text}"}), 502
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/tool", methods=["POST"])
def tool():
    data = request.json or {}
    tool_name = data.get("tool", "").strip()
    args = data.get("args", "").strip()
    if tool_name == "calculate":
        return jsonify({"result": calculate(args) if args else "Provide a math expression"})
    elif tool_name == "system_info":
        return jsonify({"result": get_system_info()})
    elif tool_name == "code":
        if not args:
            return jsonify({"result": "Describe the code you want"}), 400
        try:
            active_model = session.get("model", DEFAULT_MODEL)
            reply, _ = call_openrouter([{"role": "system", "content": CODE_SYSTEM_PROMPT}, {"role": "user", "content": f"Generate code for: {args}"}], temperature=0.3, model=active_model)
            return jsonify({"result": reply})
        except Exception as e:
            return jsonify({"result": f"Error: {e}"}), 500
    return jsonify({"error": f"Unknown tool '{tool_name}'"}), 400

@app.route("/reset", methods=["POST"])
def reset():
    session["history"] = []
    session["tokens"] = 0
    return jsonify({"ok": True})

# ─── HTML ─────────────────────────────────────────────────────────────────────
HTML = (
"""<!DOCTYPE html>
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
  --radius:12px;--radius-sm:8px;--nav-h:64px;
  --gold:#f59e0b;--gold2:#fbbf24;
}
body{font-family:"Sora",sans-serif;background:var(--bg);color:var(--text);height:100dvh;display:flex;flex-direction:column;overflow:hidden;}
header{display:flex;align-items:center;justify-content:space-between;padding:12px 20px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0;z-index:10;}
.logo{display:flex;align-items:center;gap:10px;}
.logo img{height:30px;width:auto;}
.logo-text{font-size:15px;font-weight:600;letter-spacing:-0.3px;}
.header-right{display:flex;align-items:center;gap:8px;}
.token-badge{font-size:11px;color:var(--text-muted);font-family:"JetBrains Mono",monospace;background:var(--surface2);border:1px solid var(--border);padding:4px 10px;border-radius:20px;}
.plan-badge{font-size:11px;font-weight:600;padding:4px 10px;border-radius:20px;cursor:pointer;transition:all .15s;}
.plan-badge.free{background:var(--surface2);color:var(--text-muted);border:1px solid var(--border);}
.plan-badge.basic{background:rgba(59,130,246,.15);color:var(--accent2);border:1px solid rgba(59,130,246,.3);}
.plan-badge.pro{background:rgba(245,158,11,.15);color:var(--gold2);border:1px solid rgba(245,158,11,.3);}
.icon-btn{width:32px;height:32px;border:1px solid var(--border);background:var(--surface2);color:var(--text-muted);border-radius:var(--radius-sm);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px;transition:all .15s;}
.icon-btn:hover{background:var(--surface3);color:var(--text);}
.main{display:flex;flex:1;overflow:hidden;}
.sidebar{width:220px;border-right:1px solid var(--border);background:var(--surface);padding:16px 12px;display:flex;flex-direction:column;gap:4px;flex-shrink:0;overflow-y:auto;}
.sidebar-label{font-size:10px;font-weight:600;color:var(--text-dim);letter-spacing:1px;text-transform:uppercase;padding:8px 8px 4px;}
.tool-btn{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:var(--radius-sm);border:none;background:transparent;color:var(--text-muted);font-family:"Sora",sans-serif;font-size:13px;cursor:pointer;transition:all .15s;text-align:left;width:100%;}
.tool-btn:hover{background:var(--surface2);color:var(--text);}
.tool-btn .tool-icon{width:28px;height:28px;background:var(--surface3);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0;}
.sidebar-divider{height:1px;background:var(--border);margin:8px 0;}
.sidebar-model select{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-family:"Sora",sans-serif;font-size:12px;padding:8px 10px;outline:none;cursor:pointer;appearance:none;-webkit-appearance:none;}
.upgrade-btn{display:flex;align-items:center;justify-content:center;gap:6px;padding:9px 10px;border-radius:var(--radius-sm);border:1px solid rgba(245,158,11,.3);background:rgba(245,158,11,.08);color:var(--gold2);font-family:"Sora",sans-serif;font-size:12px;font-weight:600;cursor:pointer;transition:all .15s;width:100%;margin-top:4px;}
.upgrade-btn:hover{background:rgba(245,158,11,.15);}
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
.bubble-inner p{margin-bottom:10px;}
.bubble-inner p:last-child{margin-bottom:0;}
.bubble-inner ul,.bubble-inner ol{padding-left:20px;margin-bottom:10px;}
.bubble-inner li{margin-bottom:4px;}
.bubble-inner strong{color:var(--text);font-weight:600;}
.bubble-inner h1,.bubble-inner h2,.bubble-inner h3{font-weight:600;margin:14px 0 6px;color:var(--text);}
.bubble-inner h1:first-child,.bubble-inner h2:first-child,.bubble-inner h3:first-child{margin-top:0;}
.bubble-inner a{color:var(--accent);}
.bubble-inner blockquote{border-left:3px solid var(--accent);padding-left:12px;color:var(--text-muted);margin:10px 0;}
.bubble-inner code:not(pre code){font-family:"JetBrains Mono",monospace;font-size:12px;background:var(--surface3);border:1px solid var(--border);padding:2px 6px;border-radius:4px;color:var(--accent2);}
.code-block{margin:12px 0;border-radius:var(--radius-sm);overflow:hidden;border:1px solid var(--border);background:var(--code-bg);}
.code-header{display:flex;align-items:center;justify-content:space-between;padding:7px 12px;background:#161b22;border-bottom:1px solid var(--border);}
.code-lang{font-family:"JetBrains Mono",monospace;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;}
.copy-btn{display:flex;align-items:center;gap:4px;padding:4px 10px;border-radius:5px;border:1px solid var(--border);background:var(--surface3);color:var(--text-muted);font-family:"Sora",sans-serif;font-size:11px;cursor:pointer;transition:all .15s;}
.copy-btn:hover{background:var(--surface2);color:var(--text);}
.copy-btn.copied{color:#4ade80;border-color:#4ade80;}
.code-block pre{margin:0;padding:14px 16px;overflow-x:auto;font-size:13px;line-height:1.6;}
.code-block pre code{font-family:"JetBrains Mono",monospace;background:transparent!important;padding:0!important;border:none!important;}
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
#user-input{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-family:"Sora",sans-serif;font-size:14px;resize:none;max-height:140px;line-height:1.5;padding:2px 0;}
#user-input::placeholder{color:var(--text-dim);}
.send-btn{width:34px;height:34px;background:var(--accent);border:none;border-radius:8px;color:white;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:15px;transition:all .15s;flex-shrink:0;}
.send-btn:hover{background:var(--accent2);}
.send-btn:disabled{opacity:.4;cursor:not-allowed;}
.input-hint{max-width:800px;margin:7px auto 0;font-size:11px;color:var(--text-dim);text-align:center;}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:100;}
.modal-overlay.open{display:flex;}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px;width:440px;max-width:92vw;animation:fadeUp .2s ease;}
.modal h3{font-size:15px;font-weight:600;margin-bottom:4px;}
.modal p{font-size:13px;color:var(--text-muted);margin-bottom:16px;}
.modal textarea{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-family:"Sora",sans-serif;font-size:13px;padding:10px 12px;outline:none;margin-bottom:12px;resize:vertical;transition:border-color .15s;}
.modal textarea:focus{border-color:var(--text-dim);}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;}
.btn{padding:8px 16px;border-radius:var(--radius-sm);font-family:"Sora",sans-serif;font-size:13px;font-weight:500;cursor:pointer;transition:all .15s;border:none;}
.btn-primary{background:var(--accent);color:white;}
.btn-primary:hover{background:var(--accent2);}
.btn-ghost{background:var(--surface2);color:var(--text-muted);border:1px solid var(--border);}
.btn-ghost:hover{color:var(--text);background:var(--surface3);}
.empty-state{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--text-muted);padding:40px;text-align:center;}
.empty-icon{width:52px;height:52px;background:linear-gradient(135deg,var(--accent),var(--accent2));border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:24px;margin-bottom:4px;}
.empty-state h2{font-size:17px;font-weight:600;color:var(--text);}
.empty-state p{font-size:13px;max-width:320px;line-height:1.6;}
.suggestion-chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:8px;}
.chip{padding:7px 14px;border:1px solid var(--border);border-radius:20px;font-size:12px;color:var(--text-muted);background:var(--surface2);cursor:pointer;transition:all .15s;}
.chip:hover{border-color:var(--accent);color:var(--text);}
/* ── PRICING MODAL ── */
.pricing-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin:16px 0;}
.pricing-card{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:16px;display:flex;flex-direction:column;gap:8px;position:relative;cursor:pointer;transition:all .2s;}
.pricing-card:hover{border-color:var(--accent);}
.pricing-card.current{border-color:var(--accent);background:rgba(59,130,246,.05);}
.pricing-card.pro-card{border-color:rgba(245,158,11,.3);background:rgba(245,158,11,.04);}
.pricing-card.pro-card:hover{border-color:var(--gold);}
.plan-name{font-size:13px;font-weight:600;color:var(--text);}
.plan-price{font-size:22px;font-weight:700;color:var(--text);font-family:"JetBrains Mono",monospace;}
.plan-price span{font-size:12px;color:var(--text-muted);font-weight:400;}
.plan-features{font-size:11px;color:var(--text-muted);line-height:1.6;margin-top:4px;}
.plan-cta{margin-top:8px;padding:8px;border-radius:6px;font-family:"Sora",sans-serif;font-size:12px;font-weight:600;border:none;cursor:pointer;transition:all .15s;text-align:center;}
.plan-cta.free-cta{background:var(--surface3);color:var(--text-muted);}
.plan-cta.basic-cta{background:rgba(59,130,246,.2);color:var(--accent2);}
.plan-cta.pro-cta{background:rgba(245,158,11,.2);color:var(--gold2);}
.lock-icon{position:absolute;top:8px;right:8px;font-size:11px;color:var(--text-dim);}
/* ── MOBILE ── */
.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:var(--surface);border-top:1px solid var(--border);padding:8px 16px calc(8px + env(safe-area-inset-bottom));z-index:50;}
.mobile-nav-btns{display:flex;justify-content:space-around;align-items:center;}
.mobile-nav-btn{display:flex;flex-direction:column;align-items:center;gap:3px;padding:8px 12px;border:none;background:transparent;color:var(--text-muted);font-family:"Sora",sans-serif;font-size:10px;cursor:pointer;border-radius:10px;transition:all .15s;}
.mobile-nav-btn .nav-icon{font-size:20px;}
.sheet-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:60;backdrop-filter:blur(4px);}
.sheet-overlay.open{display:block;}
.sheet{position:fixed;bottom:0;left:0;right:0;background:var(--surface);border-top:1px solid var(--border);border-radius:20px 20px 0 0;padding:16px 20px calc(32px + env(safe-area-inset-bottom));z-index:70;transform:translateY(100%);transition:transform .3s ease;}
.sheet.open{transform:translateY(0);}
.sheet-handle{width:36px;height:4px;background:var(--border);border-radius:4px;margin:0 auto 20px;}
.sheet-title{font-size:12px;font-weight:600;color:var(--text-muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;}
.sheet-tools{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px;}
.sheet-tool-btn{display:flex;align-items:center;gap:10px;padding:12px 14px;background:var(--surface2);border:1px solid var(--border);border-radius:12px;color:var(--text);font-family:"Sora",sans-serif;font-size:13px;cursor:pointer;transition:all .15s;}
.sheet-tool-btn:active{border-color:var(--accent);}
.sheet-model-select{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:10px;color:var(--text);font-family:"Sora",sans-serif;font-size:13px;padding:12px 14px;outline:none;cursor:pointer;}
.sheet-upgrade-btn{width:100%;margin-top:12px;padding:12px;border-radius:10px;border:1px solid rgba(245,158,11,.3);background:rgba(245,158,11,.08);color:var(--gold2);font-family:"Sora",sans-serif;font-size:13px;font-weight:600;cursor:pointer;}
@media(max-width:640px){
  .sidebar{display:none!important;}
  .mobile-nav{display:flex;flex-direction:column;}
  .chat-area{padding-bottom:0;}
  #messages{padding:12px 14px;padding-bottom:calc(var(--nav-h) + 100px);}
  .input-area{padding:10px 12px;padding-bottom:calc(var(--nav-h) + 10px + env(safe-area-inset-bottom));position:sticky;bottom:var(--nav-h);}
  header{padding:10px 14px;}
  .logo-text{font-size:14px;}
  .token-badge{display:none;}
  .avatar{width:26px;height:26px;font-size:11px;}
  .bubble-inner{font-size:14px;padding:10px 12px;}
  .modal{width:95vw;}
  .input-hint{display:none;}
  .pricing-grid{grid-template-columns:1fr;}
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
      <button class="sheet-tool-btn" onclick="sheetTool('code')">&#9000; Code</button>
      <button class="sheet-tool-btn" onclick="sheetTool('calculate')">&#8721; Calculator</button>
      <button class="sheet-tool-btn" onclick="sheetTool('system_info')">&#9881; System Info</button>
    </div>
  </div>
  <div id="sheet-model-section" style="display:none">
    <div class="sheet-title">Model</div>
    <select id="model-select-mobile" onchange="switchModel(this.value)" class="sheet-model-select"><option>Loading...</option></select>
    <button class="sheet-upgrade-btn" onclick="closeSheet();openPricing()">&#11088; Upgrade Plan</button>
  </div>
</div>
<header>
  <div class="logo">
    <img src="/logo" alt="Vectron" onerror="this.style.display='none'">
    <div class="logo-text">Vectron</div>
  </div>
  <div class="header-right">
    <div class="token-badge" id="token-count">0 tokens</div>
    <div class="plan-badge free" id="plan-badge" onclick="openPricing()">Free</div>
    <button class="icon-btn" onclick="resetChat()" title="New chat">&#8635;</button>
  </div>
</header>
<div class="main">
  <aside class="sidebar">
    <div class="sidebar-label">Model</div>
    <div class="sidebar-model">
      <select id="model-select" onchange="switchModel(this.value)"><option>Loading...</option></select>
    </div>
    <div class="sidebar-divider"></div>
    <div class="sidebar-label">Tools</div>
    <button class="tool-btn" onclick="openTool('code')"><div class="tool-icon">&#9000;</div> Generate Code</button>
    <button class="tool-btn" onclick="openTool('calculate')"><div class="tool-icon">&#8721;</div> Calculator</button>
    <button class="tool-btn" onclick="openTool('system_info')"><div class="tool-icon">&#9881;</div> System Info</button>
    <div class="sidebar-divider"></div>
    <div class="sidebar-label">Account</div>
    <button class="tool-btn" onclick="resetChat()"><div class="tool-icon">&#8635;</div> New Chat</button>
    <button class="upgrade-btn" onclick="openPricing()">&#11088; Upgrade Plan</button>
  </aside>
  <div class="chat-area">
    <div id="messages">
      <div class="empty-state" id="empty-state">
        <div class="empty-icon">&#9889;</div>
        <h2>What can I help with?</h2>
        <p>Ask anything, generate code, or use the tools.</p>
        <div class="suggestion-chips">
          <div class="chip" onclick="sendSuggestion('Write a Python Flask REST API with JWT auth')">Flask REST API</div>
          <div class="chip" onclick="sendSuggestion('Explain async/await in Python')">Async/Await</div>
          <div class="chip" onclick="sendSuggestion('Write a web scraper with requests and BeautifulSoup')">Web scraper</div>
          <div class="chip" onclick="sendSuggestion('How do I deploy a Flask app on Render?')">Deploy on Render</div>
        </div>
      </div>
    </div>
    <div class="input-area">
      <div class="input-wrap">
        <textarea id="user-input" rows="1" placeholder="Message Vectron..." onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
        <button class="send-btn" id="send-btn" onclick="sendMessage()">&#8679;</button>
      </div>
      <div class="input-hint">Enter to send &middot; Shift+Enter for new line</div>
    </div>
  </div>
</div>
<div class="mobile-nav">
  <div class="mobile-nav-btns">
    <button class="mobile-nav-btn" onclick="openSheet('tools')"><span class="nav-icon">&#9881;</span><span>Tools</span></button>
    <button class="mobile-nav-btn" onclick="resetChat()"><span class="nav-icon">&#8635;</span><span>New</span></button>
    <button class="mobile-nav-btn" onclick="openSheet('model')"><span class="nav-icon">&#129302;</span><span>Model</span></button>
    <button class="mobile-nav-btn" onclick="openPricing()"><span class="nav-icon">&#11088;</span><span>Plans</span></button>
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
<!-- PRICING MODAL -->
<div class="modal-overlay" id="pricing-modal">
  <div class="modal" style="max-width:560px;width:95vw;">
    <h3>&#11088; Upgrade Vectron</h3>
    <p>Unlock stronger models and more power.</p>
    <div class="pricing-grid">
      <div class="pricing-card" id="plan-free-card" onclick="selectPlan('free')">
        <div class="plan-name">Free</div>
        <div class="plan-price">$0<span>/mo</span></div>
        <div class="plan-features">5 free models<br>Unlimited chats<br>Basic tools</div>
        <div class="plan-cta free-cta">Current Plan</div>
      </div>
      <div class="pricing-card" id="plan-basic-card" onclick="selectPlan('basic')">
        <div class="plan-name">Basic</div>
        <div class="plan-price">$5<span>/mo</span></div>
        <div class="plan-features">+ Grok 3 Mini<br>+ Gemini 2.0 Flash<br>Priority support</div>
        <div class="plan-cta basic-cta">Upgrade</div>
      </div>
      <div class="pricing-card pro-card" id="plan-pro-card" onclick="selectPlan('pro')">
        <div class="lock-icon">&#128081;</div>
        <div class="plan-name">Pro</div>
        <div class="plan-price">$10<span>/mo</span></div>
        <div class="plan-features">+ Claude Sonnet<br>+ Gemini 2.5 Pro<br>+ GPT-4o</div>
        <div class="plan-cta pro-cta">Go Pro</div>
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closePricing()">Close</button>
    </div>
  </div>
</div>
<script>
marked.setOptions({breaks:true,gfm:true});
let currentTool=null;
// WeakMap stores raw code on each copy button — no encoding needed
const codeMap=new WeakMap();

// ── COPY ──────────────────────────────────────────────────────────────────────
function copyCode(btn){
  const raw=codeMap.get(btn)||"";
  const svgCopy='<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
  const svgCheck='<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>';
  const done=()=>{
    btn.classList.add("copied");
    btn.innerHTML=svgCheck+" Copied!";
    setTimeout(()=>{btn.classList.remove("copied");btn.innerHTML=svgCopy+" Copy";},2000);
  };
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(raw).then(done).catch(()=>fallbackCopy(raw,done));
  } else { fallbackCopy(raw,done); }
}
function fallbackCopy(text,cb){
  const ta=document.createElement("textarea");
  ta.value=text;ta.style.cssText="position:fixed;opacity:0;top:0;left:0";
  document.body.appendChild(ta);ta.focus();ta.select();
  try{document.execCommand("copy");cb();}catch(e){}
  document.body.removeChild(ta);
}

// ── MARKDOWN ─────────────────────────────────────────────────────────────────
function renderMarkdown(text){
  const html=marked.parse(text);
  const w=document.createElement("div");
  w.innerHTML=html;
  w.querySelectorAll("pre").forEach(pre=>{
    const code=pre.querySelector("code");
    if(!code)return;
    const lang=(code.className.replace("language-","")||"code").toLowerCase();
    const raw=code.textContent;
    const block=document.createElement("div");
    block.className="code-block";
    const header=document.createElement("div");
    header.className="code-header";
    const langEl=document.createElement("span");
    langEl.className="code-lang";langEl.textContent=lang;
    const copyBtn=document.createElement("button");
    copyBtn.className="copy-btn";
    copyBtn.innerHTML='<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg> Copy';
    codeMap.set(copyBtn,raw);
    copyBtn.addEventListener("click",()=>copyCode(copyBtn));
    header.appendChild(langEl);header.appendChild(copyBtn);
    block.appendChild(header);
    const np=document.createElement("pre");
    const nc=document.createElement("code");
    nc.textContent=raw;np.appendChild(nc);block.appendChild(np);
    pre.replaceWith(block);
  });
  w.querySelectorAll("pre code").forEach(el=>hljs.highlightElement(el));
  return w.innerHTML;
}

// ── MEMORY ───────────────────────────────────────────────────────────────────
const MK="vectron_memory";
function loadMemory(){try{return JSON.parse(localStorage.getItem(MK)||"[]");}catch{return[];}}
function saveMemory(h){localStorage.setItem(MK,JSON.stringify(h.slice(-60)));}
function clearMemory(){localStorage.removeItem(MK);}

(function restoreChat(){
  const h=loadMemory();
  if(!h.length)return;
  hideEmpty();
  h.forEach(m=>{if(m.role==="user")appendUserMsg(m.content,false);else if(m.role==="assistant")appendAgentMsg(m.content,false);});
  scrollBottom();
})();

// fix suggestion chips — bind onclick via JS not inline HTML
(function bindChips(){
  const pairs=[["Write a Python Flask REST API with JWT auth","Flask REST API"],["Explain async/await in Python","Async/Await"],["Write a web scraper with requests and BeautifulSoup","Web scraper"],["How do I deploy a Flask app on Render?","Deploy on Render"]];
  document.querySelectorAll(".suggestion-chips .chip").forEach((chip,i)=>{
    if(pairs[i]) chip.onclick=()=>sendSuggestion(pairs[i][0]);
  });
})();

// ── MESSAGES ─────────────────────────────────────────────────────────────────
function hideEmpty(){const e=document.getElementById("empty-state");if(e)e.remove();}
function escapeHtml(t){return t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function scrollBottom(){const m=document.getElementById("messages");m.scrollTop=m.scrollHeight;}

function appendUserMsg(text,save=true){
  hideEmpty();
  const d=document.createElement("div");d.className="msg user";
  d.innerHTML='<div class="avatar">U</div><div class="bubble"><div class="bubble-inner">'+escapeHtml(text)+"</div></div>";
  document.getElementById("messages").appendChild(d);scrollBottom();
  if(save){const h=loadMemory();h.push({role:"user",content:text});saveMemory(h);}
}

function appendAgentMsg(text,save=true){
  const d=document.createElement("div");d.className="msg agent";
  const avatar=document.createElement("div");avatar.className="avatar";avatar.textContent="V";
  const bubble=document.createElement("div");bubble.className="bubble";
  const inner=document.createElement("div");inner.className="bubble-inner";
  inner.innerHTML=renderMarkdown(text);
  bubble.appendChild(inner);d.appendChild(avatar);d.appendChild(bubble);
  document.getElementById("messages").appendChild(d);scrollBottom();
  if(save){const h=loadMemory();h.push({role:"assistant",content:text});saveMemory(h);}
}

function appendToolResult(name,result){
  hideEmpty();
  const d=document.createElement("div");d.className="tool-result";
  const header=document.createElement("div");header.className="tool-result-header";header.textContent="⚙ "+name;
  const body=document.createElement("div");body.innerHTML=renderMarkdown(result);
  d.appendChild(header);d.appendChild(body);
  document.getElementById("messages").appendChild(d);scrollBottom();
}

function showThinking(){
  hideEmpty();
  const d=document.createElement("div");d.className="thinking";d.id="thinking";
  d.innerHTML='<div class="avatar" style="background:linear-gradient(135deg,var(--accent),var(--accent2));color:white">V</div><div class="thinking-dots"><span></span><span></span><span></span></div>';
  document.getElementById("messages").appendChild(d);scrollBottom();
}
function removeThinking(){const t=document.getElementById("thinking");if(t)t.remove();}

// ── SEND ──────────────────────────────────────────────────────────────────────
async function sendMessage(){
  const input=document.getElementById("user-input");
  const text=input.value.trim();
  if(!text)return;
  input.value="";autoResize(input);setLoading(true);
  appendUserMsg(text);showThinking();
  try{
    const res=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:text})});
    const data=await res.json();
    removeThinking();
    if(data.error==="upgrade_required"){appendAgentMsg("**Upgrade required.** This model is not available on your current plan. Tap **Upgrade Plan** to unlock it.");openPricing();}
    else if(data.error){appendAgentMsg("**Error:** "+data.error);}
    else{appendAgentMsg(data.reply);document.getElementById("token-count").textContent=data.tokens.toLocaleString()+" tokens";}
  }catch(e){removeThinking();appendAgentMsg("**Error:** "+e.message);}
  finally{setLoading(false);}
}

function sendSuggestion(text){document.getElementById("user-input").value=text;sendMessage();}
function handleKey(e){if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendMessage();}}
function autoResize(el){el.style.height="auto";el.style.height=Math.min(el.scrollHeight,140)+"px";}
function setLoading(on){document.getElementById("send-btn").disabled=on;}

// ── TOOLS ────────────────────────────────────────────────────────────────────
const toolConfig={
  code:{title:"Generate Code",desc:"Describe what you want and AI will generate it.",placeholder:"e.g. Flask REST API with JWT auth"},
  calculate:{title:"Calculator",desc:"Evaluate a math expression.",placeholder:"e.g. (100 * 1.15) + 50"},
  system_info:{title:"System Info",desc:"Show current system info.",placeholder:""},
};
function openTool(name){
  currentTool=name;const cfg=toolConfig[name];
  document.getElementById("modal-title").textContent=cfg.title;
  document.getElementById("modal-desc").textContent=cfg.desc;
  const inp=document.getElementById("modal-input");
  inp.placeholder=cfg.placeholder;inp.value="";
  inp.style.display=name==="system_info"?"none":"block";
  document.getElementById("modal").classList.add("open");
  if(name!=="system_info")setTimeout(()=>inp.focus(),50);
}
function closeModal(){document.getElementById("modal").classList.remove("open");currentTool=null;}
async function runTool(){
  const args=document.getElementById("modal-input").value.trim();
  const tool=currentTool;closeModal();setLoading(true);
  try{
    const res=await fetch("/tool",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tool:tool||"system_info",args})});
    const data=await res.json();
    appendToolResult(toolConfig[tool]?.title||tool,data.result);
  }catch(e){appendToolResult("Error",e.message);}
  finally{setLoading(false);}
}
document.getElementById("modal").addEventListener("click",function(e){if(e.target===this)closeModal();});

// ── MODEL SWITCHER ────────────────────────────────────────────────────────────
async function loadModels(){
  const res=await fetch("/models");
  const data=await res.json();
  const opts=data.models.map(m=>{
    const locked=m.locked?" 🔒":"";
    const badge=m.plan==="pro"?" [Pro]":m.plan==="basic"?" [Basic]":"";
    return '<option value="'+m.id+'"'+(m.id===data.current?" selected":"")+(m.locked?" disabled":"")+">"+m.label+badge+locked+"</option>";
  }).join("");
  ["model-select","model-select-mobile"].forEach(id=>{const el=document.getElementById(id);if(el)el.innerHTML=opts;});
  updatePlanBadge(data.plan);
}
async function switchModel(id){
  if(!id)return;
  const res=await fetch("/model",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model:id})});
  const data=await res.json();
  if(data.error==="upgrade_required"){openPricing();return;}
  ["model-select","model-select-mobile"].forEach(sid=>{const el=document.getElementById(sid);if(el)el.value=id;});
  closeSheet();
}

// ── PRICING ───────────────────────────────────────────────────────────────────
function openPricing(){document.getElementById("pricing-modal").classList.add("open");}
function closePricing(){document.getElementById("pricing-modal").classList.remove("open");}
document.getElementById("pricing-modal").addEventListener("click",function(e){if(e.target===this)closePricing();});

async function selectPlan(plan){
  const res=await fetch("/upgrade",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({plan})});
  const data=await res.json();
  if(data.ok){
    updatePlanBadge(plan);
    closePricing();
    loadModels();
    appendAgentMsg("**Plan updated to "+data.name+"!** You now have access to "+
      (plan==="basic"?"Grok 3 Mini and Gemini 2.0 Flash":plan==="pro"?"Claude Sonnet, Gemini 2.5 Pro, and GPT-4o":"free models only")+".");
  }
}

function updatePlanBadge(plan){
  const badge=document.getElementById("plan-badge");
  badge.className="plan-badge "+plan;
  badge.textContent=plan.charAt(0).toUpperCase()+plan.slice(1);
  ["plan-free-card","plan-basic-card","plan-pro-card"].forEach(id=>{
    document.getElementById(id).classList.remove("current");
  });
  document.getElementById("plan-"+plan+"-card").classList.add("current");
}

// ── BOTTOM SHEET ─────────────────────────────────────────────────────────────
function openSheet(section){
  document.getElementById("sheet-tools-section").style.display=section==="model"?"none":"block";
  document.getElementById("sheet-model-section").style.display=section==="model"?"block":"none";
  document.getElementById("sheet").classList.add("open");
  document.getElementById("sheet-overlay").classList.add("open");
}
function closeSheet(){
  document.getElementById("sheet").classList.remove("open");
  document.getElementById("sheet-overlay").classList.remove("open");
}
function sheetTool(name){closeSheet();openTool(name);}

// ── RESET ────────────────────────────────────────────────────────────────────
async function resetChat(){
  await fetch("/reset",{method:"POST"});
  clearMemory();
  const msgs=document.getElementById("messages");
  msgs.innerHTML="";
  const es=document.createElement("div");
  es.className="empty-state";es.id="empty-state";
  es.innerHTML="<div class=\"empty-icon\">&#9889;</div><h2>What can I help with?</h2><p>Ask anything, generate code, or use the tools.</p>";
  const chips=document.createElement("div");chips.className="suggestion-chips";
  [["Write a Python Flask REST API with JWT auth","Flask REST API"],["Explain async/await in Python","Async/Await"],["Write a web scraper with requests and BeautifulSoup","Web scraper"],["How do I deploy a Flask app on Render?","Deploy on Render"]].forEach(([txt,lbl])=>{
    const c=document.createElement("div");c.className="chip";c.textContent=lbl;
    c.onclick=()=>sendSuggestion(txt);chips.appendChild(c);
  });
  es.appendChild(chips);msgs.appendChild(es);
  document.getElementById("token-count").textContent="0 tokens";
}

loadModels();
</script>
</body>
</html>"""
)

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
