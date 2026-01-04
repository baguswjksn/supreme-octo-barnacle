import sqlite3
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from collections import defaultdict
import calendar
from dotenv import load_dotenv
import os
import requests

# ================== CONFIG ==================


load_dotenv()  # load variables from .env

TELEGRAM_BOT_TOKEN = os.getenv("API_TOKEN")
TELEGRAM_USER_ID = os.getenv("ALLOWED_USER_ID")
DB_PATH = os.getenv("DB_PATH")

IMAGE_PATH = "expense_compare_month.png"

# ================== DATE LOGIC ==================

today = datetime.now()

# This month
this_year = today.year
this_month = today.month
this_day = today.day

# Last month
if this_month == 1:
    last_month = 12
    last_year = this_year - 1
else:
    last_month = this_month - 1
    last_year = this_year

last_month_days = calendar.monthrange(last_year, last_month)[1]
compare_days = min(this_day, last_month_days)

def month_range(year, month, days):
    start = datetime(year, month, 1)
    end = datetime(year, month, days, 23, 59, 59)
    return start, end

this_start, this_end = month_range(this_year, this_month, this_day)
last_start, last_end = month_range(last_year, last_month, compare_days)

# ================== DATABASE ==================

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

QUERY = """
SELECT
    CAST(strftime('%d', created_at) AS INTEGER) as day,
    category,
    SUM(amount)
FROM transactions
WHERE type = 'expense'
AND created_at >= ?
AND created_at <= ?
GROUP BY day, category
ORDER BY day
"""

cursor.execute(QUERY, (this_start, this_end))
this_rows = cursor.fetchall()

cursor.execute(QUERY, (last_start, last_end))
last_rows = cursor.fetchall()

conn.close()

if not this_rows and not last_rows:
    print("No data found")
    exit()

# ================== DATA PREP ==================

days = list(range(1, compare_days + 1))

def build_category_data(rows):
    data = defaultdict(lambda: [0] * len(days))
    day_idx = {d: i for i, d in enumerate(days)}

    for day, category, total in rows:
        if day in day_idx:
            data[category][day_idx[day]] = total
    return data

this_data = build_category_data(this_rows)
last_data = build_category_data(last_rows)

# Union categories
categories = sorted(
    set(this_data.keys()) | set(last_data.keys()),
    key=lambda c: sum(this_data.get(c, [])) + sum(last_data.get(c, [])),
    reverse=True
)

this_values = [this_data.get(cat, [0]*len(days)) for cat in categories]
last_values = [last_data.get(cat, [0]*len(days)) for cat in categories]

# ================== COLORS ==================

pastel_colors = [
    "#FFB3BA", "#FFDFBA", "#FFFFBA",
    "#BAFFC9", "#BAE1FF", "#D7BAFF",
    "#FFC6E5", "#C6FFF3"
]

colors = pastel_colors[:len(categories)]

# ================== PLOT ==================

fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)

# Last Month
axes[0].stackplot(
    days,
    last_values,
    labels=categories,
    colors=colors,
    alpha=0.9
)
axes[0].set_title("Last Month")
axes[0].set_xlabel("Day")
axes[0].set_ylabel("Amount")

# This Month
axes[1].stackplot(
    days,
    this_values,
    labels=categories,
    colors=colors,
    alpha=0.9
)
axes[1].set_title("This Month")
axes[1].set_xlabel("Day")

# Legend (shared)
handles, labels = axes[1].get_legend_handles_labels()
fig.legend(
    handles,
    labels,
    loc="upper center",
    ncol=min(4, len(categories)),
    frameon=False
)

plt.tight_layout(rect=[0, 0, 1, 0.88])
plt.savefig(IMAGE_PATH, dpi=200, bbox_inches="tight")
plt.close()

# ================== SEND TO TELEGRAM ==================
url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

with open(IMAGE_PATH, "rb") as photo:
    res = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_USER_ID,
            "caption": "📊 Expenses in the Last 7 Days"
        },
        files={"photo": photo}
    )

if res.status_code == 200:
    print("Successfully sent chart to Telegram")
else:
    print("Failed to send chart to Telegram:", res.text)
