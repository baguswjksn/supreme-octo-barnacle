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
DB_PATH = "transactions.db"
ALLOWED_USER_ID = 

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
    ratio = max(0.0, min(1.0, ratio))

    def h2rgb(h):
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    low, mid, high = h2rgb(low_hex), h2rgb(mid_hex), h2rgb(high_hex)
    if ratio <= 0.5:
        t = ratio / 0.5
        rgb = tuple(int(low[i] + (mid[i] - low[i]) * t) for i in range(3))
    else:
        t = (ratio - 0.5) / 0.5
        rgb = tuple(int(mid[i] + (high[i] - mid[i]) * t) for i in range(3))
    return "#%02X%02X%02X" % rgb


def pie_chart_svg(items, size=240):
    if not items:
        return ""
    total = sum(v for _, v in items) or 1
    cx = cy = size / 2
    r = size / 2 - 10
    start_angle = -90.0
    paths = []
    legend = []
    for i, (label, value) in enumerate(items):
        color = PALETTE[i % len(PALETTE)]
        angle = (value / total) * 360.0
        end_angle = start_angle + angle
        x1 = cx + r * math.cos(math.radians(start_angle))
        y1 = cy + r * math.sin(math.radians(start_angle))
        x2 = cx + r * math.cos(math.radians(end_angle))
        y2 = cy + r * math.sin(math.radians(end_angle))
        large_arc = 1 if angle > 180 else 0
        if len(items) == 1:
            paths.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>')
        else:
            paths.append(
                f'<path d="M{cx},{cy} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large_arc} 1 {x2:.2f},{y2:.2f} Z" '
                f'fill="{color}" stroke="#fff" stroke-width="1"/>'
            )
        pct = value / total * 100
        legend.append(
            f'<div class="legend-item"><span class="swatch" style="background:{color}"></span>'
            f'{escape(label)}: Rp{value:,.0f} ({pct:.1f}%)</div>'
        )
        start_angle = end_angle
    svg = f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">{"".join(paths)}</svg>'
    return f'<div class="chart-wrap"><div>{svg}</div><div class="legend">{"".join(legend)}</div></div>'


def line_chart_svg(months, series, height=280, pad=50, min_col_width=55):
    width = max(400, pad * 2 + max(1, len(months) - 1) * min_col_width)
    all_values = [v for vals in series.values() for v in vals] or [0]
    max_v = max(all_values + [0])
    min_v = min(all_values + [0])

    def x_pos(i):
        if len(months) <= 1:
            return pad
        return pad + i * (width - 2 * pad) / (len(months) - 1)

    def y_pos(v):
        if max_v == min_v:
            return height - pad
        return height - pad - (v - min_v) / (max_v - min_v) * (height - 2 * pad)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}">']
    parts.append(f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#999"/>')
    parts.append(f'<line x1="{pad}" y1="10" x2="{pad}" y2="{height - pad}" stroke="#999"/>')

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
  body { font-family: Arial, Helvetica, sans-serif; margin: 24px; color: #222; }
  h1 { margin-bottom: 0; }
  .muted { color: #777; margin-top: 4px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 13px; }
  th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: left; }
  th { background: #ADD8E6; font-weight: bold; }
  tr:nth-child(even) td { background: #fafafa; }
  .chart-wrap { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; margin-bottom: 8px; max-width: 100%; overflow-x: auto; }
  .legend { font-size: 12px; }
  .legend-item { margin-bottom: 4px; }
  .swatch { display: inline-block; width: 10px; height: 10px; margin-right: 6px; border-radius: 2px; }
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

    income_clean_vals = [monthly_totals[m]["income_clean"] for m in months_sorted] or [0]
    income_out_vals = [monthly_totals[m]["income_outlier"] for m in months_sorted] or [0]
    exp_clean_vals = [monthly_totals[m]["expense_clean"] for m in months_sorted] or [0]
    exp_out_vals = [monthly_totals[m]["expense_outlier"] for m in months_sorted] or [0]
    income_min, income_max = min(income_clean_vals), max(income_clean_vals)
    income_out_min, income_out_max = min(income_out_vals), max(income_out_vals)
    exp_clean_min, exp_clean_max = min(exp_clean_vals), max(exp_clean_vals)
    exp_out_min, exp_out_max = min(exp_out_vals), max(exp_out_vals)

    html = ["<html><head><meta charset='utf-8'><title>Laporan Transaksi</title>", STYLE, "</head><body>"]
    html.append("<h1>Laporan Transaksi</h1>")
    html.append(f"<p class='muted'>Dibuat {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>")

    html.append("<h2>Summary</h2>")
    html.append(line_chart_svg(months_sorted, {"Income": incomes, "Expense": expenses}))
    html.append(
        "<table><thead><tr><th>Month</th><th>Income</th><th>Income Outlier</th>"
        "<th>Expense (Outlier Excluded)</th><th>Expense Outlier</th></tr></thead><tbody>"
    )
    for m in months_sorted_desc:
        t = monthly_totals[m]
        income_color = color_scale(t["income_clean"], income_min, income_max, "F8696B", "FFEB84", "63BE7B")
        income_out_color = color_scale(
            t["income_outlier"], income_out_min, income_out_max, "F8696B", "FFEB84", "63BE7B"
        )
        exp_clean_color = color_scale(t["expense_clean"], exp_clean_min, exp_clean_max, "63BE7B", "FFEB84", "F8696B")
        exp_out_color = color_scale(t["expense_outlier"], exp_out_min, exp_out_max, "63BE7B", "FFEB84", "F8696B")
        html.append(
            "<tr>"
            f"<td>{escape(m)}</td>"
            f"<td style='background:{income_color}'>Rp{t['income_clean']:,.0f}</td>"
            f"<td style='background:{income_out_color}'>Rp{t['income_outlier']:,.0f}</td>"
            f"<td style='background:{exp_clean_color}'>Rp{t['expense_clean']:,.0f}</td>"
            f"<td style='background:{exp_out_color}'>Rp{t['expense_outlier']:,.0f}</td>"
            "</tr>"
        )
    html.append("</tbody></table>")

    for m in sorted(month_data.keys(), reverse=True):
        rows = month_data[m]
        html.append(f"<h2>{escape(m)}</h2>")

        cat_totals = {}
        for r in rows:
            if r["type"] == "expense" and not r["is_outlier"]:
                cat = r["category"] or "(tanpa kategori)"
                cat_totals[cat] = cat_totals.get(cat, 0) + (r["amount"] or 0)
        if cat_totals:
            items = sorted(cat_totals.items(), key=lambda kv: -kv[1])
            html.append(pie_chart_svg(items))

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
