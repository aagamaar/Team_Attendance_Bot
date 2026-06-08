import os
from flask import Flask
import threading

# Create Flask app for health checks
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)

# Start Flask in background thread
threading.Thread(target=run_flask, daemon=True).start()
import sqlite3
from datetime import datetime, date
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------- DATABASE SETUP ----------
conn = sqlite3.connect('attendance.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS employees (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    leave_balance INTEGER DEFAULT 4
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    date TEXT,
    status TEXT,
    UNIQUE(user_id, date)
)
''')
conn.commit()

def is_weekend(dt):
    return dt.weekday() >= 5

def reset_balance_if_needed(user_id):
    today = date.today()
    if today.day == 1:
        cursor.execute("UPDATE employees SET leave_balance = 4 WHERE user_id = ?", (user_id,))
        conn.commit()

# ---------- BOT COMMANDS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    
    cursor.execute("INSERT OR IGNORE INTO employees (user_id, name, leave_balance) VALUES (?, ?, 4)", (user_id, name))
    conn.commit()
    
    await update.message.reply_text(
        f"✅ Welcome {name}!\n\n"
        "Commands:\n"
        "/present - Mark present (Mon-Fri only)\n"
        "/leave - Take leave (uses 1 of 4 monthly leaves)\n"
        "/balance - Check remaining leaves\n"
        "/admin_today - View today's attendance\n"
        "/admin_month - View monthly report"
    )

async def present(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today_str = date.today().isoformat()
    today_obj = date.today()
    
    if is_weekend(today_obj):
        await update.message.reply_text("🏖️ Weekend! No need to mark attendance.")
        return
    
    cursor.execute("SELECT * FROM attendance WHERE user_id = ? AND date = ?", (user_id, today_str))
    if cursor.fetchone():
        await update.message.reply_text("⚠️ Already marked today!")
        return
    
    cursor.execute("INSERT INTO attendance (user_id, date, status) VALUES (?, ?, 'present')", (user_id, today_str))
    conn.commit()
    await update.message.reply_text("✅ Marked present! Have a great day.")

async def leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today_str = date.today().isoformat()
    today_obj = date.today()
    
    if is_weekend(today_obj):
        await update.message.reply_text("🏖️ Weekends don't count as leave!")
        return
    
    reset_balance_if_needed(user_id)
    
    cursor.execute("SELECT leave_balance FROM employees WHERE user_id = ?", (user_id,))
    balance = cursor.fetchone()[0]
    
    if balance <= 0:
        await update.message.reply_text("❌ No leaves left this month!")
        return
    
    cursor.execute("SELECT * FROM attendance WHERE user_id = ? AND date = ?", (user_id, today_str))
    if cursor.fetchone():
        await update.message.reply_text("⚠️ Already marked today. Can't change to leave.")
        return
    
    cursor.execute("INSERT INTO attendance (user_id, date, status) VALUES (?, ?, 'leave')", (user_id, today_str))
    new_balance = balance - 1
    cursor.execute("UPDATE employees SET leave_balance = ? WHERE user_id = ?", (new_balance, user_id))
    conn.commit()
    
    await update.message.reply_text(f"✅ Marked on leave. {new_balance} leaves remaining.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_balance_if_needed(user_id)
    
    cursor.execute("SELECT leave_balance, name FROM employees WHERE user_id = ?", (user_id,))
    balance, name = cursor.fetchone()
    await update.message.reply_text(f"📊 {name}, you have {balance} leave(s) remaining this month.\n(4 total, no carry-over)")

async def admin_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_str = date.today().isoformat()
    
    cursor.execute("SELECT user_id, name FROM employees")
    all_employees = cursor.fetchall()
    
    report = "📋 *Today's Attendance*\n\n"
    for uid, name in all_employees:
        cursor.execute("SELECT status FROM attendance WHERE user_id = ? AND date = ?", (uid, today_str))
        result = cursor.fetchone()
        status = result[0] if result else "not marked"
        emoji = "✅" if status == "present" else "📤" if status == "leave" else "❓"
        report += f"{emoji} {name}: {status}\n"
    
    await update.message.reply_text(report, parse_mode='Markdown')

async def admin_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_month = date.today().strftime("%Y-%m")
    
    cursor.execute("SELECT user_id, name FROM employees")
    all_employees = cursor.fetchall()
    
    report = f"📆 *Monthly Report - {current_month}*\n\n"
    for uid, name in all_employees:
        cursor.execute('''
            SELECT status, COUNT(*) FROM attendance 
            WHERE user_id = ? AND strftime('%Y-%m', date) = ? 
            GROUP BY status
        ''', (uid, current_month))
        results = cursor.fetchall()
        
        present_count = sum(r[1] for r in results if r[0] == 'present')
        leave_count = sum(r[1] for r in results if r[0] == 'leave')
        
        cursor.execute("SELECT leave_balance FROM employees WHERE user_id = ?", (uid,))
        balance = cursor.fetchone()[0]
        
        report += f"👤 {name}\n   ✅ Present: {present_count} | 📤 Leave used: {leave_count} | 🎫 Balance: {balance}\n\n"
    
    await update.message.reply_text(report, parse_mode='Markdown')
import csv

async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export monthly report to CSV - admin can copy to Google Sheets"""
    current_month = date.today().strftime("%Y-%m")
    
    cursor.execute("SELECT user_id, name FROM employees")
    all_employees = cursor.fetchall()
    
    # Create CSV in memory
    csv_data = [["Employee", "Present Days", "Leave Days", "Balance", "Month"]]
    
    for uid, name in all_employees:
        cursor.execute('''
            SELECT status, COUNT(*) FROM attendance 
            WHERE user_id = ? AND strftime('%Y-%m', date) = ? 
            GROUP BY status
        ''', (uid, current_month))
        results = cursor.fetchall()
        
        present_count = sum(r[1] for r in results if r[0] == 'present')
        leave_count = sum(r[1] for r in results if r[0] == 'leave')
        
        cursor.execute("SELECT leave_balance FROM employees WHERE user_id = ?", (uid,))
        balance = cursor.fetchone()[0]
        
        csv_data.append([name, present_count, leave_count, balance, current_month])
    
    # Create and send CSV file
    with open('attendance_report.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(csv_data)
    
    with open('attendance_report.csv', 'rb') as f:
        await update.message.reply_document(
            document=f, 
            filename=f'attendance_{current_month}.csv',
            caption="📊 Monthly report - Open in Google Sheets!"
        )
    
    await update.message.reply_text("✅ CSV exported! Just drag this file into Google Sheets.")
# ---------- MAIN ----------
def main():
    # IMPORTANT: Replace with your actual bot token from BotFather
    TOKEN = os.environ.get("TELEGRAM_TOKEN", "8889524999:AAE-0YXg3XGti_Vm-7iORMTl4m3RcuNM7_U")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("present", present))
    app.add_handler(CommandHandler("leave", leave))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("admin_today", admin_today))
    app.add_handler(CommandHandler("admin_month", admin_month))
    app.add_handler(CommandHandler("export", export_csv))
    print("🤖 Bot is running! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()