import os
import sqlite3
import requests
from datetime import datetime
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side
from openpyxl.chart import PieChart, Reference, LineChart
from openpyxl.formatting.rule import ColorScaleRule

# Load environment variables
load_dotenv()

DB_PATH = os.getenv("DB_PATH")
API_TOKEN = os.getenv("API_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{API_TOKEN}/sendDocument"


# ---------- Database ----------
def fetch_data_from_db(db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions")
        return cursor.fetchall()


# ---------- Excel Helpers ----------
def add_borders(ws):
    thin = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    for row in ws.iter_rows():
        for cell in row:
            cell.border = thin


def auto_resize_columns(ws):
    for col in ws.columns:
        max_length = max(
            (len(str(cell.value)) for cell in col if cell.value),
            default=0
        )
        ws.column_dimensions[col[0].column_letter].width = max_length + 2


# ---------- Excel Report ----------
def generate_excel_report(data, report_file):
    wb = Workbook()
    headers = [
        "id", "type", "category", "quantity",
        "amount", "description", "created_at", "is_outlier"
    ]

    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="ADD8E6")

    # Summary sheet
    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws_summary.append(["Month", "Income", "Expense (Clean)", "Expense (Outlier)"])

    month_data = {}
    monthly_totals = {}

    for row in data:
        month = datetime.strptime(row[6], "%Y-%m-%d %H:%M:%S").strftime("%Y%m")
        month_data.setdefault(month, []).append(row)
        monthly_totals.setdefault(month, {"income": 0, "clean": 0, "outlier": 0})

        amount = row[4]
        is_outlier = row[7] or 0

        if row[1] == "expense":
            key = "outlier" if is_outlier else "clean"
            monthly_totals[month][key] += amount
        else:
            monthly_totals[month]["income"] += amount

    for month, t in sorted(monthly_totals.items()):
        ws_summary.append([month, t["income"], t["clean"], t["outlier"]])

    ws_summary.auto_filter.ref = ws_summary.dimensions
    add_borders(ws_summary)
    auto_resize_columns(ws_summary)

    # Conditional formatting
    current_month = datetime.now().strftime("%Y%m")
    last_row = max(
        r for r in range(2, ws_summary.max_row + 1)
        if ws_summary.cell(r, 1).value <= current_month
    )

    green_red = ColorScaleRule(
        start_type="min", start_color="63BE7B",
        mid_type="percentile", mid_value=50, mid_color="FFEB84",
        end_type="max", end_color="F8696B"
    )

    ws_summary.conditional_formatting.add(f"B2:B{last_row}", green_red)
    ws_summary.conditional_formatting.add(f"C2:D{last_row}", green_red)

    # Monthly sheets
    for month, rows in sorted(month_data.items()):
        ws = wb.create_sheet(month)
        ws.append(headers)

        for c in ws[1]:
            c.font = header_font
            c.fill = header_fill

        for r in rows:
            ws.append(r)

        ws.auto_filter.ref = ws.dimensions
        auto_resize_columns(ws)
        add_borders(ws)

        # Expense pie chart (non-outliers)
        expenses = {}
        for r in rows:
            if r[1] == "expense" and not r[7]:
                expenses[r[2]] = expenses.get(r[2], 0) + r[4]

        if expenses:
            start_col = 10
            ws.cell(2, start_col, "Category").font = header_font
            ws.cell(2, start_col + 1, "Amount").font = header_font

            for i, (cat, amt) in enumerate(expenses.items(), start=3):
                ws.cell(i, start_col, cat)
                ws.cell(i, start_col + 1, amt)

            pie = PieChart()
            pie.title = "Expense Breakdown"
            pie.add_data(
                Reference(ws, start_col + 1, 2, start_col + 1, 2 + len(expenses)),
                titles_from_data=True
            )
            pie.set_categories(
                Reference(ws, start_col, 3, start_col, 2 + len(expenses))
            )
            ws.add_chart(pie, "L2")

    # Line chart
    chart = LineChart()
    chart.title = "Income vs Expense"
    chart.y_axis.title = "Amount"
    chart.x_axis.title = "Month"

    chart.add_data(
        Reference(ws_summary, 2, 1, 3, ws_summary.max_row),
        titles_from_data=True
    )
    chart.set_categories(
        Reference(ws_summary, 1, 2, 1, ws_summary.max_row)
    )

    ws_summary.add_chart(chart, "F2")

    wb.save(report_file)


# ---------- Telegram ----------
def send_report_to_telegram(report_file):
    with open(report_file, "rb") as f:
        requests.post(
            TELEGRAM_API_URL,
            data={"chat_id": ALLOWED_USER_ID},
            files={"document": f},
            timeout=30
        )

# ---------- Main ----------
def main():
    report_file = "transactions_report.xlsx"

    data = fetch_data_from_db(DB_PATH)
    generate_excel_report(data, report_file)
    send_report_to_telegram(report_file)
    os.remove(report_file)

if __name__ == "__main__":
    main()
