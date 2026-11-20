<?php
declare(strict_types=1);
require __DIR__.'/bootstrap.php';
try {
    $route=trim((string)($_GET['route'] ?? ''),'/');
    $method=strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
    if ($route==='v1/health' && $method==='GET') out(200,['ok'=>true,'service'=>'deepseek-re-bridge','mode'=>'async']);
    if ($route==='v1/models' && $method==='GET') { allowClient(); out(200,['models'=>[['id'=>'deepseek-r1-1.5b-abliterated-q4','mode'=>'job-based','local_on'=>'github-runner']]]); }
    if ($route==='v1/jobs' && $method==='GET') {
        allowClient();
        $limit=max(1,min(100,(int)($_GET['limit'] ?? 50)));
        $s=db()->query('SELECT * FROM agent_jobs ORDER BY created_at DESC LIMIT '.$limit);
        out(200,['jobs'=>array_map('safeJob',$s->fetchAll())]);
    }
    if ($route==='v1/jobs' && $method==='POST') {
        allowClient(true);
        $in=str_contains(strtolower($_SERVER['CONTENT_TYPE']??''),'multipart/form-data')? $_POST:inputJson();
        out(202,makeJob($in));
    }
    if ($route==='v1/batch' && $method==='POST') {
        allowClient(true); out(202, makeBatch(inputJson()));
    }
    if (preg_match('~^v1/batch/([0-9a-f]{32})$~',$route,$m) && $method==='GET') {
        allowClient();
        $s=db()->prepare('SELECT * FROM agent_jobs WHERE batch_id=? ORDER BY created_at ASC, id ASC');
        $s->execute([$m[1]]); $rows=$s->fetchAll();
        if (!$rows) out(404,['error'=>'Batch not found']);
        $done=0; foreach ($rows as $r) if ($r['status']==='done') $done++;
        out(200,['batch_id'=>$m[1],'count'=>count($rows),'done'=>$done,'jobs'=>array_map('safeJob',$rows)]);
    }
    if ($route==='v1/chat/completions' && $method==='POST') {
        allowClient(true);
        $in=inputJson(); $messages=$in['messages']??[];
        if (!is_array($messages) || count($messages)>20) out(400,['error'=>'Expected messages array (max 20)']);
        $prompt='';
        foreach ($messages as $m) {
            if (!is_array($m) || !in_array($m['role']??'',['system','user','assistant'],true) || !is_string($m['content']??null)) out(400,['error'=>'Invalid messages']);
            $prompt.= strtoupper($m['role']).': '.$m['content']."\n";
        }
        out(202,makeJob(['kind'=>'chat','prompt'=>$prompt]));
    }
    if (preg_match('~^v1/jobs/([0-9a-f]{32})$~',$route,$m)) {
        if ($method!=='GET') out(405,['error'=>'Method not allowed']);
        allowClient();out(200,safeJob(row($m[1])));
    }
    if (preg_match('~^v1/jobs/([0-9a-f]{32})/artifact$~',$route,$m)) {
        if ($method!=='GET') out(405,['error'=>'Method not allowed']);
        allowClient(); $j=row($m[1]);
        if (empty($j['artifact_path']) || !is_file(PANEL_ROOT.'/data/artifacts/'.$j['artifact_path'])) out(404,['error'=>'No artifact']);
        $f=PANEL_ROOT.'/data/artifacts/'.$j['artifact_path'];
        header('Content-Type: application/zip');header('Content-Disposition: attachment; filename="report-'.$j['id'].'.zip"');
        header('Content-Length: '.filesize($f));readfile($f);exit;
    }
    if (preg_match('~^v1/jobs/([0-9a-f]{32})/retry$~',$route,$m) && $method==='POST') {
        allowClient(true); $j=row($m[1]);
        if (!in_array($j['status'],['dispatch_failed','failed'],true)) out(409,['error'=>'Job is not retryable']);
        $s=db()->prepare('UPDATE agent_jobs SET status=?,error_text=NULL WHERE id=?');$s->execute(['queued',$m[1]]);
        [$ok,$msg]=dispatch($m[1]);
        if (!$ok) { $s=db()->prepare('UPDATE agent_jobs SET status=?,error_text=? WHERE id=?');$s->execute(['dispatch_failed',$msg,$m[1]]); }
        out($ok?202:502,['ok'=>$ok,'dispatch'=>$msg]);
    }
    // Worker endpoints are distinct from the public client API.
    if (preg_match('~^v1/worker/jobs/([0-9a-f]{32})$~',$route,$m) && $method==='GET') {
        allowWorker(); $j=row($m[1]);
        if (!in_array($j['status'],['queued','running'],true)) out(409,['error'=>'Job not runnable','status'=>$j['status']]);
        $s=db()->prepare('UPDATE agent_jobs SET status=? WHERE id=?');$s->execute(['running',$m[1]]);
        out(200,['id'=>$j['id'],'kind'=>$j['kind'],'prompt'=>$j['prompt'],
            'patch_spec'=>$j['patch_spec']?json_decode($j['patch_spec'],true):null,'original_name'=>$j['original_name'],
            'has_input'=>!empty($j['input_path'])]);
    }
    if (preg_match('~^v1/worker/jobs/([0-9a-f]{32})/input$~',$route,$m) && $method==='GET') {
        allowWorker();$j=row($m[1]);if (!$j['input_path']) out(404,['error'=>'No input']);
        $f=PANEL_ROOT.'/data/uploads/'.$j['input_path'];if (!is_file($f)) out(404,['error'=>'Input expired']);
        header('Content-Type: application/octet-stream');header('Content-Length: '.filesize($f));
        readfile($f);exit;
    }
    if (preg_match('~^v1/worker/jobs/([0-9a-f]{32})/result$~',$route,$m) && $method==='POST') {
        allowWorker();$j=row($m[1]);$in=inputJson();
        $status=($in['status']??'')==='done'?'done':'failed';
        $result=substr((string)($in['result']??''),0,160000);
        $error=substr((string)($in['error']??''),0,10000);
        $s=db()->prepare('UPDATE agent_jobs SET status=?,result_text=?,error_text=? WHERE id=?');
        $s->execute([$status,$result,$error,$m[1]]);out(200,['ok'=>true]);
    }
    if (preg_match('~^v1/worker/jobs/([0-9a-f]{32})/artifact$~',$route,$m) && $method==='POST') {
        allowWorker();row($m[1]);
        if (!isset($_FILES['artifact']) || (int)$_FILES['artifact']['error']!==UPLOAD_ERR_OK) out(400,['error'=>'Missing artifact']);
        $f=$_FILES['artifact'];
        if ($f['size'] > (int)config()['max_upload_bytes']) out(413,['error'=>'Artifact exceeds hosting limit']);
        $sig=file_get_contents($f['tmp_name'],false,null,0,2);
        if ($sig!=='PK') out(400,['error'=>'Expected zip']);
        $name=bin2hex(random_bytes(16)).'.zip';
        if (!move_uploaded_file($f['tmp_name'],PANEL_ROOT.'/data/artifacts/'.$name)) out(500,['error'=>'Store failed']);
        chmod(PANEL_ROOT.'/data/artifacts/'.$name,0600);
        $s=db()->prepare('UPDATE agent_jobs SET artifact_path=? WHERE id=?');$s->execute([$name,$m[1]]);
        out(200,['ok'=>true]);
    }
    out(404,['error'=>'Unknown route']);
} catch (Throwable $e) {
    error_log('RE panel error: '.$e->getMessage());
    out(500,['error'=>'Server error; check PHP error log']);
}
