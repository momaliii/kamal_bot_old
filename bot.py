import sqlite3
import csv
import re
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode
from time import sleep

# Constants
MAX_MSG_LEN = 4096
DB_PATH = 'transactions.db'
RATE_LIMIT_DELAY = 0.05  # 50ms delay between each broadcast message

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database context manager for safer handling
class Database:
    def __enter__(self):
        try:
            self.conn = sqlite3.connect(DB_PATH)
            self.cursor = self.conn.cursor()
        except sqlite3.Error as e:
            logger.error(f"Error connecting to database: {e}")
        return self.cursor

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.conn.commit()
            self.conn.close()
        except sqlite3.Error as e:
            logger.error(f"Error closing database connection: {e}")

# Utility functions
def add_user(chat_id):
    with Database() as db:
        db.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))

def save_transaction(chat_id, amount, category="general"):
    date = datetime.now().strftime("%Y-%m-%d")
    with Database() as db:
        db.execute('INSERT INTO transactions (amount, date, category, chat_id) VALUES (?, ?, ?, ?)', 
                   (amount, date, category, chat_id))

def get_total(chat_id):
    with Database() as db:
        db.execute('SELECT SUM(amount) FROM transactions WHERE chat_id = ?', (chat_id,))
        return db.fetchone()[0] or 0

# Message handlers
async def handle_message(update: Update, context):
    text = update.message.text.strip()
    chat_id = update.message.chat.id
    add_user(chat_id)

    match = re.match(r'^[+-]?\d+(\.\d+)?$', text)
    if match:
        amount = float(match.group())
        save_transaction(chat_id, amount)
        total = get_total(chat_id)
        await update.message.reply_text(f"Amount added: {amount}\nTotal: {total}")
    else:
        await update.message.reply_text("Please send a valid number starting with + or -.")

# Broadcast with Markdown support and rate limiting
async def broadcast_message(update: Update, context):
    if not context.args:
        await update.message.reply_text("Please provide a message to broadcast.")
        return

    message = " ".join(context.args).replace("\\n", "\n")
    with Database() as db:
        users = db.execute("SELECT chat_id FROM users").fetchall()

    for user in users:
        for chunk in [message[i:i + MAX_MSG_LEN] for i in range(0, len(message), MAX_MSG_LEN)]:
            try:
                await context.bot.send_message(chat_id=user[0], text=chunk, parse_mode=ParseMode.MARKDOWN)
                sleep(RATE_LIMIT_DELAY)  # Adding rate limiting to avoid hitting API limits
            except Exception as e:
                logger.error(f"Error sending message to {user[0]}: {e}")

    await update.message.reply_text("Broadcast sent.")

# Export transactions as CSV
async def export_transactions(update: Update, context):
    user_id = update.message.chat.id
    with Database() as db:
        transactions = db.execute("SELECT * FROM transactions WHERE chat_id = ?", (user_id,)).fetchall()

    file_name = f'transactions_{user_id}.csv'
    with open(file_name, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Amount", "Date", "Category", "Chat ID"])
        writer.writerows(transactions)

    await context.bot.send_document(chat_id=user_id, document=open(file_name, 'rb'))

# Lazy loading of matplotlib for graph generation
async def send_graph(update: Update, context):
    user_id = update.message.chat.id
    with Database() as db:
        transactions = db.execute("SELECT date, SUM(amount) FROM transactions WHERE chat_id = ? GROUP BY date", 
                                  (user_id,)).fetchall()

    if transactions:
        dates, totals = zip(*transactions)
        import matplotlib.pyplot as plt  # Lazy import to optimize memory
        plt.plot(dates, totals)
        plt.title('Transaction History')
        plt.xlabel('Date')
        plt.ylabel('Total Amount')
        plt.savefig('transaction_graph.png')
        await context.bot.send_photo(chat_id=user_id, photo=open('transaction_graph.png', 'rb'))
    else:
        await update.message.reply_text("No transactions found.")

# Reset user transactions
async def reset_transactions(update: Update, context):
    user_id = update.message.chat.id
    with Database() as db:
        db.execute('DELETE FROM transactions WHERE chat_id = ?', (user_id,))
    await update.message.reply_text("All your transactions have been reset.")

# Help command listing available commands
async def helpme(update: Update, context):
    help_text = (
        "/start - Start the bot\n"
        "/broadcast [message] - Send a broadcast message (admin only) use** to make word bold. use \ n to make line\n"
        "/export - Export your transactions as CSV\n"
        "/graph - Get a graphical report of your transactions\n"
        "/reset - Reset your transactions\n"
        "/helpme - Show this help message"
    )
    await update.message.reply_text(help_text)

# Bot initialization
def main():
    application = Application.builder().token('7403256647:AAFVG8PsBEkGSbOCvy_PVDE8zLz4PXKBb0w').build()

    # Command handlers mapped in a dictionary
    commands = {
        "start": helpme,
        "broadcast": broadcast_message,
        "export": export_transactions,
        "graph": send_graph,
        "reset": reset_transactions,
        "helpme": helpme,
    }

    for cmd, handler in commands.items():
        application.add_handler(CommandHandler(cmd, handler))

    # Message handler for incoming text
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduler for daily reports
    scheduler = AsyncIOScheduler()
    scheduler.start()

    # Start bot
    application.run_polling()

if __name__ == '__main__':
    main()
