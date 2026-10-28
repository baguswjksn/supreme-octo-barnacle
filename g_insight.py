#!/usr/bin/env python3
"""
Insight keuangan satu bulan dalam bentuk teks singkat (outlier ga dihitung di expense):
ringkasan income/expense/saving, perbandingan sama bulan sebelumnya di periode yang sama,
proyeksi expense akhir bulan, top kategori, kategori yang naik/turun paling banyak,
transaksi terbesar, dan outlier.

Pakai:
  python g_insight.py           -> insight bulan berjalan
  python g_insight.py 202609    -> insight bulan tertentu (YYYYMM)
"""

import os
import sys
import calendar
from datetime import datetime

from g_last_r import DB_PATH, fetch_report_data

MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
TOP_N = 5


def prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def month_label(year, month):
    return f"{MONTHS[month - 1]} {year}"


def pct_change(now, before):
    """Teks perubahan persen, misal "+12%" / "-5%". None kalau pembandingnya 0."""
    if not before:
        return None
    return f"{(now - before) / before * 100:+.0f}%"


def summarize(rows):
    """Total income, expense (outlier excluded), outlier, dan expense per kategori dari list transaksi."""
    s = {"income": 0, "expense": 0, "outlier": 0, "outlier_count": 0, "categories": {}}
    for r in rows:
        amount = r["amount"] or 0
        if r["type"] != "expense":
            s["income"] += amount
        elif r["is_outlier"]:
            s["outlier"] += amount
            s["outlier_count"] += 1
        else:
            s["expense"] += amount
            cat = r["category"] or "(tanpa kategori)"
            s["categories"][cat] = s["categories"].get(cat, 0) + amount
    return s


def rows_in(data, year, month, last_day=None):
    """Transaksi di bulan itu, kalau last_day diisi cuma tanggal 1 sampe last_day."""
    return [
        r for r in data
        if r["created_at"].year == year and r["created_at"].month == month
        and (last_day is None or r["created_at"].day <= last_day)
    ]


def budget_status(data, budgets, today=None):
    """
    Pemakaian budget bulan berjalan. budgets = {kategori: batas per bulan}.
    Return list dict (category, spent, limit, pct, projection) urut kategori, projection = kalau
    kecepatan belanja sampe hari ini diterusin sampe akhir bulan.
    """
    today = today or datetime.now()
    if isinstance(today, datetime):
        today = today.date()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    spent = summarize(rows_in(data, today.year, today.month, today.day))["categories"]
    result = []
    for cat, limit in sorted(budgets.items()):
        used = spent.get(cat, 0)
        result.append({
            "category": cat, "spent": used, "limit": limit,
            "pct": used / limit * 100 if limit else 0,
            "projection": used / today.day * days_in_month,
        })
    return result


def budget_line(b):
    """Satu baris status budget, misal "Food: Rp1,200,000 / Rp1,500,000 (80%) ⚠️"."""
    if b["spent"] > b["limit"]:
        flag = f" 🚨 lewat Rp{b['spent'] - b['limit']:,}"
    elif b["pct"] >= 80:
        flag = " ⚠️"
    elif b["projection"] > b["limit"]:
        flag = f" (proyeksi Rp{b['projection']:,.0f}, bakal lewat)"
    else:
        flag = ""
    return f"{b['category']}: Rp{b['spent']:,} / Rp{b['limit']:,} ({b['pct']:.0f}%){flag}"


def monthly_insight(data, year=None, month=None, today=None, budgets=None):
    """
    Return teks insight buat bulan year/month (default bulan berjalan).
    Bulan berjalan dibandingin sama bulan lalu di tanggal yang sama (1 sampe hari ini),
    biar adil; bulan yang udah lewat dibandingin full sebulan.
    """
    today = (today or datetime.now()).date()
    year, month = (year, month) if year and month else (today.year, today.month)
    days_in_month = calendar.monthrange(year, month)[1]
    is_current = (year, month) == (today.year, today.month)
    if (year, month) > (today.year, today.month):
        return f"{month_label(year, month)} belum jalan."

    # periode bulan ini: sampe hari ini kalau bulan berjalan, full kalau udah lewat
    day_limit = today.day if is_current else days_in_month
    py, pm = prev_month(year, month)
    prev_limit = min(day_limit, calendar.monthrange(py, pm)[1]) if is_current else None

    rows = rows_in(data, year, month, day_limit)
    cur = summarize(rows)
    prev = summarize(rows_in(data, py, pm, prev_limit))

    if not rows:
        return f"Belum ada transaksi di {month_label(year, month)}."

    period = f"1-{day_limit} {MONTHS[month - 1]}"
    prev_period = f"1-{prev_limit} {MONTHS[pm - 1]}" if is_current else month_label(py, pm)

    saving = cur["income"] - cur["expense"]
    lines = [f"Insight {month_label(year, month)} ({period})", ""]
    lines.append(f"Income: Rp{cur['income']:,}")
    lines.append(f"Expense: Rp{cur['expense']:,}")
    if cur["income"]:
        lines.append(f"Saving: Rp{saving:,} ({saving / cur['income'] * 100:.1f}% dari income)")
    else:
        lines.append(f"Saving: Rp{saving:,} (belum ada income)")

    # ---------- dibanding bulan lalu ----------
    lines += ["", f"Dibanding {prev_period}:"]
    for label, key in (("Expense", "expense"), ("Income", "income")):
        diff = cur[key] - prev[key]
        change = pct_change(cur[key], prev[key])
        change_txt = f" ({change})" if change else ""
        lines.append(f"{label}: Rp{prev[key]:,} -> Rp{cur[key]:,}, {diff:+,}{change_txt}")

    # ---------- rata-rata & proyeksi ----------
    daily_avg = cur["expense"] / day_limit
    lines += ["", f"Rata-rata expense: Rp{daily_avg:,.0f}/hari"]
    if is_current and day_limit < days_in_month:
        projection = daily_avg * days_in_month
        lines.append(f"Proyeksi expense akhir bulan: Rp{projection:,.0f}")
        if cur["income"]:
            lines.append(f"Proyeksi saving akhir bulan: Rp{cur['income'] - projection:,.0f} "
                         f"(kalau ga ada income tambahan)")

    # rata-rata expense 3 bulan full sebelumnya, patokan "normal"-nya berapa
    history = []
    hy, hm = year, month
    for _ in range(3):
        hy, hm = prev_month(hy, hm)
        history.append(summarize(rows_in(data, hy, hm))["expense"])
    history = [h for h in history if h]
    if history:
        avg_3m = sum(history) / len(history)
        compare = daily_avg * days_in_month if is_current else cur["expense"]
        verdict = "di atas" if compare > avg_3m else "di bawah"
        lines.append(f"Rata-rata expense {len(history)} bulan sebelumnya: Rp{avg_3m:,.0f}/bulan "
                     f"({'proyeksi' if is_current else 'bulan ini'} {verdict} rata-rata, "
                     f"{pct_change(compare, avg_3m)})")

    # ---------- kategori ----------
    if cur["categories"]:
        lines += ["", "Top kategori:"]
        top = sorted(cur["categories"].items(), key=lambda kv: -kv[1])[:TOP_N]
        for i, (cat, amount) in enumerate(top, 1):
            share = amount / cur["expense"] * 100
            change = pct_change(amount, prev["categories"].get(cat, 0))
            change_txt = f", {change} vs bulan lalu" if change else ", baru bulan ini"
            lines.append(f"{i}. {cat}: Rp{amount:,} ({share:.0f}%{change_txt})")

    diffs = {
        cat: cur["categories"].get(cat, 0) - prev["categories"].get(cat, 0)
        for cat in set(cur["categories"]) | set(prev["categories"])
    }
    if diffs:
        up_cat, up = max(diffs.items(), key=lambda kv: kv[1])
        down_cat, down = min(diffs.items(), key=lambda kv: kv[1])
        if up > 0 or down < 0:
            lines.append("")
        if up > 0:
            lines.append(f"Naik paling banyak: {up_cat} {up:+,}")
        if down < 0:
            lines.append(f"Turun paling banyak: {down_cat} {down:+,}")

    # ---------- transaksi terbesar & outlier ----------
    expenses = [r for r in rows if r["type"] == "expense" and not r["is_outlier"]]
    if expenses:
        biggest = max(expenses, key=lambda r: r["amount"] or 0)
        when = biggest["created_at"]
        lines += ["", f"Expense terbesar: {biggest['description']} Rp{biggest['amount'] or 0:,} "
                      f"({biggest['category']}, {when.day} {MONTHS[when.month - 1]})"]
        no_spend = day_limit - len({r["created_at"].day for r in expenses})
        lines.append(f"Hari tanpa expense: {no_spend} dari {day_limit} hari")
    if cur["outlier_count"]:
        lines.append(f"Outlier (ga dihitung di atas): Rp{cur['outlier']:,} dari {cur['outlier_count']} transaksi")

    # budget cuma relevan buat bulan berjalan (batasnya per bulan, ga ada riwayat)
    if is_current and budgets:
        lines += ["", "Budget bulan ini:"]
        lines += [budget_line(b) for b in budget_status(data, budgets, today)]

    return "\n".join(lines)


def parse_month(text):
    """"202609" atau "2026-09" -> (2026, 9). None kalau formatnya salah."""
    digits = text.strip().replace("-", "").replace("/", "")
    if len(digits) != 6 or not digits.isdigit():
        return None
    year, month = int(digits[:4]), int(digits[4:])
    if not 1 <= month <= 12:
        return None
    return year, month


def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"{DB_PATH} ga ketemu")
    year = month = None
    if len(sys.argv) > 1:
        parsed = parse_month(sys.argv[1])
        if parsed is None:
            raise SystemExit("Format bulan: YYYYMM, contoh 202609")
        year, month = parsed
    print(monthly_insight(fetch_report_data(), year, month))


if __name__ == "__main__":
    main()
