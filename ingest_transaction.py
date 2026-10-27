#!/usr/bin/env python3
"""

"Nasi Bali, 14000, expense, Food, Cash"     (deskripsi, jumlah, tipe, kategori, akun)
"Nasi Bali, 2, 14000, expense, Food, Cash"  (deskripsi, quantity, jumlah, tipe, kategori, akun)
Akun boleh ga ditulis, nanti bot nanya lewat tombol.

Command:
  /add       -> insert interaktif: pilih tipe, akun, kategori, ketik amount, ketik deskripsi
  /cancel    -> batalin /add yang lagi jalan
  /balance [n]             -> saldo tiap akun + n perubahan saldo terakhir (default 5)
  /setbalance <akun> <x>   -> koreksi saldo akun (dicatet di balance_history sebagai 'adjust')
  /last [n]  -> lihat n transaksi terakhir (default 10)
  /report    -> kirim laporan terbaru format Excel (.xlsx)
  /weekly    -> kirim gambar expense 7 hari terakhir

Bot jalan terus (long polling) sampai di-stop pakai Ctrl+C.
Pesan yang formatnya ga cocok dilewat (user dikasih tau lewat balasan).
"""

import os
import json
import time
import sqlite3
import secrets
import urllib.request
import urllib.error
import tempfile
from datetime import datetime

from g_last_r import DB_PATH, fetch_report_data, generate_xlsx_report, parse_created
from g_weekly_e import generate_weekly_png

def load_env(path):
    """Baca file .env (KEY=VALUE per baris) ke os.environ. Env var yang udah ke-set ga ditimpa."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID") or None  # kosong = semua user boleh

if not TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN belum di-set. Isi di file .env (contoh di .env.example)")

API_URL = f"https://api.telegram.org/bot{TOKEN}/"

TYPES = ["expense", "income"]

CATEGORIES = [
    "Active Income", "Bills", "Book", "Entertainment", "Food",
    "Healthcare", "House", "Internet", "Laundry", "Needs",
    "Outlier", "Rent", "Transportation", "Utilities", "Water",
]

# Sumber dana. Nambah akun baru cukup tambahin di sini, saldo awalnya set pakai /setbalance.
ACCOUNTS = ["Bank Mega Syariah", "ShopeePay", "DANA", "BCA Mobile", "Cash"]


def find_account(text):
    """Cocokin teks ke nama akun: persis (ga peduli huruf besar/spasi) atau potongan unik ("mega", "bca")."""
    key = text.strip().lower().replace(" ", "")
    if not key:
        return None
    for a in ACCOUNTS:
        if a.lower().replace(" ", "") == key:
            return a
    if len(key) >= 3:
        matches = [a for a in ACCOUNTS if key in a.lower().replace(" ", "")]
        if len(matches) == 1:
            return matches[0]
    return None

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
            category TEXT NOT NULL,
            account TEXT
        )
    """)
    # DB lama belum punya kolom account. Transaksi lama dibiarin NULL (ga ketauan dari akun mana)
    # dan ga ikut ngitung saldo; saldo tiap akun mulai dari /setbalance.
    columns = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
    if "account" not in columns:
        conn.execute("ALTER TABLE transactions ADD COLUMN account TEXT")
    init_balance_history(conn)
    conn.commit()
    conn.close()


# Efek transaksi ke saldo: income nambah, expense ngurang (outlier tetep dihitung, duitnya beneran keluar).
# Dipakai di trigger SQL di bawah, {t} diganti NEW / OLD.
_EFFECT_SQL = "(CASE WHEN {t}.type = 'income' THEN {t}.amount ELSE -{t}.amount END)"
_LAST_BALANCE_SQL = (
    "COALESCE((SELECT balance FROM balance_history WHERE account = {acc} ORDER BY id DESC LIMIT 1), 0)"
)
_NOW_SQL = "strftime('%Y%m%d%H%M%S', 'now', 'localtime')"


def init_balance_history(conn):
    """
    Tabel balance_history: satu baris tiap kali saldo sebuah akun berubah,
    `balance` = saldo akun itu setelah perubahan. Diisi otomatis lewat trigger di tabel
    transactions (insert/update/delete), jadi insert dari mana pun tetep kecatet.
    Transaksi tanpa account (data lama) ga dicatet.
    """
    columns = [r[1] for r in conn.execute("PRAGMA table_info(balance_history)")]
    if columns and "account" not in columns:
        # versi pertama tabel ini (satu saldo gabungan, isinya cuma hasil hitung ulang
        # dari transaksi): diganti versi per akun
        conn.execute("DROP TABLE balance_history")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS balance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account TEXT NOT NULL,
            transaction_id TEXT,
            created TEXT NOT NULL,
            change INTEGER NOT NULL,
            balance INTEGER NOT NULL,
            note TEXT NOT NULL
        )
    """)

    new_effect = _EFFECT_SQL.format(t="NEW")
    old_effect = _EFFECT_SQL.format(t="OLD")
    new_last = _LAST_BALANCE_SQL.format(acc="NEW.account")
    old_last = _LAST_BALANCE_SQL.format(acc="OLD.account")
    # trigger selalu dibikin ulang biar versi lama di DB ikut keganti
    conn.executescript(f"""
        DROP TRIGGER IF EXISTS trg_balance_insert;
        DROP TRIGGER IF EXISTS trg_balance_update;
        DROP TRIGGER IF EXISTS trg_balance_delete;

        CREATE TRIGGER trg_balance_insert AFTER INSERT ON transactions
        WHEN NEW.account IS NOT NULL
        BEGIN
            INSERT INTO balance_history (account, transaction_id, created, change, balance, note)
            VALUES (NEW.account, NEW.id, NEW.created, {new_effect}, {new_last} + {new_effect}, 'transaction');
        END;

        -- edit amount/type/account: balikin efek lama di akun lama, terapin efek baru di akun baru
        CREATE TRIGGER trg_balance_update AFTER UPDATE OF amount, type, account ON transactions
        WHEN OLD.account IS NOT NEW.account OR {new_effect} != {old_effect}
        BEGIN
            INSERT INTO balance_history (account, transaction_id, created, change, balance, note)
            SELECT OLD.account, OLD.id, {_NOW_SQL}, -{old_effect}, {old_last} - {old_effect}, 'update'
            WHERE OLD.account IS NOT NULL;
            INSERT INTO balance_history (account, transaction_id, created, change, balance, note)
            SELECT NEW.account, NEW.id, {_NOW_SQL}, {new_effect}, {new_last} + {new_effect}, 'update'
            WHERE NEW.account IS NOT NULL;
        END;

        CREATE TRIGGER trg_balance_delete AFTER DELETE ON transactions
        WHEN OLD.account IS NOT NULL
        BEGIN
            INSERT INTO balance_history (account, transaction_id, created, change, balance, note)
            VALUES (OLD.account, OLD.id, {_NOW_SQL}, -{old_effect}, {old_last} - {old_effect}, 'delete');
        END;
    """)


def get_balance(conn, account):
    return conn.execute(f"SELECT {_LAST_BALANCE_SQL.format(acc='?')}", (account,)).fetchone()[0]


def get_balances(conn):
    """Saldo semua akun di ACCOUNTS, urutan sama kayak ACCOUNTS."""
    return {a: get_balance(conn, a) for a in ACCOUNTS}


def set_balance(account, new_balance):
    """Koreksi saldo akun manual (misal nyamain sama saldo rekening). Return (saldo lama, saldo baru)."""
    conn = sqlite3.connect(DB_PATH)
    old = get_balance(conn, account)
    conn.execute(
        "INSERT INTO balance_history (account, transaction_id, created, change, balance, note) "
        "VALUES (?, NULL, ?, ?, ?, 'adjust')",
        (account, datetime.now().strftime("%Y%m%d%H%M%S"), new_balance - old, new_balance),
    )
    conn.commit()
    conn.close()
    return old, new_balance


def insert_transaction(description, quantity, amount, tx_type, category, created, account):
    tx_id = "r" + secrets.token_hex(7)
    # outlier cuma berlaku buat expense, income ga pernah dipisah jadi outlier
    is_outlier = 1 if category == "Outlier" and tx_type == "expense" else 0
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO transactions "
        "(id, description, created, quantity, amount, is_outlier, type, category, account) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tx_id, description, created, quantity, amount, is_outlier, tx_type, category, account),
    )
    conn.commit()
    balance = get_balance(conn, account)  # udah diupdate trigger
    conn.close()
    return tx_id, balance


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


def send_file(chat_id, method, field, file_path, content_type, caption=None):
    """Upload file ke Telegram (sendDocument/sendPhoto) pakai multipart/form-data manual."""
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
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'.encode("utf-8")
    )
    body.extend(file_data)
    body.extend(f'\r\n--{boundary}--\r\n'.encode("utf-8"))

    req = urllib.request.Request(
        API_URL + method,
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


# ---------- Keyboard builder ----------

def type_keyboard():
    buttons = [[{"text": t.capitalize(), "callback_data": f"type:{t}"} for t in TYPES]]
    buttons.append([{"text": "Batal", "callback_data": "cancel"}])
    return {"inline_keyboard": buttons}


def account_keyboard():
    """Satu tombol per akun, sekalian nampilin saldonya."""
    conn = sqlite3.connect(DB_PATH)
    balances = get_balances(conn)
    conn.close()
    buttons = [[{"text": f"{a} (Rp{b:,})", "callback_data": f"acc:{a}"}] for a, b in balances.items()]
    buttons.append([{"text": "Batal", "callback_data": "cancel"}])
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
    buttons.append([{"text": "Batal", "callback_data": "cancel"}])
    return {"inline_keyboard": buttons}


# ---------- /add interaktif ----------
# state per chat: {"step": "type"|"account"|"category"|"amount"|"description", "type": ..., "account": ..., ...}
# Insert cepat tanpa akun juga lewat sini: state-nya udah lengkap kecuali akun, step "account".
# disimpen di memory doang, jadi kalau bot di-restart /add yang setengah jalan ilang.
pending_add = {}


def save_transaction(chat_id, tx):
    """Simpan transaksi dari dict lengkap (description, quantity, amount, type, category, created, account)."""
    tx_id, balance = insert_transaction(
        tx["description"], tx["quantity"], tx["amount"], tx["type"], tx["category"], tx["created"], tx["account"],
    )
    send_message(
        chat_id,
        f"Tersimpan ✅\n"
        f"{tx['description']} | {tx['type']} | {tx['category']}\n"
        f"Qty {tx['quantity']} | Rp{tx['amount']:,} | {tx['account']}\n"
        f"ID {tx_id} | {tx['created']}\n"
        f"Saldo {tx['account']}: Rp{balance:,}",
    )


def cmd_add(chat_id, args):
    pending_add[chat_id] = {"step": "type"}
    send_message(chat_id, "Tambah transaksi. Pilih tipe:", type_keyboard())


def cmd_cancel(chat_id, args):
    if pending_add.pop(chat_id, None):
        send_message(chat_id, "Dibatalin, ga ada yang disimpan.")
    else:
        send_message(chat_id, "Ga ada /add yang lagi jalan.")


def handle_callback(cq):
    chat_id = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]
    data = cq.get("data", "")
    state = pending_add.get(chat_id)

    if not allowed(cq["from"]["id"]):
        answer_callback_query(cq["id"])
        return
    if data == "cancel":
        pending_add.pop(chat_id, None)
        answer_callback_query(cq["id"])
        edit_message_text(chat_id, message_id, "Dibatalin, ga ada yang disimpan.")
        return
    if state is None:
        answer_callback_query(cq["id"], "Sesi udah habis, ketik /add lagi")
        return

    if data.startswith("type:") and state["step"] == "type":
        state["type"] = data.split(":", 1)[1]
        state["step"] = "account"
        answer_callback_query(cq["id"])
        edit_message_text(chat_id, message_id, f"Tipe: {state['type']}\nPakai akun mana?", account_keyboard())
    elif data.startswith("acc:") and state["step"] == "account":
        state["account"] = data.split(":", 1)[1]
        answer_callback_query(cq["id"])
        if "description" in state:
            # dari insert cepat: tinggal akun yang kurang, langsung simpan
            pending_add.pop(chat_id, None)
            edit_message_text(chat_id, message_id, f"Akun: {state['account']}")
            save_transaction(chat_id, state)
            return
        state["step"] = "category"
        edit_message_text(
            chat_id, message_id,
            f"Tipe: {state['type']}\nAkun: {state['account']}\nPilih kategori:", category_keyboard(),
        )
    elif data.startswith("cat:") and state["step"] == "category":
        state["category"] = data.split(":", 1)[1]
        state["step"] = "amount"
        answer_callback_query(cq["id"])
        edit_message_text(
            chat_id, message_id,
            f"Tipe: {state['type']}\nAkun: {state['account']}\nKategori: {state['category']}\n\n"
            f"Ketik amount (contoh: 14000):",
        )
    else:
        # tombol dari pesan lama yang udah ga relevan
        answer_callback_query(cq["id"])


def handle_add_reply(msg, state):
    """Lanjutin /add dari jawaban teks user (amount atau deskripsi)."""
    chat_id = msg["chat"]["id"]
    text = msg["text"].strip()

    if state["step"] == "amount":
        amount = clean_amount(text)
        if amount is None or amount <= 0:
            send_message(chat_id, "Amount ga valid, ketik angka aja (contoh: 14000). /cancel buat batal.")
            return
        state["amount"] = amount
        state["step"] = "description"
        send_message(chat_id, f"Amount: Rp{amount:,}\nSekarang ketik deskripsinya:")
    elif state["step"] == "description":
        pending_add.pop(chat_id, None)
        state["description"] = text
        state["quantity"] = 1
        state["created"] = datetime.fromtimestamp(msg["date"]).strftime("%Y%m%d%H%M%S")
        save_transaction(chat_id, state)
    else:
        send_message(chat_id, "Pilih lewat tombol di atas dulu ya. /cancel buat batal.")


# ---------- Command ----------

def cmd_balance(chat_id, args):
    """Saldo tiap akun + total, lalu n perubahan saldo terakhir (default 5)."""
    try:
        n = max(1, min(int(args[0]), 50)) if args else 5
    except ValueError:
        n = 5
    conn = sqlite3.connect(DB_PATH)
    balances = get_balances(conn)
    rows = conn.execute(
        "SELECT b.account, b.created, b.change, b.balance, b.note, t.description FROM balance_history b "
        "LEFT JOIN transactions t ON t.id = b.transaction_id ORDER BY b.id DESC LIMIT ?",
        (n,),
    ).fetchall()
    conn.close()
    lines = ["Saldo:"]
    lines += [f"{a}: Rp{b:,}" for a, b in balances.items()]
    lines += [f"Total: Rp{sum(balances.values()):,}", "", f"{len(rows)} perubahan terakhir:"]
    for account, created, change, after, note, description in rows:
        try:
            when = parse_created(created).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            when = str(created)
        # description None = transaksinya udah dihapus dari tabel transactions
        description = description or "(transaksi dihapus)"
        label = description if note == "transaction" else f"({note}) {description}"
        if note == "adjust":
            label = "(koreksi saldo)"
        lines.append(f"{when} | {account} | {change:+,} -> Rp{after:,} | {label}")
    send_message(chat_id, "\n".join(lines))


def cmd_setbalance(chat_id, args):
    """/setbalance <akun> <angka>, misal "/setbalance mega 31625608" atau "/setbalance cash 250000"."""
    account = find_account(" ".join(args[:-1])) if len(args) >= 2 else None
    amount = clean_amount(args[-1]) if args else None
    if account is None or amount is None:
        send_message(
            chat_id,
            "Pakai: /setbalance <akun> <angka>\n"
            "Contoh: /setbalance mega 31625608\n\n"
            "Akun: " + ", ".join(ACCOUNTS),
        )
        return
    if args[-1].strip().startswith("-"):
        amount = -amount
    old, new = set_balance(account, amount)
    send_message(chat_id, f"Saldo {account} dikoreksi: Rp{old:,} -> Rp{new:,} ({new - old:+,})")


def cmd_last(chat_id, args):
    """Tampilin n transaksi yang terakhir dimasukin (urut waktu insert, bukan tanggal)."""
    try:
        n = max(1, min(int(args[0]), 50)) if args else 10
    except ValueError:
        n = 10
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    rows = conn.execute(
        "SELECT created, type, category, quantity, amount, description, account FROM transactions "
        "ORDER BY rowid DESC LIMIT ?",
        (n,),
    ).fetchall()
    conn.close()
    if not rows:
        send_message(chat_id, "Belum ada data transaksi.")
        return
    lines = [f"Total {total:,} transaksi. {len(rows)} terakhir:"]
    for created, tx_type, category, qty, amount, description, account in rows:
        try:
            when = parse_created(created).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            when = str(created)
        sign = "-" if tx_type == "expense" else "+"
        qty_txt = f" x{qty}" if qty and qty != 1 else ""
        account_txt = f" | {account}" if account else ""
        lines.append(f"{when} | {sign}Rp{amount or 0:,}{qty_txt} | {category}{account_txt} | {description}")
    send_message(chat_id, "\n".join(lines))


def cmd_report(chat_id, args):
    data = fetch_report_data()
    if not data:
        send_message(chat_id, "Belum ada data transaksi.")
        return
    now = datetime.now()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, f"report_{now:%Y%m%d_%H%M}.xlsx")
        generate_xlsx_report(data, path)
        send_file(
            chat_id, "sendDocument", "document", path,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            caption=f"Laporan transaksi per {now:%Y-%m-%d %H:%M}",
        )


def cmd_weekly(chat_id, args):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "weekly_expense.png")
        total = generate_weekly_png(fetch_report_data(), path)
        if not total:
            send_message(chat_id, "Ga ada expense 7 hari terakhir.")
            return
        send_file(
            chat_id, "sendPhoto", "photo", path, "image/png",
            caption=f"Expense 7 hari terakhir: Rp{total:,} (outlier ga dihitung)",
        )


COMMANDS = {
    "/add": cmd_add,
    "/cancel": cmd_cancel,
    "/balance": cmd_balance,
    "/setbalance": cmd_setbalance,
    "/last": cmd_last,
    "/report": cmd_report,
    "/weekly": cmd_weekly,
}

HELP_TEXT = (
    "Command:\n"
    "/add - tambah transaksi (pilih tipe, akun, kategori, amount, deskripsi)\n"
    "/cancel - batalin /add\n"
    "/balance [n] - saldo tiap akun + n perubahan terakhir\n"
    "/setbalance <akun> <angka> - koreksi saldo akun, contoh: /setbalance cash 250000\n"
    "/last [n] - lihat n transaksi terakhir\n"
    "/report - laporan Excel\n"
    "/weekly - gambar expense 7 hari terakhir\n\n"
    "Atau insert cepat (akun di akhir opsional, kalau ga ditulis nanti ditanya):\n"
    "Nasi Bali, 2, 14000, expense, Food, Cash\n\n"
    "Akun: " + ", ".join(ACCOUNTS)
)

# ---------- Parsing input ----------

def clean_amount(raw):
    raw = raw.strip().lower().replace("rp", "").replace(".", "").replace(",", "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def parse_quick_input(text):
    """
    Parse format insert cepat yang udah termasuk tipe & kategori, biar ga
    perlu inline button:
      "deskripsi, jumlah, tipe, kategori[, akun]"
      "deskripsi, quantity, jumlah, tipe, kategori[, akun]"
    Akun opsional (None kalau ga ditulis). Return None kalau bukan format ini.
    """
    parts = [p.strip() for p in text.split(",")]
    # nama akun ga ada yang sama kayak kategori, jadi aman dicek dari bagian terakhir
    account = find_account(parts[-1])
    if account is not None:
        parts = parts[:-1]
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

    return description, quantity, amount, type_raw, category, account


def allowed(user_id):
    if ALLOWED_USER_ID is None:
        return True
    return str(user_id) == str(ALLOWED_USER_ID)


def handle_message(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "").strip()

    if not allowed(user_id):
        return
    if not text:
        return
    if text.startswith("/"):
        # "/report@NamaBot" di grup juga kebaca
        parts = text.split()
        command = COMMANDS.get(parts[0].split("@")[0].lower())
        if command:
            command(chat_id, parts[1:])
        else:
            send_message(chat_id, HELP_TEXT)
        return

    state = pending_add.get(chat_id)
    if state is not None:
        handle_add_reply(msg, state)
        return

    quick = parse_quick_input(text)
    if quick is None:
        send_message(chat_id, "Format ga kebaca, ga disimpan.\n\n" + HELP_TEXT)
        return

    description, quantity, amount, tx_type, category, account = quick
    tx = {
        "description": description, "quantity": quantity, "amount": amount,
        "type": tx_type, "category": category, "account": account,
        # waktu transaksi = waktu pesan dikirim, bukan waktu script jalan
        "created": datetime.fromtimestamp(msg["date"]).strftime("%Y%m%d%H%M%S"),
    }
    if account is None:
        pending_add[chat_id] = {**tx, "step": "account"}
        send_message(chat_id, f"{description} | Rp{amount:,} | {tx_type} | {category}\nPakai akun mana?",
                     account_keyboard())
        return
    save_transaction(chat_id, tx)


def handle_update(update):
    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return
    msg = update.get("message")
    if msg and "text" in msg:
        handle_message(msg)


def main():
    init_db()
    print(f"Bot jalan, DB: {DB_PATH}. Ctrl+C buat stop.")
    offset = None
    while True:
        params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        result = api_call("getUpdates", params)
        if not result or not result.get("ok"):
            # internet putus / Telegram error: tunggu bentar terus coba lagi
            time.sleep(5)
            continue

        for update in result["result"]:
            # offset dimajuin duluan, biar update yang bikin error ga diulang terus
            offset = update["update_id"] + 1
            try:
                handle_update(update)
            except Exception as e:
                print(f"Error proses update {update['update_id']}: {e!r}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot di-stop.")
