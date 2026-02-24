from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from datetime import datetime, timedelta
from pathlib import Path
import json, os, secrets, io, logging, zipfile
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PLACEHOLDER_VALUES = {"", "your-key-here", "your-email@example.com", "your-email-password"}

try:
    from flask_mail import Mail, Message
    MAIL_IMPORT_OK = True
except Exception:
    Mail = None
    Message = None
    MAIL_IMPORT_OK = False
    logger.warning("Flask-Mail not installed - email sending disabled")

AI_CLIENT = None
AI_ENABLED = False
raw_openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_KEY = "" if raw_openai_key.lower() in PLACEHOLDER_VALUES else raw_openai_key
if OPENAI_KEY:
    try:
        from openai import OpenAI
        AI_CLIENT = OpenAI(api_key=OPENAI_KEY)
        AI_ENABLED = True
    except Exception as e:
        logger.warning("OpenAI client unavailable: %s", e)

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY is required. Set it in .env or environment.")
app.secret_key = secret_key
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///app.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
raw_mail_username = (os.getenv("MAIL_USERNAME", "") or "").strip()
raw_mail_password = (os.getenv("MAIL_PASSWORD", "") or "").strip()
raw_mail_sender = (os.getenv("MAIL_DEFAULT_SENDER", raw_mail_username) or "").strip()
app.config["MAIL_USERNAME"] = "" if raw_mail_username.lower() in PLACEHOLDER_VALUES else raw_mail_username
app.config["MAIL_PASSWORD"] = "" if raw_mail_password.lower() in PLACEHOLDER_VALUES else raw_mail_password
app.config["MAIL_DEFAULT_SENDER"] = "" if raw_mail_sender.lower() in PLACEHOLDER_VALUES else raw_mail_sender
app.jinja_env.auto_reload = True
EMAIL_VERIFICATION_REQUIRED = os.getenv("REQUIRE_EMAIL_VERIFICATION", "false").strip().lower() in {"1", "true", "yes", "on"}

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
mail = Mail(app) if MAIL_IMPORT_OK and Mail is not None else None
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)
serializer = URLSafeTimedSerializer(app.secret_key)

# ── MODELS ──────────────────────────────────────────────────
class User(UserMixin, db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    username   = db.Column(db.String(80),  unique=True, nullable=False)
    email      = db.Column(db.String(120), unique=True, nullable=False)
    password   = db.Column(db.String(200), nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items      = db.relationship("Item", backref="user", lazy=True, cascade="all, delete-orphan")

class Item(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String(100), nullable=False)
    data       = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def _token(kind: str, user_id: int) -> str:
    return serializer.dumps({"kind": kind, "uid": user_id})

def _verify_token(token: str, kind: str, max_age: int):
    try:
        data = serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if data.get("kind") != kind:
        return None
    uid = data.get("uid")
    if not uid:
        return None
    return User.query.get(int(uid))

def _send_mail(to_email: str, subject: str, body: str) -> bool:
    if (not MAIL_IMPORT_OK) or (mail is None) or (Message is None):
        logger.info("Mail library unavailable. Subject=%s Body=%s", subject, body)
        return False
    if not app.config.get("MAIL_SERVER") or not app.config.get("MAIL_DEFAULT_SENDER"):
        logger.info("Mail not configured. Subject=%s Body=%s", subject, body)
        return False
    try:
        msg = Message(subject=subject, recipients=[to_email], body=body)
        mail.send(msg)
        return True
    except Exception as e:
        logger.warning("Mail send failed: %s", e)
        logger.info("Mail fallback body: %s", body)
        return False

def _local_60sec_fix(problem_text: str) -> str:
    return (
        "Situation Snapshot:\n"
        f"- {problem_text[:180]}\n\n"
        "Immediate Actions (next 10 minutes):\n"
        "- 4 deep breaths (4-4-6 pattern)\n"
        "- Pani piyo, face wash karo, posture straight karo\n"
        "- Ek chhota task choose karo aur 10-minute timer lagao\n"
        "- Boss ya issue ka short neutral note likho (facts only)\n\n"
        "Motivation:\n"
        "- Aaj ka din kharab ho sakta hai, lekin next 60 minutes tum control kar sakte ho.\n\n"
        "Next 2-3 Hour Micro Plan:\n"
        "- 0-20 min: Quick recovery + priority list\n"
        "- 20-80 min: Deep work sprint on top priority\n"
        "- 80-100 min: Break + stretch\n"
        "- 100-150 min: Second focused sprint\n"
    )

# ── ROUTES ───────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if not all([username, email, password]):
            flash("All fields are required!", "error")
        elif len(username) < 3:
            flash("Username must be at least 3 characters!", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters!", "error")
        elif password != confirm:
            flash("Passwords do not match!", "error")
        elif User.query.filter_by(username=username).first():
            flash("Username already taken!", "error")
        elif User.query.filter_by(email=email).first():
            flash("Email already registered!", "error")
        else:
            user = User(
                username=username,
                email=email,
                password=generate_password_hash(password, method="pbkdf2:sha256")
            )
            db.session.add(user)
            db.session.commit()
            if not EMAIL_VERIFICATION_REQUIRED:
                user.is_verified = True
                db.session.commit()
                login_user(user, remember=True)
                flash(f"Welcome, {user.username}! Account created.", "success")
                return redirect(url_for("dashboard"))
            token = _token("verify", user.id)
            verify_link = url_for("verify_email", token=token, _external=True)
            sent = _send_mail(
                user.email,
                "Verify your account",
                f"Hi {user.username}, verify your account: {verify_link}"
            )
            if sent:
                flash("Account created. Check your email to verify.", "success")
            else:
                flash(f"Account created. Mail not configured. Verify here: {verify_link}", "error")
            return redirect(url_for("login"))
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter((User.username == username) | (User.email == username.lower())).first()
        if user and check_password_hash(user.password, password):
            if not user.is_verified:
                if EMAIL_VERIFICATION_REQUIRED:
                    flash("Please verify your email before login.", "error")
                    return redirect(url_for("login"))
                user.is_verified = True
                db.session.commit()
            login_user(user, remember=True)
            flash(f"Welcome back, {user.username}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password!", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("home"))

@app.route("/verify-email/<token>")
def verify_email(token):
    user = _verify_token(token, "verify", max_age=60 * 60 * 24)
    if not user:
        flash("Invalid or expired verification link.", "error")
        return redirect(url_for("login"))
    user.is_verified = True
    db.session.commit()
    flash("Email verified. You can login now.", "success")
    return redirect(url_for("login"))

@app.route("/resend-verification", methods=["POST"])
@csrf.exempt
def resend_verification():
    email = request.form.get("email", "").strip().lower()
    user = User.query.filter_by(email=email).first()
    if user and not user.is_verified:
        token = _token("verify", user.id)
        verify_link = url_for("verify_email", token=token, _external=True)
        _send_mail(user.email, "Verify your account", f"Verify: {verify_link}")
    flash("If account exists, verification link was sent.", "success")
    return redirect(url_for("login"))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            token = _token("reset", user.id)
            reset_link = url_for("reset_password", token=token, _external=True)
            _send_mail(user.email, "Reset your password", f"Reset password: {reset_link}")
        flash("If account exists, password reset link was sent.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = _verify_token(token, "reset", max_age=60 * 60)
    if not user:
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            user.password = generate_password_hash(password, method="pbkdf2:sha256")
            db.session.commit()
            flash("Password reset successful. Please login.", "success")
            return redirect(url_for("login"))
    return render_template("reset_password.html")

@app.route("/dashboard")
@login_required
def dashboard():
    items = Item.query.filter_by(user_id=current_user.id).order_by(Item.updated_at.desc()).all()
    fallback_msg = None
    try:
        with open("ai-fallback-reason.txt", "r", encoding="utf-8") as f:
            fallback_msg = f.read().strip()
    except FileNotFoundError:
        pass
    if fallback_msg:
        lowered = fallback_msg.lower()
        if "openai client unavailable" in lowered or "openai_api_key missing" in lowered:
            fallback_msg = None
    return render_template("dashboard.html", items=items, fallback_msg=fallback_msg, project_name="create-a-production-ready-flask-web-app-called-60secai-ai-fix-my")

@app.route("/fallback/clear", methods=["POST"])
@login_required
def clear_fallback_reason():
    try:
        os.remove("ai-fallback-reason.txt")
        flash("Fallback notice cleared.", "success")
    except FileNotFoundError:
        pass
    except Exception:
        flash("Could not clear fallback notice.", "error")
    return redirect(url_for("dashboard"))

@app.route("/item/new")
@login_required
def new_item():
    return render_template("item_form.html", item=None)

@app.route("/item/save", methods=["POST"])
@login_required
def save_item():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        generate_fix = bool(data.pop("generate_fix", False))
        problem_text = str(data.get("content", "")).strip()
        generate_fix = generate_fix or bool(problem_text)
        item_id = data.get("id")
        if item_id:
            item = Item.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
            item.title      = str(data.get("title", item.title))[:100]
            item.data       = json.dumps(data)
            item.updated_at = datetime.utcnow()
        else:
            item = Item(
                title   = str(data.get("title", "Untitled"))[:100],
                data    = json.dumps(data),
                user_id = current_user.id
            )
            db.session.add(item)
        db.session.commit()

        if generate_fix:
            ai_fix = ""
            if AI_ENABLED and AI_CLIENT and problem_text:
                ai_prompt = (
                    "User problem:\n"
                    f"{problem_text}\n\n"
                    "You are a 60-second life coach. Return:\n"
                    "1) Quick situation analysis\n"
                    "2) 3-5 immediate actions\n"
                    "3) Short motivation line\n"
                    "4) Next 2-3 hour micro-plan\n"
                    "Tone: empathetic, clear, practical.\n"
                )
                try:
                    response = AI_CLIENT.chat.completions.create(
                        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        messages=[{"role": "user", "content": ai_prompt}],
                        max_tokens=450,
                        temperature=0.7,
                    )
                    ai_fix = (response.choices[0].message.content or "").strip()
                except Exception as e:
                    logger.warning("AI fix generation failed: %s", e)
            if not ai_fix and problem_text:
                ai_fix = _local_60sec_fix(problem_text)
            if ai_fix:
                current_data = json.loads(item.data)
                current_data["ai_fix"] = ai_fix
                item.data = json.dumps(current_data)
                db.session.commit()

        return jsonify({"success": True, "id": item.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/item/<int:item_id>")
@login_required
def view_item(item_id):
    item = Item.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
    data = json.loads(item.data)

    # Backfill AI fix for older items that were saved before auto-fix logic.
    if "ai_fix" not in data:
        problem_text = str(data.get("content", "")).strip()
        if problem_text:
            ai_fix = ""
            if AI_ENABLED and AI_CLIENT:
                ai_prompt = (
                    "User problem:\n"
                    f"{problem_text}\n\n"
                    "You are a 60-second life coach. Return:\n"
                    "1) Quick situation analysis\n"
                    "2) 3-5 immediate actions\n"
                    "3) Short motivation line\n"
                    "4) Next 2-3 hour micro-plan\n"
                    "Tone: empathetic, clear, practical.\n"
                )
                try:
                    response = AI_CLIENT.chat.completions.create(
                        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        messages=[{"role": "user", "content": ai_prompt}],
                        max_tokens=450,
                        temperature=0.7,
                    )
                    ai_fix = (response.choices[0].message.content or "").strip()
                except Exception as e:
                    logger.warning("AI backfill failed: %s", e)
            if not ai_fix:
                ai_fix = _local_60sec_fix(problem_text)
            data["ai_fix"] = ai_fix
            item.data = json.dumps(data)
            db.session.commit()

    return render_template("item_view.html", item=item, data=data)

@app.route("/item/<int:item_id>/edit")
@login_required
def edit_item(item_id):
    item = Item.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
    return render_template("item_form.html", item=item)

@app.route("/item/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(item_id):
    item = Item.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    flash("Deleted successfully!", "success")
    return redirect(url_for("dashboard"))

@app.route("/project/export.zip")
@login_required
def export_project_zip():
    root = Path.cwd()
    excluded = {".git", "__pycache__", ".venv", "venv", ".pytest_cache"}
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in excluded for part in path.parts):
                continue
            rel = path.relative_to(root)
            zf.write(path, arcname=str(rel))
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"create-a-production-ready-flask-web-app-called-60secai-ai-fix-my.zip"
    )

@app.route("/item/<int:item_id>/pdf")
@login_required
def download_pdf(item_id):
    flash("PDF download is temporarily disabled. Use browser print instead!", "info")
    return redirect(url_for("view_item", item_id=item_id))

@app.route("/ai/bullets", methods=["POST"])
@login_required
def ai_bullets():
    if not AI_ENABLED or AI_CLIENT is None:
        return jsonify({"error": "AI unavailable. Set OPENAI_API_KEY and install openai."}), 503
    data = request.get_json() or {}
    section = str(data.get("section", "")).strip()
    context = str(data.get("context", "")).strip()
    if not section or not context:
        return jsonify({"error": "section and context are required"}), 400
    prompt = (
        "You are a professional resume writer.\n"
        "Generate 3-4 strong, ATS-optimized bullet points.\n"
        f"Section: {section}\n"
        f"Context: {context}\n"
        "Rules:\n"
        "- Start each bullet with a strong action verb\n"
        "- Include metrics where possible\n"
        "- Keep each bullet under 120 characters\n"
        "Return ONLY bullets, one per line, starting with •"
    )
    response = AI_CLIENT.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.7,
    )
    text = response.choices[0].message.content or ""
    bullets = [line.strip() for line in text.split("\n") if line.strip().startswith("•")]
    if not bullets:
        bullets = [line.strip() for line in text.split("\n") if line.strip()]
    return jsonify({"bullets": bullets})

# ── ERROR HANDLERS ───────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, use_reloader=True, reloader_type="stat", port=5000)
