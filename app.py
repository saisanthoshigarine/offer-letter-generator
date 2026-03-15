import token
from flask import Flask, render_template, request, redirect, session, url_for, send_file
import sqlite3
import os
import pandas as pd
import uuid
from flask import Flask, render_template, request
from datetime import datetime, timedelta
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.ttfonts import TTFont
from email.mime.text import MIMEText
import base64
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
import io
import json
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
app.secret_key = os.environ.get("SECRET_KEY", "dev_key")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
PDF_FOLDER = os.path.join(BASE_DIR, "generated_letters")
DB = os.path.join(BASE_DIR, "database.db")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PDF_FOLDER, exist_ok=True)

# ---------------- DATABASE INIT ----------------
print("BREVO_API_KEY:", os.environ.get("BREVO_API_KEY"))
print("BASE_URL:", os.environ.get("BASE_URL"))
DB = "offers.db"  # define the database file at the top

def init_db():
    with sqlite3.connect(DB) as conn:
        # Create offers table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS offers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                email TEXT,
                role TEXT,
                joining_date TEXT,
                company TEXT,
                work_type TEXT,
                status TEXT,
                token TEXT,
                sent_time TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            email TEXT,
            password TEXT,
            reset_token TEXT,
            reset_expiry TEXT
            )
        """)
    print("✅ Tables 'users' and 'offers' are ready.")

# Call the function once at app startup
init_db()
# ---------------- LOGIN REQUIRED ----------------

def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrap(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/")
        return f(*args, **kwargs)
    return wrap

# ---------------- LOGIN ----------------

@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        with sqlite3.connect(DB) as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username=? AND password=?",
                (username,password)
            ).fetchone()

        if user:
            session["user_id"] = user[0]
            return redirect("/dashboard")
        else:
            return "Invalid Credentials"

    return render_template("login.html")

# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        with sqlite3.connect(DB) as conn:
            conn.execute("INSERT INTO users(username,password) VALUES(?,?)",
                         (username,password))

        return redirect("/")

    return render_template("register.html")
# ---------------- FORGOT PASSWORD ----------------
@app.route("/forgot_password", methods=["GET","POST"])
def forgot_password():

    if request.method == "POST":
        email = request.form.get("email")

        if not email:
            return "Please enter email"

        with sqlite3.connect(DB) as conn:
            conn.row_factory = sqlite3.Row
            user = conn.execute(
                "SELECT * FROM users WHERE email=?",
                (email,)
            ).fetchone()

            if not user:
                return "Email not registered"

            token = str(uuid.uuid4())
            expiry = (datetime.now() + timedelta(minutes=30)).isoformat()

            conn.execute(
                "UPDATE users SET reset_token=?, reset_expiry=? WHERE id=?",
                (token, expiry, user["id"])
            )
            conn.commit()

        reset_link = f"{BASE_URL}/reset_password/{token}"

        # ---------------- BREVO EMAIL CODE ----------------

        try:
            configuration = Configuration()
            configuration.api_key['api-key'] =os.environ.get("BREVO_API_KEY")

            api_instance = TransactionalEmailsApi(ApiClient(configuration))

            email_data = SendSmtpEmail(
                to=[{"email": email}],
                sender={
                    "email": os.environ.get("SENDER_EMAIL"),
                    "name": "Offer Letter System"
                },
                subject="Reset Your Password",
                html_content=f"""
                    <h3>Password Reset</h3>
                    <p>Click below to reset your password:</p>
                    <a href="{reset_link}">Reset Password</a>
                    <p>This link expires in 30 minutes.</p>
                """
            )

            api_instance.send_transac_email(email_data)

        except Exception as e:
            print("Email sending failed:", e)
            return "Failed to send email"

        # --------------------------------------------------

        return "Reset link sent to your email."

    return render_template("forgot_password.html")
#----------------- RESET PASSWORD ----------------
@app.route("/reset_password/<token>", methods=["GET","POST"])
def reset_password(token):

    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute(
            "SELECT * FROM users WHERE reset_token=?",
            (token,)
        ).fetchone()

        if not user:
            return "Invalid or expired link"

        if datetime.now() > datetime.fromisoformat(user["reset_expiry"]):
            return "Reset link expired"

        if request.method == "POST":
            new_password = request.form["password"]

            conn.execute(
                "UPDATE users SET password=?, reset_token=NULL, reset_expiry=NULL WHERE id=?",
                (new_password, user["id"])
            )
            conn.commit()

            return redirect("/")

    return render_template("reset_password.html")
# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
@login_required
def dashboard():
    now = datetime.now()

    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        offers = conn.execute(
            "SELECT * FROM offers WHERE user_id=?",
            (session["user_id"],)
        ).fetchall()

        # Update expired offers
        for offer in offers:
            if offer["status"] == "action_pending" and offer["sent_time"]:
                sent_time = datetime.fromisoformat(offer["sent_time"])
                if now > sent_time + timedelta(hours=48):
                    conn.execute(
                        "UPDATE offers SET status='cancelled' WHERE id=?",
                        (offer["id"],)
                    )

        # 🔥 IMPORTANT
        conn.commit()

        # Now count again AFTER updates
        total = conn.execute(
            "SELECT COUNT(*) FROM offers WHERE user_id=?",
            (session["user_id"],)
        ).fetchone()[0]

        pending = conn.execute(
            "SELECT COUNT(*) FROM offers WHERE user_id=? AND status='action_pending'",
            (session["user_id"],)
        ).fetchone()[0]

        accepted = conn.execute(
            "SELECT COUNT(*) FROM offers WHERE user_id=? AND status='accepted'",
            (session["user_id"],)
        ).fetchone()[0]

        declined = conn.execute(
            "SELECT COUNT(*) FROM offers WHERE user_id=? AND status='declined'",
            (session["user_id"],)
        ).fetchone()[0]

        cancelled = conn.execute(
            "SELECT COUNT(*) FROM offers WHERE user_id=? AND status='cancelled'",
            (session["user_id"],)
        ).fetchone()[0]

    return render_template(
        "dashboard.html",
        total=total,
        pending=pending,
        accepted=accepted,
        declined=declined,
        cancelled=cancelled
    )
# ---------------- VIEW OFFERS BY STATUS ----------------
@app.route("/offers/<status>")
@login_required
def view_offers(status):

    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        if status == "total":
            # Show ALL offers for logged-in user
            offers = conn.execute(
                "SELECT name, email, role, joining_date, status FROM offers WHERE user_id=?",
                (session["user_id"],)
            ).fetchall()
        else:
            # Show filtered offers
            offers = conn.execute(
                "SELECT name, email, role, joining_date, status FROM offers WHERE user_id=? AND status=?",
                (session["user_id"], status)
            ).fetchall()

    return render_template("offer_list.html", offers=offers, status=status)
# ---------------- UPLOAD ----------------

@app.route("/upload", methods=["GET","POST"])
@login_required
def upload():

    if request.method == "POST":
        file = request.files["file"]

        if not file.filename.endswith(".xlsx"):
            return "File not supported"

        filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(filepath)

        df = pd.read_excel(filepath)

        required = ["Name","Status","Role","Joining date","Gmail id"]

        for col in required:
            if col not in df.columns:
                return "File doesn't have required information"

        session["excel_data"] = df.to_dict(orient="records")
        return redirect("/company")

    return render_template("upload.html")

# ---------------- COMPANY ----------------

@app.route("/company", methods=["GET","POST"])
@login_required
def company():
    if request.method == "POST":
        company = request.form.get("company")
        session["company"] = company
        print("Company saved in session:", company)
        return redirect("/worktype")

    return render_template("company.html")
# ---------------- WORKTYPE ----------------

@app.route("/worktype", methods=["GET","POST"])
@login_required
def worktype():
    if request.method == "POST":
        session["worktype"] = request.form["worktype"]
        return redirect("/pattern")

    return render_template("worktype.html")

# ---------------- PATTERNS ----------------
def get_patterns(worktype):
    
    pattern_data = {
    "Full Time": [
        {"id": "ft1", "name": "Corporate Classic",
         "content": """Dear {Name},

We are delighted to formally offer you the position of {Role} within our 

esteemed organization.Your appointment will be on a {Status} basis,
 
reflecting our confidence in your exceptional skills, diligence, and 

professional acumen. 

Your joining date is {Joining_date}, when you are expected to report and

complete onboarding. This role offers a strategic opportunity for 

professional growth and impactful contributions. 

We eagerly anticipate your addition to our team and a rewarding collaboration. 

Warm regards,

HR Team"""},
        
        {"id": "ft2", "name": "Modern Executive",
         "content": """Dear {Name},

Congratulations on your selection as {Role}. Your employment will commence

on {Joining_date} and will be on a {Status} basis. We value your innovative

approach and strategic mindset. 

In this role, you will contribute to high-impact initiatives, fostering 

excellence and innovation within the organization. Your professionalism and

commitment will be key to our shared success. 

Welcome aboard!

Sincerely,

HR Team"""},
        
        {"id": "ft3", "name": "Premium Minimal",
         "content": """Dear {Name},

We are pleased to confirm your engagement as {Role}, starting {Joining_date}
 
on a {Status} basis. This letter acknowledges our confidence in your 

expertise and dedication. 

You are expected to uphold exemplary standards, contribute meaningfully, and

collaborate effectively within the organization. 

We look forward to a successful and mutually rewarding association.

Best regards,

HR Team"""},
        
        {"id": "custom", "name": "✨ Custom Full-Time Pattern", "content": None}
    ],

    "Part Time": [
        {"id": "pt1", "name": "Flexible Schedule",
         "content": """Dear {Name},

We are pleased to engage you as {Role} on a {Status} part-time basis, commencing 

{Joining_date}. This flexible arrangement allows you to contribute effectively 

while maintaining adaptability. 

You are expected to perform your responsibilities with diligence, accountability,
 
and professionalism. 

We welcome your collaboration and anticipate a productive engagement.

Sincerely,

HR Team"""},
        
        {"id": "pt2", "name": "Consultant Style",
         "content": """Dear {Name},

We are pleased to confirm your engagement as {Role} commencing {Joining_date} 

on a {Status} basis. You will provide expertise and guidance with professional

independence while contributing to strategic objectives. 

Your insights and skills are highly valued, and we anticipate a mutually 

beneficial association. 

Best regards,

HR Team"""},
        
        {"id": "custom", "name": "✨ Custom Part-Time Pattern", "content": None}
    ],

    "Internship": [
        {"id": "int1", "name": "Academic Internship",
         "content": """Dear {Name},

We are pleased to offer you an Academic Internship as {Role} beginning {Joining_date}

on a {Status} basis. This opportunity provides structured learning, practical 

exposure, and professional mentorship. 

You will actively participate in tasks to enhance your academic and professional
 
acumen.We look forward to guiding your development and fostering growth. 

Sincerely,

HR Team"""},
        
        {"id": "int2", "name": "Startup Internship",
         "content": """Dear {Name},

Congratulations on your selection as {Role} Intern starting {Joining_date}

on a {Status} basis. This dynamic internship will provide hands-on experience

in a collaborative, fast-paced environment. 

You are encouraged to take initiative, contribute meaningfully, and develop

valuable skills for your professional journey. 

Warm regards,

HR Team"""},
        
        {"id": "custom", "name": "✨ Custom Internship Pattern", "content": None}
    ]
}
    return pattern_data.get(worktype, [])

@app.route("/pattern")
@login_required
def pattern():

    worktype = session.get("worktype")

    if not worktype:
        return redirect("/worktype")

    patterns = get_patterns(worktype)

    return render_template("pattern.html", patterns=patterns)
@app.route("/select_pattern/<pattern_id>")
@login_required
def select_pattern(pattern_id):

    worktype = session.get("worktype")
    patterns = get_patterns(worktype)

    selected_pattern = next(
        (p for p in patterns if p["id"] == pattern_id),
        None
    )

    if not selected_pattern:
        return "Invalid Pattern", 400

    # If custom pattern
    if pattern_id == "custom":
        return redirect("/custom_pattern")

    session["template"] = selected_pattern["content"]

    return redirect("/preview")
# ---------------- CUSTOM PATTERN ----------------
@app.route("/custom_pattern", methods=["GET","POST"])
@login_required
def custom_pattern():
    if request.method == "POST":
        custom_text = request.form.get("custom_text")
        if not custom_text.strip():
            return "Please enter a custom template", 400
        session["template"] = custom_text
        return redirect("/preview")
    return render_template("custom.html")
# ---------------- SERVE PREVIEW PDF ----------------
@app.route("/preview_file/<filename>")
@login_required
def preview_file(filename):
    file_path = os.path.join(PDF_FOLDER, filename)

    if not os.path.exists(file_path):
        return "File not found", 404

    return send_file(file_path, mimetype="application/pdf")

# ---------------- PREVIEW PAGE ----------------
@app.route("/preview", methods=["GET","POST"])
@login_required
def preview():
    data_list = session["excel_data"]
    template = session["template"]

    data = data_list[0]

    joining_raw = data["Joining date"]
    joining_date = joining_raw.strftime("%d %B %Y") if hasattr(joining_raw, "strftime") else str(joining_raw)

    content = template.format(
        Name=data["Name"],
        Role=data["Role"],
        Status=data["Status"],
        Joining_date=joining_date
    )

    if request.method == "POST":

        edited = request.form["edited_content"]
        action = request.form["action"]

        if action == "send_all":

            for row in data_list:

                pdf_path = generate_pdf(template.format(
                    Name=row["Name"],
                    Role=row["Role"],
                    Status=row["Status"],
                    Joining_date=row["Joining date"]
                ), preview=False)

                send_mail_function(pdf_path, row)

            return redirect("/dashboard")

        elif action == "download_all":

            from zipfile import ZipFile
            import io

            zip_buffer = io.BytesIO()

            with ZipFile(zip_buffer, "w") as zip_file:

                for row in data_list:

                    pdf_path = generate_pdf(template.format(
                        Name=row["Name"],
                        Role=row["Role"],
                        Status=row["Status"],
                        Joining_date=row["Joining date"]
                    ), preview=False)

                    zip_file.write(pdf_path, os.path.basename(pdf_path))

            zip_buffer.seek(0)

            return send_file(
                zip_buffer,
                mimetype="application/zip",
                as_attachment=True,
                download_name="offer_letters.zip"
            )

    preview_file_name = generate_pdf(content, preview=True)

    if not preview_file_name:
        return "Letterhead PDF not found. Cannot generate preview."

    return render_template(
        "preview.html",
        content=content,
        pdf_file=preview_file_name
    )
# ---------------- GENERATE PDF ----------------
def generate_pdf(content, preview=False):
    
    file_name = "preview_offer.pdf" if preview else f"offer_{uuid.uuid4().hex}.pdf"
    file_path = os.path.join(PDF_FOLDER, file_name)

    # Get company
    company = session.get("company", "").strip()
    company_clean = company.replace(" ", "").lower()

    print("Selected company:", company_clean)

    # Letterhead folder
    letterhead_folder = os.path.join(app.root_path, "static", "letterheads")

    letterhead_path = os.path.join(
        letterhead_folder,
        f"{company_clean}_letterhead.pdf"
    )

    print("Looking for:", letterhead_path)

    if not os.path.exists(letterhead_path):
        print("❌ Letterhead PDF not found")
        return None

    print("✅ Using letterhead:", letterhead_path)

    # Create content in memory
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)

    text_obj = can.beginText()
    text_obj.setTextOrigin(70, 650)
    text_obj.setFont("Helvetica", 12)

    for line in content.split("\n"):
        text_obj.textLine(line)

    can.drawText(text_obj)
    can.save()

    packet.seek(0)

    # Merge with letterhead
    letterhead_pdf = PdfReader(letterhead_path)
    content_pdf = PdfReader(packet)

    writer = PdfWriter()

    page = letterhead_pdf.pages[0]
    page.merge_page(content_pdf.pages[0])

    writer.add_page(page)

    with open(file_path, "wb") as output:
        writer.write(output)

    if preview:
        return file_name

    return file_path

    # -------- MERGE WITH LETTERHEAD --------
    letterhead_pdf = PdfReader(letterhead_path)
    content_pdf = PdfReader(packet)
    writer = PdfWriter()

    page = letterhead_pdf.pages[0]
    page.merge_page(content_pdf.pages[0])
    writer.add_page(page)

    with open(file_path, "wb") as output:
        writer.write(output)

    if preview:
        return file_name

    return file_path
# ---------------- SEND MAIL ----------------
from sib_api_v3_sdk import ApiClient, Configuration, TransactionalEmailsApi
from sib_api_v3_sdk.models import SendSmtpEmail
import base64, uuid
from flask import url_for, session
import sqlite3
from datetime import datetime
import os

DB = "offers.db"

def send_mail_function(pdf_path, data):
    """
    Send an offer letter email via Brevo with tokenized accept/decline links,
    and save the offer record to DB with status 'action_pending'.
    """

    # ---------------- CONFIGURE BREVO ----------------
    from sib_api_v3_sdk import ApiClient, Configuration, TransactionalEmailsApi
    from sib_api_v3_sdk.models import SendSmtpEmail
    import base64, uuid
    from datetime import datetime

    configuration = Configuration()
    # Replace with your **full-access transactional email API key**
    configuration.api_key['api-key'] = os.environ.get("BREVO_API_KEY")

    api_instance = TransactionalEmailsApi(ApiClient(configuration))

    # ---------------- GENERATE TOKEN LINKS ----------------
    token = str(uuid.uuid4())

    accept_link = f"{BASE_URL}/accept/{token}"
    decline_link = f"{BASE_URL}/decline/{token}"

    print("ACCEPT LINK:", accept_link)
    print("DECLINE LINK:", decline_link)

    body_html = f"""
    <p>Hello {data['Name']},</p>
    <p>Please find your offer letter attached.</p>
    <p>
        ✅ <a href="{accept_link}">Accept Offer</a><br>
        ❌ <a href="{decline_link}">Decline Offer</a>
    </p>
    <p>Respond within 48 hours.</p>
    """

    # ---------------- READ PDF AS BASE64 ----------------
    try:
        with open(pdf_path, "rb") as f:
            pdf_content = base64.b64encode(f.read()).decode()
    except Exception as e:
        print(f"❌ Failed to read PDF {pdf_path}: {e}")
        return

    # ---------------- CREATE EMAIL ----------------
    email = SendSmtpEmail(
        to=[{"email": data["Gmail id"], "name": data["Name"]}],
        sender={"email": os.environ.get("SENDER_EMAIL"), "name": "HR Team"},
        subject="Offer Letter from Company",
        html_content=body_html,
        attachment=[{
            "content": pdf_content,
            "name": os.path.basename(pdf_path),
            "type": "application/pdf"
        }]
    )

    # ---------------- SEND EMAIL ----------------
    try:
        api_instance.send_transac_email(email)
        print(f"✅ Email sent to {data['Gmail id']}")

        # ---------------- SAVE TO DATABASE ----------------
        with sqlite3.connect(DB) as conn:
            conn.execute("""
            INSERT INTO offers(
                user_id, name, email, role, joining_date,
                company, work_type, status, token, sent_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session["user_id"],
            data["Name"],
            data["Gmail id"],
            data["Role"],
            str(data["Joining date"]),
            session.get("company"),
            session.get("worktype"),
            "action_pending",
            token,
            datetime.now().isoformat()
            ))

        conn.commit()   # 🔴 ADD THIS
    except Exception as e:
        print(f"❌ Failed to send email to {data['Gmail id']}: {e}")
# ---------------- ACCEPT / DECLINE ----------------
@app.route('/accept/<token>')
def accept(token):
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        offer = conn.execute(
            "SELECT status FROM offers WHERE token=?",
            (token,)
        ).fetchone()

        if not offer:
            return "Invalid Offer Link ❌", 404

        if offer["status"] != "action_pending":
            return "You have already responded to this offer.", 400

        conn.execute(
            "UPDATE offers SET status=? WHERE token=?",
            ("accepted", token)
        )
        conn.commit()

    return "Offer Accepted ✅"

@app.route('/decline/<token>')
def decline(token):
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row

        offer = conn.execute(
            "SELECT status FROM offers WHERE token=?",
            (token,)
        ).fetchone()

        if not offer:
            return "Invalid Offer Link ❌", 404

        if offer["status"] != "action_pending":
            return "You have already responded to this offer.", 400

        conn.execute(
            "UPDATE offers SET status=? WHERE token=?",
            ("declined", token)
        )
        conn.commit()

    return "Offer Declined ❌"
# ---------------- LOGOUT ----------------

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(debug=True)