<?php
declare(strict_types=1);
require __DIR__.'/bootstrap.php';
$error='';$configReady=is_file(__DIR__.'/config.php');
if (empty($_SESSION['csrf'])) $_SESSION['csrf']=bin2hex(random_bytes(20));
if (isset($_GET['logout'])) { $_SESSION=[]; session_regenerate_id(true);header('Location: index.php');exit; }
if ($_SERVER['REQUEST_METHOD']==='POST' && isset($_POST['login'])) {
    if (!$configReady) $error='config.php missing';
    elseif (!hash_equals($_SESSION['csrf'], (string)($_POST['csrf']??''))) $error='Session expired';
    elseif (($_SESSION['login_block_until'] ?? 0) > time()) $error='Too many attempts; retry later';
    elseif (password_verify((string)($_POST['password']??''), (string)config()['admin_password_hash'])) {
        session_regenerate_id(true);$_SESSION['is_admin']=true;unset($_SESSION['login_failures'],$_SESSION['login_block_until']);header('Location: index.php');exit;
    } else { $error='Wrong password'; $_SESSION['login_failures']=($_SESSION['login_failures']??0)+1; if ($_SESSION['login_failures']>=5) {$_SESSION['login_block_until']=time()+180;$_SESSION['login_failures']=0;} }
}
$logged=!empty($_SESSION['is_admin']);$csrf=htmlspecialchars($_SESSION['csrf'],ENT_QUOTES,'UTF-8');
?><!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DeepSeek RE • Agent Dashboard</title>
<style>
:root{color-scheme:dark;font:16px system-ui,sans-serif;background:#0b1020;color:#e6edf7}*{box-sizing:border-box}
body{margin:0 auto;max-width:1120px;padding:24px 18px}header,.cols,.toolbar{display:flex;gap:16px;align-items:start;justify-content:space-between}
.cols{align-items:stretch}section,.box{border:1px solid #29364b;border-radius:15px;background:#131b2c;padding:20px;margin:14px 0}
section{flex:1}small,.sub{color:#9cb0ca}input,select,textarea,button{font:inherit;padding:11px;border-radius:9px;border:1px solid #34455d;background:#0a1220;color:inherit;width:100%}
textarea{min-height:100px;resize:vertical}input[type=file]{font-size:14px}button{cursor:pointer;background:#1769db;border:0;font-weight:650}
a{color:#74b8ff}label{display:block;margin:13px 0 6px;color:#c3d3e9;font-size:14px}h1{font-size:26px;margin:0}h2{font-size:18px}
.result{white-space:pre-wrap;overflow-wrap:anywhere;max-height:370px;overflow:auto;background:#0a1220;border-radius:9px;padding:13px}
.job{border-top:1px solid #29364b;padding:14px 0}code{background:#1d3049;padding:2px 5px;border-radius:4px}
.tag{border:1px solid #365174;color:#aad1fe;padding:4px 9px;border-radius:999px;font-size:12px}.link{display:inline-block;margin:9px 10px 0 0}
@media(max-width:760px){.cols,header{display:block}section{width:100%}}
</style></head><body>
<header><div><h1>DeepSeek RE Agent</h1><p class="sub">GitHub Actions • PHP / MySQL • async inference • authorized binary analysis</p></div><div><?= $logged?'<a href="?logout=1">Log out</a>':'<span class="tag">Sign in required</span>' ?></div></header>
<?php if (!$configReady): ?><section><h2>Installation required</h2><p>Copy <code>config.example.php</code> to <code>config.php</code>, set secrets, import <code>schema.sql</code>.</p></section>
<?php elseif (!$logged): ?><section style="max-width:480px;margin:70px auto"><h2>Admin login</h2><form method="post"><input type="hidden" name="csrf" value="<?= $csrf ?>"><input type="hidden" name="login" value="1"><label>Admin password</label><input type="password" name="password" required autocomplete="current-password"><p><button>Log in</button></p></form><p><?= htmlspecialchars($error) ?></p></section>
<?php else: ?>
<div class="cols"><section><h2>New job</h2>
<form id="jobForm"><label>Operation</label><select name="kind" id="kind"><option value="chat">AI Chat</option><option value="analyze">Analyze uploaded binary</option><option value="patch">Exact-match authorized Smali patch</option></select>
<label>Prompt</label><textarea name="prompt" required placeholder="Analyze this APK or explain my code..." maxlength="8000"></textarea>
<label>APK / DEX / Smali / .so (optional for chat)</label><input type="file" name="file" accept=".apk,.dex,.smali,.so">
<div id="patchFields" style="display:none"><label>Smali relative path (for APK; blank for .smali upload)</label><input name="patch_path" placeholder="smali/com/example/MyClass.smali"><label>Exact existing text</label><textarea name="patch_find" placeholder="Original Smali lines"></textarea><label>Replacement text</label><textarea name="patch_replace" placeholder="New Smali lines"></textarea></div>
<p><button type="submit" id="submit">Dispatch GitHub job</button></p><p id="notice" class="sub"></p></form></section>
<section><h2>API & Service</h2><p><span class="tag">Async queued API</span></p><p>Your base endpoint: <code id="base"></code></p><p>Send API key via <code>Authorization: Bearer API_KEY</code>.</p><p><b>POST</b> <code>/api/v1/jobs</code></p><p><b>GET</b> <code>/api/v1/jobs/{id}</code></p><p><b>POST</b> <code>/api/v1/chat/completions</code> (HTTP 202)</p><p>For binaries use multipart/form-data. Job processing executes on demand with GitHub Actions; no GPU or persistent public inference server.</p></section></div>
<section><div class="toolbar"><h2>Recent jobs</h2><button style="width:auto;padding:8px 15px" onclick="refresh()">Refresh</button></div><div id="jobs">Loading…</div></section>
<script>
const base=new URL('./api/v1/',location.href).href;
document.querySelector('#base').textContent=base;
const csrf=<?= json_encode($_SESSION['csrf']) ?>;
const kind=document.querySelector('#kind');kind.addEventListener('change',()=>document.querySelector('#patchFields').style.display=kind.value==='patch'?'block':'none');
function esc(x){return String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}
async function refresh(){const res=await fetch(base+'jobs');const d=await res.json();document.querySelector('#jobs').innerHTML=(d.jobs||[]).map(j=>`<div class="job"><b>${esc(j.kind)} </b><span class="tag">${esc(j.status)}</span> <small>${esc(j.created_at)} · ${esc(j.id)}</small>${j.original_name?'<p>File: '+esc(j.original_name)+'</p>':''}<p>${esc(j.prompt).slice(0,300)}</p>${j.result?'<div class="result">'+esc(j.result)+'</div>':''}${j.error?'<p style="color:#ffaca5">'+esc(j.error)+'</p>':''}${j.artifact_ready?'<a class="link" href="'+base+'jobs/'+encodeURIComponent(j.id)+'/artifact">Download analysis ZIP</a>':''}${['failed','dispatch_failed'].includes(j.status)?'<button style="width:auto" onclick="retry(\''+j.id+'\')">Retry</button>':''}</div>`).join('')||'No jobs yet';}
async function retry(id){const r=await fetch(base+'jobs/'+id+'/retry',{method:'POST',headers:{'X-CSRF-Token':csrf}});alert(JSON.stringify(await r.json()));refresh();}
document.querySelector('#jobForm').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.target),n=document.querySelector('#notice');if(kind.value==='patch'){f.set('patch_spec',JSON.stringify({path:f.get('patch_path'),find:f.get('patch_find'),replace:f.get('patch_replace')}));}n.textContent='Dispatching…';try{const r=await fetch(base+'jobs',{method:'POST',headers:{'X-CSRF-Token':csrf},body:f});const j=await r.json();n.textContent=r.ok?`Job ${j.id}: ${j.status}`:`Error: ${JSON.stringify(j)}`;await refresh()}catch(err){n.textContent=String(err)}});
refresh();setInterval(refresh,15000);
</script>
<?php endif; ?></body></html>
