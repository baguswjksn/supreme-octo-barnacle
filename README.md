# supreme-octo-barnacle

Bot Telegram pencatat keuangan pribadi. Transaksi expense/income disimpan di SQLite (`transactions.db`), saldo dicatat per akun (bank, e-wallet, cash), dan laporan bisa diambil langsung dari Telegram dalam bentuk Excel atau gambar.

## Fitur

- Input transaksi lewat tombol (`/add`) atau satu baris teks (format cepat)
- Saldo per akun yang otomatis berubah setiap ada transaksi, lengkap dengan riwayatnya
- Laporan Excel: ringkasan bulanan (Income, Expense, Expense Ratio, Saving, Saving %) + detail transaksi per bulan
- Gambar expense 7 hari terakhir: per hari dan per kategori
- Insight bulanan dalam bentuk teks: perbandingan dengan bulan lalu, proyeksi akhir bulan, top kategori, dan lainnya
- Gambar tren income, expense, dan saving % per bulan
- Transfer antar akun yang tidak dihitung sebagai income/expense
- Budget bulanan per kategori, dengan peringatan setiap kali transaksi disimpan
- Transaksi rutin bulanan yang dicatat otomatis
- Hapus, undo, dan edit transaksi langsung dari Telegram
- Pencarian transaksi dari deskripsi
- Laporan HTML lewat command line

## Persiapan

Butuh Python 3.10 ke atas.

```
pip install -r requirements.txt
```

Salin `.env.example` jadi `.env`, lalu isi:

```
TELEGRAM_BOT_TOKEN=<token dari @BotFather>
ALLOWED_USER_ID=<user ID Telegram kamu, cek lewat @userinfobot>
```

`ALLOWED_USER_ID` membatasi siapa yang boleh pakai bot. Kalau dikosongkan, semua orang yang menemukan bot bisa memasukkan data. File `.env` sudah ada di `.gitignore`, jangan di-commit.

## Menjalankan bot

```
python ingest_transaction.py
```

Bot jalan terus (long polling) dan langsung membalas setiap pesan. Stop dengan `Ctrl+C`. Kalau internet putus, bot mencoba lagi sendiri setiap 5 detik.

Database selalu `transactions.db` di folder yang sama dengan script, jadi script bisa dijalankan dari folder mana pun. Tabel dan kolom yang belum ada dibuat otomatis saat bot start.

## Command Telegram

| Command | Fungsi |
|---|---|
| `/add` | Tambah transaksi: pilih tipe → akun → kategori → ketik amount → ketik deskripsi |
| `/cancel` | Batalkan `/add` yang sedang berjalan |
| `/balance [n]` | Saldo tiap akun + total, dan `n` perubahan saldo terakhir (default 5) |
| `/setbalance <akun> <angka>` | Koreksi saldo akun supaya sama dengan saldo aslinya, misalnya `/setbalance cash 250000` |
| `/last [n]` | `n` transaksi yang terakhir dimasukkan (default 10, maksimal 50) |
| `/report` | Kirim laporan Excel (.xlsx) |
| `/weekly` | Kirim gambar expense 7 hari terakhir |
| `/insight [YYYYMM]` | Insight bulan berjalan, atau bulan tertentu, misalnya `/insight 202609` |
| `/trend [n]` | Gambar tren income vs expense dan saving % `n` bulan terakhir (default 12) |
| `/transfer <dari> <ke> <amount> [catatan]` | Pindah saldo antar akun, misalnya `/transfer mega dana 500000 topup` |
| `/undo` | Hapus transaksi atau transfer yang terakhir dimasukkan (ada tombol konfirmasi) |
| `/delete <id>` | Hapus transaksi atau transfer tertentu (ada tombol konfirmasi) |
| `/edit <id> <field> <nilai>` | Ubah transaksi. Field: `amount`, `desc`, `qty`, `category`, `type`, `account` |
| `/budget` | Status semua budget bulan ini |
| `/budget <kategori> <angka>` | Set budget bulanan kategori, `0` untuk menghapus |
| `/recurring` | Daftar transaksi rutin |
| `/recurring add <tanggal>, ...` | Tambah transaksi rutin, sisanya format cepat dengan akun wajib |
| `/recurring del <id>` | Hapus transaksi rutin |
| `/search <kata>` | Cari transaksi yang deskripsinya mengandung kata itu |
| `/backup` | Kirim file `.zip` berisi semua data, satu CSV per tabel (`transactions.csv`, `balance_history.csv`, `transfers.csv`, `budgets.csv`, `recurring.csv`) |

Command lain atau format yang tidak dikenali dibalas dengan daftar bantuan.

### Insight

`/insight` membalas ringkasan teks satu bulan (expense selalu tanpa outlier):

- Income, expense, saving, dan saving %
- Perbandingan dengan bulan lalu. Untuk bulan berjalan, yang dibandingkan periode yang sama (misalnya tanggal 1–28 bulan ini vs 1–28 bulan lalu), jadi tetap adil walaupun bulan belum selesai
- Rata-rata expense per hari, proyeksi expense dan saving di akhir bulan (hanya bulan berjalan)
- Rata-rata expense 3 bulan sebelumnya sebagai patokan normal, dan apakah bulan ini di atas atau di bawahnya
- 5 kategori terbesar beserta porsi dan perubahannya dibanding bulan lalu
- Kategori yang naik dan turun paling banyak
- Expense terbesar, jumlah hari tanpa expense, dan total outlier

Kalau ada budget, `/insight` bulan berjalan juga menampilkan status budget.

### Budget

`/budget Food 1500000` menetapkan batas expense Food per bulan (outlier tidak dihitung). Setiap kali expense Food disimpan, balasan bot menyertakan pemakaian budget bulan ini:

- ⚠️ kalau sudah 80% atau lebih
- 🚨 kalau sudah lewat, beserta kelebihannya
- peringatan proyeksi kalau dengan kecepatan belanja sekarang budget akan lewat di akhir bulan

### Transaksi rutin

```
/recurring add 25, Kos, 1500000, expense, Rent, mega
/recurring add 1, Internet, 300000, expense, Internet, mega
```

Bot mencatat transaksinya otomatis setiap bulan di tanggal itu dan mengirim pesan konfirmasi. Tanggal 29–31 di bulan yang lebih pendek dicatat di hari terakhir bulan itu. Kalau tanggalnya sudah lewat saat ditambahkan, pencatatan otomatis mulai bulan depan; catat manual untuk bulan ini kalau belum. Bot harus sedang jalan supaya pencatatan terjadi. Kalau bot mati di tanggal itu, transaksinya dicatat begitu bot jalan lagi (masih di bulan yang sama).

### Hapus dan edit

ID transaksi ada di pesan "Tersimpan", `/last`, dan `/search`. Hapus dan edit lewat bot ikut menyesuaikan saldo akun. Transfer tidak bisa diedit; hapus lalu buat ulang.

### Format cepat

Kirim satu baris dengan bagian dipisah koma:

```
deskripsi, amount, tipe, kategori, akun
deskripsi, quantity, amount, tipe, kategori, akun
```

Contoh:

```
Nasi Bali, 14000, expense, Food, Cash
Nasi Bali, 2, 28000, expense, Food, dana
Gaji, 5000000, income, Active Income, mega
```

- `amount` adalah total, bukan harga satuan. Boleh ditulis `14000`, `14.000`, atau `Rp 14.000`.
- `tipe`: `expense` atau `income`.
- `akun` boleh tidak ditulis. Kalau tidak ada, bot menanyakan akunnya lewat tombol.
- Nama akun boleh disingkat atau ditulis huruf kecil, selama tidak ambigu: `mega`, `bca`, `dana`, `shopee`, `cash`.
- Waktu transaksi adalah waktu pesan dikirim.

### Kategori

Active Income, Bills, Book, Entertainment, Food, Healthcare, House, Internet, Laundry, Needs, Outlier, Rent, Transportation, Utilities, Water.

Expense dengan kategori **Outlier** ditandai sebagai outlier: tidak dihitung di Expense (Outlier Excluded), Expense Ratio, Saving, maupun gambar weekly, tapi tetap mengurangi saldo akun. Income tidak pernah ditandai outlier.

## Akun dan saldo

Daftar akun ada di `ACCOUNTS` di `ingest_transaction.py`:

```python
ACCOUNTS = ["Bank Mega Syariah", "ShopeePay", "DANA", "BCA Mobile", "Cash"]
```

Untuk menambah akun, tambahkan namanya di sana, restart bot, lalu set saldo awalnya dengan `/setbalance`.

Saldo dihitung lewat trigger SQLite di tabel `transactions`, jadi tetap benar walaupun data diubah di luar bot (misalnya lewat DB Browser):

- **Insert**: income menambah saldo akun, expense mengurangi.
- **Update** amount, tipe, atau akun: efek lama dibatalkan di akun lama, efek baru diterapkan di akun baru.
- **Delete**: efek transaksi dibatalkan.
- **`/setbalance`**: dicatat sebagai koreksi (`adjust`).

Transaksi tanpa akun (data lama sebelum fitur akun ada) tidak ikut menghitung saldo, tapi tetap masuk laporan.

Pindah uang antar akun dicatat dengan `/transfer` di tabel `transfers`, terpisah dari `transactions`, jadi tidak ikut terhitung di laporan income/expense. Saldo kedua akun diubah lewat trigger dan dicatat di `balance_history` dengan note `transfer`. Biaya admin transfer dicatat terpisah sebagai expense.

## Laporan dari command line

```
python g_last_r.py                 # report.html
python g_last_r.py laporan.html    # HTML ke file tertentu
python g_last_r.py laporan.xlsx    # Excel
python g_weekly_e.py               # weekly_expense.png
python g_weekly_e.py minggu.png    # PNG ke file tertentu
python g_insight.py                # insight bulan berjalan
python g_insight.py 202609         # insight bulan tertentu
python g_trend.py                  # trend.png, 12 bulan terakhir
python g_trend.py tren.png 6       # 6 bulan terakhir ke file tertentu
```

Warna merah–kuning–hijau di tabel ringkasan hanya diterapkan sampai bulan berjalan. Bulan di masa depan (biasanya salah input tanggal) dibiarkan polos.

## Struktur database

**`transactions`**

| Kolom | Isi |
|---|---|
| `id` | ID transaksi, contoh `r5d526bb03b79a7` |
| `description` | Deskripsi |
| `created` | Waktu transaksi, format `YYYYMMDDHHMMSS` |
| `quantity` | Jumlah barang |
| `amount` | Total nominal (Rupiah) |
| `is_outlier` | `1` kalau expense outlier |
| `type` | `expense` atau `income` |
| `category` | Kategori |
| `account` | Nama akun, `NULL` untuk data lama |

**`balance_history`**: satu baris setiap kali saldo sebuah akun berubah.

| Kolom | Isi |
|---|---|
| `account` | Nama akun |
| `transaction_id` | Transaksi penyebab perubahan (`NULL` untuk koreksi manual) |
| `created` | Waktu perubahan |
| `change` | Perubahan saldo (`+` / `−`) |
| `balance` | Saldo akun setelah perubahan |
| `note` | `transaction`, `transfer`, `update`, `delete`, atau `adjust` |

**`transfers`**: transfer antar akun.

| Kolom | Isi |
|---|---|
| `id` | ID transfer, diawali `t` |
| `created` | Waktu transfer |
| `from_account` / `to_account` | Akun asal / tujuan |
| `amount` | Nominal |
| `note` | Catatan (opsional) |

**`budgets`**: `category`, `amount` (batas per bulan).

**`recurring`**: transaksi rutin. `day` tanggal pencatatan, kolom transaksinya sama dengan `transactions`, `chat_id` tujuan pesan konfirmasi, `last_run` bulan terakhir dicatat (`YYYYMM`).

## Struktur file

| File | Isi |
|---|---|
| `ingest_transaction.py` | Bot Telegram: input transaksi, akun dan saldo, command |
| `g_last_r.py` | Laporan HTML dan Excel |
| `g_weekly_e.py` | Gambar expense 7 hari terakhir |
| `g_insight.py` | Insight bulanan (teks) dan status budget |
| `g_trend.py` | Gambar tren bulanan |
| `requirements.txt` | Dependency (`openpyxl`, `matplotlib`) |
| `.env.example` | Template konfigurasi |
