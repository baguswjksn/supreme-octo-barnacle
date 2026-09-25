#!/usr/bin/env python3
"""
Command di Telegram:
  /start            - lihat cara pakai
  /cancel           - batalin input yang lagi jalan
  /get_last_report  - generate laporan HTML dari semua data & kirim sebagai file

Insert transaksi, kirim pesan:
  "Nasi Bali, 14000"        (format: deskripsi, jumlah)
  "Nasi Bali, 2, 14000"     (format: deskripsi, quantity, jumlah)
  -> pilih tipe & kategori lewat inline button

Insert cepat (skip inline button), kirim pesan:
  "Nasi Bali, 14000, expense, Food"        (format: deskripsi, jumlah, tipe, kategori)
  "Nasi Bali, 2, 14000, expense, Food"     (format: deskripsi, quantity, jumlah, tipe, kategori)
  -> langsung tersimpan, ga perlu klik apa-apa
"""

import os
import sqlite3
import json
import secrets
import time
import math
import urllib.request
import urllib.error
from datetime import datetime
from html import escape as _escape


def escape(value):
    """escape() versi aman, handle None/angka tanpa crash."""
    return _escape(str(value) if value is not None else "")

TOKEN = ""
DB_PATH = ""
ALLOWED_USER_ID =  # string atau None

if not TOKEN:
    raise SystemExit("Set env var TELEGRAM_BOT_TOKEN dulu")

API_URL = f"https://api.telegram.org/bot{TOKEN}/"

TYPES = ["expense", "income"]

CATEGORIES = [
    "Active Income", "Bills", "Book", "Entertainment", "Food",
    "Healthcare", "House", "Internet", "Laundry", "Needs",
    "Outlier", "Rent", "Transportation", "Utilities", "Water",
]

PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948",
    "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC", "#86BCB6", "#D37295",
    "#FABFD2", "#B6992D", "#499894",
]

CREATED_FORMATS = (
    "%Y%m%d%H%M%S",
    "%Y-%m-%d %H:%M:%S.%fZ",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S",
)

# state per chat_id, nunggu input berikutnya
# { chat_id: {"description":..., "quantity":..., "amount":..., "type":...} }
pending = {}


# ---------- SQLite ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            created TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            is_outlier INTEGER NOT NULL DEFAULT 0,
            type TEXT NOT NULL,
            category TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def insert_transaction(description, quantity, amount, tx_type, category):
    tx_id = "r" + secrets.token_hex(7)
    created = datetime.now().strftime("%Y%m%d%H%M%S")
    is_outlier = 1 if category == "Outlier" else 0
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO transactions "
        "(id, description, created, quantity, amount, is_outlier, type, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tx_id, description, created, quantity, amount, is_outlier, tx_type, category),
    )
    conn.commit()
    conn.close()
    return tx_id, created


def parse_created(value):
    value = str(value).strip()
    for fmt in CREATED_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Tidak bisa parse tanggal: {value!r}")


def fetch_report_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM transactions ORDER BY created").fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "type": r["type"],
            "category": r["category"],
            "quantity": r["quantity"],
            "amount": r["amount"],
            "description": r["description"],
            "created_at": parse_created(r["created"]),
            "is_outlier": r["is_outlier"],
        })
    return result


# ---------- Telegram API helper ----------

def api_call(method, params):
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        API_URL + method,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=65) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print("HTTP error:", e.read())
        return None
    except urllib.error.URLError as e:
        print("URL error:", e)
        return None


def send_message(chat_id, text, reply_markup=None):
    params = {"chat_id": chat_id, "text": text}
    if reply_markup:
        params["reply_markup"] = reply_markup
    return api_call("sendMessage", params)


def answer_callback_query(callback_query_id, text=None):
    params = {"callback_query_id": callback_query_id}
    if text:
        params["text"] = text
    return api_call("answerCallbackQuery", params)


def edit_message_text(chat_id, message_id, text, reply_markup=None):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        params["reply_markup"] = reply_markup
    return api_call("editMessageText", params)


def send_document(chat_id, file_path, caption=None):
    """Upload file ke Telegram pakai multipart/form-data manual (stdlib doang)."""
    boundary = "----FinanceBotBoundary" + secrets.token_hex(8)
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        file_data = f.read()

    body = bytearray()

    def add_field(name, value):
        body.extend(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )

    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption)
    body.extend(
        f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f'Content-Type: text/html\r\n\r\n'.encode("utf-8")
    )
    body.extend(file_data)
    body.extend(f'\r\n--{boundary}--\r\n'.encode("utf-8"))

    req = urllib.request.Request(
        API_URL + "sendDocument",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print("HTTP error:", e.read())
        return None
    except urllib.error.URLError as e:
        print("URL error:", e)
        return None


# ---------- Keyboard builder ----------

def type_keyboard():
    buttons = [[{"text": t.capitalize(), "callback_data": f"type:{t}"}] for t in TYPES]
    return {"inline_keyboard": buttons}


def category_keyboard():
    buttons = []
    row = []
    for i, c in enumerate(CATEGORIES, 1):
        row.append({"text": c, "callback_data": f"cat:{c}"})
        if i % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return {"inline_keyboard": buttons}


# ---------- Parsing input ----------

def clean_amount(raw):
    raw = raw.strip().lower().replace("rp", "").replace(".", "").replace(",", "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def parse_input(text):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 2:
        description, amount_raw = parts
        quantity = 1
    elif len(parts) >= 3:
        description = ",".join(parts[:-2]).strip()
        quantity_raw, amount_raw = parts[-2], parts[-1]
        try:
            quantity = int(quantity_raw)
        except ValueError:
            return None
    else:
        tokens = text.strip().rsplit(" ", 1)
        if len(tokens) != 2:
            return None
        description, amount_raw = tokens
        quantity = 1

    amount = clean_amount(amount_raw)
    description = description.strip()
    if not description or amount is None or amount <= 0:
        return None
    return description, quantity, amount


def parse_quick_input(text):
    """
    Parse format insert cepat yang udah termasuk tipe & kategori, biar ga
    perlu inline button:
      "deskripsi, jumlah, tipe, kategori"
      "deskripsi, quantity, jumlah, tipe, kategori"
    Return None kalau bukan format ini (fallback ke parse_input biasa).
    """
    parts = [p.strip() for p in text.split(",")]
    if len(parts) not in (4, 5):
        return None

    type_raw = parts[-2].strip().lower()
    if type_raw not in TYPES:
        return None

    category_raw = parts[-1].strip().lower()
    category = next((c for c in CATEGORIES if c.lower() == category_raw), None)
    if category is None:
        return None

    if len(parts) == 4:
        description, amount_raw = parts[0], parts[1]
        quantity = 1
    else:
        description, quantity_raw, amount_raw = parts[0], parts[1], parts[2]
        try:
            quantity = int(quantity_raw)
        except ValueError:
            return None

    amount = clean_amount(amount_raw)
    description = description.strip()
    if not description or amount is None or amount <= 0:
        return None

    return description, quantity, amount, type_raw, category


# ---------- Chart / color helpers (pure SVG, no dependency) ----------

def color_scale(value, min_v, max_v, low_hex, mid_hex, high_hex):
    if max_v == min_v:
        ratio = 0.5
    else:
        ratio = (value - min_v) / (max_v - min_v)
    if ratio < 0.5:
        r = int(low_hex[0:2], 16) + (int(mid_hex[0:2], 16) - int(low_hex[0:2], 16)) * ratio * 2
        g = int(low_hex[2:4], 16) + (int(mid_hex[2:4], 16) - int(low_hex[2:4], 16)) * ratio * 2
        b = int(low_hex[4:6], 16) + (int(mid_hex[4:6], 16) - int(low_hex[4:6], 16)) * ratio * 2
    else:
        r = int(mid_hex[0:2], 16) + (int(high_hex[0:2], 16) - int(mid_hex[0:2], 16)) * (ratio - 0.5) * 2
        g = int(mid_hex[2:4], 16) + (int(high_hex[2:4], 16) - int(mid_hex[2:4], 16)) * (ratio - 0.5) * 2
        b = int(mid_hex[4:6], 16) + (int(high_hex[4:6], 16) - int(mid_hex[4:6], 16)) * (ratio - 0.5) * 2
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def pie_chart_svg(items):
    total = sum(v for _, v in items)
    if total == 0:
        return ""
    start_angle = 0
    parts = [f'<svg width="300" height="300" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300">']
    for idx, (label, value) in enumerate(items):
        angle = (value / total) * 360
        start_rad = math.radians(start_angle)
        end_rad = math.radians(start_angle + angle)
        large_arc = 1 if angle > 180 else 0
        x1 = 150 + 100 * math.cos(start_rad)
        y1 = 150 + 100 * math.sin(start_rad)
        x2 = 150 + 100 * math.cos(end_rad)
        y2 = 150 + 100 * math.sin(end_rad)
        color = PALETTE[idx % len(PALETTE)]
        parts.append(
            f'<path d="M 150 150 L {x1} {y1} A 100 100 0 {large_arc} 1 {x2} {y2} Z" '
            f'fill="{color}" stroke="white" stroke-width="2"/>'
        )
        mid_angle = math.radians(start_angle + angle / 2)
        label_x = 150 + 65 * math.cos(mid_angle)
        label_y = 150 + 65 * math.sin(mid_angle)
        parts.append(
            f'<text x="{label_x}" y="{label_y}" text-anchor="middle" '
            f'font-size="11" fill="white" font-weight="bold">{escape(f"{value:,.0f}")}</text>'
        )
        start_angle += angle
    parts.append("</svg>")
    legend = []
    for idx, (label, value) in enumerate(items):
        pct = (value / total * 100) if total > 0 else 0
        color = PALETTE[idx % len(PALETTE)]
        legend.append(
            f'<div class="legend-item"><span class="swatch" style="background:{color}"></span>'
            f'{escape(label)} &ndash; Rp{value:,.0f} ({pct:.1f}%)</div>'
        )
    return '<div class="chart-wrap"><div>' + "".join(parts) + '</div><div>' + "".join(legend) + "</div></div>"


def line_chart_svg(months, series):
    if not months:
        return ""
    width, height = 700, 300
    pad = 40
    plot_width = width - pad * 2
    plot_height = height - pad * 2
    max_val = max((max(vals) if vals else 0) for vals in series.values()) or 1
    x_pos = lambda i: pad + (i / (len(months) - 1 if len(months) > 1 else 1)) * plot_width
    y_pos = lambda v: pad + plot_height - (v / max_val * plot_height)
    parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#ccc" stroke-width="1"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="#ccc" stroke-width="1"/>',
    ]

    colors = {"Income": "#2E7D32", "Expense": "#C62828"}
    legend = []
    for label, values in series.items():
        color = colors.get(label, "#333")
        points = " ".join(f"{x_pos(i):.1f},{y_pos(v):.1f}" for i, v in enumerate(values))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        for i, v in enumerate(values):
            parts.append(f'<circle cx="{x_pos(i):.1f}" cy="{y_pos(v):.1f}" r="3" fill="{color}"/>')
        legend.append(f'<span class="swatch" style="background:{color}"></span>{escape(label)}')

    for i, m in enumerate(months):
        x, y = x_pos(i), height - pad + 14
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="10" text-anchor="end" '
            f'transform="rotate(-45 {x:.1f} {y:.1f})">{escape(m)}</text>'
        )

    parts.append("</svg>")
    legend_html = '<div class="legend">' + " &nbsp;&nbsp; ".join(legend) + "</div>"
    return '<div class="chart-wrap"><div>' + "".join(parts) + "</div>" + legend_html + "</div>"


STYLE = """
<style>
  * { box-sizing: border-box; }
  body { 
    font-family: Arial, Helvetica, sans-serif; 
    margin: 0; 
    padding: 16px; 
    color: #222; 
    background: #fff;
  }
  h1 { margin: 0 0 4px; font-size: 24px; }
  h2 { margin: 24px 0 12px; font-size: 18px; }
  .muted { color: #777; margin-top: 4px; font-size: 13px; }
  
  /* Table responsive */
  .table-wrapper { width: 100%; overflow-x: auto; margin: 12px 0 28px; }
  table { 
    border-collapse: collapse; 
    width: 100%; 
    font-size: 13px; 
    min-width: 800px;
  }
  th, td { 
    border: 1px solid #ccc; 
    padding: 8px 6px; 
    text-align: right;
  }
  th { 
    background: #ADD8E6; 
    font-weight: bold;
    position: sticky;
    top: 0;
  }
  td:first-child, th:first-child { text-align: left; }
  tr:nth-child(even) td { background: #fafafa; }
  tr:hover td { background: #f0f0f0 !important; }
  
  /* Ratio columns styling */
  .ratio-value { font-weight: 600; }
  .ratio-good { color: #2E7D32; }
  .ratio-warn { color: #F57C00; }
  .ratio-bad { color: #C62828; }
  
  /* Chart responsive */
  .chart-wrap { 
    display: flex; 
    align-items: center; 
    gap: 20px; 
    flex-wrap: wrap; 
    margin-bottom: 8px; 
    max-width: 100%; 
    overflow-x: auto; 
  }
  .chart-wrap svg { 
    max-width: 100%; 
    height: auto; 
  }
  .legend { 
    font-size: 12px; 
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
  }
  .legend-item { 
    margin-bottom: 4px; 
    white-space: nowrap;
  }
  .swatch { 
    display: inline-block; 
    width: 10px; 
    height: 10px; 
    margin-right: 6px; 
    border-radius: 2px; 
    vertical-align: middle;
  }
  
  /* Mobile responsif */
  @media (max-width: 768px) {
    body { padding: 12px; }
    h1 { font-size: 20px; }
    h2 { font-size: 16px; margin: 16px 0 8px; }
    table { font-size: 12px; min-width: 600px; }
    th, td { padding: 6px 4px; }
    .table-wrapper { margin: 8px 0 20px; }
    .chart-wrap { gap: 12px; }
    .legend { font-size: 11px; gap: 12px; }
  }
  
  @media (max-width: 480px) {
    body { padding: 8px; }
    h1 { font-size: 18px; }
    h2 { font-size: 14px; margin: 12px 0 6px; }
    table { font-size: 11px; min-width: 550px; }
    th, td { padding: 5px 3px; }
    .ratio-value { font-weight: 600; }
    .legend { font-size: 10px; gap: 8px; }
  }
</style>
"""


def generate_html_report(data, out_path):
    month_data = {}
    monthly_totals = {}
    for row in data:
        month_str = row["created_at"].strftime("%Y%m")
        month_data.setdefault(month_str, []).append(row)
        totals = monthly_totals.setdefault(
            month_str, {"income_clean": 0, "income_outlier": 0, "expense_clean": 0, "expense_outlier": 0}
        )
        if row["type"] == "expense":
            if row["is_outlier"]:
                totals["expense_outlier"] += row["amount"] or 0
            else:
                totals["expense_clean"] += row["amount"] or 0
        else:
            if row["is_outlier"]:
                totals["income_outlier"] += row["amount"] or 0
            else:
                totals["income_clean"] += row["amount"] or 0

    months_sorted = sorted(monthly_totals.keys())
    months_sorted_desc = sorted(monthly_totals.keys(), reverse=True)
    incomes = [
        monthly_totals[m]["income_clean"] + monthly_totals[m]["income_outlier"] for m in months_sorted
    ]
    expenses = [monthly_totals[m]["expense_clean"] + monthly_totals[m]["expense_outlier"] for m in months_sorted]

    # Cuma bulan sampe bulan berjalan yang di-color grade. Bulan yang
    # kecatet di masa depan (misal salah input tanggal) dibiarin polos.
    current_month = datetime.now().strftime("%Y%m")
    graded_months = [m for m in months_sorted if m <= current_month]

    income_clean_vals = [monthly_totals[m]["income_clean"] for m in graded_months] or [0]
    income_out_vals = [monthly_totals[m]["income_outlier"] for m in graded_months] or [0]
    exp_clean_vals = [monthly_totals[m]["expense_clean"] for m in graded_months] or [0]
    exp_out_vals = [monthly_totals[m]["expense_outlier"] for m in graded_months] or [0]
    income_min, income_max = min(income_clean_vals), max(income_clean_vals)
    income_out_min, income_out_max = min(income_out_vals), max(income_out_vals)
    exp_clean_min, exp_clean_max = min(exp_clean_vals), max(exp_clean_vals)
    exp_out_min, exp_out_max = min(exp_out_vals), max(exp_out_vals)

    html = ["<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Laporan Transaksi</title>", STYLE, "</head><body>"]
    html.append("<h1>Laporan Transaksi</h1>")
    html.append(f"<p class='muted'>Dibuat {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>")

    html.append("<h2>Summary</h2>")
    html.append(line_chart_svg(months_sorted, {"Income": incomes, "Expense": expenses}))
    html.append("<div class='table-wrapper'>")
    html.append(
        "<table><thead><tr><th>Month</th><th>Income</th><th>Income Outlier</th>"
        "<th>Expense (Outlier Excluded)</th><th>Expense Outlier</th><th>Expense Ratio</th><th>Saving %</th></tr></thead><tbody>"
    )
    for m in months_sorted_desc:
        t = monthly_totals[m]
        total_income = t["income_clean"] + t["income_outlier"]
        expense_clean = t["expense_clean"]
        
        # Calculate ratios
        if total_income > 0:
            expense_ratio = (expense_clean / total_income) * 100
            saving_pct = ((total_income - expense_clean) / total_income) * 100
        else:
            expense_ratio = 0
            saving_pct = 0
        
        # Determine color for ratios
        if expense_ratio <= 50:
            exp_ratio_class = "ratio-good"
        elif expense_ratio <= 75:
            exp_ratio_class = "ratio-warn"
        else:
            exp_ratio_class = "ratio-bad"
        
        if saving_pct >= 30:
            saving_class = "ratio-good"
        elif saving_pct >= 10:
            saving_class = "ratio-warn"
        else:
            saving_class = "ratio-bad"
        
        if m <= current_month:
            income_color = color_scale(t["income_clean"], income_min, income_max, "F8696B", "FFEB84", "63BE7B")
            income_out_color = color_scale(
                t["income_outlier"], income_out_min, income_out_max, "F8696B", "FFEB84", "63BE7B"
            )
            exp_clean_color = color_scale(t["expense_clean"], exp_clean_min, exp_clean_max, "63BE7B", "FFEB84", "F8696B")
            exp_out_color = color_scale(t["expense_outlier"], exp_out_min, exp_out_max, "63BE7B", "FFEB84", "F8696B")
            income_style = f" style='background:{income_color}'"
            income_out_style = f" style='background:{income_out_color}'"
            exp_clean_style = f" style='background:{exp_clean_color}'"
            exp_out_style = f" style='background:{exp_out_color}'"
        else:
            # bulan di masa depan: ga di-grade
            income_style = income_out_style = exp_clean_style = exp_out_style = ""
        html.append(
            "<tr>"
            f"<td>{escape(m)}</td>"
            f"<td{income_style}>Rp{t['income_clean']:,.0f}</td>"
            f"<td{income_out_style}>Rp{t['income_outlier']:,.0f}</td>"
            f"<td{exp_clean_style}>Rp{t['expense_clean']:,.0f}</td>"
            f"<td{exp_out_style}>Rp{t['expense_outlier']:,.0f}</td>"
            f"<td class='ratio-value {exp_ratio_class}'>{expense_ratio:.1f}%</td>"
            f"<td class='ratio-value {saving_class}'>{saving_pct:.1f}%</td>"
            "</tr>"
        )
    html.append("</tbody></table>")
    html.append("</div>")

    for m in sorted(month_data.keys(), reverse=True):
        rows = month_data[m]
        html.append(f"<h2>{escape(m)}</h2>")

        cat_totals = {}
        for r in rows:
            if r["type"] == "expense" and not r["is_outlier"]:
                cat = r["category"] or "(tanpa kategori)"
                cat_totals[cat] = cat_totals.get(cat, 0) + (r["amount"] or 0)
        if cat_totals:
            html.append(pie_chart_svg(sorted(cat_totals.items(), key=lambda kv: -kv[1])))

        html.append("<div class='table-wrapper'>")
        html.append(
            "<table><thead><tr><th>ID</th><th>Type</th><th>Category</th><th>Qty</th>"
            "<th>Amount</th><th>Description</th><th>Created</th><th>Outlier</th></tr></thead><tbody>"
        )
        for r in sorted(rows, key=lambda x: x["created_at"], reverse=True):
            html.append(
                "<tr>"
                f"<td>{escape(r['id'])}</td>"
                f"<td>{escape(r['type'])}</td>"
                f"<td>{escape(r['category'])}</td>"
                f"<td>{r['quantity'] if r['quantity'] is not None else ''}</td>"
                f"<td>Rp{(r['amount'] or 0):,.0f}</td>"
                f"<td>{escape(r['description'])}</td>"
                f"<td>{r['created_at'].strftime('%Y-%m-%d %H:%M:%S')}</td>"
                f"<td>{'Yes' if r['is_outlier'] else ''}</td>"
                "</tr>"
            )
        html.append("</tbody></table>")
        html.append("</div>")

    html.append("</body></html>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))


# ---------- Handler ----------

def allowed(user_id):
    if ALLOWED_USER_ID is None:
        return True
    return str(user_id) == str(ALLOWED_USER_ID)


def handle_message(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "")

    if not allowed(user_id):
        send_message(chat_id, "Kamu ga punya akses ke bot ini.")
        return

    if text.strip() == "/cancel":
        pending.pop(chat_id, None)
        send_message(chat_id, "Dibatalin.")
        return

    if text.strip() == "/start":
        send_message(
            chat_id,
            "Kirim data dengan format:\n"
            "deskripsi, jumlah\n"
            "atau\n"
            "deskripsi, quantity, jumlah\n"
            "-> nanti pilih tipe & kategori lewat tombol\n\n"
            "Insert cepat (skip tombol):\n"
            "deskripsi, jumlah, tipe, kategori\n"
            "atau\n"
            "deskripsi, quantity, jumlah, tipe, kategori\n"
            "Contoh: Nasi Bali, 2, 14000, expense, Food\n\n"
            "Command lain:\n"
            "/get_last_report - generate & kirim laporan HTML\n"
            "/cancel - batalin input yang lagi jalan",
        )
        return

    if text.strip() == "/get_last_report":
        data = fetch_report_data()
        if not data:
            send_message(chat_id, "Belum ada data transaksi.")
            return
        send_message(chat_id, "Lagi generate laporan...")
        report_path = f"report_{secrets.token_hex(4)}.html"
        try:
            generate_html_report(data, report_path)
            send_document(chat_id, report_path)
        finally:
            if os.path.exists(report_path):
                os.remove(report_path)
        return

    # coba format insert cepat dulu (udah termasuk tipe & kategori)
    quick = parse_quick_input(text)
    if quick is not None:
        description, quantity, amount, tx_type, category = quick
        tx_id, created = insert_transaction(description, quantity, amount, tx_type, category)
        send_message(
            chat_id,
            f"Tersimpan \u2705\n"
            f"{description} | {tx_type} | {category}\n"
            f"Qty {quantity} | Rp{amount:,}\n"
            f"ID {tx_id} | {created}",
        )
        return

    parsed = parse_input(text)
    if parsed is None:
        send_message(
            chat_id,
            "Format ga kebaca. Contoh:\n"
            "Nasi Bali, 14000\n"
            "atau langsung: Nasi Bali, 2, 14000, expense, Food",
        )
        return

    description, quantity, amount = parsed
    pending[chat_id] = {
        "description": description,
        "quantity": quantity,
        "amount": amount,
    }
    send_message(
        chat_id,
        f"{description} | qty {quantity} | Rp{amount:,}\nPilih tipe:",
        type_keyboard(),
    )


def handle_callback(cq):
    chat_id = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]
    user_id = cq["from"]["id"]
    data = cq["data"]

    if not allowed(user_id):
        answer_callback_query(cq["id"], "Ga punya akses.")
        return

    state = pending.get(chat_id)
    if state is None:
        answer_callback_query(cq["id"], "Sesi expired, kirim ulang datanya.")
        return

    if data.startswith("type:"):
        state["type"] = data.split(":", 1)[1]
        answer_callback_query(cq["id"])
        edit_message_text(chat_id, message_id, "Pilih kategori:", category_keyboard())
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        if "type" not in state:
            answer_callback_query(cq["id"], "Pilih tipe dulu.")
            return
        tx_id, created = insert_transaction(
            state["description"], state["quantity"], state["amount"], state["type"], category
        )
        answer_callback_query(cq["id"], "Tersimpan.")
        edit_message_text(
            chat_id, message_id,
            f"Tersimpan \u2705\n"
            f"{state['description']} | {state['type']} | {category}\n"
            f"Qty {state['quantity']} | Rp{state['amount']:,}\n"
            f"ID {tx_id} | {created}",
        )
        pending.pop(chat_id, None)
        return


# ---------- Main loop ----------

def main():
    init_db()
    print("Bot jalan, polling...")
    offset = 0
    while True:
        result = api_call("getUpdates", {"offset": offset, "timeout": 60})
        if not result or not result.get("ok"):
            time.sleep(2)
            continue
        for update in result["result"]:
            offset = update["update_id"] + 1
            if "message" in update and "text" in update["message"]:
                handle_message(update["message"])
            elif "callback_query" in update:
                handle_callback(update["callback_query"])


if __name__ == "__main__":
    main()
