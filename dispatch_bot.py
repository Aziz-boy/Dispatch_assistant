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
from PIL import Image
import pytesseract
import fitz  # PyMuPDF

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

# -------- OCR FOR IMAGES ----------
def extract_text_from_image(image_path):
    """Extract text from image using OCR"""
    try:
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image, lang='eng')
        return text.strip()
    except Exception as e:
        print(f"OCR Error: {e}")
        return ""

# -------- ENHANCED PDF TEXT EXTRACTION WITH OCR ----------
def extract_text_from_pdf(pdf_path):
    """
    Extract text from PDF with automatic OCR fallback for image-based PDFs
    """
    text = ""
    
    # First, try pdfplumber for text-based PDFs
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text
    except Exception as e:
        print(f"PDFPlumber error: {e}")
    
    # If very little text was extracted, it's likely an image-based PDF
    # Use OCR with PyMuPDF
    if len(text.strip()) < 100:
        print("⚠️ Minimal text detected. Using OCR...")
        text = extract_text_from_pdf_with_ocr(pdf_path)
    
    return text.strip()

def extract_text_from_pdf_with_ocr(pdf_path):
    """
    Extract text from image-based PDF using OCR
    """
    text = ""
    try:
        doc = fitz.open(pdf_path)
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Convert PDF page to image
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better quality
            img_data = pix.tobytes("png")
            
            # Save temporarily and OCR
            temp_img = f"temp_page_{page_num}.png"
            with open(temp_img, "wb") as f:
                f.write(img_data)
            
            # Extract text using OCR
            page_text = extract_text_from_image(temp_img)
            text += f"\n--- Page {page_num + 1} ---\n{page_text}\n"
            
            # Clean up temp image
            os.remove(temp_img)
        
        doc.close()
    except Exception as e:
        print(f"OCR PDF Error: {e}")
    
    return text

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
        await update.message.reply_text("📄 PDF received. Extracting info... Please wait ⏳")
        
        text = extract_text_from_pdf(file_path)
        
        if not text or len(text) < 50:
            await update.message.reply_text("⚠️ Could not extract text from PDF. Please ensure the file is readable.")
            return

        result = extract_dispatch_info_with_ai(text)

        # Send back the result
        await update.message.reply_text(result)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.photo[-1].get_file()
    file_path = f"temp_{file.file_id}.jpg"
    await file.download_to_drive(file_path)

    try:
        await update.message.reply_text("📷 Image received. Extracting text with OCR... Please wait ⏳")
        
        # Extract text from image using OCR
        text = extract_text_from_image(file_path)
        
        if not text or len(text) < 50:
            await update.message.reply_text("⚠️ Could not extract text from image. Please ensure the image is clear and readable.")
            return
        
        # Process with AI
        result = extract_dispatch_info_with_ai(text)
        await update.message.reply_text(result)
        
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error processing image: {e}")
    finally:
        if os.path.exists(file_path):
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