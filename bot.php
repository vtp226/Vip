<?php
// این فایل به صورت داینامیک توسط هوش مصنوعی تکمیل می‌شود.
require_once __DIR__ . '/config.php';

$string = file_get_contents("php://input");
$update = json_decode($string, true);
if (!$update) exit;

function bot($method, $datas=[]){
    $url = "https://api.telegram.org/bot".API_KEY."/".$method;
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $datas);
    $res = curl_exec($ch);
    if(curl_error($ch)){ var_dump(curl_error($ch)); } else { return json_decode($res); }
}

$chat_id = $update['message']['chat']['id'] ?? null;
$message = $update['message']['text'] ?? null;

// --- کدهای تولید شده توسط مغز مصنوعی از اینجا به بعد اضافه می‌شوند ---


