#!/usr/bin/env python3
"""
Generate gambar PNG tren bulanan (outlier ga dihitung di expense):
atas income vs expense per bulan, bawah saving % per bulan.

Pakai:
  python g_trend.py              -> 12 bulan terakhir ke trend.png
  python g_trend.py hasil.png 6  -> 6 bulan terakhir ke file yang ditentuin
"""

import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # ga butuh layar, cukup render ke file
import matplotlib.pyplot as plt

from g_last_r import DB_PATH, fetch_report_data

MONTHS = 12


def monthly_trend(data, months=MONTHS, today=None):
    """Return list (YYYYMM, income, expense) n bulan terakhir sampe bulan berjalan, bulan kosong diisi 0."""
    today = today or datetime.now()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y}{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    keys.reverse()
    totals = {k: [0, 0] for k in keys}
    for row in data:
        k = row["created_at"].strftime("%Y%m")
        if k not in totals:
            continue
        amount = row["amount"] or 0
        if row["type"] != "expense":
            totals[k][0] += amount
        elif not row["is_outlier"]:
            totals[k][1] += amount
    # buang bulan kosong di depan (sebelum ada data sama sekali)
    while keys and totals[keys[0]] == [0, 0]:
        keys.pop(0)
    return [(k, *totals[k]) for k in keys]


def saving_color(pct):
    # ambang sama kayak tabel Summary di laporan HTML
    if pct >= 30:
        return "#2E7D32"
    if pct >= 10:
        return "#F57C00"
    return "#C62828"


def generate_trend_png(data, out_path, months=MONTHS, today=None):
    """Tulis PNG ke out_path. Return jumlah bulan yang digambar (0 = ga ada data, file ga dibuat)."""
    trend = monthly_trend(data, months, today)
    if not trend:
        return 0

    labels = [f"{k[4:]}/{k[2:4]}" for k, _, _ in trend]
    incomes = [i for _, i, _ in trend]
    expenses = [e for _, _, e in trend]
    savings = [(i - e) / i * 100 if i else 0 for i, e in zip(incomes, expenses)]

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [3, 2]},
    )
    fig.suptitle(f"Tren {len(trend)} bulan terakhir (outlier ga dihitung)", fontweight="bold")

    x = range(len(trend))
    width = 0.4
    ax_top.bar([i - width / 2 for i in x], incomes, width, color="#2E7D32", label="Income")
    ax_top.bar([i + width / 2 for i in x], expenses, width, color="#C62828", label="Expense")
    avg = sum(expenses) / len(expenses)
    ax_top.axhline(avg, color="#555", linestyle="--", linewidth=1, label=f"Rata-rata expense Rp{avg:,.0f}")
    ax_top.yaxis.set_major_formatter(lambda v, _: f"{v / 1e6:,.1f} jt")
    ax_top.spines[["top", "right"]].set_visible(False)
    ax_top.legend(frameon=False, loc="upper left", ncol=3)
    ax_top.margins(y=0.15)

    bars = ax_bot.bar(x, savings, color=[saving_color(s) for s in savings])
    ax_bot.bar_label(bars, labels=[f"{s:.0f}%" for s in savings], fontsize=9, padding=2)
    ax_bot.axhline(0, color="#999", linewidth=0.8)
    ax_bot.set_ylabel("Saving %")
    ax_bot.spines[["top", "right"]].set_visible(False)
    ax_bot.set_xticks(list(x), labels)
    ax_bot.margins(y=0.2)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return len(trend)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "trend.png"
    months = int(sys.argv[2]) if len(sys.argv) > 2 else MONTHS
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"{DB_PATH} ga ketemu")
    if not generate_trend_png(fetch_report_data(), out_path, months):
        raise SystemExit("Belum ada data transaksi.")
    print(f"Gambar tersimpan: {out_path}")


if __name__ == "__main__":
    main()
