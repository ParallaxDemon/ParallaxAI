#!/usr/bin/env python3
"""ParallaxAI terminal client — single chat, batched chats, status and artifacts.

Uses only the Python standard library. Configure once with:

    parallax config --base https://ai.parallaxserver.online --key YOUR_API_KEY

Environment variables PANEL_BASE_URL and PANEL_API_KEY override the saved config.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / '.config' / 'parallax-ai' / 'config.json'
TERMINAL = ('done', 'failed', 'dispatch_failed')
MARK = {'done': '[OK]', 'failed': '[FAIL]', 'dispatch_failed': '[DISPATCH FAIL]',
        'queued': '[queued]', 'running': '[running]'}


def die(message, code=1):
    print('error: ' + str(message), file=sys.stderr)
    raise SystemExit(code)


def load_config():
    saved = {}
    if CONFIG_PATH.is_file():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            die('cannot read config: ' + str(exc))
    base = os.environ.get('PANEL_BASE_URL') or saved.get('base_url') or ''
    key = os.environ.get('PANEL_API_KEY') or saved.get('api_key') or ''
    return base.rstrip('/'), key


def save_config(base, key):
    base = (base or '').rstrip('/')
    if not base.startswith('https://'):
        die('base URL must start with https://')
    if not key:
        die('API key must not be empty')
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({'base_url': base, 'api_key': key}, indent=2),
                           encoding='utf-8')
    os.chmod(CONFIG_PATH, 0o600)
    print('saved config to ' + str(CONFIG_PATH) + ' (mode 600)')


def request(method, path, payload=None, raw=False, timeout=60, allow_404=False):
    base, key = load_config()
    if not base or not key:
        die('not configured. Run: parallax config --base URL --key KEY')
    url = base + '/api/v1/' + path.lstrip('/')
    data = None
    headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json',
               'User-Agent': 'ParallaxAI-CLI/1.0'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            if raw:
                return body
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and allow_404:
            return None
        body = exc.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(body)
        except ValueError:
            detail = {'error': body[:300]}
        die('API returned HTTP {}: {}'.format(exc.code, detail))
    except urllib.error.URLError as exc:
        die('cannot reach panel: ' + str(exc.reason))


def emit(obj, as_json=False):
    if as_json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        print(obj if isinstance(obj, str) else json.dumps(obj, indent=2, ensure_ascii=False))


def short(text, limit=70):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def print_job(job, index=None):
    prefix = '{:>2}. '.format(index) if index else ''
    print('{}{} {}  {}'.format(prefix, MARK.get(job.get('status'), '[?]'),
                               job.get('id', '')[:12], short(job.get('prompt'), 55)))
    if job.get('status') == 'done' and job.get('result'):
        print('      ' + short(job['result'], 200))
    elif job.get('error'):
        print('      error: ' + short(job['error'], 200))


def wait_for_job(job_id, timeout, interval=3):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        job = request('GET', 'jobs/' + job_id)
        if job.get('status') != last:
            print(MARK.get(job.get('status'), '[?]') + ' ' + job.get('status', ''))
            last = job.get('status')
        if job.get('status') in TERMINAL:
            return job
        time.sleep(interval)
    die('timed out waiting for job ' + job_id)


def wait_for_batch(batch_id, timeout, interval=3):
    deadline = time.monotonic() + timeout
    last = -1
    while time.monotonic() < deadline:
        batch = request('GET', 'batch/' + batch_id)
        jobs = batch.get('jobs', [])
        if batch.get('done') != last:
            print('progress: {}/{} done'.format(batch.get('done'), batch.get('count')))
            last = batch.get('done')
        if jobs and all(j.get('status') in TERMINAL for j in jobs):
            return batch
        time.sleep(interval)
    die('timed out waiting for batch ' + batch_id)


def cmd_config(args):
    _, key = load_config()
    save_config(args.base, args.key or key)


def cmd_chat(args):
    result = request('POST', 'jobs', {'kind': 'chat', 'prompt': args.prompt})
    if args.json and not args.wait:
        emit(result, True)
        return
    print('job: ' + result['id'])
    if args.no_wait:
        return
    job = wait_for_job(result['id'], args.timeout)
    if args.json:
        emit(job, True)
    else:
        print('result:')
        print(job.get('result') or '')
        if job.get('status') != 'done':
            print('error: ' + str(job.get('error') or ''), file=sys.stderr)
            raise SystemExit(2)


def cmd_batch(args):
    prompts = list(args.prompts)
    if args.file:
        lines = [line.strip() for line in Path(args.file).read_text(encoding='utf-8').splitlines()]
        prompts.extend(line for line in lines if line and not line.startswith('#'))
    if not prompts:
        die('provide at least one prompt, or use --file prompts.txt')
    if len(prompts) > 20:
        die('maximum 20 prompts per batch')
    result = request('POST', 'batch', {'kind': 'chat', 'prompts': prompts})
    if args.json and not args.wait:
        emit(result, True)
        return
    print('batch: {} ({} chats)'.format(result['batch_id'], result['count']))
    if args.no_wait:
        return
    batch = wait_for_batch(result['batch_id'], args.timeout)
    if args.json:
        emit(batch, True)
        return
    print('')
    for i, job in enumerate(batch.get('jobs', []), 1):
        print('--- chat {} | {} ---'.format(i, job.get('id')))
        print('Q: ' + str(job.get('prompt')))
        print('A: ' + (job.get('result') or '').strip())
        if job.get('error'):
            print('ERROR: ' + str(job['error']))
        print('')
    failed = [j for j in batch.get('jobs', []) if j.get('status') != 'done']
    if failed:
        raise SystemExit(2)


def cmd_status(args):
    target = args.target
    if len(target) == 32 and not args.batch:
        obj = request('GET', 'jobs/' + target, allow_404=True)
        if obj is None:
            obj = request('GET', 'batch/' + target)
        if args.json:
            emit(obj, True)
        elif 'jobs' in obj:
            print('batch {} — {}/{} done'.format(obj['batch_id'], obj['done'], obj['count']))
            for i, job in enumerate(obj['jobs'], 1):
                print_job(job, i)
        else:
            print_job(obj)
            if obj.get('result'):
                print('\n' + obj['result'])
        return
    obj = request('GET', 'batch/' + target)
    if args.json:
        emit(obj, True)
    else:
        print('batch {} — {}/{} done'.format(obj['batch_id'], obj['done'], obj['count']))
        for i, job in enumerate(obj['jobs'], 1):
            print_job(job, i)


def cmd_jobs(args):
    data = request('GET', 'jobs?limit=' + str(args.limit))
    jobs = data.get('jobs', [])
    if args.json:
        emit(jobs, True)
    else:
        for i, job in enumerate(jobs, 1):
            print_job(job, i)


def cmd_artifact(args):
    body = request('GET', 'jobs/' + args.job_id + '/artifact', raw=True)
    if not body[:2] == b'PK':
        die('response is not a ZIP file')
    output = args.output or ('report-' + args.job_id[:12] + '.zip')
    Path(output).write_bytes(body)
    print('saved ' + output + ' (' + str(len(body)) + ' bytes)')


def build_parser():
    p = argparse.ArgumentParser(prog='parallax', description='ParallaxAI terminal client')
    sub = p.add_subparsers(dest='command', required=True)

    c = sub.add_parser('config', help='save panel URL and API key')
    c.add_argument('--base', required=True, help='https://ai.parallaxserver.online')
    c.add_argument('--key', help='client API key (keeps existing if omitted)')
    c.set_defaults(func=cmd_config)

    c = sub.add_parser('chat', help='send one chat and wait for the reply')
    c.add_argument('prompt')
    c.add_argument('--no-wait', action='store_true')
    c.add_argument('--wait', action='store_true', help=argparse.SUPPRESS)
    c.add_argument('--timeout', type=int, default=900)
    c.add_argument('--json', action='store_true')
    c.set_defaults(func=cmd_chat)

    c = sub.add_parser('batch', help='send many chats in one batch')
    c.add_argument('prompts', nargs='*')
    c.add_argument('--file', help='text file with one prompt per line')
    c.add_argument('--no-wait', action='store_true')
    c.add_argument('--wait', action='store_true', help=argparse.SUPPRESS)
    c.add_argument('--timeout', type=int, default=1800)
    c.add_argument('--json', action='store_true')
    c.set_defaults(func=cmd_batch)

    c = sub.add_parser('status', help='show a job or batch')
    c.add_argument('target')
    c.add_argument('--batch', action='store_true', help='treat target as a batch id')
    c.add_argument('--json', action='store_true')
    c.set_defaults(func=cmd_status)

    c = sub.add_parser('jobs', help='list recent jobs')
    c.add_argument('--limit', type=int, default=15)
    c.add_argument('--json', action='store_true')
    c.set_defaults(func=cmd_jobs)

    c = sub.add_parser('artifact', help='download a job report ZIP')
    c.add_argument('job_id')
    c.add_argument('output', nargs='?')
    c.set_defaults(func=cmd_artifact)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
