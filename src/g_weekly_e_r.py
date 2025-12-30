import sqlite3
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import numpy as np
import requests
import os
from dotenv import load_dotenv

# Load .env values
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

# Apply dark theme
plt.style.use('dark_background')

# Connect to the SQLite database
DB_PATH = os.getenv("DB_PATH")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Get the current date and the date 7 days ago
today = datetime.now()
start_date = today - timedelta(days=6)

# Query to get daily expense data
query = '''
    SELECT DATE(created_at) as date, SUM(amount) as total_expense
    FROM transactions
    WHERE type = 'expense' AND DATE(created_at) BETWEEN ? AND ?
    GROUP BY DATE(created_at)
    ORDER BY DATE(created_at)
'''

cursor.execute(query, (start_date.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')))
data = cursor.fetchall()
conn.close()

# Prepare data for chart
dates = [start_date + timedelta(days=i) for i in range(7)]
date_labels = [date.strftime('%Y-%m-%d') for date in dates]
expenses = [0] * 7
expense_dict = {row[0]: row[1] for row in data}

for i, date_label in enumerate(date_labels):
    expenses[i] = expense_dict.get(date_label, 0)

# Check for threshold
threshold = 30000
exceeded_threshold_days = [(date_labels[i], expense) for i, expense in enumerate(expenses) if expense > threshold]

# Create the chart
plt.figure(figsize=(10, 5))
plt.plot(date_labels, expenses, marker='o', color='cyan', linewidth=2)
plt.axhline(y=threshold, color='red', linestyle='--', linewidth=1.5, label=f'Threshold ({threshold})')
plt.xticks(rotation=45, color='white')
plt.yticks(color='white')
plt.title('Weekly Expense Report (Last 7 Days)', color='white')
plt.xlabel('Date', color='white')
plt.ylabel('Expense Amount (in currency)', color='white')
plt.legend(facecolor='black', edgecolor='white')
plt.grid(True, linestyle='--', alpha=0.5, color='gray')

for i, expense in enumerate(expenses):
    plt.text(i, expense + 0.5, f'{expense:.2f}', ha='center', va='bottom', fontsize=9, color='white')

output_path = "weekly_expense_report.png"
plt.tight_layout()
plt.savefig(output_path, facecolor='black')
plt.close()

# Send image via Telegram
with open(output_path, 'rb') as photo:
    send_url = f"https://api.telegram.org/bot{API_TOKEN}/sendPhoto"
    response = requests.post(send_url, data={
        'chat_id': ALLOWED_USER_ID,
        'caption': "📊 Your weekly expense report (last 7 days)"
    }, files={'photo': photo})

# Delete the image
if os.path.exists(output_path):
    os.remove(output_path)
