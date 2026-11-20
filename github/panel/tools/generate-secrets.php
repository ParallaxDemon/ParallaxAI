<?php
if (PHP_SAPI !== 'cli') { http_response_code(403); exit; }
if ($argc > 1) { $password = $argv[1]; }
else {
    fwrite(STDOUT,"Choose admin password (hidden): ");
    if (strncasecmp(PHP_OS,'WIN',3)!==0) system('stty -echo 2>/dev/null');
    $password = rtrim((string)fgets(STDIN),"\r\n");
    if (strncasecmp(PHP_OS,'WIN',3)!==0) system('stty echo 2>/dev/null');
    fwrite(STDOUT,"\n");
}
if (strlen($password)<12) { fwrite(STDERR,"Admin password must have at least 12 characters.\n"); exit(2); }
echo "ADMIN_PASSWORD_HASH: " . password_hash($password, PASSWORD_DEFAULT) . PHP_EOL;
echo "CLIENT_API_KEY: " . bin2hex(random_bytes(32)) . PHP_EOL;
echo "WORKER_KEY: " . bin2hex(random_bytes(32)) . PHP_EOL;
