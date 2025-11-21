import 'dotenv/config';
import TelegramBot from 'node-telegram-bot-api';
import OpenAI from 'openai';
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import pdfParse from 'pdf-parse';
import Tesseract from 'tesseract.js';
import pdf2img from 'pdf2img';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;

// Initialize OpenAI client
const openai = new OpenAI({ apiKey: OPENAI_API_KEY });

// Initialize Telegram Bot
const bot = new TelegramBot(TELEGRAM_BOT_TOKEN, { polling: true });

// Express app for health check
const app = express();

app.get('/', (req, res) => {
  res.status(200).send('🤖 Dispatch Bot is running!');
});

app.get('/health', (req, res) => {
  res.status(200).send('OK');
});

// -------- START COMMAND ----------
bot.onText(/\/start/, async (msg) => {
  const chatId = msg.chat.id;
  const firstName = msg.from.first_name;
  
  const welcomeMessage = `👋 Hey ${firstName}!

I'm your **Dispatch Assistant Bot** 🚛

Send me a **Rate Confirmation PDF** or an **image** of one, and I'll extract key info for you — Load#, REF#, PU/DEL, Rate, Miles, and Notes.

📎 Just upload your file and I'll handle the rest!

Need help? Type /help`;

  await bot.sendMessage(chatId, welcomeMessage, { parse_mode: 'Markdown' });
});

// -------- OCR FOR IMAGES ----------
async function extractTextFromImage(imagePath) {
  try {
    console.log(`Starting OCR on: ${imagePath}`);
    const { data: { text } } = await Tesseract.recognize(imagePath, 'eng', {
      logger: info => console.log(info)
    });
    console.log(`OCR completed. Text length: ${text.length}`);
    return text.trim();
  } catch (error) {
    console.error('OCR Error:', error);
    return '';
  }
}

// -------- PDF TEXT EXTRACTION ----------
async function extractTextFromPDF(pdfPath) {
  try {
    console.log('Attempting to extract text from PDF...');
    const dataBuffer = fs.readFileSync(pdfPath);
    const data = await pdfParse(dataBuffer);
    const text = data.text.trim();
    
    console.log(`Extracted text length: ${text.length}`);
    
    // If very little text was extracted, it's likely an image-based PDF
    if (text.length < 100) {
      console.log('⚠️ Minimal text detected. Using OCR...');
      return await extractTextFromPDFWithOCR(pdfPath);
    }
    
    return text;
  } catch (error) {
    console.error('PDF Parse Error:', error);
    console.log('Falling back to OCR...');
    return await extractTextFromPDFWithOCR(pdfPath);
  }
}

// -------- PDF OCR EXTRACTION ----------
async function extractTextFromPDFWithOCR(pdfPath) {
  try {
    console.log('Converting PDF pages to PNG...');
    
    // Create temp directory if it doesn't exist
    if (!fs.existsSync('./temp')) {
      fs.mkdirSync('./temp');
    }
    
    // Convert PDF to images using pdf2img
    const result = await new Promise((resolve, reject) => {
      pdf2img.setOptions({
        type: 'png',
        size: 2000,
        density: 300,
        outputdir: './temp',
        outputname: 'page',
        page: null
      });
      
      pdf2img.convert(pdfPath, (err, info) => {
        if (err) reject(err);
        else resolve(info);
      });
    });
    
    console.log(`Converted ${result.length} pages to PNG`);
    
    let fullText = '';
    
    // Process each page with OCR
    for (let i = 0; i < result.length; i++) {
      try {
        const imagePath = result[i].path;
        console.log(`Processing page ${i + 1} with OCR: ${imagePath}`);
        
        // Extract text using OCR
        const pageText = await extractTextFromImage(imagePath);
        fullText += `\n--- Page ${i + 1} ---\n${pageText}\n`;
        
        // Clean up temp image
        if (fs.existsSync(imagePath)) {
          fs.unlinkSync(imagePath);
        }
      } catch (err) {
        console.error(`Error processing page ${i + 1}:`, err);
      }
    }
    
    console.log(`Total OCR text length: ${fullText.length}`);
    return fullText;
  } catch (error) {
    console.error('OCR PDF Error:', error);
    return '';
  }
}

// -------- OPENAI DISPATCH EXTRACTION ----------
async function extractDispatchInfoWithAI(text) {
  console.log('=== SENDING TO AI ===');
  console.log('Text length:', text.length);
  console.log('First 500 characters of text:');
  console.log(text.substring(0, 500));
  console.log('=====================');
  
  const prompt = `You are an expert logistics dispatcher bot.

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
${text}`;

  console.log('Calling OpenAI API...');
  
  const response = await openai.chat.completions.create({
    model: 'gpt-4o-mini',
    messages: [
      { role: 'system', content: 'You are an expert logistics dispatcher assistant.' },
      { role: 'user', content: prompt }
    ],
    temperature: 0.2,
  });

  const result = response.choices[0].message.content.trim();
  
  console.log('=== AI RESPONSE ===');
  console.log(result);
  console.log('===================');

  return result;
}

// -------- TELEGRAM HANDLERS ----------
bot.on('document', async (msg) => {
  const chatId = msg.chat.id;
  const document = msg.document;
  
  // Check if it's a PDF
  if (!document.mime_type || !document.mime_type.includes('pdf')) {
    return;
  }
  
  const fileId = document.file_id;
  const fileName = document.file_name || `temp_${fileId}.pdf`;
  const filePath = path.join(__dirname, fileName);
  
  try {
    await bot.sendMessage(chatId, '📄 PDF received. Extracting info... Please wait ⏳');
    
    // Download the file
    const file = await bot.getFile(fileId);
    const fileUrl = `https://api.telegram.org/file/bot${TELEGRAM_BOT_TOKEN}/${file.file_path}`;
    
    console.log(`Downloading file: ${fileUrl}`);
    
    // Download file
    const https = await import('https');
    const fileStream = fs.createWriteStream(filePath);
    
    await new Promise((resolve, reject) => {
      https.get(fileUrl, (response) => {
        response.pipe(fileStream);
        fileStream.on('finish', () => {
          fileStream.close();
          resolve();
        });
      }).on('error', reject);
    });
    
    console.log('File downloaded successfully');
    
    // Extract text
    const text = await extractTextFromPDF(filePath);
    
    if (!text || text.length < 50) {
      await bot.sendMessage(chatId, '⚠️ Could not extract text from PDF. Please ensure the file is readable.');
      return;
    }
    
    console.log('Text extracted, sending to AI...');
    
    // Process with AI
    const result = await extractDispatchInfoWithAI(text);
    await bot.sendMessage(chatId, result);
    
  } catch (error) {
    console.error('Error:', error);
    await bot.sendMessage(chatId, `⚠️ Error: ${error.message}`);
  } finally {
    // Clean up
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  }
});

bot.on('photo', async (msg) => {
  const chatId = msg.chat.id;
  const photo = msg.photo[msg.photo.length - 1]; // Get highest resolution
  const fileId = photo.file_id;
  const filePath = path.join(__dirname, `temp_${fileId}.jpg`);
  
  try {
    await bot.sendMessage(chatId, '📷 Image received. Extracting text with OCR... Please wait ⏳');
    
    // Download the file
    const file = await bot.getFile(fileId);
    const fileUrl = `https://api.telegram.org/file/bot${TELEGRAM_BOT_TOKEN}/${file.file_path}`;
    
    console.log(`Downloading image: ${fileUrl}`);
    
    // Download file
    const https = await import('https');
    const fileStream = fs.createWriteStream(filePath);
    
    await new Promise((resolve, reject) => {
      https.get(fileUrl, (response) => {
        response.pipe(fileStream);
        fileStream.on('finish', () => {
          fileStream.close();
          resolve();
        });
      }).on('error', reject);
    });
    
    console.log('Image downloaded, starting OCR...');
    
    // Extract text using OCR
    const text = await extractTextFromImage(filePath);
    
    if (!text || text.length < 50) {
      await bot.sendMessage(chatId, '⚠️ Could not extract text from image. Please ensure the image is clear and readable.');
      return;
    }
    
    console.log('OCR completed, sending to AI...');
    
    // Process with AI
    const result = await extractDispatchInfoWithAI(text);
    await bot.sendMessage(chatId, result);
    
  } catch (error) {
    console.error('Error processing image:', error);
    await bot.sendMessage(chatId, `⚠️ Error processing image: ${error.message}`);
  } finally {
    // Clean up
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  }
});

// Create temp directory if it doesn't exist
if (!fs.existsSync('./temp')) {
  fs.mkdirSync('./temp');
}

// -------- START SERVER ----------
const PORT = process.env.PORT || 10000;

app.listen(PORT, '0.0.0.0', () => {
  console.log(`🌐 Server started on port ${PORT}`);
  console.log('🤖 Bot is running... Send /start or a PDF in Telegram.');
});