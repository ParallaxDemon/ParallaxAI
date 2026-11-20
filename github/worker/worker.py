#!/usr/bin/env python3
"""Single-use, unprivileged GitHub Actions worker. No arbitrary code execution from prompts."""
import difflib
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import zipfile

import requests

JOB = os.environ.get('JOB_ID','')
BASE = os.environ.get('PANEL_BASE_URL','').rstrip('/')
KEY = os.environ.get('WORKER_KEY','')
MODEL = os.environ.get('MODEL_HF','mradermacher/DeepSeek-R1-Distill-Qwen-1.5B-abliterated-GGUF:Q4_K_M')
ROOT = pathlib.Path.cwd()
OUTPUT = ROOT / 'output'
OUTPUT.mkdir(exist_ok=True)


def run(args, cwd=None, timeout=240, limit=10000):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, errors='replace', timeout=timeout, check=False)
        text = (p.stdout + '\n' + p.stderr)
        return {'exit_code':p.returncode, 'output':text[:limit]}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {'exit_code':124, 'output':str(e)}


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):h.update(chunk)
    return h.hexdigest()


def api(path, method='GET', **kwargs):
    url=BASE+'/api/v1/worker/'+path.lstrip('/')
    headers={'X-Worker-Key':KEY}
    headers.update(kwargs.pop('headers',{}))
    r=requests.request(method,url,headers=headers,timeout=kwargs.pop('timeout',90),**kwargs)
    r.raise_for_status()
    return r


def get_job():
    return api('jobs/'+JOB).json()


def download(job, destination):
    if not job['has_input']:return None
    clean=pathlib.Path(job.get('original_name') or 'input.apk').name
    ext=pathlib.Path(clean).suffix.lower()
    if ext not in ['.apk','.dex','.smali','.so']:
        raise ValueError('Unsupported input extension')
    dst=destination/('input'+ext)
    with api('jobs/'+JOB+'/input',stream=True,timeout=120) as response:
        with open(dst,'wb') as f:
            for c in response.iter_content(1024*1024):
                if c: f.write(c)
    if dst.stat().st_size>26*1024*1024:raise ValueError('Oversized binary')
    return dst


def verify_apk_zip(path):
    with zipfile.ZipFile(path) as z:
        files=z.infolist()
        if len(files)>20000 or sum(m.file_size for m in files)>750*1024*1024:
            raise ValueError('Refusing oversized ZIP/archive bomb')
        if not any(x.filename=='AndroidManifest.xml' for x in files):
            raise ValueError('Not an Android APK (manifest missing)')


def inspect_native(path):
    result = {
        'file':run(['file','-b',str(path)],timeout=20),
        'elf_header':run(['readelf','-h',str(path)],timeout=25,limit=5000),
        'dynamic_symbols':run(['readelf','-Ws',str(path)],timeout=30,limit=10500),
        'dynamic_tags':run(['readelf','-d',str(path)],timeout=20,limit=5000),
        'imported_libraries':run(['readelf','-d',str(path)],timeout=20,limit=5000),
    }
    ghidra_home = os.environ.get('GHIDRA_HOME','')
    if ghidra_home and pathlib.Path(ghidra_home,'support','analyzeHeadless').is_file():
        with tempfile.TemporaryDirectory(prefix='ghidra-project-') as d:
            result['ghidra_headless'] = run([str(pathlib.Path(ghidra_home,'support','analyzeHeadless')),d,'AuthorizedRE','-import',str(path),'-analysisTimeoutPerFile','180','-deleteProject'],timeout=240,limit=6500)
    return result


def analyze(path, workspace, decode=False):
    ext=path.suffix.lower()
    summary={'filename':path.name,'sha256':sha256(path),'bytes':path.stat().st_size,'type':ext}
    if ext=='.apk':
        verify_apk_zip(path)
        with zipfile.ZipFile(path) as z:
            names=z.namelist()
            dex=[n for n in names if re.fullmatch(r'classes\d*\.dex',n)]
            libs=[n for n in names if re.fullmatch(r'lib/[^/]+/[^/]+\.so',n)]
            summary.update({'dex_files':dex[:60], 'native_libraries':libs[:120], 'zip_entries':len(names)})
            samples=[]
            for name in libs[:1 if os.environ.get('GHIDRA_HOME') else 3]:
                if z.getinfo(name).file_size>20*1024*1024:continue
                local=workspace/('native_'+len(samples).__str__()+'.so')
                with z.open(name) as src,open(local,'wb') as dst:shutil.copyfileobj(src,dst)
                samples.append({'path':name,'meta':inspect_native(local)})
            summary['native_sample']=samples
        if decode:
            dest=workspace/'decoded'
            summary['apktool']=run(['apktool','d','-f','-o',str(dest),str(path)],timeout=320,limit=4500)
            if summary['apktool']['exit_code'] != 0: raise RuntimeError('Apktool decode failed: '+summary['apktool']['output'][-1500:])
            summary['decoded_smali_files']=len(list(dest.glob('smali*/**/*.smali')))
        jadx_dest=workspace/'jadx'
        summary['jadx']=run(['jadx','--no-res','-d',str(jadx_dest),str(path)],timeout=320,limit=2500)
        if jadx_dest.exists():
            files=list(jadx_dest.rglob('*.java'))
            summary['decompiled_java_files']=len(files)
            summary['java_samples']=[str(f.relative_to(jadx_dest)) for f in files[:40]]
    elif ext=='.dex':
        summary['header_magic']=path.read_bytes()[:8].hex()
        summary['jadx']=run(['jadx','--no-res','-d',str(workspace/'jadx'),str(path)],timeout=320,limit=2500)
        summary['decompiled_java_files']=len(list((workspace/'jadx').rglob('*.java')))
    elif ext=='.so':
        summary['native']=inspect_native(path)
    elif ext=='.smali':
        text=path.read_text(encoding='utf-8')
        summary['line_count']=text.count('\n')+1
        summary['class_declarations']=re.findall(r'^\.class\s+.+$',text,re.M)[:20]
        summary['method_count']=len(re.findall(r'^\.method\b',text,re.M))
        summary['sample']=text[:12000]
    else:raise ValueError('Unrecognized extension')
    return summary


def exact_patch(job, path, workspace):
    """User-specified explicit Smali text replacement. Never patch machine code or run generated shell."""
    spec=job.get('patch_spec') or {}
    find=spec.get('find','');replace=spec.get('replace','')
    if not isinstance(find,str) or not find or not isinstance(replace,str): raise ValueError('Invalid patch specification')
    if path.suffix=='.smali':
        target=path
    elif path.suffix=='.apk':
        rel=spec.get('path')
        if not isinstance(rel,str) or not re.fullmatch(r'smali[^/]*/(?:[a-zA-Z0-9_$.-]+/)*[a-zA-Z0-9_$.-]+\.smali',rel):
            raise ValueError('Smali path must be within a smali*/ folder')
        target=(workspace/'decoded'/rel).resolve()
        target.relative_to((workspace/'decoded').resolve())
        if target.is_symlink() or not target.is_file(): raise ValueError('Target Smali file not found or unsafe')
    else:raise ValueError('Patching supported only for APK Smali or standalone Smali')
    src=target.read_text(encoding='utf-8')
    count=src.count(find)
    if count!=1:raise ValueError(f'Exact-match expected once, found {count}')
    new=src.replace(find,replace,1)
    original_hash=hashlib.sha256(src.encode()).hexdigest()
    target.write_text(new,encoding='utf-8')
    diff=''.join(difflib.unified_diff(src.splitlines(True),new.splitlines(True),fromfile='before.smali',tofile='after.smali'))
    (OUTPUT/'patch.diff').write_text(diff,encoding='utf-8')
    patched=OUTPUT/'patched.smali';shutil.copyfile(target,patched)
    outcome={'patch_applied':True,'target':str(target.relative_to(workspace)) if path.suffix=='.apk' else path.name,
             'original_sha256':original_hash,'patched_sha256':sha256(target),'diff_preview':diff[:10000]}
    if path.suffix=='.apk':
        unsigned=OUTPUT/'rebuild-UNSIGNED.apk'
        result=run(['apktool','b',str(workspace/'decoded'),'-o',str(unsigned)],timeout=320,limit=5000)
        outcome['apktool_rebuild']=result
        if result['exit_code'] or not unsigned.is_file():
            outcome['note']='Smali patch generated, but APK rebuild failed. APK signing is intentionally not automated.'
        else:outcome['note']='Rebuilt APK is UNSIGNED; requires your own valid signing key before installation.'
    return outcome


def infer(prompt, system=None):
    # Server is available only inside the ephemeral runner, not over the public internet.
    llama='/tmp/llama.cpp/build/bin/llama-server'
    log=open(OUTPUT/'llama-server.log','w')
    proc=subprocess.Popen([llama,'-hf',MODEL,'--host','127.0.0.1','--port','8080','-c','16384','-t','4','-ngl','0'],stdout=log,stderr=subprocess.STDOUT)
    try:
        deadline=time.monotonic()+540
        while time.monotonic()<deadline:
            if proc.poll() is not None:raise RuntimeError('llama-server exited during startup; see workflow artifacts')
            try:
                r=requests.get('http://127.0.0.1:8080/health',timeout=3)
                if r.status_code==200:break
            except requests.RequestException:pass
            time.sleep(4)
        else:raise RuntimeError('Model startup timeout')
        system = system or 'You are a code analysis assistant. Focus on authorized software, reproducible static analysis, and correct code edits. State uncertainty. Do not invent runtime observations.'
        req={'model':MODEL,'messages':[{'role':'system','content':system},{'role':'user','content':prompt[:23000]}],'temperature':0.25,'max_tokens':1100,'stream':False}
        response=requests.post('http://127.0.0.1:8080/v1/chat/completions',json=req,timeout=420)
        if response.status_code==400 and 'context' in response.text.lower():
            # Static findings can exceed the model context. Retry with a smaller, still-useful prompt.
            req['messages'][-1]['content']=prompt[:2000]
            response=requests.post('http://127.0.0.1:8080/v1/chat/completions',json=req,timeout=420)
        response.raise_for_status()
        message=response.json()['choices'][0]['message']
        return message.get('content') or message.get('reasoning_content') or '[Model returned no text]' 
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill()
        log.close()


def report_artifact():
    archive=OUTPUT/'report.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for f in OUTPUT.iterdir():
            if f.is_file() and f!=archive:
                # Skip huge rebuilt APK: avoid PHP upload limits, but keep it in the GitHub Actions artifact.
                if f.suffix=='.apk' and f.stat().st_size>15*1024*1024:continue
                if f.name=='llama-server.log':
                    z.write(f,'logs/llama-server.log')
                else:z.write(f,f.name)
    return archive


def main():
    if not re.fullmatch('[0-9a-f]{32}',JOB):raise ValueError('Invalid JOB_ID')
    if not BASE.startswith('https://') or not KEY:raise ValueError('PANEL_BASE_URL HTTPS and WORKER_KEY secret are required')
    if not MODEL.startswith('mradermacher/'):raise ValueError('Model repo not allowlisted')
    job=get_job()
    with tempfile.TemporaryDirectory(prefix='authorized-re-') as t:
        work=pathlib.Path(t); path=download(job,work)
        summary={'job_id':JOB,'kind':job['kind'],'model':MODEL}
        if path:
            summary['analysis']=analyze(path,work,decode=(job['kind']=='patch'))
        if job['kind']=='patch':summary['patch']=exact_patch(job,path,work)
        (OUTPUT/'analysis.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
        chat_system = ('You are a helpful, concise assistant. Answer the user directly and briefly. '
                       'You are text-only: you cannot see, read, or open images or files. If an image is attached, '
                       'say you cannot see images and ask the user to describe it in text. '
                       'Never invent job metadata, findings or runtime observations.')
        use_chat_system = (job['kind']=='chat' and not path)
        prompt=('User request:\n'+job['prompt']+'\n\nStatic findings (bounded):\n'+json.dumps(summary,ensure_ascii=False)[:16000]
                if not use_chat_system else job['prompt'])
        text=infer(prompt, chat_system if use_chat_system else None)
        (OUTPUT/'model-response.txt').write_text(text,encoding='utf-8')
        z=report_artifact()
        try:
            with open(z,'rb') as file:
                api('jobs/'+JOB+'/artifact','POST',files={'artifact':('report.zip',file,'application/zip')},timeout=120)
        except Exception as exc:
            text+='\n\n[Warning: Could not upload ZIP to PHP host; retrieve it from GitHub Actions artifacts: '+str(exc)[:150]+']'
        api('jobs/'+JOB+'/result','POST',json={'status':'done','result':text})


if __name__=='__main__':
    try:main()
    except Exception as exc:
        msg=f'{type(exc).__name__}: {exc}'
        (OUTPUT/'error.txt').write_text(msg,encoding='utf-8')
        print(msg,flush=True)
        if re.fullmatch('[0-9a-f]{32}',JOB) and BASE.startswith('https://') and KEY:
            try:api('jobs/'+JOB+'/result','POST',json={'status':'failed','error':msg})
            except Exception as callback_error:print('Result callback failed:',callback_error,flush=True)
        raise
