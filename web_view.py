#!/usr/bin/env python3
"""
Web view lokal buat transactions.db: ringkasan, analisis pengeluaran, analisis lanjutan,
dan penjelajah data. Tampilannya di web_view.html, perhitungannya di g_analysis.py.

Pakai:
  python web_view.py              -> http://127.0.0.1:8765, langsung kebuka di browser
  python web_view.py 9000         -> pakai port lain
  python web_view.py --no-open    -> ga usah buka browser

Data keuangan ini sensitif, jadi server cuma dengerin 127.0.0.1 (ga kebuka ke jaringan),
database dibuka read-only, dan request dengan Host selain localhost ditolak.
"""

import json
import os
import sqlite3
import sys
import traceback
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from g_analysis import NO_ACCOUNT, advanced, dashboard
from g_last_r import DB_PATH, parse_created

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_view.html")
DEFAULT_DAYS = 365

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
    ),
}


def connect():
    """Koneksi read-only, jadi web view ga mungkin ngubah data, walaupun bot lagi jalan."""
    return sqlite3.connect(f"{Path(DB_PATH).as_uri()}?mode=ro", uri=True)


def table_names(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def load_transactions(conn):
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM transactions ORDER BY created").fetchall()
    conn.row_factory = None
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "category": r["category"],
            "quantity": r["quantity"],
            "amount": r["amount"],
            "description": r["description"],
            "created_at": parse_created(r["created"]),
            "is_outlier": r["is_outlier"],
            "account": r["account"] if "account" in r.keys() else None,
        }
        for r in rows
    ]


def load_history(conn, tables):
    if "balance_history" not in tables:
        return []
    rows = conn.execute("SELECT account, created, balance, note FROM balance_history ORDER BY id").fetchall()
    return [
        {"account": account, "created_at": parse_created(created), "balance": balance, "note": note}
        for account, created, balance, note in rows
    ]


def load_budgets(conn, tables):
    if "budgets" not in tables:
        return {}
    return dict(conn.execute("SELECT category, amount FROM budgets").fetchall())


def parse_date(value, name):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Parameter {name} harus berformat YYYY-MM-DD") from None


def api_overview(params):
    with connect() as conn:
        tables = table_names(conn)
        data = load_transactions(conn)
        history = load_history(conn, tables)
    today = date.today()
    first = min((r["created_at"].date() for r in data), default=today)

    # default ga lewat hari ini: transaksi bertanggal masa depan ga ikut kehitung sebagai sudah terjadi
    end = parse_date(params["to"], "to") if params.get("to") else today
    if params.get("from"):
        start = parse_date(params["from"], "from")
    elif "from" in params:
        start = first
    else:
        start = today - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise ValueError("Tanggal awal setelah tanggal akhir")

    accounts = sorted({r["account"] for r in data if r["account"]} | {h["account"] for h in history})
    result = dashboard(data, start, end, params.get("account") or None, params.get("outliers") == "1")
    result["accounts"] = accounts
    result["no_account_value"] = NO_ACCOUNT
    result["first_date"] = first.isoformat()
    return result


def api_advanced(params):
    with connect() as conn:
        tables = table_names(conn)
        data = load_transactions(conn)
        history = load_history(conn, tables)
        budgets = load_budgets(conn, tables)
    return advanced(data, history, budgets)


def api_tables(params):
    """Semua tabel apa adanya. Sama kayak /backup: tabel baru otomatis ikut."""
    with connect() as conn:
        result = {}
        for table in table_names(conn):
            cursor = conn.execute(f'SELECT * FROM "{table}"')
            result[table] = {
                "columns": [column[0] for column in cursor.description],
                "rows": cursor.fetchall(),
            }
    return result


ROUTES = {
    "/api/overview": api_overview,
    "/api/advanced": api_advanced,
    "/api/tables": api_tables,
}


class Handler(BaseHTTPRequestHandler):
    allowed_hosts = set()

    def send_body(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_body(status, "application/json; charset=utf-8", body)

    def do_GET(self):
        # Halaman web lain bisa ngarahin nama domainnya ke 127.0.0.1 (DNS rebinding),
        # jadi Host-nya dicek biar data ga kebaca dari situ.
        if self.headers.get("Host") not in self.allowed_hosts:
            self.send_json(403, {"error": "Host tidak diizinkan"})
            return

        url = urlsplit(self.path)
        if url.path == "/":
            with open(PAGE_PATH, "rb") as f:
                self.send_body(200, "text/html; charset=utf-8", f.read())
            return

        route = ROUTES.get(url.path)
        if route is None:
            self.send_json(404, {"error": "Tidak ditemukan"})
            return
        params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        try:
            self.send_json(200, route(params))
        except ValueError as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self.send_json(500, {"error": f"Gagal memproses data: {e}"})

    def log_message(self, format, *args):
        pass  # log per request cuma bikin terminal rame


def main():
    args = sys.argv[1:]
    open_browser = "--no-open" not in args
    ports = [a for a in args if a.isdigit()]
    port = int(ports[0]) if ports else DEFAULT_PORT

    if not os.path.exists(DB_PATH):
        raise SystemExit(f"{DB_PATH} ga ketemu")

    Handler.allowed_hosts = {f"{HOST}:{port}", f"localhost:{port}"}
    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as e:
        raise SystemExit(f"Port {port} ga bisa dipakai ({e}). Coba: python web_view.py {port + 1}")

    url = f"http://{HOST}:{port}"
    print(f"Web view jalan di {url}  (Ctrl+C buat stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb view di-stop.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
