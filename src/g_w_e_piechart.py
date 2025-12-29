import sqlite3
from dotenv import load_dotenv
import matplotlib.pyplot as plt
from matplotlib.table import Table
from datetime import datetime, timedelta
import numpy as np
import os
import requests

# ================== CONFIG ==================

load_dotenv()  # load variables from .env

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")
DB_PATH = os.getenv("DB_PATH")

IMAGE_PATH = "expense_last_7_days.png"

# ================== DATABASE ==================
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

start_dt = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
end_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

query = """
SELECT category, SUM(amount) as total
FROM transactions
WHERE type = 'expense'
AND created_at >= ?
AND created_at < ?
GROUP BY category
ORDER BY total DESC
"""
cursor.execute(query, (start_dt, end_dt))
data = cursor.fetchall()
conn.close()

if not data:
    print("No expense data for the last 7 days")
    exit()

categories = [row[0] for row in data]
totals = np.array([row[1] for row in data])
grand_total = totals.sum()
percentages = (totals / grand_total) * 100

# ================== COLORS (PASTEL) ==================
pastel_colors = [
    "#FFB3BA", "#FFDFBA", "#FFFFBA",
    "#BAFFC9", "#BAE1FF", "#D7BAFF",
    "#FFC6E5", "#C6FFF3"
]

# ================== FIGURE ==================
fig, (ax_pie, ax_table) = plt.subplots(
    1, 2, figsize=(10, 5),
    gridspec_kw={"width_ratios": [1, 1]}
)

# ================== PIE CHART (DONUT) ==================
wedges, _ = ax_pie.pie(
    totals,
    colors=pastel_colors[:len(totals)],
    startangle=90,
    wedgeprops=dict(width=0.4)
)

# --- Center text ---
ax_pie.text(
    0, 0.05,
    f"{grand_total:,.0f}",
    ha="center", va="center",
    fontsize=18, fontweight="bold", color="black"
)
ax_pie.text(
    0, -0.15,
    "Total",
    ha="center", va="center",
    fontsize=10, color="gray"
)

ax_pie.set_title("Expenses in the Last 7 Days", fontsize=12)
ax_pie.axis("equal")

# ================== TABLE ==================
ax_table.axis("off")

table_data = [
    ["■", c, f"{t:,.0f}", f"{p:.1f}%"]
    for c, t, p in zip(categories, totals, percentages)
]

col_labels = ["", "Category", "Total", "%"]

table = ax_table.table(
    cellText=table_data,
    colLabels=col_labels,
    loc="center",
    cellLoc="left",
    colLoc="left"
)

table.auto_set_font_size(False)
table.set_fontsize(11)
table.scale(1.5, 1.4)  # wider table

for (row, col), cell in table.get_celld().items():
    if row == 0:
        cell.set_text_props(weight="bold")
        cell.set_facecolor("#F2F2F2")
    if col == 0 and row > 0:
        cell.set_text_props(color=pastel_colors[row-1], fontsize=16, ha="center")
    if col in (2, 3):
        cell.set_text_props(ha="right")
    cell.set_edgecolor("white")
    if col == 0:
        cell.set_width(0.05)
    elif col == 1:
        cell.set_width(0.4)
    elif col == 2:
        cell.set_width(0.25)
    elif col == 3:
        cell.set_width(0.2)

# ================== SAVE PNG ==================
plt.tight_layout()
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
