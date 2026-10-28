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
  /insight [YYYYMM]        -> insight bulan berjalan (atau bulan tertentu): perbandingan, proyeksi, top kategori
  /trend [n]               -> gambar tren income/expense & saving % n bulan terakhir (default 12)
  /transfer <dari> <ke> <x> [catatan] -> pindah saldo antar akun, ga dihitung income/expense
  /undo                    -> hapus transaksi/transfer terakhir (pakai konfirmasi)
  /delete <id>             -> hapus transaksi/transfer tertentu (pakai konfirmasi)
  /edit <id> <field> <x>   -> ubah amount/desc/qty/category/type/account transaksi
  /budget [kategori x]     -> lihat status budget bulan ini / set budget (0 = hapus)
  /recurring [add|del]     -> transaksi rutin bulanan yang dicatet otomatis
  /search <kata>           -> cari transaksi dari deskripsi

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
import calendar
from datetime import datetime, timedelta

from g_last_r import DB_PATH, fetch_report_data, generate_xlsx_report, parse_created
from g_weekly_e import generate_weekly_png
from g_insight import monthly_insight, parse_month, budget_status, budget_line
from g_trend import generate_trend_png

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
    init_transfers(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            category TEXT PRIMARY KEY,
            amount INTEGER NOT NULL
        )
    """)
    # transaksi rutin: dicatet otomatis tiap bulan di tanggal `day`, last_run = YYYYMM terakhir dicatet
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recurring (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            day INTEGER NOT NULL,
            description TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            type TEXT NOT NULL,
            category TEXT NOT NULL,
            account TEXT NOT NULL,
            last_run TEXT
        )
    """)
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


def init_transfers(conn):
    """
    Tabel transfers: pindah duit antar akun. Disimpen terpisah dari transactions biar ga
    kehitung jadi income/expense di laporan. Saldo kedua akun diupdate lewat trigger,
    dicatet di balance_history dengan note 'transfer' (hapus transfer -> 'delete').
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id TEXT PRIMARY KEY,
            created TEXT NOT NULL,
            from_account TEXT NOT NULL,
            to_account TEXT NOT NULL,
            amount INTEGER NOT NULL,
            note TEXT
        )
    """)
    from_last = _LAST_BALANCE_SQL.format(acc="{t}.from_account")
    to_last = _LAST_BALANCE_SQL.format(acc="{t}.to_account")
    conn.executescript(f"""
        DROP TRIGGER IF EXISTS trg_transfer_insert;
        DROP TRIGGER IF EXISTS trg_transfer_delete;

        CREATE TRIGGER trg_transfer_insert AFTER INSERT ON transfers
        BEGIN
            INSERT INTO balance_history (account, transaction_id, created, change, balance, note)
            VALUES (NEW.from_account, NEW.id, NEW.created, -NEW.amount, {from_last.format(t="NEW")} - NEW.amount, 'transfer');
            INSERT INTO balance_history (account, transaction_id, created, change, balance, note)
            VALUES (NEW.to_account, NEW.id, NEW.created, NEW.amount, {to_last.format(t="NEW")} + NEW.amount, 'transfer');
        END;

        CREATE TRIGGER trg_transfer_delete AFTER DELETE ON transfers
        BEGIN
            INSERT INTO balance_history (account, transaction_id, created, change, balance, note)
            VALUES (OLD.from_account, OLD.id, {_NOW_SQL}, OLD.amount, {from_last.format(t="OLD")} + OLD.amount, 'delete');
            INSERT INTO balance_history (account, transaction_id, created, change, balance, note)
            VALUES (OLD.to_account, OLD.id, {_NOW_SQL}, -OLD.amount, {to_last.format(t="OLD")} - OLD.amount, 'delete');
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


def insert_transfer(from_account, to_account, amount, created, note=None):
    """Simpan transfer antar akun. Return (id, saldo akun asal, saldo akun tujuan) setelah transfer."""
    tr_id = "t" + secrets.token_hex(7)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO transfers (id, created, from_account, to_account, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
        (tr_id, created, from_account, to_account, amount, note),
    )
    conn.commit()
    balances = get_balance(conn, from_account), get_balance(conn, to_account)
    conn.close()
    return (tr_id, *balances)


def find_record(record_id):
    """
    Cari transaksi atau transfer berdasarkan ID. Return (tabel, teks ringkas) atau None.
    ID transfer diawali "t", selain itu dianggap transaksi.
    """
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT created, type, category, amount, description, account FROM transactions WHERE id = ?",
        (record_id,),
    ).fetchone()
    if row:
        conn.close()
        created, tx_type, category, amount, description, account = row
        return "transactions", f"{description} | {tx_type} | {category} | Rp{amount:,} | {account or '-'} | {created}"
    row = conn.execute(
        "SELECT created, from_account, to_account, amount, note FROM transfers WHERE id = ?", (record_id,),
    ).fetchone()
    conn.close()
    if row:
        created, from_account, to_account, amount, note = row
        note_txt = f" | {note}" if note else ""
        return "transfers", f"Transfer {from_account} -> {to_account} | Rp{amount:,}{note_txt} | {created}"
    return None


def last_record_id():
    """ID transaksi/transfer yang terakhir dimasukin dan masih ada (buat /undo)."""
    conn = sqlite3.connect(DB_PATH)
    # balance_history urut waktu insert dan nyatet transaksi & transfer, jadi bisa dipakai buat
    # nentuin mana yang paling baru di antara dua tabel itu
    row = conn.execute(
        "SELECT b.transaction_id FROM balance_history b "
        "WHERE b.note IN ('transaction', 'transfer') AND ("
        "  EXISTS (SELECT 1 FROM transactions t WHERE t.id = b.transaction_id) OR"
        "  EXISTS (SELECT 1 FROM transfers r WHERE r.id = b.transaction_id)"
        ") ORDER BY b.id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        # transaksi tanpa akun (data lama) ga ada di balance_history
        row = conn.execute("SELECT id FROM transactions ORDER BY rowid DESC LIMIT 1").fetchone()
    conn.close()
    return row[0] if row else None


def delete_record(table, record_id):
    """Hapus transaksi/transfer, saldo dibalikin sama trigger."""
    conn = sqlite3.connect(DB_PATH)
    # table cuma dari find_record ("transactions"/"transfers"), bukan dari input user
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()


EDIT_FIELDS = ("amount", "desc", "qty", "category", "type", "account")


def update_transaction(tx_id, field, raw):
    """
    Ubah satu kolom transaksi. Return pesan error (str) atau None kalau berhasil.
    Saldo ikut disesuaiin sama trigger kalau amount/type/account berubah.
    """
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT type, category FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    conn.close()
    if row is None:
        return "Transaksi ga ketemu (transfer ga bisa diedit, hapus terus bikin ulang)."
    tx_type, category = row
    if field == "amount":
        value = clean_amount(raw)
        if not value:
            return "Amount ga valid."
        column = "amount"
    elif field == "qty":
        if not raw.strip().isdigit() or int(raw) <= 0:
            return "Qty harus angka."
        column, value = "quantity", int(raw)
    elif field == "desc":
        column, value = "description", raw.strip()
        if not value:
            return "Deskripsi kosong."
    elif field == "category":
        value = next((c for c in CATEGORIES if c.lower() == raw.strip().lower()), None)
        if value is None:
            return "Kategori ga dikenal. Pilihan: " + ", ".join(CATEGORIES)
        column, category = "category", value
    elif field == "type":
        value = raw.strip().lower()
        if value not in TYPES:
            return "Tipe harus expense atau income."
        column, tx_type = "type", value
    elif field == "account":
        value = find_account(raw)
        if value is None:
            return "Akun ga dikenal. Pilihan: " + ", ".join(ACCOUNTS)
        column = "account"
    else:
        return "Field yang bisa diedit: " + ", ".join(EDIT_FIELDS)
    is_outlier = 1 if category == "Outlier" and tx_type == "expense" else 0
    # column dari daftar di atas, bukan dari input user
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"UPDATE transactions SET {column} = ?, is_outlier = ? WHERE id = ?", (value, is_outlier, tx_id))
    conn.commit()
    conn.close()
    return None


def get_budgets():
    conn = sqlite3.connect(DB_PATH)
    budgets = dict(conn.execute("SELECT category, amount FROM budgets").fetchall())
    conn.close()
    return budgets


def set_budget(category, amount):
    """Set budget bulanan kategori. amount 0 = hapus budget."""
    conn = sqlite3.connect(DB_PATH)
    if amount:
        conn.execute("INSERT OR REPLACE INTO budgets (category, amount) VALUES (?, ?)", (category, amount))
    else:
        conn.execute("DELETE FROM budgets WHERE category = ?", (category,))
    conn.commit()
    conn.close()


def run_recurring(now=None):
    """
    Catet transaksi rutin yang udah jatuh tempo bulan ini dan belum dicatet.
    Tanggal 29-31 di bulan yang lebih pendek dicatet di hari terakhir bulan itu.
    Return list (chat_id, tx) yang barusan disimpen.
    """
    now = now or datetime.now()
    month = now.strftime("%Y%m")
    last_day = calendar.monthrange(now.year, now.month)[1]
    conn = sqlite3.connect(DB_PATH)
    due = conn.execute(
        "SELECT id, chat_id, description, quantity, amount, type, category, account FROM recurring "
        "WHERE (last_run IS NULL OR last_run < ?) AND MIN(day, ?) <= ?",
        (month, last_day, now.day),
    ).fetchall()
    for rec_id, *_ in due:
        conn.execute("UPDATE recurring SET last_run = ? WHERE id = ?", (month, rec_id))
    conn.commit()
    conn.close()
    saved = []
    for rec_id, chat_id, description, quantity, amount, tx_type, category, account in due:
        tx = {
            "description": description, "quantity": quantity, "amount": amount, "type": tx_type,
            "category": category, "account": account, "created": now.strftime("%Y%m%d%H%M%S"),
        }
        saved.append((chat_id, tx))
    return saved


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


def save_transaction(chat_id, tx, header="Tersimpan ✅"):
    """Simpan transaksi dari dict lengkap (description, quantity, amount, type, category, created, account)."""
    tx_id, balance = insert_transaction(
        tx["description"], tx["quantity"], tx["amount"], tx["type"], tx["category"], tx["created"], tx["account"],
    )
    text = (
        f"{header}\n"
        f"{tx['description']} | {tx['type']} | {tx['category']}\n"
        f"Qty {tx['quantity']} | Rp{tx['amount']:,} | {tx['account']}\n"
        f"ID {tx_id} | {tx['created']}\n"
        f"Saldo {tx['account']}: Rp{balance:,}"
    )
    # kalau kategorinya punya budget, sekalian kasih tau udah kepake berapa bulan ini
    budgets = get_budgets()
    if tx["type"] == "expense" and tx["category"] in budgets:
        status = budget_status(fetch_report_data(), {tx["category"]: budgets[tx["category"]]})[0]
        text += "\n\nBudget " + budget_line(status)
    send_message(chat_id, text)


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
    # konfirmasi /delete & /undo, ga ada hubungannya sama state /add
    if data.startswith("del:"):
        answer_callback_query(cq["id"])
        record_id = data.split(":", 1)[1]
        found = find_record(record_id)
        if found is None:
            edit_message_text(chat_id, message_id, "Udah ga ada (mungkin udah dihapus).")
            return
        delete_record(found[0], record_id)
        edit_message_text(chat_id, message_id, f"Dihapus 🗑️\n{found[1]}\nSaldo udah dibalikin.")
        return
    if data == "delno":
        answer_callback_query(cq["id"])
        edit_message_text(chat_id, message_id, "Ga jadi dihapus.")
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
        "SELECT b.account, b.created, b.change, b.balance, b.note, "
        "COALESCE(t.description, 'transfer ' || r.from_account || ' -> ' || r.to_account) FROM balance_history b "
        "LEFT JOIN transactions t ON t.id = b.transaction_id "
        "LEFT JOIN transfers r ON r.id = b.transaction_id ORDER BY b.id DESC LIMIT ?",
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
        label = description if note in ("transaction", "transfer") else f"({note}) {description}"
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


def cmd_insight(chat_id, args):
    """/insight -> bulan berjalan, /insight 202609 -> bulan tertentu."""
    year = month = None
    if args:
        parsed = parse_month(args[0])
        if parsed is None:
            send_message(chat_id, "Pakai: /insight atau /insight YYYYMM, contoh: /insight 202609")
            return
        year, month = parsed
    send_message(chat_id, monthly_insight(fetch_report_data(), year, month, budgets=get_budgets()))


def cmd_transfer(chat_id, args):
    """/transfer <dari> <ke> <amount> [catatan], misal "/transfer mega dana 500000 topup"."""
    from_account = find_account(args[0]) if len(args) >= 3 else None
    to_account = find_account(args[1]) if len(args) >= 3 else None
    amount = clean_amount(args[2]) if len(args) >= 3 else None
    if from_account is None or to_account is None or not amount:
        send_message(
            chat_id,
            "Pakai: /transfer <dari> <ke> <amount> [catatan]\n"
            "Contoh: /transfer mega dana 500000 topup\n\n"
            "Transfer ga dihitung sebagai income/expense. Biaya admin catet terpisah sebagai expense.\n"
            "Akun: " + ", ".join(ACCOUNTS),
        )
        return
    if from_account == to_account:
        send_message(chat_id, "Akun asal dan tujuan sama.")
        return
    note = " ".join(args[3:]) or None
    tr_id, from_balance, to_balance = insert_transfer(
        from_account, to_account, amount, datetime.now().strftime("%Y%m%d%H%M%S"), note,
    )
    send_message(
        chat_id,
        f"Transfer tersimpan ✅\n{from_account} -> {to_account} | Rp{amount:,}\nID {tr_id}\n"
        f"Saldo {from_account}: Rp{from_balance:,}\nSaldo {to_account}: Rp{to_balance:,}",
    )


def confirm_delete(chat_id, record_id):
    found = find_record(record_id)
    if found is None:
        send_message(chat_id, f"ID {record_id} ga ketemu. Cek ID-nya lewat /last atau /search.")
        return
    keyboard = {"inline_keyboard": [[
        {"text": "Ya, hapus", "callback_data": f"del:{record_id}"},
        {"text": "Batal", "callback_data": "delno"},
    ]]}
    send_message(chat_id, f"Hapus ini?\n{found[1]}\nID {record_id}", keyboard)


def cmd_undo(chat_id, args):
    """Hapus transaksi/transfer yang terakhir dimasukin (pakai konfirmasi)."""
    record_id = last_record_id()
    if record_id is None:
        send_message(chat_id, "Belum ada transaksi.")
        return
    confirm_delete(chat_id, record_id)


def cmd_delete(chat_id, args):
    """/delete <id> -> hapus transaksi/transfer tertentu (pakai konfirmasi)."""
    if not args:
        send_message(chat_id, "Pakai: /delete <id>. ID ada di /last, /search, atau pesan Tersimpan.")
        return
    confirm_delete(chat_id, args[0])


def cmd_edit(chat_id, args):
    """/edit <id> <field> <nilai>, misal "/edit r5d526bb03b79a7 amount 15000"."""
    if len(args) < 3:
        send_message(
            chat_id,
            "Pakai: /edit <id> <field> <nilai>\n"
            "Field: " + ", ".join(EDIT_FIELDS) + "\n"
            "Contoh: /edit r5d526bb03b79a7 amount 15000\n"
            "/edit r5d526bb03b79a7 desc Nasi Padang",
        )
        return
    tx_id, field, value = args[0], args[1].lower(), " ".join(args[2:])
    error = update_transaction(tx_id, field, value)
    if error:
        send_message(chat_id, error)
        return
    found = find_record(tx_id)
    send_message(chat_id, f"Diubah ✏️\n{found[1]}")


def cmd_budget(chat_id, args):
    """/budget -> status semua budget, /budget <kategori> <angka> -> set (0 = hapus)."""
    if not args:
        budgets = get_budgets()
        if not budgets:
            send_message(chat_id, "Belum ada budget. Set pakai: /budget Food 1500000")
            return
        status = budget_status(fetch_report_data(), budgets)
        total_spent = sum(b["spent"] for b in status)
        total_limit = sum(b["limit"] for b in status)
        lines = ["Budget bulan ini:"] + [budget_line(b) for b in status]
        lines.append(f"\nTotal: Rp{total_spent:,} / Rp{total_limit:,}")
        send_message(chat_id, "\n".join(lines))
        return
    amount = clean_amount(args[-1]) if len(args) >= 2 else None
    name = " ".join(args[:-1]).lower()
    category = next((c for c in CATEGORIES if c.lower() == name and c != "Outlier"), None)
    if category is None or amount is None:
        send_message(
            chat_id,
            "Pakai: /budget <kategori> <angka> (0 = hapus budget)\n"
            "Contoh: /budget Food 1500000\n\n"
            "Kategori: " + ", ".join(c for c in CATEGORIES if c != "Outlier"),
        )
        return
    set_budget(category, amount)
    if not amount:
        send_message(chat_id, f"Budget {category} dihapus.")
        return
    status = budget_status(fetch_report_data(), {category: amount})[0]
    send_message(chat_id, f"Budget {category} di-set Rp{amount:,}/bulan\n" + budget_line(status))


def cmd_recurring(chat_id, args):
    """
    /recurring                 -> daftar transaksi rutin
    /recurring add <tgl>, ...  -> tambah, sisanya format cepat (akun wajib)
    /recurring del <id>        -> hapus
    """
    sub = args[0].lower() if args else ""
    usage = (
        "Pakai:\n"
        "/recurring - daftar transaksi rutin\n"
        "/recurring add <tanggal>, deskripsi, amount, tipe, kategori, akun\n"
        "/recurring del <id>\n\n"
        "Contoh: /recurring add 25, Kos, 1500000, expense, Rent, mega\n"
        "Dicatet otomatis tiap bulan di tanggal itu (29-31 di bulan pendek = hari terakhir)."
    )
    conn = sqlite3.connect(DB_PATH)
    if sub == "add":
        text = " ".join(args[1:])
        day_raw, _, rest = text.partition(",")
        quick = parse_quick_input(rest) if rest else None
        if not day_raw.strip().isdigit() or not 1 <= int(day_raw) <= 31 or quick is None or quick[5] is None:
            conn.close()
            send_message(chat_id, "Format ga kebaca (akun wajib ditulis).\n\n" + usage)
            return
        day = int(day_raw)
        description, quantity, amount, tx_type, category, account = quick
        now = datetime.now()
        # tanggalnya udah lewat bulan ini: dianggap udah dicatet manual, mulai bulan depan
        already_due = min(day, calendar.monthrange(now.year, now.month)[1]) <= now.day
        cur = conn.execute(
            "INSERT INTO recurring (chat_id, day, description, quantity, amount, type, category, account, last_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, day, description, quantity, amount, tx_type, category, account,
             now.strftime("%Y%m") if already_due else None),
        )
        conn.commit()
        conn.close()
        start = "mulai bulan depan (bulan ini catet manual kalau belum)" if already_due else "mulai bulan ini"
        send_message(
            chat_id,
            f"Transaksi rutin #{cur.lastrowid} tersimpan ✅\n"
            f"Tiap tanggal {day}: {description} | Rp{amount:,} | {tx_type} | {category} | {account}\n{start}",
        )
        return
    if sub == "del":
        rec_id = args[1] if len(args) > 1 else ""
        deleted = conn.execute("DELETE FROM recurring WHERE id = ?", (rec_id,)).rowcount if rec_id.isdigit() else 0
        conn.commit()
        conn.close()
        send_message(chat_id, f"Transaksi rutin #{rec_id} dihapus." if deleted else f"#{rec_id} ga ketemu.\n\n" + usage)
        return
    rows = conn.execute(
        "SELECT id, day, description, amount, type, category, account, last_run FROM recurring ORDER BY day"
    ).fetchall()
    conn.close()
    if sub or not rows:
        send_message(chat_id, ("Belum ada transaksi rutin.\n\n" if not sub else "") + usage)
        return
    lines = ["Transaksi rutin:"]
    for rec_id, day, description, amount, tx_type, category, account, last_run in rows:
        sign = "-" if tx_type == "expense" else "+"
        last = f" | terakhir {last_run[4:]}/{last_run[:4]}" if last_run else ""
        lines.append(f"#{rec_id} tgl {day} | {sign}Rp{amount:,} | {category} | {account} | {description}{last}")
    lines.append(f"\nTotal rutin per bulan: expense Rp{sum(r[3] for r in rows if r[4] == 'expense'):,}, "
                 f"income Rp{sum(r[3] for r in rows if r[4] != 'expense'):,}")
    send_message(chat_id, "\n".join(lines))


def cmd_search(chat_id, args):
    """/search <kata> -> transaksi yang deskripsinya mengandung kata itu: total per periode + 10 terakhir."""
    keyword = " ".join(args).strip().lower()
    if not keyword:
        send_message(chat_id, "Pakai: /search <kata>, contoh: /search kopi")
        return
    matches = [r for r in fetch_report_data() if keyword in (r["description"] or "").lower()]
    if not matches:
        send_message(chat_id, f"Ga ada transaksi yang mengandung \"{keyword}\".")
        return
    now = datetime.now()
    this_month = now.strftime("%Y%m")
    last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")

    def total(rows):
        return sum(r["amount"] or 0 for r in rows), len(rows)

    lines = [f"\"{keyword}\": {len(matches)} transaksi"]
    for label, rows in (
        ("Bulan ini", [r for r in matches if r["created_at"].strftime("%Y%m") == this_month]),
        ("Bulan lalu", [r for r in matches if r["created_at"].strftime("%Y%m") == last_month]),
        ("Semua", matches),
    ):
        amount, count = total([r for r in rows if r["type"] == "expense"])
        if count:
            lines.append(f"{label}: expense Rp{amount:,} ({count}x)")
        amount, count = total([r for r in rows if r["type"] != "expense"])
        if count:
            lines.append(f"{label}: income Rp{amount:,} ({count}x)")
    lines.append("\n10 terakhir:")
    for r in sorted(matches, key=lambda x: x["created_at"], reverse=True)[:10]:
        sign = "-" if r["type"] == "expense" else "+"
        account_txt = f" | {r['account']}" if r["account"] else ""
        lines.append(f"{r['created_at']:%Y-%m-%d} | {sign}Rp{r['amount'] or 0:,} | {r['category']}{account_txt} | "
                     f"{r['description']} | {r['id']}")
    send_message(chat_id, "\n".join(lines))


def cmd_trend(chat_id, args):
    """/trend [n] -> gambar income vs expense dan saving % n bulan terakhir (default 12)."""
    try:
        months = max(2, min(int(args[0]), 36)) if args else 12
    except ValueError:
        months = 12
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "trend.png")
        count = generate_trend_png(fetch_report_data(), path, months)
        if not count:
            send_message(chat_id, "Belum ada data transaksi.")
            return
        send_file(chat_id, "sendPhoto", "photo", path, "image/png",
                  caption=f"Tren {count} bulan terakhir (outlier ga dihitung)")


COMMANDS = {
    "/add": cmd_add,
    "/cancel": cmd_cancel,
    "/balance": cmd_balance,
    "/setbalance": cmd_setbalance,
    "/last": cmd_last,
    "/report": cmd_report,
    "/weekly": cmd_weekly,
    "/insight": cmd_insight,
    "/transfer": cmd_transfer,
    "/undo": cmd_undo,
    "/delete": cmd_delete,
    "/edit": cmd_edit,
    "/budget": cmd_budget,
    "/recurring": cmd_recurring,
    "/search": cmd_search,
    "/trend": cmd_trend,
}

HELP_TEXT = (
    "Command:\n"
    "/add - tambah transaksi (pilih tipe, akun, kategori, amount, deskripsi)\n"
    "/cancel - batalin /add\n"
    "/balance [n] - saldo tiap akun + n perubahan terakhir\n"
    "/setbalance <akun> <angka> - koreksi saldo akun, contoh: /setbalance cash 250000\n"
    "/last [n] - lihat n transaksi terakhir\n"
    "/report - laporan Excel\n"
    "/weekly - gambar expense 7 hari terakhir\n"
    "/insight [YYYYMM] - insight bulan ini/bulan tertentu\n"
    "/trend [n] - gambar tren income/expense n bulan terakhir\n"
    "/transfer <dari> <ke> <amount> - pindah saldo antar akun\n"
    "/undo - hapus transaksi terakhir\n"
    "/delete <id> - hapus transaksi/transfer\n"
    "/edit <id> <field> <nilai> - ubah transaksi\n"
    "/budget [kategori angka] - lihat/set budget bulanan\n"
    "/recurring - transaksi rutin bulanan\n"
    "/search <kata> - cari transaksi\n\n"
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
        # dicek tiap putaran (paling lama ~50 detik sekali karena long polling)
        try:
            for chat_id, tx in run_recurring():
                save_transaction(chat_id, tx, header="Transaksi rutin dicatet otomatis 🔁")
        except Exception as e:
            print(f"Error transaksi rutin: {e!r}")
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
