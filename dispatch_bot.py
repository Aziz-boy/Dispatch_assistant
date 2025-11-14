import os
import pdfplumber
from openai import OpenAI
from telegram import Update
from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)
from threading import Thread
from flask import Flask

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Flask app for health check
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return '🤖 Dispatch Bot is running!', 200

@flask_app.route('/health')
def health():
    return 'OK', 200

# -------- START COMMAND ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_first_name = update.effective_user.first_name
    welcome_message = (
        f"👋 Hey {user_first_name}!\n\n"
        "I'm your **Dispatch Assistant Bot** 🚛\n\n"
        "Send me a **Rate Confirmation PDF** or an **image** of one, "
        "and I'll extract key info for you — Load#, REF#, PU/DEL, Rate, Miles, and Notes.\n\n"
        "📎 Just upload your file and I'll handle the rest!\n\n"
        "Need help? Type /help"
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

# -------- PDF TEXT EXTRACTION ----------
def extract_text_from_pdf(pdf_path):
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
    return text.strip()

# -------- OPENAI DISPATCH EXTRACTION ----------
def extract_dispatch_info_with_ai(text):
    prompt = f"""
You are an expert logistics dispatcher bot.

Read the rate confirmation text below and extract the following fields:
- Load #
- REF #
- Pickup (PU): date, time, shipper name, address
- Delivery (DEL): date, time, receiver name, address
- Rate
- Miles
- Fines or Notes
- Any other important details or numbers found in the text.


Return the answer in this exact format (if there is any additional info, include it):

Load# [number]

REF# [reference number]

⏳ PU: [pickup date + time (Earliest-Latest)]

[shipper name]
[address line 1]
[address line 2 if any]

⏳ DEL: [delivery date + time (Earliest-Latest)]

[receiver name]
[address line 1]
[address line 2 if any]

_____

Rate: [amount] $
Mile: [miles] miles

⏰Late pick up = $250 fine❗️
⏰Late delivery = $250 fine❗️ important to keep the business
📝BOL/POD/Freight/Seal pictures MUST send otherwise $250 fine❗️
🚨 No update / $250 fine❗️

Your communication is really going smoothly❗️

If any field is missing, write "Not found" but **keep the format identical**.

---
RATE CONFIRMATION TEXT:
{text}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an expert logistics dispatcher assistant."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
    )

    return response.choices[0].message.content.strip()

# -------- TELEGRAM HANDLERS ----------
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.document.get_file()
    file_path = f"temp_{update.message.document.file_name}"
    await file.download_to_drive(file_path)

    try:
        text = extract_text_from_pdf(file_path)
        await update.message.reply_text("📄 PDF received. Extracting info... Please wait ⏳")

        result = extract_dispatch_info_with_ai(text)

        # Send back the result directly without code block formatting
        await update.message.reply_text(result)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

    finally:
        os.remove(file_path)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.photo[-1].get_file()
    file_path = f"temp_{file.file_id}.jpg"
    await file.download_to_drive(file_path)

    try:
        await update.message.reply_text("📷 Image received. OCR extraction not implemented yet. Send as PDF for full parsing.")
    finally:
        os.remove(file_path)
        
# -------- RUN FLASK IN BACKGROUND THREAD ----------
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port, use_reloader=False)

# -------- MAIN ENTRY POINT ----------
if __name__ == "__main__":
    # Run Flask in a separate thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("🌐 Flask server started on port 10000")
    
    # Run bot in MAIN thread
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("🤖 Bot is running... Send /start or a PDF in Telegram.")
    app.run_polling()