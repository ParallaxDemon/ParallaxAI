<?php
declare(strict_types=1);
ini_set('display_errors','0');
header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: no-referrer');
header('X-Frame-Options: DENY');
if (session_status() === PHP_SESSION_NONE) {
    session_set_cookie_params(['httponly'=>true,'secure'=>(!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off'),'samesite'=>'Strict','path'=>'/']);
    session_start();
}
const PANEL_ROOT = __DIR__;
function config(): array {
    static $c = null;
    if ($c === null) {
        if (!is_file(PANEL_ROOT.'/config.php')) { throw new RuntimeException('config.php missing: follow README setup'); }
        $c = require PANEL_ROOT.'/config.php';
    }
    return $c;
}
function db(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $d = config()['db'];
        $dsn = 'mysql:host='.$d['host'].';port='.((int)($d['port'] ?? 3306)).';dbname='.$d['name'].';charset=utf8mb4';
        $pdo = new PDO($dsn, $d['user'], $d['password'], [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC,PDO::ATTR_EMULATE_PREPARES=>false]);
    }
    return $pdo;
}
function out(int $status, array $body): never {
    http_response_code($status); header('Content-Type: application/json; charset=utf-8');
    echo json_encode($body, JSON_UNESCAPED_SLASHES|JSON_INVALID_UTF8_SUBSTITUTE); exit;
}
function authToken(string $header, string $expected): bool {
    return $expected !== '' && !str_starts_with($expected,'PASTE_') && hash_equals($expected, $header);
}
function bearer(): string {
    $auth = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
    return preg_match('/^Bearer\s+(.+)$/i', $auth, $m) ? trim($m[1]) : ($_SERVER['HTTP_X_API_KEY'] ?? '');
}
function allowClient(bool $write = false): void {
    $c = config();
    if (authToken(bearer(), (string)$c['client_api_key'])) return;
    if (!empty($_SESSION['is_admin'])) {
        if (!$write || (isset($_SERVER['HTTP_X_CSRF_TOKEN']) && hash_equals((string)($_SESSION['csrf'] ?? ''), (string)$_SERVER['HTTP_X_CSRF_TOKEN']))) return;
        out(403,['error'=>'Invalid CSRF token']);
    }
    out(401,['error'=>'Unauthorized API key or session']);
}
function allowWorker(): void {
    if (!authToken((string)($_SERVER['HTTP_X_WORKER_KEY'] ?? ''), (string)config()['worker_key'])) out(401,['error'=>'Worker authentication required']);
}
function inputJson(): array {
    $raw = file_get_contents('php://input');
    if (strlen($raw ?: '') > 100000) out(413,['error'=>'Request JSON too large']);
    $x = json_decode($raw ?: '{}', true);
    if (!is_array($x)) out(400,['error'=>'Invalid JSON']);
    return $x;
}
function row(string $id): array {
    $s = db()->prepare('SELECT * FROM agent_jobs WHERE id=?'); $s->execute([$id]);
    $v=$s->fetch(); if (!$v) out(404,['error'=>'Job not found']); return $v;
}
function safeJob(array $j): array {
    return [
        'id'=>$j['id'],'batch_id'=>$j['batch_id'] ?? null,'kind'=>$j['kind'],'status'=>$j['status'],
        'prompt'=>$j['prompt'],'original_name'=>$j['original_name'],
        'result'=>$j['result_text'],'error'=>$j['error_text'],
        'artifact_ready'=>(bool)$j['artifact_path'],
        'github_workflow_url'=>$j['github_workflow_url'],
        'created_at'=>$j['created_at'],'updated_at'=>$j['updated_at']
    ];
}
function saveUpload(): array {
    if (!isset($_FILES['file']) || (int)$_FILES['file']['error'] === UPLOAD_ERR_NO_FILE) return [null,null];
    $f = $_FILES['file'];
    if ((int)$f['error'] !== UPLOAD_ERR_OK) out(400,['error'=>'Upload failed; check PHP upload_max_filesize/post_max_size']);
    if ((int)$f['size'] > (int)config()['max_upload_bytes']) out(413,['error'=>'File exceeds configured limit']);
    $name=basename((string)$f['name']); $ext=strtolower(pathinfo($name,PATHINFO_EXTENSION));
    if (!in_array($ext,['apk','dex','smali','so'],true)) out(400,['error'=>'Allowed extensions: apk, dex, smali, so']);
    $handle=fopen($f['tmp_name'],'rb'); $sig=fread($handle,8); fclose($handle);
    if (($ext==='apk' && substr($sig,0,2)!=='PK') || ($ext==='so' && substr($sig,0,4)!=="\x7fELF") || ($ext==='dex' && substr($sig,0,4)!=="dex\n")) out(400,['error'=>'File signature does not match extension']);
    if ($ext==='smali' && (str_contains($sig,"\0") || !preg_match('//u',file_get_contents($f['tmp_name'])))) out(400,['error'=>'Smali must be UTF-8 text']);
    $basename=bin2hex(random_bytes(16)).'.'.$ext;
    $dest=PANEL_ROOT.'/data/uploads/'.$basename;
    if (!move_uploaded_file($f['tmp_name'], $dest)) out(500,['error'=>'Unable to store upload']);
    chmod($dest,0600);
    return [$name,$basename];
}
function dispatch(string $id): array {
    $c=config(); $repo=(string)($c['github_repo']??'');
    if (!preg_match('/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/',$repo)) return [false,'Configure github_repo as OWNER/REPO'];
    if (empty($c['github_token']) || str_starts_with((string)$c['github_token'],'PASTE_')) return [false,'Configure github_token'];
    $url='https://api.github.com/repos/'.$repo.'/actions/workflows/deepseek-agent.yml/dispatches';
    $payload=json_encode(['ref'=>$c['github_ref'] ?? 'main','inputs'=>['job_id'=>$id]],JSON_THROW_ON_ERROR);
    $ch=curl_init($url);
    curl_setopt_array($ch,[CURLOPT_RETURNTRANSFER=>true,CURLOPT_POST=>true,CURLOPT_POSTFIELDS=>$payload,CURLOPT_CONNECTTIMEOUT=>10,CURLOPT_TIMEOUT=>25,CURLOPT_HTTPHEADER=>[
        'Accept: application/vnd.github+json','Authorization: Bearer '.$c['github_token'],
        'X-GitHub-Api-Version: 2022-11-28','User-Agent: DeepSeek-RE-PHP-Bridge','Content-Type: application/json'
    ]]);
    $body=curl_exec($ch); $status=curl_getinfo($ch,CURLINFO_HTTP_CODE); $err=curl_error($ch); curl_close($ch);
    if ($status===204) return [true,'Workflow dispatched'];
    return [false,'GitHub API HTTP '.(int)$status.': '.substr($err ?: (string)$body,0,500)];
}
function makeJob(array $input, ?string $batchId = null): array {
    $kind=(string)($input['kind']??'chat');
    if (!in_array($kind,['chat','analyze','patch'],true)) out(400,['error'=>'Invalid job kind']);
    $prompt=trim((string)($input['prompt']??''));
    if ($prompt==='' || mb_strlen($prompt)>8000) out(400,['error'=>'Prompt required (max 8000 chars)']);
    $patch=null;
    if ($kind==='patch') {
        $spec=$input['patch_spec']??null;
        if (is_string($spec)) $spec=json_decode($spec,true);
        if (!is_array($spec)) out(400,['error'=>'patch_spec requires path, find, replace; exact-match only']);
        $path=(string)($spec['path']??''); $find=(string)($spec['find']??''); $replace=(string)($spec['replace']??'');
        if ($find==='' || strlen($find)>12000 || strlen($replace)>12000 || strlen($path)>255) out(400,['error'=>'Invalid patch content']);
        $patch=json_encode(['path'=>$path,'find'=>$find,'replace'=>$replace],JSON_THROW_ON_ERROR);
    }
    [$name,$file]=saveUpload();
    if ($kind!=='chat' && !$file) out(400,['error'=>'Upload an APK, DEX, Smali or SO']);
    if ($kind==='patch' && !in_array(strtolower(pathinfo((string)$name,PATHINFO_EXTENSION)),['apk','smali'],true)) out(400,['error'=>'Patch supports APK Smali or standalone Smali only']);
    $id=bin2hex(random_bytes(16));
    $s=db()->prepare('INSERT INTO agent_jobs (id,batch_id,kind,status,prompt,patch_spec,original_name,input_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)');
    $s->execute([$id,$batchId,$kind,'queued',$prompt,$patch,$name,$file]);
    // Plain chats are answered by the always-on local phone worker (one long-running
    // process), NOT by GitHub Actions. Only binary-analysis jobs dispatch a workflow,
    // so we never spin up a runner for every chat message.
    if ($kind === 'chat') {
        return ['id'=>$id,'batch_id'=>$batchId,'status'=>'queued','dispatch'=>'answered by always-on phone worker','status_url'=>'api/v1/jobs/'.$id];
    }
    [$ok,$message]=dispatch($id);
    if (!$ok) { $s=db()->prepare('UPDATE agent_jobs SET status=?,error_text=? WHERE id=?');$s->execute(['dispatch_failed',$message,$id]); }
    return ['id'=>$id,'batch_id'=>$batchId,'status'=>$ok?'queued':'dispatch_failed','dispatch'=>$message,'status_url'=>'api/v1/jobs/'.$id];
}
function makeBatch(array $input): array {
    $prompts = $input['prompts'] ?? null;
    if (!is_array($prompts) || count($prompts) < 1 || count($prompts) > 20) out(400,['error'=>'prompts must be an array of 1 to 20 strings']);
    $kind = (string)($input['kind'] ?? 'chat');
    if ($kind !== 'chat') out(400,['error'=>'Batch mode supports chat only']);
    $clean = [];
    foreach ($prompts as $p) {
        if (!is_string($p)) out(400,['error'=>'Every prompt must be a string']);
        $p = trim($p);
        if ($p === '' || mb_strlen($p) > 8000) out(400,['error'=>'Each prompt must be 1 to 8000 characters']);
        $clean[] = $p;
    }
    $batch = bin2hex(random_bytes(16));
    $jobs = [];
    foreach ($clean as $p) $jobs[] = makeJob(['kind'=>'chat','prompt'=>$p], $batch);
    return ['batch_id'=>$batch,'count'=>count($jobs),'jobs'=>$jobs,'status_url'=>'api/v1/batch/'.$batch];
}
