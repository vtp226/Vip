<?php
// این فایل مسیرها را برای سرور داخلی PHP مدیریت می‌کند (لازم برای اجرا روی Railway)

$uri = urldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH));

// اگر فایلی با همین مسیر واقعا وجود دارد (مثلا bot.php یا farhad.php) همان را اجرا کن
$file = __DIR__ . $uri;
if ($uri !== '/' && file_exists($file) && !is_dir($file)) {
    return false; // اجازه بده PHP خودش فایل درخواستی را serve کند
}

// مسیر ریشه: فقط برای health-check ساده Railway
header('Content-Type: text/plain; charset=utf-8');
echo "مغز مصنوعی فعال است.";
