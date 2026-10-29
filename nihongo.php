<?php
/**
 * Nihongo — aplikasi belajar bahasa Jepang ala Duolingo.
 * Satu file PHP + SQLite (database dibuat dan diisi otomatis saat pertama dibuka).
 *
 * Jalankan:  php -S localhost:8000   lalu buka http://localhost:8000/nihongo.php
 * Butuh:     PHP 8.1+ dengan ekstensi pdo_sqlite.
 */
declare(strict_types=1);

date_default_timezone_set('Asia/Jakarta');
session_start();

const DB_FILE = __DIR__ . '/nihongo.sqlite';
const MAX_HEARTS = 5;
const HEART_REFILL_SECONDS = 20 * 60;
const XP_LESSON = 10;
const XP_PERFECT_BONUS = 5;
const XP_PRACTICE = 5;
const DAILY_GOALS = [10 => 'Santai', 20 => 'Biasa', 30 => 'Serius', 50 => 'Intens'];

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

function db(): PDO
{
    static $pdo = null;
    if ($pdo) {
        return $pdo;
    }
    $pdo = new PDO('sqlite:' . DB_FILE, null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    $pdo->exec('PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;');
    migrate($pdo);
    return $pdo;
}

function q(string $sql, array $params = []): PDOStatement
{
    $st = db()->prepare($sql);
    $st->execute($params);
    return $st;
}

function migrate(PDO $pdo): void
{
    $pdo->exec(<<<SQL
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            hearts INTEGER NOT NULL DEFAULT 5,
            hearts_at INTEGER NOT NULL DEFAULT 0,
            streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            last_active TEXT,
            daily_goal INTEGER NOT NULL DEFAULT 20,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            color TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            title TEXT NOT NULL,
            icon TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('char', 'word', 'sentence')),
            jp TEXT NOT NULL,
            kanji TEXT,
            romaji TEXT NOT NULL,
            meaning TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS progress (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
            completed INTEGER NOT NULL DEFAULT 0,
            best_accuracy INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, lesson_id)
        );
        CREATE TABLE IF NOT EXISTS user_items (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            strength INTEGER NOT NULL DEFAULT 0,
            correct INTEGER NOT NULL DEFAULT 0,
            wrong INTEGER NOT NULL DEFAULT 0,
            last_seen INTEGER NOT NULL,
            PRIMARY KEY (user_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS xp_log (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        );
        CREATE INDEX IF NOT EXISTS idx_items_lesson ON items(lesson_id);
        CREATE INDEX IF NOT EXISTS idx_xp_day ON xp_log(day);
    SQL);

    if ((int)$pdo->query('SELECT COUNT(*) FROM units')->fetchColumn() === 0) {
        seed($pdo);
    }
}

/** Materi pelajaran. Item char: [kana, romaji]; word: [kana, romaji, arti, kanji?]; sentence: [token token, romaji, arti]. */
function seedData(): array
{
    $chars = fn(string $s): array => array_map(fn($p) => explode(':', $p), explode(' ', $s));

    return [
        ['Hiragana 1', 'Huruf vokal dan baris K, S, T, N', '#58cc02', [
            ['Vokal', 'あ', 'char', $chars('あ:a い:i う:u え:e お:o')],
            ['Baris K', 'か', 'char', $chars('か:ka き:ki く:ku け:ke こ:ko')],
            ['Baris S', 'さ', 'char', $chars('さ:sa し:shi す:su せ:se そ:so')],
            ['Baris T', 'た', 'char', $chars('た:ta ち:chi つ:tsu て:te と:to')],
            ['Baris N', 'な', 'char', $chars('な:na に:ni ぬ:nu ね:ne の:no')],
        ]],
        ['Hiragana 2', 'Baris H, M, Y, R, W dan ん', '#1cb0f6', [
            ['Baris H', 'は', 'char', $chars('は:ha ひ:hi ふ:fu へ:he ほ:ho')],
            ['Baris M', 'ま', 'char', $chars('ま:ma み:mi む:mu め:me も:mo')],
            ['Baris Y & W', 'や', 'char', $chars('や:ya ゆ:yu よ:yo わ:wa を:wo ん:n')],
            ['Baris R', 'ら', 'char', $chars('ら:ra り:ri る:ru れ:re ろ:ro')],
        ]],
        ['Salam', 'Sapaan sehari-hari', '#ce82ff', [
            ['Sapaan', '👋', 'word', [
                ['おはよう', 'ohayou', 'selamat pagi'],
                ['こんにちは', 'konnichiwa', 'halo / selamat siang'],
                ['こんばんは', 'konbanwa', 'selamat malam'],
                ['さようなら', 'sayounara', 'selamat tinggal'],
            ]],
            ['Sopan santun', '🙇', 'word', [
                ['ありがとう', 'arigatou', 'terima kasih'],
                ['すみません', 'sumimasen', 'permisi / maaf'],
                ['おやすみ', 'oyasumi', 'selamat tidur'],
                ['はじめまして', 'hajimemashite', 'salam kenal'],
                ['はい', 'hai', 'ya'],
                ['いいえ', 'iie', 'tidak'],
            ]],
        ]],
        ['Angka', 'Berhitung 1 sampai 10', '#ff9600', [
            ['1 - 5', '一', 'word', [
                ['いち', 'ichi', 'satu', '一'],
                ['に', 'ni', 'dua', '二'],
                ['さん', 'san', 'tiga', '三'],
                ['よん', 'yon', 'empat', '四'],
                ['ご', 'go', 'lima', '五'],
            ]],
            ['6 - 10', '十', 'word', [
                ['ろく', 'roku', 'enam', '六'],
                ['なな', 'nana', 'tujuh', '七'],
                ['はち', 'hachi', 'delapan', '八'],
                ['きゅう', 'kyuu', 'sembilan', '九'],
                ['じゅう', 'juu', 'sepuluh', '十'],
            ]],
        ]],
        ['Katakana', 'Huruf untuk kata serapan', '#ff4b4b', [
            ['Vokal', 'ア', 'char', $chars('ア:a イ:i ウ:u エ:e オ:o')],
            ['Baris K', 'カ', 'char', $chars('カ:ka キ:ki ク:ku ケ:ke コ:ko')],
            ['Baris S & T', 'サ', 'char', $chars('サ:sa シ:shi ス:su タ:ta テ:te ト:to')],
            ['Kata serapan', '☕', 'word', [
                ['コーヒー', 'koohii', 'kopi'],
                ['テレビ', 'terebi', 'televisi'],
                ['パン', 'pan', 'roti'],
                ['カメラ', 'kamera', 'kamera'],
                ['ホテル', 'hoteru', 'hotel'],
                ['ジュース', 'juusu', 'jus'],
            ]],
        ]],
        ['Kata Benda', 'Hewan, benda, dan orang', '#2b70c9', [
            ['Hewan & benda', '🐱', 'word', [
                ['ねこ', 'neko', 'kucing', '猫'],
                ['いぬ', 'inu', 'anjing', '犬'],
                ['みず', 'mizu', 'air', '水'],
                ['ほん', 'hon', 'buku', '本'],
                ['くるま', 'kuruma', 'mobil', '車'],
            ]],
            ['Orang & tempat', '🏫', 'word', [
                ['がっこう', 'gakkou', 'sekolah', '学校'],
                ['せんせい', 'sensei', 'guru', '先生'],
                ['がくせい', 'gakusei', 'pelajar', '学生'],
                ['いえ', 'ie', 'rumah', '家'],
                ['たべもの', 'tabemono', 'makanan', '食べ物'],
            ]],
        ]],
        ['Kata Sifat', 'Menjelaskan sesuatu', '#00cd9c', [
            ['Ukuran & suhu', '🌡️', 'word', [
                ['おおきい', 'ookii', 'besar', '大きい'],
                ['ちいさい', 'chiisai', 'kecil', '小さい'],
                ['あつい', 'atsui', 'panas', '暑い'],
                ['さむい', 'samui', 'dingin', '寒い'],
            ]],
            ['Harga & rasa', '💴', 'word', [
                ['おいしい', 'oishii', 'enak'],
                ['たかい', 'takai', 'mahal', '高い'],
                ['やすい', 'yasui', 'murah', '安い'],
                ['あたらしい', 'atarashii', 'baru', '新しい'],
            ]],
        ]],
        ['Kalimat Dasar', 'Menyusun kalimat pertamamu', '#a560e8', [
            ['Perkenalan', '🙋', 'sentence', [
                ['わたし は がくせい です', 'watashi wa gakusei desu', 'Saya adalah pelajar'],
                ['わたし は インドネシア じん です', 'watashi wa indoneshia jin desu', 'Saya orang Indonesia'],
                ['これ は ほん です', 'kore wa hon desu', 'Ini adalah buku'],
                ['あれ は がっこう です', 'are wa gakkou desu', 'Itu adalah sekolah'],
            ]],
            ['Sehari-hari', '🍵', 'sentence', [
                ['ねこ が すき です', 'neko ga suki desu', 'Saya suka kucing'],
                ['みず を ください', 'mizu o kudasai', 'Tolong beri saya air'],
                ['いぬ が います', 'inu ga imasu', 'Ada anjing'],
                ['この コーヒー は おいしい です', 'kono koohii wa oishii desu', 'Kopi ini enak'],
            ]],
        ]],
    ];
}

function seed(PDO $pdo): void
{
    $pdo->beginTransaction();
    $unitSt = $pdo->prepare('INSERT INTO units (position, title, description, color) VALUES (?, ?, ?, ?)');
    $lessonSt = $pdo->prepare('INSERT INTO lessons (unit_id, position, title, icon) VALUES (?, ?, ?, ?)');
    $itemSt = $pdo->prepare('INSERT INTO items (lesson_id, kind, jp, kanji, romaji, meaning) VALUES (?, ?, ?, ?, ?, ?)');
    foreach (seedData() as $ui => [$title, $desc, $color, $lessons]) {
        $unitSt->execute([$ui + 1, $title, $desc, $color]);
        $unitId = (int)$pdo->lastInsertId();
        foreach ($lessons as $li => [$lessonTitle, $icon, $kind, $items]) {
            $lessonSt->execute([$unitId, $li + 1, $lessonTitle, $icon]);
            $lessonId = (int)$pdo->lastInsertId();
            foreach ($items as $it) {
                $itemSt->execute([$lessonId, $kind, $it[0], $it[3] ?? null, $it[1], $it[2] ?? $it[1]]);
            }
        }
    }
    $pdo->commit();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function h(mixed $s): string
{
    return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8');
}

function today(): string
{
    return date('Y-m-d');
}

function yesterday(): string
{
    return date('Y-m-d', strtotime('-1 day'));
}

function csrf(): string
{
    return $_SESSION['csrf'] ??= bin2hex(random_bytes(16));
}

function checkCsrf(?string $token): void
{
    if (!$token || !hash_equals(csrf(), $token)) {
        http_response_code(403);
        exit('Token CSRF tidak valid. Muat ulang halaman.');
    }
}

function redirect(string $to): never
{
    header('Location: ' . $to);
    exit;
}

function jsonOut(array $data, int $code = 200): never
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function flash(?string $msg = null): ?string
{
    if ($msg !== null) {
        $_SESSION['flash'] = $msg;
        return null;
    }
    $m = $_SESSION['flash'] ?? null;
    unset($_SESSION['flash']);
    return $m;
}

/** User yang login, dengan hati yang sudah diisi ulang dan streak yang sudah dicek. */
function currentUser(): ?array
{
    if (empty($_SESSION['uid'])) {
        return null;
    }
    $u = q('SELECT * FROM users WHERE id = ?', [$_SESSION['uid']])->fetch();
    if (!$u) {
        unset($_SESSION['uid']);
        return null;
    }
    foreach (['id', 'xp', 'hearts', 'hearts_at', 'streak', 'best_streak', 'daily_goal'] as $k) {
        $u[$k] = (int)$u[$k];
    }

    $now = time();
    if ($u['hearts'] < MAX_HEARTS) {
        $gain = intdiv($now - $u['hearts_at'], HEART_REFILL_SECONDS);
        if ($gain > 0) {
            $u['hearts'] = min(MAX_HEARTS, $u['hearts'] + $gain);
            $u['hearts_at'] = $u['hearts'] >= MAX_HEARTS ? $now : $u['hearts_at'] + $gain * HEART_REFILL_SECONDS;
            q('UPDATE users SET hearts = ?, hearts_at = ? WHERE id = ?', [$u['hearts'], $u['hearts_at'], $u['id']]);
        }
    }

    if ($u['streak'] > 0 && !in_array($u['last_active'], [today(), yesterday()], true)) {
        $u['streak'] = 0;
        q('UPDATE users SET streak = 0 WHERE id = ?', [$u['id']]);
    }
    return $u;
}

function secondsToNextHeart(array $u): int
{
    if ($u['hearts'] >= MAX_HEARTS) {
        return 0;
    }
    return max(0, $u['hearts_at'] + HEART_REFILL_SECONDS - time());
}

function dailyXp(int $uid): int
{
    return (int)q('SELECT xp FROM xp_log WHERE user_id = ? AND day = ?', [$uid, today()])->fetchColumn();
}

/** Semua pelajaran berurutan beserta status terbuka/selesai untuk user. */
function lessonMap(int $uid): array
{
    $rows = q(<<<SQL
        SELECT l.id, l.title, l.icon, l.position, u.id AS unit_id, u.title AS unit_title,
               u.description AS unit_desc, u.color, COALESCE(p.completed, 0) AS completed,
               COALESCE(p.best_accuracy, 0) AS best_accuracy
        FROM lessons l
        JOIN units u ON u.id = l.unit_id
        LEFT JOIN progress p ON p.lesson_id = l.id AND p.user_id = ?
        ORDER BY u.position, l.position
    SQL, [$uid])->fetchAll();

    $prevDone = true;
    $currentSet = false;
    foreach ($rows as &$r) {
        $r['done'] = (int)$r['completed'] > 0;
        $r['unlocked'] = $prevDone;
        $r['current'] = false;
        if ($r['unlocked'] && !$r['done'] && !$currentSet) {
            $r['current'] = $currentSet = true;
        }
        $prevDone = $r['unlocked'] && $r['done'];
    }
    return $rows;
}

function findLesson(int $uid, int $lessonId): ?array
{
    foreach (lessonMap($uid) as $l) {
        if ((int)$l['id'] === $lessonId) {
            return $l;
        }
    }
    return null;
}

function itemsForJs(array $rows): array
{
    return array_map(fn($r) => [
        'id' => (int)$r['id'],
        'kind' => $r['kind'],
        'jp' => $r['jp'],
        'kanji' => $r['kanji'],
        'romaji' => $r['romaji'],
        'meaning' => $r['meaning'],
    ], $rows);
}

// ---------------------------------------------------------------------------
// API JSON (dipanggil dari halaman pelajaran)
// ---------------------------------------------------------------------------

if (isset($_GET['api'])) {
    $u = currentUser();
    if (!$u) {
        jsonOut(['error' => 'Belum login'], 401);
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        jsonOut(['error' => 'Method tidak didukung'], 405);
    }
    checkCsrf($_SERVER['HTTP_X_CSRF'] ?? null);
    $in = json_decode((string)file_get_contents('php://input'), true) ?: [];
    $practice = !empty($in['practice']);

    switch ($_GET['api']) {
        case 'answer':
            $itemId = (int)($in['item_id'] ?? 0);
            $ok = !empty($in['correct']);
            if (!q('SELECT 1 FROM items WHERE id = ?', [$itemId])->fetchColumn()) {
                jsonOut(['error' => 'Item tidak ditemukan'], 404);
            }
            q(<<<SQL
                INSERT INTO user_items (user_id, item_id, strength, correct, wrong, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, item_id) DO UPDATE SET
                    strength = MAX(0, MIN(5, strength + ?)),
                    correct = correct + excluded.correct,
                    wrong = wrong + excluded.wrong,
                    last_seen = excluded.last_seen
            SQL, [$u['id'], $itemId, $ok ? 1 : 0, $ok ? 1 : 0, $ok ? 0 : 1, time(), $ok ? 1 : -2]);

            if (!$ok && !$practice && $u['hearts'] > 0) {
                $heartsAt = $u['hearts'] >= MAX_HEARTS ? time() : $u['hearts_at'];
                $u['hearts']--;
                q('UPDATE users SET hearts = ?, hearts_at = ? WHERE id = ?', [$u['hearts'], $heartsAt, $u['id']]);
            }
            jsonOut(['hearts' => $u['hearts']]);

        case 'finish':
            $total = max(1, (int)($in['total'] ?? 1));
            $mistakes = max(0, (int)($in['mistakes'] ?? 0));
            $accuracy = (int)round(100 * $total / ($total + $mistakes));

            if ($practice) {
                $xp = XP_PRACTICE;
                $hearts = min(MAX_HEARTS, $u['hearts'] + 1);
                $heartsAt = $hearts >= MAX_HEARTS ? time() : $u['hearts_at'];
                q('UPDATE users SET hearts = ?, hearts_at = ? WHERE id = ?', [$hearts, $heartsAt, $u['id']]);
                $u['hearts'] = $hearts;
            } else {
                $lesson = findLesson($u['id'], (int)($in['lesson_id'] ?? 0));
                if (!$lesson || !$lesson['unlocked']) {
                    jsonOut(['error' => 'Pelajaran terkunci'], 403);
                }
                if ($u['hearts'] <= 0) {
                    jsonOut(['error' => 'Hati habis'], 403);
                }
                $xp = XP_LESSON + ($mistakes === 0 ? XP_PERFECT_BONUS : 0);
                q(<<<SQL
                    INSERT INTO progress (user_id, lesson_id, completed, best_accuracy, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT (user_id, lesson_id) DO UPDATE SET
                        completed = completed + 1,
                        best_accuracy = MAX(best_accuracy, excluded.best_accuracy),
                        updated_at = excluded.updated_at
                SQL, [$u['id'], $lesson['id'], $accuracy, time()]);
            }

            $extended = $u['last_active'] !== today();
            $streak = $extended ? ($u['last_active'] === yesterday() ? $u['streak'] + 1 : 1) : $u['streak'];
            q('UPDATE users SET xp = xp + ?, streak = ?, best_streak = MAX(best_streak, ?), last_active = ? WHERE id = ?',
                [$xp, $streak, $streak, today(), $u['id']]);
            q(<<<SQL
                INSERT INTO xp_log (user_id, day, xp) VALUES (?, ?, ?)
                ON CONFLICT (user_id, day) DO UPDATE SET xp = xp + excluded.xp
            SQL, [$u['id'], today(), $xp]);

            jsonOut([
                'xp' => $xp,
                'accuracy' => $accuracy,
                'streak' => $streak,
                'extended' => $extended,
                'daily_xp' => dailyXp($u['id']),
                'daily_goal' => $u['daily_goal'],
                'hearts' => $u['hearts'],
            ]);
    }
    jsonOut(['error' => 'API tidak dikenal'], 404);
}

// ---------------------------------------------------------------------------
// Aksi form
// ---------------------------------------------------------------------------

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    checkCsrf($_POST['csrf'] ?? null);
    $action = $_POST['action'] ?? '';
    $username = trim((string)($_POST['username'] ?? ''));
    $password = (string)($_POST['password'] ?? '');

    if ($action === 'register') {
        if (!preg_match('/^[A-Za-z0-9_]{3,20}$/', $username)) {
            flash('Username 3-20 karakter: huruf, angka, atau _.');
        } elseif (strlen($password) < 6) {
            flash('Password minimal 6 karakter.');
        } elseif (q('SELECT 1 FROM users WHERE username = ?', [$username])->fetchColumn()) {
            flash('Username sudah dipakai.');
        } else {
            q('INSERT INTO users (username, password_hash, hearts, hearts_at) VALUES (?, ?, ?, ?)',
                [$username, password_hash($password, PASSWORD_DEFAULT), MAX_HEARTS, time()]);
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)db()->lastInsertId();
            flash('Selamat datang, ' . $username . '! ようこそ 🎉');
            redirect('?page=home');
        }
        redirect('?page=auth&tab=register');
    }

    if ($action === 'login') {
        $row = q('SELECT id, password_hash FROM users WHERE username = ?', [$username])->fetch();
        if ($row && password_verify($password, $row['password_hash'])) {
            session_regenerate_id(true);
            $_SESSION['uid'] = (int)$row['id'];
            redirect('?page=home');
        }
        flash('Username atau password salah.');
        redirect('?page=auth');
    }

    $u = currentUser();
    if (!$u) {
        redirect('?page=auth');
    }

    if ($action === 'logout') {
        $_SESSION = [];
        session_destroy();
        redirect('?page=auth');
    }

    if ($action === 'goal') {
        $goal = (int)($_POST['goal'] ?? 20);
        if (isset(DAILY_GOALS[$goal])) {
            q('UPDATE users SET daily_goal = ? WHERE id = ?', [$goal, $u['id']]);
            flash('Target harian diubah ke ' . $goal . ' XP.');
        }
        redirect('?page=profile');
    }

    redirect('?page=home');
}

// ---------------------------------------------------------------------------
// Halaman
// ---------------------------------------------------------------------------

$user = currentUser();
$page = $_GET['page'] ?? 'home';
if (!$user) {
    $page = 'auth';
} elseif ($page === 'auth') {
    $page = 'home';
}

function layoutStart(string $title, ?array $user, bool $bare = false): void
{
    ?><!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title><?= h($title) ?> · Nihongo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@600;700;800;900&family=Noto+Sans+JP:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
    --green: #58cc02; --green-d: #58a700; --blue: #1cb0f6; --blue-d: #1899d6;
    --red: #ff4b4b; --red-d: #ea2b2b; --gold: #ffc800; --gold-d: #e5a400;
    --bg: #ffffff; --card: #ffffff; --text: #3c3c3c; --muted: #777777; --line: #e5e5e5;
    --soft: #f7f7f7; --good-bg: #d7ffb8; --bad-bg: #ffdfe0; --lock: #e5e5e5; --lock-d: #b7b7b7;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #131f24; --card: #1a2b33; --text: #f1f7fb; --muted: #8fa3ad; --line: #37464f;
        --soft: #202f36; --good-bg: #1f3a10; --bad-bg: #3f1a1c; --lock: #37464f; --lock-d: #2a383f;
    }
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; }
body {
    font-family: Nunito, system-ui, sans-serif; font-weight: 700; color: var(--text);
    background: var(--bg); min-height: 100vh;
}
a { color: inherit; text-decoration: none; }
.jp { font-family: 'Noto Sans JP', 'Hiragino Sans', 'Yu Gothic', sans-serif; }
.wrap { max-width: 600px; margin: 0 auto; padding: 0 16px 110px; }
.btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    border: 0; border-radius: 16px; padding: 14px 20px; font: inherit; font-weight: 800;
    text-transform: uppercase; letter-spacing: .6px; font-size: 15px; cursor: pointer;
    background: var(--green); color: #fff; box-shadow: 0 4px 0 var(--green-d);
    transition: transform .05s, filter .15s; width: 100%;
}
.btn:active:not(:disabled) { transform: translateY(4px); box-shadow: 0 0 0 transparent; }
.btn:hover:not(:disabled) { filter: brightness(1.05); }
.btn:disabled { background: var(--lock); color: var(--muted); box-shadow: 0 4px 0 var(--lock-d); cursor: default; }
.btn.blue { background: var(--blue); box-shadow: 0 4px 0 var(--blue-d); }
.btn.red { background: var(--red); box-shadow: 0 4px 0 var(--red-d); }
.btn.ghost { background: transparent; color: var(--blue); box-shadow: none; border: 2px solid var(--line); box-shadow: 0 3px 0 var(--line); }
.card { background: var(--card); border: 2px solid var(--line); border-radius: 18px; padding: 18px; margin: 16px 0; }
.muted { color: var(--muted); }
.flash { background: var(--blue); color: #fff; padding: 12px 16px; border-radius: 14px; margin: 16px 0 0; }
.topbar {
    position: sticky; top: 0; z-index: 10; background: var(--bg); border-bottom: 2px solid var(--line);
}
.topbar .in { max-width: 600px; margin: 0 auto; padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.logo { font-weight: 900; font-size: 22px; color: var(--green); letter-spacing: -.5px; }
.stats { display: flex; gap: 16px; font-size: 17px; }
.stat { display: flex; align-items: center; gap: 4px; }
.stat.fire { color: #ff9600; } .stat.gem { color: var(--blue); } .stat.heart { color: var(--red); }
.stat.off { filter: grayscale(1); opacity: .6; }
.nav {
    position: fixed; bottom: 0; left: 0; right: 0; background: var(--bg); border-top: 2px solid var(--line); z-index: 10;
    padding-bottom: env(safe-area-inset-bottom);
}
.nav .in { max-width: 600px; margin: 0 auto; display: flex; }
.nav a { flex: 1; text-align: center; padding: 10px 4px 12px; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
.nav a span { display: block; font-size: 24px; margin-bottom: 2px; }
.nav a.on { color: var(--blue); }
.nav a.on span { transform: scale(1.1); }
.bar { height: 16px; background: var(--line); border-radius: 99px; overflow: hidden; }
.bar > i { display: block; height: 100%; background: var(--gold); border-radius: 99px; transition: width .4s; position: relative; }
.bar > i::after { content: ''; position: absolute; left: 8px; right: 8px; top: 4px; height: 4px; border-radius: 9px; background: rgba(255,255,255,.35); }
.unit { border-radius: 18px; color: #fff; padding: 18px 20px; margin: 26px 0 8px; box-shadow: 0 4px 0 rgba(0,0,0,.18); }
.unit small { display: block; text-transform: uppercase; opacity: .85; letter-spacing: 1px; font-size: 12px; }
.unit b { font-size: 21px; display: block; }
.unit p { margin: 4px 0 0; opacity: .95; font-weight: 600; }
.path { display: flex; flex-direction: column; align-items: center; gap: 34px; padding: 34px 0 18px; }
.node { position: relative; display: flex; flex-direction: column; align-items: center; width: 120px; }
.node .c {
    width: 72px; height: 66px; border-radius: 50%; display: grid; place-items: center; font-size: 28px; color: #fff;
    background: var(--col); box-shadow: 0 7px 0 rgba(0,0,0,.22); transition: transform .08s; font-family: 'Noto Sans JP', sans-serif;
}
.node a:active .c { transform: translateY(6px); box-shadow: 0 1px 0 rgba(0,0,0,.22); }
.node.locked .c { background: var(--lock); box-shadow: 0 7px 0 var(--lock-d); color: var(--muted); filter: grayscale(1); }
.node.done .c { background: var(--gold); box-shadow: 0 7px 0 var(--gold-d); }
.node .t { margin-top: 12px; font-size: 13px; text-align: center; color: var(--muted); }
.node.current .c { outline: 6px solid color-mix(in srgb, var(--col) 30%, transparent); outline-offset: 4px; animation: pulse 1.6s infinite; }
.node .bubble {
    position: absolute; top: -40px; background: var(--card); color: var(--col); border: 2px solid var(--line);
    padding: 6px 12px; border-radius: 12px; font-size: 13px; font-weight: 900; text-transform: uppercase; white-space: nowrap;
    animation: bob 1.2s ease-in-out infinite alternate;
}
.node .crown { position: absolute; right: 18px; bottom: 26px; font-size: 18px; }
@keyframes pulse { 50% { outline-offset: 8px; } }
@keyframes bob { to { transform: translateY(-5px); } }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.tile-stat { border: 2px solid var(--line); border-radius: 16px; padding: 14px; }
.tile-stat b { font-size: 22px; display: block; }
.tile-stat span { color: var(--muted); font-size: 13px; }
.row { display: flex; align-items: center; gap: 12px; }
.lb { list-style: none; padding: 0; margin: 0; }
.lb li { display: flex; align-items: center; gap: 14px; padding: 12px 10px; border-radius: 14px; }
.lb li.me { background: color-mix(in srgb, var(--blue) 15%, transparent); }
.lb .rank { width: 28px; text-align: center; font-weight: 900; color: var(--muted); }
.lb .av { width: 42px; height: 42px; border-radius: 50%; display: grid; place-items: center; color: #fff; font-weight: 900; }
.lb .nm { flex: 1; }
.chart { display: flex; align-items: flex-end; gap: 8px; height: 120px; margin-top: 12px; }
.chart div { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; font-size: 11px; color: var(--muted); }
.chart i { width: 100%; background: var(--gold); border-radius: 8px 8px 4px 4px; min-height: 4px; margin: 4px 0; }
.chart div.today i { background: var(--green); }
.goals { display: grid; gap: 10px; }
.goals label { display: flex; justify-content: space-between; border: 2px solid var(--line); border-radius: 14px; padding: 14px 16px; cursor: pointer; }
.goals input { display: none; }
.goals input:checked + label { border-color: var(--blue); background: color-mix(in srgb, var(--blue) 12%, transparent); color: var(--blue); }
.kana-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.kana { border: 2px solid var(--line); border-bottom-width: 4px; border-radius: 14px; padding: 10px 4px; text-align: center; cursor: pointer; background: var(--card); color: var(--text); font: inherit; }
.kana b { display: block; font-size: 26px; font-family: 'Noto Sans JP', sans-serif; }
.kana small { color: var(--muted); }
.kana.learned { border-color: color-mix(in srgb, var(--green) 60%, var(--line)); }
input[type=text], input[type=password] {
    width: 100%; font: inherit; font-size: 17px; padding: 14px 16px; border-radius: 14px; border: 2px solid var(--line);
    background: var(--soft); color: var(--text); outline: none;
}
input[type=text]:focus, input[type=password]:focus { border-color: var(--blue); }
h1 { font-weight: 900; }
<?php if ($bare): ?>body { overflow-x: hidden; }<?php endif; ?>
</style>
</head>
<body>
<?php
}

function topbar(array $user): void
{
    $next = secondsToNextHeart($user);
    ?>
<div class="topbar"><div class="in">
    <a class="logo" href="?page=home">にほんご</a>
    <div class="stats">
        <span class="stat fire <?= $user['streak'] ? '' : 'off' ?>" title="Streak">🔥 <?= $user['streak'] ?></span>
        <span class="stat gem" title="Total XP">💎 <?= $user['xp'] ?></span>
        <span class="stat heart" title="<?= $next ? 'Hati berikutnya dalam ' . ceil($next / 60) . ' menit' : 'Hati penuh' ?>">❤️ <?= $user['hearts'] ?></span>
    </div>
</div></div>
<?php
}

function layoutEnd(?array $user, string $active = ''): void
{
    if ($user) {
        $tabs = ['home' => ['🏠', 'Belajar'], 'kana' => ['あ', 'Huruf'], 'leaderboard' => ['🏆', 'Peringkat'], 'profile' => ['👤', 'Profil']];
        echo '<nav class="nav"><div class="in">';
        foreach ($tabs as $k => [$ico, $label]) {
            printf('<a href="?page=%s" class="%s"><span>%s</span>%s</a>', $k, $k === $active ? 'on' : '', $ico, $label);
        }
        echo '</div></nav>';
    }
    echo "\n</body>\n</html>";
}

function avatarColor(string $name): string
{
    $colors = ['#58cc02', '#1cb0f6', '#ce82ff', '#ff9600', '#ff4b4b', '#2b70c9', '#00cd9c'];
    return $colors[crc32(strtolower($name)) % count($colors)];
}

// --- Login / daftar --------------------------------------------------------
if ($page === 'auth') {
    $tab = ($_GET['tab'] ?? '') === 'register' ? 'register' : 'login';
    $msg = flash();
    layoutStart('Masuk', null);
    ?>
<div class="wrap" style="padding-top:40px">
    <div style="text-align:center">
        <div style="font-size:72px">🦉</div>
        <h1 style="font-size:34px;margin:8px 0 4px;color:var(--green)">にほんご</h1>
        <p class="muted" style="margin:0 0 24px">Belajar bahasa Jepang 5 menit sehari. Gratis, seru, dan efektif.</p>
    </div>
    <?php if ($msg): ?><div class="flash"><?= h($msg) ?></div><?php endif; ?>
    <div class="card">
        <div class="row" style="margin-bottom:16px">
            <a class="btn <?= $tab === 'login' ? 'blue' : 'ghost' ?>" href="?page=auth">Masuk</a>
            <a class="btn <?= $tab === 'register' ? 'blue' : 'ghost' ?>" href="?page=auth&tab=register">Daftar</a>
        </div>
        <form method="post" style="display:grid;gap:12px">
            <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
            <input type="hidden" name="action" value="<?= $tab ?>">
            <input type="text" name="username" placeholder="Username" autocomplete="username" required autofocus>
            <input type="password" name="password" placeholder="Password" autocomplete="<?= $tab === 'login' ? 'current-password' : 'new-password' ?>" required>
            <button class="btn"><?= $tab === 'login' ? 'Masuk' : 'Buat akun' ?></button>
        </form>
    </div>
    <div class="grid2">
        <div class="tile-stat"><b>🎯 Bertahap</b><span>Mulai dari hiragana sampai kalimat.</span></div>
        <div class="tile-stat"><b>🔥 Streak</b><span>Latihan tiap hari, jaga apimu.</span></div>
        <div class="tile-stat"><b>🔊 Audio</b><span>Dengar pelafalan asli bahasa Jepang.</span></div>
        <div class="tile-stat"><b>🏆 Liga</b><span>Bersaing XP mingguan.</span></div>
    </div>
</div>
<?php
    layoutEnd(null);
    exit;
}

// --- Beranda: peta pelajaran ----------------------------------------------
if ($page === 'home') {
    $lessons = lessonMap($user['id']);
    $daily = dailyXp($user['id']);
    $doneCount = count(array_filter($lessons, fn($l) => $l['done']));
    $msg = flash();
    layoutStart('Belajar', $user);
    topbar($user);
    ?>
<div class="wrap">
    <?php if ($msg): ?><div class="flash"><?= h($msg) ?></div><?php endif; ?>
    <div class="card">
        <div class="row" style="justify-content:space-between;margin-bottom:10px">
            <span>🎯 Target harian</span>
            <span class="muted"><?= min($daily, $user['daily_goal']) ?> / <?= $user['daily_goal'] ?> XP</span>
        </div>
        <div class="bar"><i style="width:<?= min(100, round(100 * $daily / max(1, $user['daily_goal']))) ?>%"></i></div>
        <?php if ($daily >= $user['daily_goal']): ?><p style="margin:10px 0 0;color:var(--green)">Target hari ini tercapai! すごい！</p><?php endif; ?>
    </div>
    <?php if ($doneCount > 0): ?>
    <a class="card row" href="?page=practice" style="display:flex">
        <span style="font-size:34px">🏋️</span>
        <span style="flex:1"><b>Latihan</b><br><span class="muted" style="font-weight:600">Ulangi kata yang sering salah. Tanpa kehilangan hati, +1 ❤️</span></span>
        <span style="font-size:22px;color:var(--muted)">›</span>
    </a>
    <?php endif; ?>
    <?php if ($user['hearts'] <= 0): ?>
    <div class="card" style="border-color:var(--red)">💔 Hatimu habis. Hati baru dalam <?= ceil(secondsToNextHeart($user) / 60) ?> menit, atau dapatkan lewat Latihan.</div>
    <?php endif; ?>
    <?php
    $offsets = [0, 44, 70, 44, 0, -44, -70, -44];
    $unitId = null;
    $i = 0;
    foreach ($lessons as $l):
        if ($l['unit_id'] !== $unitId):
            if ($unitId !== null) echo '</div>';
            $unitId = $l['unit_id'];
            $i = 0; ?>
    <div class="unit" style="background:<?= h($l['color']) ?>">
        <small>Unit</small><b><?= h($l['unit_title']) ?></b><p><?= h($l['unit_desc']) ?></p>
    </div>
    <div class="path">
        <?php endif;
        $cls = $l['done'] ? 'done' : ($l['unlocked'] ? 'open' : 'locked');
        if ($l['current']) $cls .= ' current'; ?>
        <div class="node <?= $cls ?>" style="--col:<?= h($l['color']) ?>;transform:translateX(<?= $offsets[$i++ % 8] ?>px)">
            <?php if ($l['current']): ?><span class="bubble">Mulai</span><?php endif; ?>
            <?php if ($l['unlocked']): ?>
                <a href="?page=lesson&id=<?= (int)$l['id'] ?>" title="<?= h($l['title']) ?>"><span class="c"><?= h($l['icon']) ?></span></a>
            <?php else: ?>
                <span class="c">🔒</span>
            <?php endif; ?>
            <?php if ($l['done']): ?><span class="crown">👑</span><?php endif; ?>
            <span class="t"><?= h($l['title']) ?><?= $l['done'] ? ' · ' . (int)$l['best_accuracy'] . '%' : '' ?></span>
        </div>
    <?php endforeach; ?>
    </div>
    <?php if ($doneCount === count($lessons)): ?>
    <div class="card" style="text-align:center"><div style="font-size:48px">🎌</div><b>Semua pelajaran selesai!</b><p class="muted">Terus latihan supaya tidak lupa. がんばって！</p></div>
    <?php endif; ?>
</div>
<?php
    layoutEnd($user, 'home');
    exit;
}

// --- Tabel huruf ------------------------------------------------------------
if ($page === 'kana') {
    $rows = q(<<<SQL
        SELECT i.jp, i.romaji, u.title AS unit_title, ui.strength
        FROM items i JOIN lessons l ON l.id = i.lesson_id JOIN units u ON u.id = l.unit_id
        LEFT JOIN user_items ui ON ui.item_id = i.id AND ui.user_id = ?
        WHERE i.kind = 'char' ORDER BY u.position, l.position, i.id
    SQL, [$user['id']])->fetchAll();
    $groups = [];
    foreach ($rows as $r) {
        $script = preg_match('/\p{Katakana}/u', $r['jp']) ? 'Katakana' : 'Hiragana';
        $groups[$script][] = $r;
    }
    layoutStart('Huruf', $user);
    topbar($user);
    ?>
<div class="wrap">
    <h1 style="margin:20px 0 4px">Huruf Jepang</h1>
    <p class="muted" style="margin:0">Ketuk huruf untuk mendengar bunyinya. Huruf yang sudah kamu latih diberi garis hijau.</p>
    <?php foreach ($groups as $name => $list): ?>
    <div class="card">
        <b style="font-size:18px"><?= h($name) ?></b>
        <div class="kana-grid" style="margin-top:12px">
            <?php foreach ($list as $r): ?>
            <button class="kana <?= $r['strength'] !== null ? 'learned' : '' ?>" data-say="<?= h($r['jp']) ?>"><b><?= h($r['jp']) ?></b><small><?= h($r['romaji']) ?></small></button>
            <?php endforeach; ?>
        </div>
    </div>
    <?php endforeach; ?>
</div>
<script>
document.querySelectorAll('[data-say]').forEach(b => b.addEventListener('click', () => {
    if (!('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(b.dataset.say);
    u.lang = 'ja-JP'; u.rate = 0.8;
    const v = speechSynthesis.getVoices().find(v => /^ja/i.test(v.lang));
    if (v) u.voice = v;
    speechSynthesis.cancel(); speechSynthesis.speak(u);
}));
</script>
<?php
    layoutEnd($user, 'kana');
    exit;
}

// --- Peringkat mingguan -----------------------------------------------------
if ($page === 'leaderboard') {
    $monday = date('Y-m-d', strtotime('monday this week'));
    $rows = q(<<<SQL
        SELECT u.id, u.username, u.streak, COALESCE(SUM(x.xp), 0) AS week_xp
        FROM users u LEFT JOIN xp_log x ON x.user_id = u.id AND x.day >= ?
        GROUP BY u.id ORDER BY week_xp DESC, u.xp DESC, u.username LIMIT 30
    SQL, [$monday])->fetchAll();
    $daysLeft = 7 - (int)date('N') + 1;
    layoutStart('Peringkat', $user);
    topbar($user);
    ?>
<div class="wrap">
    <div style="text-align:center;margin-top:20px">
        <div style="font-size:56px">🏆</div>
        <h1 style="margin:4px 0">Liga Mingguan</h1>
        <p class="muted" style="margin:0">XP minggu ini · reset dalam <?= $daysLeft ?> hari</p>
    </div>
    <div class="card" style="padding:8px">
        <ul class="lb">
        <?php foreach ($rows as $n => $r):
            $medal = ['🥇', '🥈', '🥉'][$n] ?? ($n + 1); ?>
            <li class="<?= (int)$r['id'] === $user['id'] ? 'me' : '' ?>">
                <span class="rank"><?= $medal ?></span>
                <span class="av" style="background:<?= avatarColor($r['username']) ?>"><?= h(strtoupper(mb_substr($r['username'], 0, 1))) ?></span>
                <span class="nm"><?= h($r['username']) ?><?= $r['streak'] ? ' <small class="muted">🔥' . (int)$r['streak'] . '</small>' : '' ?></span>
                <span><?= (int)$r['week_xp'] ?> XP</span>
            </li>
        <?php endforeach; ?>
        </ul>
    </div>
</div>
<?php
    layoutEnd($user, 'leaderboard');
    exit;
}

// --- Profil -------------------------------------------------------------------
if ($page === 'profile') {
    $lessonsDone = (int)q('SELECT COUNT(*) FROM progress WHERE user_id = ?', [$user['id']])->fetchColumn();
    $itemsLearned = (int)q('SELECT COUNT(*) FROM user_items WHERE user_id = ? AND correct > 0', [$user['id']])->fetchColumn();
    $log = q('SELECT day, xp FROM xp_log WHERE user_id = ? AND day >= ?', [$user['id'], date('Y-m-d', strtotime('-6 days'))])
        ->fetchAll(PDO::FETCH_KEY_PAIR);
    $days = [];
    for ($d = 6; $d >= 0; $d--) {
        $key = date('Y-m-d', strtotime("-$d days"));
        $days[$key] = (int)($log[$key] ?? 0);
    }
    $maxXp = max(1, ...array_values($days));
    $dayNames = ['Min', 'Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab'];
    $msg = flash();
    layoutStart('Profil', $user);
    topbar($user);
    ?>
<div class="wrap">
    <?php if ($msg): ?><div class="flash"><?= h($msg) ?></div><?php endif; ?>
    <div class="row" style="margin:24px 0 8px">
        <span class="av" style="width:72px;height:72px;border-radius:50%;display:grid;place-items:center;color:#fff;font-size:32px;font-weight:900;background:<?= avatarColor($user['username']) ?>"><?= h(strtoupper(mb_substr($user['username'], 0, 1))) ?></span>
        <div><h1 style="margin:0"><?= h($user['username']) ?></h1><span class="muted">Bergabung <?= h(date('d M Y', strtotime($user['created_at']))) ?></span></div>
    </div>
    <div class="grid2">
        <div class="tile-stat"><b>🔥 <?= $user['streak'] ?></b><span>Streak hari ini</span></div>
        <div class="tile-stat"><b>⚡ <?= $user['best_streak'] ?></b><span>Streak terbaik</span></div>
        <div class="tile-stat"><b>💎 <?= $user['xp'] ?></b><span>Total XP</span></div>
        <div class="tile-stat"><b>📚 <?= $lessonsDone ?></b><span>Pelajaran selesai</span></div>
        <div class="tile-stat"><b>🧠 <?= $itemsLearned ?></b><span>Huruf & kata dikuasai</span></div>
        <div class="tile-stat"><b>❤️ <?= $user['hearts'] ?>/<?= MAX_HEARTS ?></b><span><?= secondsToNextHeart($user) ? '+1 dalam ' . ceil(secondsToNextHeart($user) / 60) . ' menit' : 'Hati penuh' ?></span></div>
    </div>
    <div class="card">
        <b>XP 7 hari terakhir</b>
        <div class="chart">
            <?php foreach ($days as $day => $xp): ?>
            <div class="<?= $day === today() ? 'today' : '' ?>">
                <span><?= $xp ?: '' ?></span>
                <i style="height:<?= round(80 * $xp / $maxXp) ?>%"></i>
                <span><?= $dayNames[(int)date('w', strtotime($day))] ?></span>
            </div>
            <?php endforeach; ?>
        </div>
    </div>
    <form class="card" method="post">
        <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
        <input type="hidden" name="action" value="goal">
        <b>Target harian</b>
        <div class="goals" style="margin:12px 0 16px">
            <?php foreach (DAILY_GOALS as $xp => $label): ?>
            <input type="radio" name="goal" id="g<?= $xp ?>" value="<?= $xp ?>" <?= $user['daily_goal'] === $xp ? 'checked' : '' ?>>
            <label for="g<?= $xp ?>"><span><?= $label ?></span><span><?= $xp ?> XP / hari</span></label>
            <?php endforeach; ?>
        </div>
        <button class="btn blue">Simpan</button>
    </form>
    <form method="post">
        <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
        <input type="hidden" name="action" value="logout">
        <button class="btn ghost" style="color:var(--red)">Keluar</button>
    </form>
</div>
<?php
    layoutEnd($user, 'profile');
    exit;
}

// --- Pelajaran & latihan -------------------------------------------------------
if ($page === 'lesson' || $page === 'practice') {
    $practice = $page === 'practice';
    $lesson = null;
    $blocked = null;

    if ($practice) {
        $items = q(<<<SQL
            SELECT i.* FROM items i
            JOIN progress p ON p.lesson_id = i.lesson_id AND p.user_id = ?
            LEFT JOIN user_items ui ON ui.item_id = i.id AND ui.user_id = p.user_id
            ORDER BY COALESCE(ui.strength, 0), COALESCE(ui.wrong, 0) DESC, COALESCE(ui.last_seen, 0), RANDOM()
            LIMIT 8
        SQL, [$user['id']])->fetchAll();
        if (!$items) {
            $blocked = ['📚', 'Belum ada yang bisa dilatih', 'Selesaikan pelajaran pertamamu dulu.'];
        }
        $title = 'Latihan';
    } else {
        $lesson = findLesson($user['id'], (int)($_GET['id'] ?? 0));
        if (!$lesson) {
            redirect('?page=home');
        }
        if (!$lesson['unlocked']) {
            $blocked = ['🔒', 'Pelajaran terkunci', 'Selesaikan pelajaran sebelumnya untuk membuka ini.'];
        } elseif ($user['hearts'] <= 0) {
            $blocked = ['💔', 'Hatimu habis', 'Hati baru dalam ' . ceil(secondsToNextHeart($user) / 60) . ' menit. Atau isi ulang lewat Latihan.'];
        }
        $items = q('SELECT * FROM items WHERE lesson_id = ? ORDER BY id', [$lesson['id']])->fetchAll();
        $title = $lesson['title'];
    }

    layoutStart($title, $user, true);
    if ($blocked) {
        [$ico, $head, $text] = $blocked; ?>
<div class="wrap" style="text-align:center;padding-top:80px">
    <div style="font-size:80px"><?= $ico ?></div>
    <h1><?= h($head) ?></h1>
    <p class="muted"><?= h($text) ?></p>
    <div style="display:grid;gap:12px;margin-top:28px">
        <?php if ($head === 'Hatimu habis'): ?><a class="btn blue" href="?page=practice">Latihan</a><?php endif; ?>
        <a class="btn ghost" href="?page=home">Kembali</a>
    </div>
</div>
<?php
        layoutEnd(null);
        exit;
    }

    $pool = itemsForJs(q('SELECT * FROM items')->fetchAll());
    $data = [
        'id' => $lesson ? (int)$lesson['id'] : null,
        'title' => $title,
        'practice' => $practice,
        'items' => itemsForJs($items),
        'pool' => $pool,
        'hearts' => $user['hearts'],
        'csrf' => csrf(),
    ];
    ?>
<style>
.lesson { max-width: 640px; margin: 0 auto; min-height: 100vh; display: flex; flex-direction: column; padding: 0 16px; }
.ltop { display: flex; align-items: center; gap: 14px; padding: 18px 0 10px; }
.x { background: none; border: 0; font-size: 26px; color: var(--muted); cursor: pointer; padding: 0 4px; }
.ltop .bar { flex: 1; }
.ltop .bar > i { background: var(--green); }
.lhearts { color: var(--red); font-size: 18px; min-width: 44px; text-align: right; }
.lhearts.shake { animation: shake .4s; }
#ex { flex: 1; padding: 20px 0 190px; }
#ex h2 { font-size: 24px; margin: 8px 0 22px; font-weight: 900; }
.tag { display: inline-block; color: #ce82ff; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
.prompt { display: flex; align-items: center; gap: 16px; margin-bottom: 26px; flex-wrap: wrap; }
.prompt .big { font-size: 64px; line-height: 1.1; }
.prompt .sent { font-size: 26px; border: 2px solid var(--line); padding: 12px 16px; border-radius: 16px; position: relative; }
.prompt .sub { color: var(--muted); font-size: 15px; display: block; margin-top: 4px; font-family: Nunito, sans-serif; }
.prompt .kanji { color: var(--muted); font-size: 22px; }
.spk {
    width: 56px; height: 56px; border-radius: 16px; border: 0; background: var(--blue); color: #fff; font-size: 26px;
    box-shadow: 0 4px 0 var(--blue-d); cursor: pointer; flex: none;
}
.spk.huge { width: 120px; height: 120px; border-radius: 28px; font-size: 54px; margin: 0 auto 30px; display: block; }
.spk:active { transform: translateY(4px); box-shadow: none; }
.opts { display: grid; gap: 12px; }
.opts.jp-opts { grid-template-columns: 1fr 1fr; }
.opt {
    display: flex; align-items: center; gap: 12px; width: 100%; text-align: left; font: inherit; font-size: 18px;
    padding: 14px 16px; border-radius: 16px; border: 2px solid var(--line); border-bottom-width: 4px;
    background: var(--card); color: var(--text); cursor: pointer;
}
.opt .k { border: 2px solid var(--line); border-radius: 8px; width: 28px; height: 28px; display: grid; place-items: center; font-size: 13px; color: var(--muted); flex: none; }
.jp-opts .opt { flex-direction: column; justify-content: center; font-size: 34px; padding: 22px 8px; font-family: 'Noto Sans JP', sans-serif; text-align: center; }
.jp-opts .opt .k { display: none; }
.opt.sel { border-color: var(--blue); background: color-mix(in srgb, var(--blue) 14%, var(--card)); color: var(--blue); }
.opt.sel .k { border-color: var(--blue); color: var(--blue); }
.opt:disabled { cursor: default; }
.opt.right { border-color: var(--green); background: var(--good-bg); color: var(--green-d); }
.opt.wrong { border-color: var(--red); background: var(--bad-bg); color: var(--red); animation: shake .35s; }
.opt.gone { opacity: .35; border-bottom-width: 2px; }
.match { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.match .col { display: grid; gap: 12px; align-content: start; }
.match .opt { justify-content: center; text-align: center; min-height: 62px; }
.match .opt.jp { font-family: 'Noto Sans JP', sans-serif; font-size: 24px; }
.answer-line { min-height: 64px; border-bottom: 2px solid var(--line); display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 0; margin-bottom: 24px; align-content: flex-start; }
.tiles { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.tilebtn {
    font: inherit; font-size: 18px; padding: 10px 14px; border-radius: 14px; border: 2px solid var(--line); border-bottom-width: 4px;
    background: var(--card); color: var(--text); cursor: pointer;
}
.tilebtn.jp { font-family: 'Noto Sans JP', sans-serif; }
.tilebtn.used { background: var(--line); color: transparent; border-color: var(--line); pointer-events: none; }
textarea.typed {
    width: 100%; min-height: 120px; font: inherit; font-size: 20px; padding: 16px; border-radius: 16px; border: 2px solid var(--line);
    background: var(--soft); color: var(--text); resize: none; outline: none;
}
textarea.typed:focus { border-color: var(--blue); }
#foot { position: fixed; left: 0; right: 0; bottom: 0; border-top: 2px solid var(--line); background: var(--bg); padding-bottom: env(safe-area-inset-bottom); transition: background .15s; }
#foot .in { max-width: 640px; margin: 0 auto; padding: 18px 16px 22px; display: grid; gap: 14px; }
#foot.good { background: var(--good-bg); border-color: transparent; }
#foot.bad { background: var(--bad-bg); border-color: transparent; }
#fb { display: none; }
#foot.good #fb, #foot.bad #fb { display: block; }
#fb b { font-size: 22px; display: block; margin-bottom: 4px; }
#foot.good #fb { color: var(--green-d); }
#foot.bad #fb { color: var(--red); }
#fb .jp { font-size: 18px; }
.result { text-align: center; padding-top: 30px; }
.result .big-ico { font-size: 96px; animation: pop .5s; }
.result .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 26px 0; }
.result .rc { border: 2px solid var(--c); border-radius: 16px; overflow: hidden; }
.result .rc small { display: block; background: var(--c); color: #fff; padding: 6px; text-transform: uppercase; font-size: 12px; letter-spacing: .5px; }
.result .rc b { display: block; padding: 14px 4px; font-size: 22px; color: var(--c); }
@keyframes shake { 20%, 60% { transform: translateX(-6px); } 40%, 80% { transform: translateX(6px); } }
@keyframes pop { 0% { transform: scale(.3); } 70% { transform: scale(1.15); } 100% { transform: scale(1); } }
@media (min-width: 560px) { .opts.jp-opts { grid-template-columns: repeat(4, 1fr); } }
</style>
<div class="lesson">
    <div class="ltop">
        <button class="x" id="quit" title="Keluar">✕</button>
        <div class="bar"><i id="prog" style="width:0"></i></div>
        <span class="lhearts" id="hearts"></span>
    </div>
    <div id="ex"></div>
</div>
<div id="foot"><div class="in">
    <div id="fb"></div>
    <button class="btn" id="btn" disabled>Periksa</button>
</div></div>
<script>
window.LESSON = <?= json_encode($data, JSON_UNESCAPED_UNICODE | JSON_HEX_TAG | JSON_HEX_AMP) ?>;
</script>
<script>
(() => {
'use strict';
const L = window.LESSON;
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const shuffle = a => { a = [...a]; for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const pick = a => a[Math.floor(Math.random() * a.length)];
const answerOf = it => it.kind === 'char' ? it.romaji : it.meaning;
const normText = s => s.toLowerCase().replace(/[.,!?;:"'/()-]/g, ' ').replace(/\s+/g, ' ').trim();
const normJp = s => s.replace(/[\s　。、！？!?]/g, '');
function normRomaji(s) {
    s = s.toLowerCase().replace(/[^a-z]/g, '');
    const map = [['sha', 'sya'], ['shu', 'syu'], ['sho', 'syo'], ['shi', 'si'], ['cha', 'tya'], ['chu', 'tyu'], ['cho', 'tyo'],
        ['chi', 'ti'], ['tsu', 'tu'], ['fu', 'hu'], ['ja', 'zya'], ['ju', 'zyu'], ['jo', 'zyo'], ['ji', 'zi']];
    for (const [a, b] of map) s = s.split(a).join(b);
    return s.replace(/ou/g, 'o').replace(/([aiueo])\1+/g, '$1');
}

// ---- Suara -----------------------------------------------------------------
let jaVoice = null;
const canSpeak = 'speechSynthesis' in window;
const loadVoice = () => { if (canSpeak) jaVoice = speechSynthesis.getVoices().find(v => /^ja/i.test(v.lang)) || null; };
const voiceReady = () => new Promise(res => {
    if (!canSpeak) return res();
    loadVoice();
    if (jaVoice) return res();
    const t = setTimeout(res, 1200);
    speechSynthesis.addEventListener('voiceschanged', () => { loadVoice(); if (jaVoice) { clearTimeout(t); res(); } });
});
function speak(text, slow) {
    if (!canSpeak) return;
    const u = new SpeechSynthesisUtterance(normJp(text));
    u.lang = 'ja-JP';
    u.rate = slow ? 0.55 : 0.85;
    if (jaVoice) u.voice = jaVoice;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
}
let actx = null;
function chime(ok) {
    try {
        actx = actx || new (window.AudioContext || window.webkitAudioContext)();
        const notes = ok ? [523, 784] : [311, 233];
        notes.forEach((f, i) => {
            const t = actx.currentTime + i * 0.11, o = actx.createOscillator(), g = actx.createGain();
            o.type = ok ? 'sine' : 'triangle';
            o.frequency.value = f;
            g.gain.setValueAtTime(0.0001, t);
            g.gain.exponentialRampToValueAtTime(0.18, t + 0.02);
            g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
            o.connect(g).connect(actx.destination);
            o.start(t); o.stop(t + 0.2);
        });
    } catch (_) {}
}

// ---- Server ------------------------------------------------------------------
function api(name, body) {
    return fetch('?api=' + name, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF': L.csrf },
        body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({}));
}

// ---- Membuat soal --------------------------------------------------------------
// Pengecoh tidak boleh punya jawaban yang sama (mis. あ dan ア sama-sama "a").
const sameKind = it => L.pool.filter(p => p.kind === it.kind && p.id !== it.id && answerOf(p) !== answerOf(it) && p.jp !== it.jp);
function distract(it, getter, n) {
    const own = getter(it);
    const vals = [...new Set(sameKind(it).map(getter))].filter(v => v !== own);
    return shuffle(vals).slice(0, n);
}

function build() {
    const singles = L.items.filter(i => i.kind !== 'sentence');
    const sents = L.items.filter(i => i.kind === 'sentence');
    const listen = jaVoice ? ['listen'] : [];
    const singleTypes = ['mc_x', 'type', ...listen];
    const sentTypes = ['bank_jp', ...(jaVoice ? ['listen_bank'] : [])];
    const first = [], extra = [];
    for (const it of singles) {
        first.push({ type: L.practice ? pick(['mc_jp', ...singleTypes]) : 'mc_jp', item: it });
        extra.push({ type: pick(singleTypes), item: it });
    }
    for (const it of sents) {
        first.push({ type: L.practice ? pick(['bank_id', ...sentTypes]) : 'bank_id', item: it });
        extra.push({ type: pick(sentTypes), item: it });
    }
    const list = L.practice ? shuffle(first) : first;
    if (singles.length >= 4) list.push({ type: 'match', items: singles });
    const target = L.practice ? list.length + 2 : Math.max(12, list.length + 3);
    for (const e of shuffle(extra)) { if (list.length >= target) break; list.push(e); }
    return list;
}

// ---- Status ------------------------------------------------------------------
let queue = [], total = 0, done = 0, mistakes = 0, hearts = L.hearts, cur = null, phase = 'answer';
const btn = $('#btn'), foot = $('#foot'), fb = $('#fb'), box = $('#ex');

function updateTop() {
    $('#prog').style.width = Math.round(100 * done / Math.max(1, total)) + '%';
    $('#hearts').textContent = L.practice ? '❤️ ∞' : '❤️ ' + hearts;
}
function setFoot(mode, html) {
    foot.className = mode === 'check' ? '' : mode;
    fb.innerHTML = html || '';
    if (mode === 'check') { btn.textContent = 'Periksa'; btn.className = 'btn'; btn.disabled = true; }
    else { btn.textContent = 'Lanjut'; btn.className = 'btn' + (mode === 'bad' ? ' red' : ''); btn.disabled = false; }
}
const ready = on => { if (phase === 'answer') btn.disabled = !on; };

// ---- Tampilan soal ----------------------------------------------------------------
const spkBtn = (text, cls = '') => canSpeak ? `<button class="spk ${cls}" data-say="${esc(text)}" title="Dengarkan">🔊</button>` : '';
function optionsHtml(opts, jp) {
    return `<div class="opts ${jp ? 'jp-opts' : ''}">` + opts.map((o, i) =>
        `<button class="opt" data-v="${esc(o)}"><span class="k">${i + 1}</span><span>${esc(o)}</span></button>`).join('') + '</div>';
}
function bankHtml(tiles, jp) {
    return `<div class="answer-line" id="line"></div><div class="tiles">` + tiles.map((t, i) =>
        `<button class="tilebtn ${jp ? 'jp' : ''}" data-i="${i}">${esc(t)}</button>`).join('') + '</div>';
}

function render(e) {
    const it = e.item;
    e.value = null;
    let h = '';
    switch (e.type) {
        case 'mc_jp': {
            e.correct = answerOf(it);
            const isChar = it.kind === 'char';
            h = `<span class="tag">${isChar ? 'Huruf baru' : 'Kosakata'}</span>
                <h2>${isChar ? 'Bagaimana cara membaca huruf ini?' : 'Apa arti kata ini?'}</h2>
                <div class="prompt">${spkBtn(it.jp)}<div><div class="big jp">${esc(it.jp)}</div>
                ${!isChar ? `<span class="sub">${esc(it.romaji)}${it.kanji ? ' · <span class="jp">' + esc(it.kanji) + '</span>' : ''}</span>` : ''}</div></div>
                ${optionsHtml(shuffle([e.correct, ...distract(it, answerOf, 3)]), false)}`;
            break;
        }
        case 'mc_x':
        case 'listen': {
            e.correct = it.jp;
            const opts = shuffle([it.jp, ...distract(it, p => p.jp, 3)]);
            if (e.type === 'listen') {
                h = `<h2>Pilih yang kamu dengar</h2>${spkBtn(it.jp, 'huge')}${optionsHtml(opts, true)}`;
            } else {
                const q = it.kind === 'char' ? `Pilih huruf untuk bunyi “${esc(it.romaji)}”` : `Mana yang berarti “${esc(it.meaning)}”?`;
                h = `<h2>${q}</h2>${optionsHtml(opts, true)}`;
            }
            break;
        }
        case 'type':
            h = `<h2>Tulis dalam romaji</h2>
                <div class="prompt">${spkBtn(it.jp)}<div><div class="big jp">${esc(it.jp)}</div>
                ${it.kind !== 'char' ? `<span class="sub">artinya: ${esc(it.meaning)}</span>` : ''}</div></div>
                <textarea class="typed" id="typed" placeholder="Ketik di sini..." autocomplete="off" autocapitalize="off" spellcheck="false"></textarea>`;
            break;
        case 'bank_id': {
            const words = it.meaning.split(' ');
            const lower = new Set(words.map(w => w.toLowerCase()));
            const others = [...new Set(L.pool.filter(p => p.kind === 'sentence' && p.id !== it.id)
                .flatMap(p => p.meaning.split(' ')).filter(w => !lower.has(w.toLowerCase())))];
            e.tiles = shuffle([...words, ...shuffle(others).slice(0, 3)]);
            h = `<span class="tag">Kalimat</span><h2>Terjemahkan kalimat ini</h2>
                <div class="prompt">${spkBtn(it.jp)}<div class="sent jp">${esc(it.jp)}<span class="sub">${esc(it.romaji)}</span></div></div>
                ${bankHtml(e.tiles, false)}`;
            break;
        }
        case 'bank_jp':
        case 'listen_bank': {
            const toks = it.jp.split(' ');
            const others = [...new Set(L.pool.filter(p => p.kind === 'sentence' && p.id !== it.id)
                .flatMap(p => p.jp.split(' ')).filter(t => !toks.includes(t)))];
            e.tiles = shuffle([...toks, ...shuffle(others).slice(0, 3)]);
            h = e.type === 'listen_bank'
                ? `<h2>Susun yang kamu dengar</h2>${spkBtn(it.jp, 'huge')}${bankHtml(e.tiles, true)}`
                : `<h2>Tulis dalam bahasa Jepang</h2><div class="prompt"><div class="sent">${esc(it.meaning)}</div></div>${bankHtml(e.tiles, true)}`;
            break;
        }
        case 'match': {
            const seen = new Set();
            e.pairs = shuffle(e.items).filter(p => !seen.has(answerOf(p)) && !seen.has(p.jp) && seen.add(answerOf(p)) && seen.add(p.jp)).slice(0, 5);
            e.left = shuffle(e.pairs); e.right = shuffle(e.pairs); e.matched = 0;
            h = `<h2>Pasangkan</h2><div class="match">
                <div class="col">${e.left.map(p => `<button class="opt jp" data-side="l" data-id="${p.id}">${esc(p.jp)}</button>`).join('')}</div>
                <div class="col">${e.right.map(p => `<button class="opt" data-side="r" data-id="${p.id}">${esc(answerOf(p))}</button>`).join('')}</div></div>`;
            break;
        }
    }
    box.innerHTML = h;
    wire(e);
    const auto = box.querySelector('.spk.huge') || (e.type === 'mc_jp' && box.querySelector('.spk'));
    if (auto) setTimeout(() => speak(auto.dataset.say), 250);
}

function wire(e) {
    box.querySelectorAll('[data-say]').forEach(b => {
        let slow = false;
        b.addEventListener('click', () => { speak(b.dataset.say, slow); slow = !slow; });
    });
    if (e.type === 'match') return wireMatch(e);
    box.querySelectorAll('.opt').forEach(o => o.addEventListener('click', () => {
        if (phase !== 'answer') return;
        box.querySelectorAll('.opt').forEach(x => x.classList.remove('sel'));
        o.classList.add('sel');
        e.value = o.dataset.v;
        if (e.type !== 'mc_jp' && e.type !== 'listen') speak(e.value);
        ready(true);
    }));
    const typed = $('#typed');
    if (typed) {
        typed.addEventListener('input', () => { e.value = typed.value; ready(typed.value.trim() !== ''); });
        typed.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); if (!btn.disabled) btn.click(); } });
        setTimeout(() => typed.focus(), 50);
    }
    const line = $('#line');
    if (line) {
        e.chosen = [];
        const jp = e.type !== 'bank_id';
        const redraw = () => {
            line.innerHTML = e.chosen.map((i, pos) => `<button class="tilebtn ${jp ? 'jp' : ''}" data-pos="${pos}">${esc(e.tiles[i])}</button>`).join('');
            box.querySelectorAll('.tiles .tilebtn').forEach(t => t.classList.toggle('used', e.chosen.includes(+t.dataset.i)));
            line.querySelectorAll('.tilebtn').forEach(t => t.addEventListener('click', () => {
                if (phase !== 'answer') return;
                e.chosen.splice(+t.dataset.pos, 1); redraw();
            }));
            e.value = e.chosen.map(i => e.tiles[i]);
            ready(e.chosen.length > 0);
        };
        box.querySelectorAll('.tiles .tilebtn').forEach(t => t.addEventListener('click', () => {
            if (phase !== 'answer') return;
            e.chosen.push(+t.dataset.i);
            if (jp) speak(e.tiles[+t.dataset.i]);
            redraw();
        }));
    }
}

function wireMatch(e) {
    let sel = null;
    box.querySelectorAll('.match .opt').forEach(o => o.addEventListener('click', () => {
        if (o.classList.contains('gone')) return;
        if (o.dataset.side === 'l') speak(o.textContent);
        if (!sel || sel.dataset.side === o.dataset.side) {
            if (sel) sel.classList.remove('sel');
            sel = o; o.classList.add('sel');
            return;
        }
        const a = sel, b = o;
        sel = null;
        if (a.dataset.id === b.dataset.id) {
            [a, b].forEach(x => { x.classList.remove('sel'); x.classList.add('right'); x.disabled = true; });
            setTimeout(() => [a, b].forEach(x => { x.classList.remove('right'); x.classList.add('gone'); }), 350);
            if (++e.matched === e.pairs.length) {
                chime(true);
                done++; updateTop(); phase = 'feedback';
                setFoot('good', '<b>Mantap! 🎉</b>Semua pasangan benar.');
            }
        } else {
            [a, b].forEach(x => { x.classList.remove('sel'); x.classList.add('wrong'); });
            chime(false);
            setTimeout(() => [a, b].forEach(x => x.classList.remove('wrong')), 450);
        }
    }));
}

// ---- Alur ------------------------------------------------------------------------
function check() {
    const e = cur, it = e.item;
    let ok = false, shown = '';
    switch (e.type) {
        case 'mc_jp': ok = e.value === e.correct; shown = esc(e.correct); break;
        case 'mc_x': case 'listen': ok = e.value === e.correct; shown = `<span class="jp">${esc(it.jp)}</span> (${esc(it.romaji)}) = ${esc(answerOf(it))}`; break;
        case 'type': {
            const v = (e.value || '').trim();
            ok = normRomaji(v) === normRomaji(it.romaji) || (v !== '' && normJp(v) === normJp(it.jp));
            shown = esc(it.romaji);
            break;
        }
        case 'bank_id': ok = normText(e.value.join(' ')) === normText(it.meaning); shown = esc(it.meaning); break;
        case 'bank_jp': case 'listen_bank': ok = normJp(e.value.join('')) === normJp(it.jp); shown = `<span class="jp">${esc(it.jp)}</span><br>${esc(it.romaji)} · ${esc(it.meaning)}`; break;
    }
    phase = 'feedback';
    box.querySelectorAll('.opt').forEach(o => {
        o.disabled = true;
        if (o.classList.contains('sel')) o.classList.add(ok ? 'right' : 'wrong');
    });
    const typed = $('#typed'); if (typed) typed.disabled = true;
    chime(ok);
    api('answer', { item_id: it.id, correct: ok, practice: L.practice }).then(r => {
        if (!L.practice && typeof r.hearts === 'number') { hearts = r.hearts; updateTop(); }
    });
    if (ok) {
        done++;
        const praise = pick(['Benar!', 'Bagus!', 'Hebat!', 'Sempurna!', 'すごい！', 'いいね！']);
        const extra = e.type === 'mc_jp' || e.type === 'type' ? `<span class="jp">${esc(it.jp)}</span> = ${esc(it.romaji)}${it.kind !== 'char' ? ' · ' + esc(it.meaning) : ''}` : '';
        setFoot('good', `<b>${praise}</b>${extra}`);
    } else {
        mistakes++;
        if (!L.practice) {
            hearts = Math.max(0, hearts - 1);
            const hEl = $('#hearts'); hEl.classList.remove('shake'); void hEl.offsetWidth; hEl.classList.add('shake');
        }
        queue.push({ type: e.type, item: it });
        setFoot('bad', `<b>Jawaban yang benar:</b>${shown}`);
    }
    updateTop();
}

function next() {
    if (!L.practice && hearts <= 0) return failed();
    if (!queue.length) return finish();
    cur = queue.shift();
    phase = 'answer';
    setFoot('check');
    render(cur);
    window.scrollTo(0, 0);
}

function failed() {
    phase = 'end';
    foot.hidden = true;
    box.innerHTML = `<div class="result"><div class="big-ico">💔</div><h1>Hatimu habis</h1>
        <p class="muted">Jangan menyerah! Isi ulang hati dengan Latihan atau tunggu sebentar.</p>
        <div style="display:grid;gap:12px;margin-top:30px"><a class="btn blue" href="?page=practice">Latihan</a><a class="btn ghost" href="?page=home">Kembali</a></div></div>`;
}

async function finish() {
    phase = 'end';
    foot.hidden = true;
    box.innerHTML = '<div class="result"><p class="muted">Menyimpan…</p></div>';
    const r = await api('finish', { lesson_id: L.id, practice: L.practice, total, mistakes });
    if (r.error) {
        box.innerHTML = `<div class="result"><div class="big-ico">⚠️</div><h1>${esc(r.error)}</h1><a class="btn" href="?page=home">Kembali</a></div>`;
        return;
    }
    chime(true);
    const perfect = mistakes === 0;
    const goalHit = r.daily_xp >= r.daily_goal && r.daily_xp - r.xp < r.daily_goal;
    box.innerHTML = `<div class="result">
        <div class="big-ico">${perfect ? '🏆' : '🎉'}</div>
        <h1 style="color:var(--gold-d)">${L.practice ? 'Latihan selesai!' : perfect ? 'Pelajaran sempurna!' : 'Pelajaran selesai!'}</h1>
        <p class="muted">${r.extended ? `🔥 Streak ${r.streak} hari! Lanjutkan besok.` : 'おつかれさま！ Terus semangat.'}</p>
        <div class="cards">
            <div class="rc" style="--c:var(--gold)"><small>XP didapat</small><b>⚡ ${r.xp}</b></div>
            <div class="rc" style="--c:var(--green)"><small>Akurasi</small><b>🎯 ${r.accuracy}%</b></div>
            <div class="rc" style="--c:#ff9600"><small>Streak</small><b>🔥 ${r.streak}</b></div>
        </div>
        ${L.practice ? '<p>❤️ +1 hati</p>' : ''}
        ${goalHit ? '<p style="color:var(--green)">🎯 Target harian tercapai!</p>' : `<p class="muted">Target harian: ${Math.min(r.daily_xp, r.daily_goal)} / ${r.daily_goal} XP</p>`}
        <div style="display:grid;gap:12px;margin-top:20px"><a class="btn" href="?page=home" id="cont">Lanjut</a>
        ${L.practice ? '<a class="btn ghost" href="?page=practice">Latihan lagi</a>' : ''}</div></div>`;
}

btn.addEventListener('click', () => {
    if (btn.disabled) return;
    if (phase === 'answer') check(); else if (phase === 'feedback') next();
});
document.addEventListener('keydown', ev => {
    if (ev.target.tagName === 'TEXTAREA') return;
    if (ev.key === 'Enter') { ev.preventDefault(); if (phase === 'end') { const c = $('#cont'); if (c) c.click(); } else btn.click(); }
    if (phase === 'answer' && /^[1-9]$/.test(ev.key)) {
        const o = box.querySelectorAll('.opts .opt')[+ev.key - 1];
        if (o) o.click();
    }
});
$('#quit').addEventListener('click', () => {
    if (phase === 'end' || confirm('Keluar dari pelajaran? Progres pelajaran ini akan hilang.')) location.href = '?page=home';
});

voiceReady().then(() => {
    queue = build();
    total = queue.length;
    updateTop();
    next();
});
})();
</script>
<?php
    layoutEnd(null);
    exit;
}

redirect('?page=home');
