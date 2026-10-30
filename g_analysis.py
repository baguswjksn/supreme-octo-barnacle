#!/usr/bin/env python3
"""
Analisis keuangan buat web view (web_view.py). Semua fungsi di sini murni: nerima list
transaksi (format fetch_report_data, plus "account") dan balikin dict siap jadi JSON.

Dua bagian utama:
  dashboard() -> ringkasan satu periode, kena filter tanggal/akun/outlier
  advanced()  -> analisis jangka panjang, selalu dari seluruh riwayat sampai hari ini

Aturan yang dipakai di semua analisis:
  - Expense ga ngitung outlier, kecuali diminta (sama kayak laporan & /insight).
  - Transaksi bertanggal setelah hari ini ga ikut analisis jangka panjang, dilaporin
    terpisah di data_quality.
  - Patokan "normal" cuma dari bulan lengkap, mulai bulan pertama yang datanya beneran
    kecatet (lihat ACTIVE_MONTH_MIN_TX), jadi bulan awal yang isinya 2-3 transaksi ga
    narik rata-rata ke bawah.
"""

import calendar
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from g_insight import MONTHS, budget_status, summarize

ACTIVE_MONTH_MIN_TX = 20    # bulan dengan transaksi segini ke atas dianggap kecatet beneran
BASELINE_MONTHS = 6         # patokan pengeluaran normal & runway
TREND_MONTHS = 12           # jendela tren, biaya rutin, stabilitas income
FORECAST_MONTHS = 12        # bulan pembanding buat proyeksi akhir bulan
ANOMALY_WINDOW_DAYS = 180
ANOMALY_Z = 3.5             # ambang robust z-score yang umum dipakai
PAYDAY_MIN_INCOME = 1_000_000
BUDGET_ROUND = 50_000
NO_ACCOUNT = "__none__"     # nilai filter buat transaksi lama yang belum punya akun

# Biaya tetap yang jatuh temponya sekali sebulan: kalau ikut dihitung, tanggal bayarnya
# bakal selalu kebaca sebagai lonjakan.
FIXED_CATEGORIES = {"Rent"}

# Inflasi cuma dibaca dari barang harian yang harganya bisa dibandingin. Kost, bensin, buku, dll
# nominalnya berubah karena jumlah/jenisnya beda, bukan karena harganya naik.
PRICE_CATEGORIES = {"Food", "Water"}

WEEKDAYS = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
WEEKDAY_NAMES = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

SIZE_BINS = [
    (0, 5_000, "< 5 rb"),
    (5_000, 10_000, "5-10 rb"),
    (10_000, 25_000, "10-25 rb"),
    (25_000, 50_000, "25-50 rb"),
    (50_000, 100_000, "50-100 rb"),
    (100_000, 250_000, "100-250 rb"),
    (250_000, 500_000, "250-500 rb"),
    (500_000, None, "> 500 rb"),
]

_MONTH_WORDS = (
    "januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember|"
    "january|february|march|may|june|july|august|october|december"
)


# ---------- Helper umum ----------

def rp(value):
    sign = "-" if value < 0 else ""
    return f"{sign}Rp{abs(value):,.0f}".replace(",", ".")


def dec(value, digits=1):
    """Desimal gaya Indonesia, biar sama kayak angka di tampilan: 68,5."""
    return f"{value:.{digits}f}".replace(".", ",")


def month_key(when):
    return when.strftime("%Y%m")


def month_label(key, short=False):
    year, month = key[:4], int(key[4:])
    return f"{MONTHS[month - 1]} {year[2:] if short else year}"


def next_month_key(key):
    year, month = int(key[:4]), int(key[4:])
    return f"{year + 1}01" if month == 12 else f"{year}{month + 1:02d}"


def month_keys_between(first, last):
    keys = []
    key = first
    while key <= last:
        keys.append(key)
        key = next_month_key(key)
    return keys


def quantile(values, q):
    """Kuantil interpolasi linear. values boleh belum urut, list kosong -> 0."""
    ordered = sorted(values)
    if not ordered:
        return 0
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def pct_diff(now, before):
    """Perubahan persen, None kalau pembandingnya 0 (ga bisa dihitung)."""
    if not before:
        return None
    return (now - before) / before * 100


def linear_fit(values):
    """Regresi linear y terhadap urutan 0..n-1. Return (slope, r2)."""
    n = len(values)
    if n < 2:
        return 0, 0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    ss_xx = sum((i - mean_x) ** 2 for i in range(n))
    ss_xy = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    ss_yy = sum((v - mean_y) ** 2 for v in values)
    slope = ss_xy / ss_xx
    r2 = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy else 0
    return slope, r2


def spend_rows(rows, include_outliers=False):
    return [r for r in rows if r["type"] == "expense" and (include_outliers or not r["is_outlier"])]


def clean_label(text):
    """Buang nama bulan & tahun: "Kost bulan Juli 2026" jadi "Kost bulan"."""
    text = text or ""
    cleaned = re.sub(rf"\b({_MONTH_WORDS})\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b20\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|") or text.strip()
    return cleaned[:1].upper() + cleaned[1:]


def normalize_desc(text):
    """Satuin deskripsi yang cuma beda bulan/tahun/huruf besar, biar kehitung barang yang sama."""
    return clean_label(text).lower()


def group_by_desc(rows):
    """Return {deskripsi ternormalisasi: (label paling sering dipakai, rows)}."""
    groups = defaultdict(list)
    for r in rows:
        groups[normalize_desc(r["description"])].append(r)
    result = {}
    for key, items in groups.items():
        label = Counter(r["description"] for r in items).most_common(1)[0][0]
        result[key] = (clean_label(label), items)
    return result


def unit_price(row):
    return (row["amount"] or 0) / max(row["quantity"] or 1, 1)


def monthly_totals(data, today):
    """{YYYYMM: {income, expense, outlier}} sampai hari ini, expense tanpa outlier."""
    totals = defaultdict(lambda: {"income": 0, "expense": 0, "outlier": 0})
    for r in data:
        if r["created_at"].date() > today:
            continue
        amount = r["amount"] or 0
        bucket = totals[month_key(r["created_at"])]
        if r["type"] != "expense":
            bucket["income"] += amount
        elif r["is_outlier"]:
            bucket["outlier"] += amount
        else:
            bucket["expense"] += amount
    return totals


def complete_months(data, today):
    """Bulan lengkap dari bulan aktif pertama sampai bulan lalu, urut lama ke baru."""
    counts = Counter(month_key(r["created_at"]) for r in data if r["created_at"].date() <= today)
    active = sorted(k for k, n in counts.items() if n >= ACTIVE_MONTH_MIN_TX)
    current = today.strftime("%Y%m")
    if not active or active[0] >= current:
        return []
    return [k for k in month_keys_between(active[0], current) if k != current]


# ---------- Dashboard satu periode ----------

def filter_rows(data, start, end, account=None):
    rows = [r for r in data if start <= r["created_at"].date() <= end]
    if account == NO_ACCOUNT:
        return [r for r in rows if not r["account"]]
    if account:
        return [r for r in rows if r["account"] == account]
    return rows


def period_kpi(rows, days, include_outliers):
    s = summarize(rows)
    expense = s["expense"] + (s["outlier"] if include_outliers else 0)
    saving = s["income"] - expense
    return {
        "income": s["income"],
        "expense": expense,
        "outlier": s["outlier"],
        "outlier_count": s["outlier_count"],
        "saving": saving,
        "saving_pct": saving / s["income"] * 100 if s["income"] else None,
        "daily": expense / days if days else 0,
        "count": len(rows),
    }


def period_monthly(rows, start, end, include_outliers):
    totals = defaultdict(lambda: {"income": 0, "expense": 0})
    for r in rows:
        bucket = totals[month_key(r["created_at"])]
        amount = r["amount"] or 0
        if r["type"] != "expense":
            bucket["income"] += amount
        elif include_outliers or not r["is_outlier"]:
            bucket["expense"] += amount
    if not totals:
        return []

    result = []
    cumulative = 0
    for key in month_keys_between(min(totals), max(totals)):
        t = totals[key]
        year, month = int(key[:4]), int(key[4:])
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        saving = t["income"] - t["expense"]
        cumulative += saving
        result.append({
            "month": key,
            "label": month_label(key),
            "short": month_label(key, short=True),
            "income": t["income"],
            "expense": t["expense"],
            "saving": saving,
            "saving_pct": saving / t["income"] * 100 if t["income"] else None,
            "cumulative": cumulative,
            "partial": first < start or last > end,
        })
    return result


def period_categories(spend, prev_spend):
    current = defaultdict(lambda: {"total": 0, "count": 0})
    for r in spend:
        bucket = current[r["category"] or "(tanpa kategori)"]
        bucket["total"] += r["amount"] or 0
        bucket["count"] += 1
    previous = defaultdict(int)
    for r in prev_spend:
        previous[r["category"] or "(tanpa kategori)"] += r["amount"] or 0
    grand = sum(c["total"] for c in current.values()) or 1
    items = [
        {
            "category": cat, "total": c["total"], "count": c["count"],
            "share": c["total"] / grand * 100,
            "prev": previous[cat], "change": pct_diff(c["total"], previous[cat]),
        }
        for cat, c in current.items()
    ]
    return sorted(items, key=lambda c: -c["total"])


def category_heatmap(spend, categories, max_months=18):
    months = sorted({month_key(r["created_at"]) for r in spend})[-max_months:]
    names = [c["category"] for c in categories]
    cells = defaultdict(int)
    for r in spend:
        cells[(r["category"] or "(tanpa kategori)", month_key(r["created_at"]))] += r["amount"] or 0
    return {
        "months": [{"key": m, "short": month_label(m, short=True), "label": month_label(m)} for m in months],
        "categories": names,
        "values": [[cells[(c, m)] for m in months] for c in names],
    }


def weekday_pattern(spend, start, end):
    occurrences = Counter()
    day = start
    while day <= end:
        occurrences[day.weekday()] += 1
        day += timedelta(days=1)
    totals = Counter()
    for r in spend:
        totals[r["created_at"].weekday()] += r["amount"] or 0
    return [
        {
            "day": WEEKDAYS[i], "name": WEEKDAY_NAMES[i], "total": totals[i],
            "avg": totals[i] / occurrences[i] if occurrences[i] else 0,
        }
        for i in range(7)
    ]


def hour_heatmap(spend):
    """
    Matriks [hari][jam] jumlah rupiah. Jam-nya jam pencatatan, jadi cuma mendekati jam belanja.
    Baris jam 00:00:00 dilewat: itu data backfill yang ga punya jam.
    """
    matrix = [[0] * 24 for _ in range(7)]
    skipped = 0
    for r in spend:
        when = r["created_at"]
        if (when.hour, when.minute, when.second) == (0, 0, 0):
            skipped += 1
            continue
        matrix[when.weekday()][when.hour] += r["amount"] or 0
    return {"days": WEEKDAYS, "values": matrix, "skipped": skipped}


def daily_calendar(spend, start, end, max_days=371):
    start = max(start, end - timedelta(days=max_days - 1))
    totals = Counter()
    for r in spend:
        totals[r["created_at"].date()] += r["amount"] or 0
    days = []
    day = start
    while day <= end:
        days.append({"date": day.isoformat(), "weekday": day.weekday(), "total": totals[day]})
        day += timedelta(days=1)
    return days


def size_distribution(spend):
    amounts = sorted((r["amount"] or 0 for r in spend), reverse=True)
    bins = []
    for low, high, label in SIZE_BINS:
        members = [a for a in amounts if a >= low and (high is None or a < high)]
        bins.append({"label": label, "count": len(members), "total": sum(members)})
    total = sum(amounts)
    top_n = math.ceil(len(amounts) * 0.2)
    return {
        "bins": bins,
        "median": statistics.median(amounts) if amounts else 0,
        "mean": total / len(amounts) if amounts else 0,
        "top20_share": sum(amounts[:top_n]) / total * 100 if total else 0,
    }


def top_items(spend, limit=15):
    items = []
    for label, rows in group_by_desc(spend).values():
        total = sum(r["amount"] or 0 for r in rows)
        items.append({
            "description": label, "total": total, "count": len(rows), "avg": total / len(rows),
            "category": rows[0]["category"],
        })
    return sorted(items, key=lambda i: -i["total"])[:limit]


def biggest_transactions(spend, limit=8):
    """Biaya tetap dilewat, kalau ga isinya kost semua."""
    rows = [r for r in spend if r["category"] not in FIXED_CATEGORIES]
    rows = sorted(rows, key=lambda r: -(r["amount"] or 0))[:limit]
    return [
        {
            "date": r["created_at"].date().isoformat(), "description": r["description"],
            "category": r["category"], "account": r["account"], "amount": r["amount"] or 0,
            "is_outlier": bool(r["is_outlier"]),
        }
        for r in rows
    ]


def by_severity(items):
    rank = {"critical": 0, "warning": 1, "good": 2, "info": 3}
    return sorted(items, key=lambda i: rank[i["level"]])


def period_insights(cur, prev, categories, sizes, weekdays, no_spend_days, days, has_prev):
    """Kesimpulan teks buat periode yang lagi dipilih. level: good/warning/critical/info."""
    out = []
    if not cur["count"]:
        return out

    if cur["saving_pct"] is not None:
        level = "good" if cur["saving_pct"] >= 30 else "warning" if cur["saving_pct"] >= 10 else "critical"
        out.append({
            "level": level,
            "title": f"Saving rate {dec(cur['saving_pct'])}%",
            "detail": f"{rp(cur['saving'])} tersisa dari income {rp(cur['income'])}.",
        })
    elif cur["expense"]:
        out.append({
            "level": "info",
            "title": "Belum ada income di periode ini",
            "detail": "Saving rate belum bisa dihitung.",
        })

    if has_prev and prev["expense"]:
        change = pct_diff(cur["expense"], prev["expense"])
        level = "warning" if change >= 10 else "good" if change <= -10 else "info"
        word = "naik" if change >= 0 else "turun"
        out.append({
            "level": level,
            "title": f"Expense {word} {abs(change):.0f}% dari periode sebelumnya",
            "detail": f"{rp(prev['expense'])} menjadi {rp(cur['expense'])}.",
        })

    if categories:
        top = categories[0]
        out.append({
            "level": "info",
            "title": f"{top['category']} menyerap {top['share']:.0f}% expense",
            "detail": f"{rp(top['total'])} dari {top['count']} transaksi.",
        })
        risers = [c for c in categories if c["change"] is not None and c["total"] - c["prev"] >= 100_000]
        if has_prev and risers:
            riser = max(risers, key=lambda c: c["total"] - c["prev"])
            out.append({
                "level": "warning",
                "title": f"{riser['category']} naik paling banyak",
                "detail": f"{rp(riser['prev'])} menjadi {rp(riser['total'])}.",
            })

    if sizes["top20_share"] and cur["count"] >= 20:
        out.append({
            "level": "info",
            "title": f"20% transaksi terbesar = {sizes['top20_share']:.0f}% expense",
            "detail": f"Median transaksi {rp(sizes['median'])}, rata-rata {rp(sizes['mean'])}.",
        })

    busiest = max(weekdays, key=lambda d: d["avg"])
    if busiest["avg"]:
        out.append({
            "level": "info",
            "title": f"{busiest['name']} hari paling boros",
            "detail": f"Rata-rata {rp(busiest['avg'])} per hari. "
                      f"Ada {no_spend_days} dari {days} hari tanpa expense.",
        })
    return by_severity(out)


def dashboard(data, start, end, account=None, include_outliers=False):
    """Ringkasan periode start..end (inklusif, tipe date)."""
    rows = filter_rows(data, start, end, account)
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    prev_rows = filter_rows(data, prev_start, prev_end, account)

    cur = period_kpi(rows, days, include_outliers)
    prev = period_kpi(prev_rows, days, include_outliers)
    spend = spend_rows(rows, include_outliers)
    prev_spend = spend_rows(prev_rows, include_outliers)

    categories = period_categories(spend, prev_spend)
    sizes = size_distribution(spend)
    weekdays = weekday_pattern(spend, start, end)
    no_spend_days = days - len({r["created_at"].date() for r in spend})

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "kpi": cur,
        "prev": prev if prev_rows else None,
        "prev_range": [prev_start.isoformat(), prev_end.isoformat()],
        "monthly": period_monthly(rows, start, end, include_outliers),
        "categories": categories,
        "category_heatmap": category_heatmap(spend, categories),
        "weekdays": weekdays,
        "hours": hour_heatmap(spend),
        "calendar": daily_calendar(spend, start, end),
        "sizes": sizes,
        "no_spend_days": no_spend_days,
        "top_items": top_items(spend),
        "biggest": biggest_transactions(spend),
        "insights": period_insights(
            cur, prev, categories, sizes, weekdays, no_spend_days, days, bool(prev_rows),
        ),
    }


# ---------- Analisis jangka panjang ----------

def forecast_month(data, months, today):
    """
    Proyeksi expense akhir bulan berjalan pakai pola bulan-bulan lalu, bukan rata-rata harian
    biasa: "sampai tanggal segini biasanya udah berapa persen dari total sebulan". Jadi biaya
    yang numpuk di akhir bulan (kost, misalnya) tetep kehitung.
    mape = rata-rata meleset kalau cara ini dites ke tiap bulan lampau di tanggal yang sama.
    """
    base = months[-FORECAST_MONTHS:]
    current = today.strftime("%Y%m")
    daily = defaultdict(lambda: defaultdict(int))
    for r in spend_rows(data):
        when = r["created_at"].date()
        if when <= today:
            daily[month_key(when)][when.day] += r["amount"] or 0

    day = today.day
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    def cumulative(key, upto):
        return sum(v for d, v in daily[key].items() if d <= upto)

    def equivalent(key, upto):
        """
        Total sampai posisi yang setara di bulan lain: 30 Okt setara 29 Sep. Kalau pakai tanggal
        yang sama, di akhir bulan semua bulan pendek kebaca udah selesai dan proyeksinya ngaco.
        """
        year, month = int(key[:4]), int(key[4:])
        return cumulative(key, round(upto / days_in_month * calendar.monthrange(year, month)[1]))

    totals = {k: cumulative(k, 31) for k in base}
    base = [k for k in base if totals[k]]
    if len(base) < 3:
        return None

    spent = cumulative(current, day)
    shares = [equivalent(k, day) / totals[k] for k in base]
    typical = statistics.median(shares)
    if not typical or not spent:
        return None

    errors = []
    for key in base:
        others = statistics.median(equivalent(o, day) / totals[o] for o in base if o != key)
        if others:
            errors.append(abs(equivalent(key, day) / others - totals[key]) / totals[key])

    projection = max(spent / typical, spent)
    baseline = statistics.mean(totals[k] for k in base[-BASELINE_MONTHS:])
    curve = []
    for d in range(1, days_in_month + 1):
        history = [equivalent(k, d) for k in base]
        curve.append({
            "day": d,
            "actual": cumulative(current, d) if d <= day else None,
            "p25": quantile(history, 0.25),
            "p50": quantile(history, 0.5),
            "p75": quantile(history, 0.75),
        })
    return {
        "month": current,
        "label": month_label(current),
        "day": day,
        "days_in_month": days_in_month,
        "spent": spent,
        "projection": projection,
        "low": max(spent / quantile(shares, 0.75), spent),
        "high": max(spent / quantile(shares, 0.25), spent),
        "mape": statistics.mean(errors) * 100 if errors else None,
        "baseline": baseline,
        "vs_baseline": pct_diff(projection, baseline),
        "baseline_months": len(base[-BASELINE_MONTHS:]),
        "pattern_months": len(base),
        "remaining_daily": (projection - spent) / (days_in_month - day) if day < days_in_month else 0,
        "curve": curve,
    }


def trend_analysis(totals, months, today):
    window = months[-TREND_MONTHS:]
    series = []
    for i, key in enumerate(window):
        t = totals[key]
        roll = window[max(0, i - 2):i + 1]
        roll_income = sum(totals[k]["income"] for k in roll)
        roll_expense = sum(totals[k]["expense"] for k in roll)
        series.append({
            "month": key,
            "label": month_label(key),
            "short": month_label(key, short=True),
            "income": t["income"],
            "expense": t["expense"],
            "outlier": t["outlier"],
            # gajian kadang jatuh di bulan yang beda, jadi saving rate dibaca 3 bulanan
            "rolling_saving_pct": (roll_income - roll_expense) / roll_income * 100 if roll_income else None,
        })
    expenses = [s["expense"] for s in series]
    slope, r2 = linear_fit(expenses)
    recent = expenses[-BASELINE_MONTHS:]
    return {
        "series": series,
        "slope": slope,
        "r2": r2,
        "clear": r2 >= 0.3,
        "next": {
            "label": month_label(next_month_key(today.strftime("%Y%m"))),
            "median": statistics.median(recent) if recent else 0,
            "low": quantile(recent, 0.25),
            "high": quantile(recent, 0.75),
        },
    }


def runway_analysis(totals, months, balance_total):
    recent = months[-BASELINE_MONTHS:]
    if not recent:
        return None
    burn = statistics.mean(totals[k]["expense"] for k in recent)
    burn_all = statistics.mean(totals[k]["expense"] + totals[k]["outlier"] for k in recent)
    income = statistics.mean(totals[k]["income"] for k in recent)
    incomes = [totals[k]["income"] for k in months[-TREND_MONTHS:]]
    mean_income = statistics.mean(incomes)
    return {
        "basis_months": len(recent),
        "balance": balance_total,
        "burn": burn,
        "burn_with_outlier": burn_all,
        "income": income,
        "net": income - burn_all,
        "runway": balance_total / burn if burn else None,
        "runway_with_outlier": balance_total / burn_all if burn_all else None,
        "income_cv": statistics.pstdev(incomes) / mean_income * 100 if mean_income else None,
        "income_median": statistics.median(incomes),
        "months_no_income": sum(1 for v in incomes if not v),
        "income_months": len(incomes),
    }


def recurring_analysis(data, months, today):
    """
    Pisahin dua pola pengeluaran yang berulang:
      fixed  = biaya rutin: hampir tiap bulan ada, tapi jarang (kost, internet, laundry).
               Dicek per kategori dulu karena namanya sering beda-beda ("Paket 7GB", "Paket
               Sakti"), lalu per deskripsi yang nominalnya stabil.
      habits = kebiasaan: dibeli berkali-kali tiap bulan, tiap transaksi kecil tapi numpuk
    """
    spend = [r for r in spend_rows(data) if r["created_at"].date() <= today]

    def in_window(rows, keys):
        return [r for r in rows if month_key(r["created_at"]) in keys]

    fixed_window = set(months[-TREND_MONTHS:])
    window_rows = in_window(spend, fixed_window)
    fixed = []

    by_category = defaultdict(list)
    for r in window_rows:
        by_category[r["category"]].append(r)
    fixed_categories = set()
    for cat, rows in by_category.items():
        seen = {month_key(r["created_at"]) for r in rows}
        if len(seen) / len(fixed_window) < 0.75 or len(rows) / len(seen) > 3:
            continue
        fixed_categories.add(cat)
        total = sum(r["amount"] or 0 for r in rows)
        fixed.append({
            "description": cat, "kind": "Kategori", "amount": total / len(rows), "months": len(seen),
            "window": len(fixed_window), "monthly": total / len(fixed_window),
            "last": max(r["created_at"] for r in rows).date().isoformat(),
        })

    for label, rows in group_by_desc(window_rows).values():
        if rows[0]["category"] in fixed_categories:
            continue
        seen = {month_key(r["created_at"]) for r in rows}
        amounts = [r["amount"] or 0 for r in rows]
        mean = statistics.mean(amounts)
        if len(seen) / len(fixed_window) < 0.75 or len(rows) / len(seen) > 1.5:
            continue
        if not mean or statistics.pstdev(amounts) / mean > 0.4:
            continue
        fixed.append({
            "description": label, "kind": "Deskripsi", "amount": statistics.median(amounts),
            "months": len(seen), "window": len(fixed_window),
            "monthly": sum(amounts) / len(fixed_window),
            "last": max(r["created_at"] for r in rows).date().isoformat(),
        })
    fixed.sort(key=lambda f: -f["monthly"])
    fixed_keys = {normalize_desc(f["description"]) for f in fixed}

    # kategori yang selalu ada tiap bulan tapi bulan ini belum muncul, padahal tanggal biasanya
    # udah lewat: kemungkinan belum dibayar atau belum dicatat, dan proyeksi jadi kerendahan
    current = today.strftime("%Y%m")
    missing = []
    for cat in fixed_categories:
        rows = by_category[cat]
        seen = {month_key(r["created_at"]) for r in rows}
        if len(seen) / len(fixed_window) < 0.9 or any(month_key(r["created_at"]) == current for r in spend if r["category"] == cat):
            continue
        usual_day = round(statistics.median(r["created_at"].day for r in rows))
        if today.day >= usual_day:
            latest = max(rows, key=lambda r: r["created_at"])
            missing.append({"category": cat, "usual_day": usual_day, "last_amount": latest["amount"] or 0})
    missing.sort(key=lambda m: -m["last_amount"])

    habit_window = months[-BASELINE_MONTHS:]
    habit_keys = set(habit_window)
    habits = []
    for key, (label, rows) in group_by_desc(in_window(spend, habit_keys)).items():
        if key in fixed_keys or rows[0]["category"] in fixed_categories:
            continue
        seen = {month_key(r["created_at"]) for r in rows}
        if len(seen) < 3 or len(rows) / len(habit_keys) < 3:
            continue
        total = sum(r["amount"] or 0 for r in rows)
        habits.append({
            "description": label, "category": rows[0]["category"], "count": len(rows),
            "per_month": len(rows) / len(habit_keys),
            "avg": total / len(rows),
            "monthly": total / len(habit_keys),
            "yearly": total / len(habit_keys) * 12,
        })
    habits.sort(key=lambda h: -h["yearly"])
    return {
        "fixed": fixed,
        "fixed_monthly": sum(f["monthly"] for f in fixed),
        "missing": missing,
        "habits": habits[:12],
        "habit_months": len(habit_window),
    }


def inflation_analysis(data, today, min_purchases=12, min_gap_days=90):
    """
    Harga barang yang sama dari waktu ke waktu: median harga satuan sepertiga pembelian
    pertama vs sepertiga terakhir. Basket = gabungan semua barang, bobotnya jumlah beli terbaru.
    """
    spend = [
        r for r in spend_rows(data)
        if r["created_at"].date() <= today and r["category"] in PRICE_CATEGORIES
    ]
    items = []
    for label, rows in group_by_desc(spend).values():
        if len(rows) < min_purchases:
            continue
        rows = sorted(rows, key=lambda r: r["created_at"])
        third = len(rows) // 3
        early, late = rows[:third], rows[-third:]
        if (late[0]["created_at"] - early[-1]["created_at"]).days < min_gap_days:
            continue
        before = statistics.median(unit_price(r) for r in early)
        after = statistics.median(unit_price(r) for r in late)
        if not before:
            continue
        items.append({
            "description": label, "category": rows[0]["category"], "purchases": len(rows),
            "before": before, "after": after, "change": pct_diff(after, before),
            "before_until": early[-1]["created_at"].date().isoformat(),
            "after_from": late[0]["created_at"].date().isoformat(),
            "weight": len(late),
        })
    weighted_before = sum(i["before"] * i["weight"] for i in items)
    weighted_after = sum(i["after"] * i["weight"] for i in items)
    items.sort(key=lambda i: -abs(i["change"]))
    return {
        "basket_change": pct_diff(weighted_after, weighted_before),
        "items_count": len(items),
        "items": items[:10],
        "raised": sum(1 for i in items if i["change"] > 0),
        "lowered": sum(1 for i in items if i["change"] < 0),
    }


def anomaly_analysis(data, months, today):
    """Hari yang expense-nya jauh di atas biasanya (robust z-score) dan lonjakan kategori bulanan."""
    spend = [r for r in spend_rows(data) if r["created_at"].date() <= today]
    daily = defaultdict(list)
    for r in spend:
        if r["category"] not in FIXED_CATEGORIES:
            daily[r["created_at"].date()].append(r)
    totals = {d: sum(r["amount"] or 0 for r in rows) for d, rows in daily.items()}

    days = []
    if totals:
        median = statistics.median(totals.values())
        mad = statistics.median(abs(v - median) for v in totals.values())
        cutoff = today - timedelta(days=ANOMALY_WINDOW_DAYS)
        for day, total in totals.items():
            score = 0.6745 * (total - median) / mad if mad else 0
            if score >= ANOMALY_Z and day >= cutoff:
                top = max(daily[day], key=lambda r: r["amount"] or 0)
                days.append({
                    "date": day.isoformat(), "total": total, "score": score,
                    "times_median": total / median if median else None,
                    "top": top["description"], "top_amount": top["amount"] or 0,
                })
    days.sort(key=lambda d: -d["total"])

    by_month = defaultdict(lambda: defaultdict(int))
    for r in spend:
        by_month[r["category"] or "(tanpa kategori)"][month_key(r["created_at"])] += r["amount"] or 0
    current = today.strftime("%Y%m")
    check = (months[-2:] + [current]) if months else [current]
    spikes = []
    for cat, values in by_month.items():
        for key in check:
            history = [values.get(k, 0) for k in month_keys_before(months, key)]
            if len(history) < 3:
                continue
            median = statistics.median(history)
            amount = values.get(key, 0)
            if amount >= median * 1.5 and amount - median >= 100_000:
                spikes.append({
                    "category": cat, "month": key, "label": month_label(key), "amount": amount,
                    "median": median, "times": amount / median if median else None,
                    "partial": key == current,
                })
    spikes.sort(key=lambda s: (s["month"], s["amount"]), reverse=True)
    return {
        "days": days[:8], "days_found": len(days), "window_days": ANOMALY_WINDOW_DAYS,
        "spikes": spikes[:8],
    }


def month_keys_before(months, key, count=BASELINE_MONTHS):
    return [k for k in months if k < key][-count:]


def payday_cycle(data, months, today):
    """Rata-rata expense per hari di tiap minggu setelah gajian. Kost & outlier ga dihitung."""
    if not months:
        return None
    start = months[0]
    incomes = sorted(
        r["created_at"].date() for r in data
        if r["type"] != "expense" and (r["amount"] or 0) >= PAYDAY_MIN_INCOME
        and r["created_at"].date() <= today and month_key(r["created_at"]) >= start
    )
    paydays = []
    for day in incomes:
        if not paydays or (day - paydays[-1]).days > 5:
            paydays.append(day)

    daily = Counter()
    for r in spend_rows(data):
        if r["category"] not in FIXED_CATEGORIES:
            daily[r["created_at"].date()] += r["amount"] or 0

    bins = [{"label": f"Minggu {i + 1}", "total": 0, "days": 0} for i in range(4)]
    cycles = 0
    for first, second in zip(paydays, paydays[1:]):
        gap = (second - first).days
        if not 20 <= gap <= 45:
            continue  # gajian kelewat kecatet atau jaraknya ga wajar
        cycles += 1
        for offset in range(gap):
            bucket = bins[min(offset // 7, 3)]
            bucket["total"] += daily[first + timedelta(days=offset)]
            bucket["days"] += 1
    if cycles < 3:
        return None
    for b in bins:
        b["daily"] = b["total"] / b["days"] if b["days"] else 0
    return {"cycles": cycles, "bins": bins}


def budget_suggestions(data, totals_by_cat_months, budgets, today):
    """
    Saran batas per kategori dari 6 bulan terakhir: persentil 75 dibulatkan ke atas, jadi
    realistis buat bulan biasa tapi tetep nahan bulan yang kebablasan.
    """
    statuses = {b["category"]: b for b in budget_status(data, budgets, today)}
    current = summarize([
        r for r in data
        if month_key(r["created_at"]) == today.strftime("%Y%m") and r["created_at"].date() <= today
    ])["categories"]
    result = []
    for cat, values in totals_by_cat_months.items():
        upper = quantile(values, 0.75)
        if upper < BUDGET_ROUND / 2:
            continue  # kategori yang jarang dipakai ga perlu dibatasi
        suggested = math.ceil(upper / BUDGET_ROUND) * BUDGET_ROUND
        status = statuses.get(cat)
        result.append({
            "category": cat, "median": statistics.median(values), "suggested": suggested,
            "spent": current.get(cat, 0), "budget": status["limit"] if status else None,
            "pct": current.get(cat, 0) / suggested * 100 if suggested else 0,
        })
    return sorted(result, key=lambda b: -b["suggested"])


def category_months(data, months, today):
    """{kategori: [total tiap bulan di jendela baseline]}, bulan kosong ikut sebagai 0."""
    window = months[-BASELINE_MONTHS:]
    cells = defaultdict(int)
    for r in spend_rows(data):
        when = r["created_at"]
        if when.date() <= today and month_key(when) in window:
            cells[(r["category"] or "(tanpa kategori)", month_key(when))] += r["amount"] or 0
    categories = {cat for cat, _ in cells}
    return {cat: [cells[(cat, m)] for m in window] for cat in sorted(categories)}


def balance_state(history):
    """
    history: baris balance_history urut id. Return saldo terakhir tiap akun, riwayat total harian
    (mulai setelah semua akun punya saldo awal), dan saldo pembuka.
    """
    latest = {}
    updated = {}
    opening = {}
    accounts = {h["account"] for h in history}
    daily_total = {}
    for h in history:
        if h["note"] == "adjust" and h["account"] not in opening:
            opening[h["account"]] = h["balance"]
        latest[h["account"]] = h["balance"]
        updated[h["account"]] = h["created_at"]
        if len(latest) == len(accounts):
            daily_total[h["created_at"].date().isoformat()] = sum(latest.values())
    adjusts = [h["created_at"] for h in history if h["note"] == "adjust"]
    return {
        "accounts": sorted(
            (
                {"account": a, "balance": b, "updated": updated[a].isoformat(sep=" ", timespec="minutes")}
                for a, b in latest.items()
            ),
            key=lambda a: -a["balance"],
        ),
        "total": sum(latest.values()),
        "history": [{"date": d, "total": t} for d, t in sorted(daily_total.items())],
        "opening_total": sum(opening.values()),
        "opened_at": min(adjusts) if adjusts else None,
    }


def reconciliation(data, balances, today):
    """
    Bandingin saldo pembuka dengan income - expense yang kecatet sebelum saldo itu diisi.
    Selisihnya bukan berarti salah: bisa pengeluaran yang ga kecatet, saldo awal yang ga
    kecatet, atau transaksi bertanggal masa depan yang sebenernya udah dibayar.
    """
    opened_at = balances["opened_at"]
    if opened_at is None:
        return None
    before = [r for r in data if r["created_at"] < opened_at]
    income = sum(r["amount"] or 0 for r in before if r["type"] != "expense")
    expense = sum(r["amount"] or 0 for r in before if r["type"] == "expense")
    future = sum(
        r["amount"] or 0 for r in data
        if r["type"] == "expense" and r["created_at"].date() > today
    )
    recorded = income - expense
    gap = recorded - balances["opening_total"]
    # transaksi tanpa akun ga ngubah saldo, jadi kalau ada setelah saldo diisi, saldonya melenceng
    unassigned = sum(
        1 for r in data
        if opened_at <= r["created_at"] and r["created_at"].date() <= today and not r["account"]
    )
    return {
        "opened_at": opened_at.isoformat(sep=" ", timespec="minutes"),
        "income": income,
        "expense": expense,
        "recorded": recorded,
        "opening_total": balances["opening_total"],
        "gap": gap,
        "future_expense": future,
        "gap_after_future": gap - future,
        "unassigned_after_opening": unassigned,
    }


def entry_bursts(data, today, min_count=5, max_minutes=10):
    """
    Baris kembar (deskripsi & nominal sama) yang masuk beruntun dalam hitungan menit. Bisa duplikat,
    bisa juga sengaja nyicil catatan lama sekaligus. Dua-duanya bikin expense hari itu keliatan
    gede, padahal uangnya mungkin keluar di bulan-bulan lain.
    """
    groups = defaultdict(list)
    for r in data:
        if r["type"] == "expense" and r["created_at"].date() <= today:
            groups[(r["created_at"].date(), normalize_desc(r["description"]), r["amount"])].append(r)
    bursts = []
    for (day, _, amount), rows in groups.items():
        times = sorted(r["created_at"] for r in rows)
        minutes = (times[-1] - times[0]).total_seconds() / 60
        # jam yang persis sama berarti data backfill, bukan diketik beruntun
        if len(rows) >= min_count and len(set(times)) > 1 and minutes <= max_minutes:
            bursts.append({
                "date": day.isoformat(), "month": month_key(day), "description": rows[0]["description"],
                "category": rows[0]["category"], "amount": amount or 0, "count": len(rows),
                "total": sum(r["amount"] or 0 for r in rows), "minutes": max(1, math.ceil(minutes)),
            })
    return sorted(bursts, key=lambda b: b["date"], reverse=True)


def data_quality(data, months, today):
    future = [r for r in data if r["created_at"].date() > today]
    current_data = [r for r in data if r["created_at"].date() <= today]
    counts = Counter(month_key(r["created_at"]) for r in current_data)
    first_active = months[0] if months else None
    sparse = sorted(k for k in counts if first_active and k < first_active)
    future_categories = Counter(r["category"] for r in future)
    return {
        "total": len(data),
        "future_count": len(future),
        "future_total": sum(r["amount"] or 0 for r in future),
        "future_last": max(r["created_at"] for r in future).date().isoformat() if future else None,
        "future_categories": future_categories.most_common(3),
        "no_account": sum(1 for r in data if not r["account"]),
        "no_time": sum(
            1 for r in data
            if (r["created_at"].hour, r["created_at"].minute, r["created_at"].second) == (0, 0, 0)
        ),
        "first_active": month_label(first_active) if first_active else None,
        "sparse_months": len(sparse),
        "sparse_transactions": sum(counts[k] for k in sparse),
        "bursts": entry_bursts(data, today),
    }


def advanced_insights(forecast, trend, runway, recurring, inflation, payday, recon, quality, budgets):
    out = []

    for item in recurring["missing"]:
        out.append({
            "level": "warning",
            "title": f"{item['category']} belum tercatat bulan ini",
            "detail": f"Biasanya sekitar tanggal {item['usual_day']}, terakhir {rp(item['last_amount'])}. "
                      "Kalau memang belum dibayar, proyeksi bulan ini belum memasukkannya.",
        })

    for burst in quality["bursts"]:
        out.append({
            "level": "warning",
            "title": f"{burst['description']} tercatat {burst['count']}x dalam {burst['minutes']} menit",
            "detail": f"{burst['date']}, total {rp(burst['total'])}, kategori {burst['category']}. "
                      "Bisa duplikat atau catatan lama yang dimasukkan sekaligus. "
                      "Expense hari dan bulan itu jadi terlihat lebih besar.",
        })

    if forecast and forecast["vs_baseline"] is not None:
        # pencatatan massal di bulan ini ga mencerminkan laju belanja, jadi status dinilai tanpa itu
        burst_total = sum(b["total"] for b in quality["bursts"] if b["month"] == forecast["month"])
        adjusted = forecast["projection"] - burst_total
        change = forecast["vs_baseline"]
        judged = pct_diff(adjusted, forecast["baseline"])
        level = "critical" if judged >= 25 else "warning" if judged >= 10 else "good" if judged <= -10 else "info"
        word = "di atas" if change >= 0 else "di bawah"
        note = f" Cara ini rata-rata meleset {forecast['mape']:.0f}%." if forecast["mape"] is not None else ""
        if burst_total:
            note += f" Tanpa pencatatan massal {rp(burst_total)}: {rp(adjusted)} ({judged:+.0f}%)."
        out.append({
            "level": level,
            "title": f"{forecast['label']} diproyeksikan {abs(change):.0f}% {word} normal",
            "detail": f"Proyeksi akhir bulan {rp(forecast['projection'])}, "
                      f"normalnya {rp(forecast['baseline'])}.{note}",
        })

    if runway and runway["runway"] is not None:
        months_left = runway["runway"]
        level = "good" if months_left >= 6 else "warning" if months_left >= 3 else "critical"
        out.append({
            "level": level,
            "title": f"Saldo cukup untuk {dec(months_left)} bulan",
            "detail": f"Kalau tanpa income, dengan pengeluaran rutin {rp(runway['burn'])} per bulan. "
                      f"Termasuk outlier: {dec(runway['runway_with_outlier'])} bulan.",
        })

    if trend["clear"]:
        direction = "naik" if trend["slope"] > 0 else "turun"
        out.append({
            "level": "warning" if trend["slope"] > 0 else "good",
            "title": f"Expense bulanan cenderung {direction}",
            "detail": f"Sekitar {rp(abs(trend['slope']))} per bulan selama {len(trend['series'])} bulan terakhir.",
        })
    else:
        out.append({
            "level": "info",
            "title": "Expense bulanan belum menunjukkan tren yang jelas",
            "detail": f"Naik turunnya lebih besar dari arah umumnya (R² {dec(trend['r2'], 2)}).",
        })

    rolling = [s["rolling_saving_pct"] for s in trend["series"] if s["rolling_saving_pct"] is not None]
    if len(rolling) >= 4:
        now, before = rolling[-1], rolling[-4]
        level = "good" if now >= before else "warning"
        out.append({
            "level": level,
            "title": f"Saving rate 3 bulanan {dec(now)}%",
            "detail": f"Tiga bulan sebelumnya {dec(before)}%.",
        })

    if runway and recurring["fixed_monthly"] and runway["burn"]:
        share = recurring["fixed_monthly"] / runway["burn"] * 100
        out.append({
            "level": "info",
            "title": f"Biaya rutin sekitar {share:.0f}% pengeluaran",
            "detail": f"{rp(recurring['fixed_monthly'])} per bulan muncul hampir tiap bulan, "
                      f"sisanya {rp(runway['burn'] - recurring['fixed_monthly'])} lebih bervariasi.",
        })

    if recurring["habits"]:
        habit = recurring["habits"][0]
        out.append({
            "level": "info",
            "title": f"{habit['description']} setara {rp(habit['yearly'])} per tahun",
            "detail": f"Dibeli sekitar {habit['per_month']:.0f} kali per bulan, rata-rata {rp(habit['avg'])}.",
        })

    if inflation["basket_change"] is not None and inflation["items_count"] >= 3:
        change = inflation["basket_change"]
        out.append({
            "level": "warning" if change >= 5 else "info",
            "title": f"Harga makanan & minuman harian {'naik' if change >= 0 else 'turun'} {dec(abs(change))}%",
            "detail": f"Dari {inflation['items_count']} barang yang sering dibeli, "
                      f"{inflation['raised']} naik dan {inflation['lowered']} turun.",
        })

    if payday:
        high = max(payday["bins"], key=lambda b: b["daily"])
        low = min(payday["bins"], key=lambda b: b["daily"])
        if low["daily"] and high["daily"] / low["daily"] >= 1.3:
            out.append({
                "level": "info",
                "title": f"Belanja paling tinggi di {high['label'].lower()} setelah gajian",
                "detail": f"{rp(high['daily'])} per hari, terendah {rp(low['daily'])} di {low['label'].lower()}. "
                          f"Dari {payday['cycles']} siklus gaji, tanpa kost.",
            })

    if recon and recon["gap"] > 0:
        out.append({
            "level": "warning",
            "title": f"Catatan dan saldo nyata selisih {rp(recon['gap'])}",
            "detail": f"Income dikurangi expense yang tercatat menyisakan {rp(recon['recorded'])}, "
                      f"saldo nyata saat diisi {rp(recon['opening_total'])}. Bisa dari pengeluaran "
                      "yang belum tercatat. Detailnya di bagian Rekonsiliasi.",
        })

    if quality["future_count"]:
        out.append({
            "level": "info",
            "title": f"{quality['future_count']} transaksi bertanggal masa depan",
            "detail": f"Total {rp(quality['future_total'])} sampai {quality['future_last']}. "
                      "Tidak dihitung di analisis sampai tanggalnya lewat.",
        })

    if not budgets:
        out.append({
            "level": "info",
            "title": "Belum ada budget",
            "detail": "Saran batas per kategori ada di bagian Saran budget.",
        })
    return by_severity(out)


def advanced(data, history, budgets, today=None):
    """
    Analisis seluruh riwayat sampai hari ini.
    history = baris balance_history, budgets = {kategori: batas per bulan}.
    """
    now = today or datetime.now()
    today = now.date() if isinstance(now, datetime) else now
    months = complete_months(data, today)
    totals = monthly_totals(data, today)
    balances = balance_state(history)

    forecast = forecast_month(data, months, today)
    trend = trend_analysis(totals, months, today)
    runway = runway_analysis(totals, months, balances["total"])
    recurring = recurring_analysis(data, months, today)
    inflation = inflation_analysis(data, today)
    anomalies = anomaly_analysis(data, months, today)
    payday = payday_cycle(data, months, today)
    per_category = category_months(data, months, today)
    budget = budget_suggestions(data, per_category, budgets, today)
    recon = reconciliation(data, balances, today)
    quality = data_quality(data, months, today)

    return {
        "today": today.isoformat(),
        "balances": balances_public(balances),
        "forecast": forecast,
        "trend": trend,
        "runway": runway,
        "recurring": recurring,
        "inflation": inflation,
        "anomalies": anomalies,
        "payday": payday,
        "budget": budget,
        "reconciliation": recon,
        "quality": quality,
        "simulation": {
            "categories": sorted(
                ({"category": c, "monthly": statistics.mean(v)} for c, v in per_category.items()),
                key=lambda c: -c["monthly"],
            ),
            "income": runway["income"] if runway else 0,
            "burn": runway["burn"] if runway else 0,
            "balance": balances["total"],
        },
        "insights": advanced_insights(
            forecast, trend, runway, recurring, inflation, payday, recon, quality, budgets,
        ),
    }


def balances_public(balances):
    """balance_state tanpa field datetime, biar bisa langsung jadi JSON."""
    opened = balances["opened_at"]
    return {**balances, "opened_at": opened.isoformat(sep=" ", timespec="minutes") if opened else None}
