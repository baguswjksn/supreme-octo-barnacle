#!/usr/bin/env python3
"""
Generate gambar PNG expense 7 hari terakhir (outlier ga dihitung):
kiri bar chart harian, kanan donut per kategori.

Pakai:
  python g_weekly_e.py              -> tulis ke weekly_expense.png
  python g_weekly_e.py hasil.png    -> tulis ke file yang ditentuin
"""

import os
import sys
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")  # ga butuh layar, cukup render ke file
import matplotlib.pyplot as plt

from g_last_r import DB_PATH, PALETTE, fetch_report_data

DAYS = 7


def weekly_expense(data, today=None):
    """Return (days, daily_totals, category_totals) buat 7 hari terakhir termasuk hari ini."""
    today = (today or datetime.now()).date()
    days = [today - timedelta(days=i) for i in range(DAYS - 1, -1, -1)]
    daily = {d: 0 for d in days}
    categories = {}
    for row in data:
        d = row["created_at"].date()
        if row["type"] != "expense" or row["is_outlier"] or d not in daily:
            continue
        amount = row["amount"] or 0
        daily[d] += amount
        cat = row["category"] or "(tanpa kategori)"
        categories[cat] = categories.get(cat, 0) + amount
    return days, [daily[d] for d in days], categories


def generate_weekly_png(data, out_path, today=None):
    """Tulis PNG ke out_path. Return total expense minggu ini (0 = ga ada data, file ga dibuat)."""
    days, daily, categories = weekly_expense(data, today)
    total = sum(daily)
    if total == 0:
        return 0

    fig, (ax_bar, ax_pie) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [3, 2]})
    fig.suptitle(f"Expense {days[0]:%d %b} - {days[-1]:%d %b %Y}  |  Total Rp{total:,.0f}", fontweight="bold")

    labels = [f"{d:%a}\n{d:%d/%m}" for d in days]
    bars = ax_bar.bar(labels, daily, color="#4E79A7")
    ax_bar.axhline(total / DAYS, color="#C62828", linestyle="--", linewidth=1,
                   label=f"Rata-rata Rp{total / DAYS:,.0f}/hari")
    ax_bar.bar_label(bars, labels=[f"{v:,.0f}" if v else "" for v in daily], fontsize=9, padding=2)
    ax_bar.set_title("Harian")
    ax_bar.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    ax_bar.spines[["top", "right"]].set_visible(False)
    ax_bar.legend(frameon=False, loc="upper left")
    ax_bar.margins(y=0.15)

    items = sorted(categories.items(), key=lambda kv: -kv[1])
    values = [v for _, v in items]
    ax_pie.pie(values, colors=PALETTE[:len(values)], startangle=90, counterclock=False,
               wedgeprops={"width": 0.4, "edgecolor": "white"})
    ax_pie.text(0, 0, f"Rp{total:,.0f}", ha="center", va="center", fontsize=12, fontweight="bold")
    ax_pie.legend(
        [f"{c} - Rp{v:,.0f} ({v / total * 100:.0f}%)" for c, v in items],
        loc="center left", bbox_to_anchor=(1, 0.5), frameon=False, fontsize=9,
    )
    ax_pie.set_title("Per Kategori")

    fig.tight_layout()
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return total


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "weekly_expense.png"
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"{DB_PATH} ga ketemu")
    if not generate_weekly_png(fetch_report_data(), out_path):
        raise SystemExit("Ga ada expense 7 hari terakhir.")
    print(f"Gambar tersimpan: {out_path}")


if __name__ == "__main__":
    main()
