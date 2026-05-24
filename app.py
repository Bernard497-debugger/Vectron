from flask import Flask, request, jsonify, session, send_file
import requests
import os
import re
import platform
import secrets

app = Flask(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "baidu/cobuddy:free")
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
SITE_URL = os.environ.get("SITE_URL", "https://vectron.onrender.com")

AVAILABLE_MODELS = [
    {"id": "baidu/cobuddy:free",                         "label": "CoBuddy — Free"},
    {"id": "poolside/laguna-xs.2:free",                  "label": "Laguna XS.2 — Free"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free",     "label": "Llama 3.3 70B — Free"},
    {"id": "deepseek/deepseek-chat:free",                "label": "DeepSeek V3 — Free"},
    {"id": "mistralai/mistral-small-3.1-24b-instruct:free", "label": "Mistral Small — Free"},
    {"id": "x-ai/grok-3-mini-beta",                      "label": "Grok 3 Mini"},
    {"id": "google/gemini-2.0-flash-001",                "label": "Gemini 2.0 Flash"},
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
    return jsonify({"models": AVAILABLE_MODELS, "current": current})

@app.route("/model", methods=["POST"])
def set_model():
    model_id = (request.json or {}).get("model", "").strip()
    if not model_id:
        return jsonify({"error": "No model provided"}), 400
    session["model"] = model_id
    return jsonify({"ok": True, "model": model_id})

@app.route("/")
def index():
    session.setdefault("history", [])
    session.setdefault("tokens", 0)
    return HTML

@app.route("/chat", methods=["POST"])
def chat():
    user_message = (request.json or {}).get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    history = session.get("history", [])
    history.append({"role": "user", "content": user_message})

    active_model = session.get("model", DEFAULT_MODEL)
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        reply, usage = call_openrouter(messages, model=active_model)
        tokens = session.get("tokens", 0) + usage.get("total_tokens", 0)
        history.append({"role": "assistant", "content": reply})
        # keep last 40 messages to avoid token bloat
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
        if not args:
            return jsonify({"result": "Provide a math expression"})
        return jsonify({"result": calculate(args)})

    elif tool_name == "system_info":
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
            reply, _ = call_openrouter(messages, temperature=0.3, model=active_model)
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
  width:220px;border-right:1px solid var(--border);
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

/* ── MODAL ── */
.modal-overlay{
  position:fixed;inset:0;background:rgba(0,0,0,.6);
  backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:100;
}
.modal-overlay.open{display:flex;}
.modal{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:24px;width:420px;max-width:92vw;animation:fadeUp .2s ease;
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
  #messages{padding:12px 14px;padding-bottom:calc(var(
