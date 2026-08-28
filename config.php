<?php
// =======================================================
// تنظیمات مرکزی ربات
// این فایل مقادیر حساس را از Environment Variables می‌خواند
// (روی Railway از بخش Variables ست می‌شوند)
// =======================================================

// در محیط لوکال اگر فایل .env وجود داشت، آن را لود کن (اختیاری)
if (file_exists(__DIR__ . '/.env')) {
    $lines = file(__DIR__ . '/.env', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    foreach ($lines as $line) {
        if (strpos(trim($line), '#') === 0) continue;
        if (strpos($line, '=') === false) continue;
        [$key, $value] = explode('=', $line, 2);
        $key = trim($key);
        $value = trim($value);
        if (!getenv($key)) {
            putenv("$key=$value");
        }
    }
}

if (!defined('API_KEY')) {
    define('API_KEY', getenv('TELEGRAM_BOT_TOKEN') ?: '');
}
if (!defined('OPENROUTER_KEY')) {
    define('OPENROUTER_KEY', getenv('OPENROUTER_KEY') ?: '');
}
if (!defined('OWNER_ID')) {
    define('OWNER_ID', getenv('OWNER_ID') ?: '');
}
if (!defined('BOT_NAME')) {
    define('BOT_NAME', 'مغز مصنوعی');
}
if (!defined('TARGET_FILE')) {
    define('TARGET_FILE', __DIR__ . '/bot.php');
}
if (!defined('ERROR_LOG_FILE')) {
    define('ERROR_LOG_FILE', __DIR__ . '/error_log');
}
