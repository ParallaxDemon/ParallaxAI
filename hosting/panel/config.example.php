<?php
declare(strict_types=1);
// Copy to config.php; NEVER commit the resulting config.php.
return [
    'db' => [
        'host' => 'localhost', 'port' => 3306,
        'name' => 'YOUR_DB_NAME', 'user' => 'YOUR_DB_USER',
        'password' => 'YOUR_DB_PASSWORD',
    ],
    // Generate using: php tools/generate-secrets.php
    'admin_password_hash' => 'PASTE_PASSWORD_HASH_HERE',
    'client_api_key' => 'PASTE_64_HEX_CLIENT_API_KEY',
    'worker_key' => 'PASTE_64_HEX_WORKER_KEY',
    // Fine-grained GitHub PAT limited to your agent repo: Actions -> Read and write.
    'github_token' => 'PASTE_GITHUB_PAT',
    'github_repo' => 'YOUR_USERNAME/YOUR_AGENT_REPO',
    'github_ref' => 'main',
    // Maximum input uploaded through PHP, in bytes. Align PHP upload_max_filesize/post_max_size.
    'max_upload_bytes' => 25 * 1024 * 1024,
];
