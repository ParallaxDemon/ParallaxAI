#!/usr/bin/env python3
"""Six-hour watchdog: re-dispatch stalled or failed panel jobs.

Runs on a schedule from GitHub Actions. It never runs a model itself; it only
checks the PHP panel's job table and asks the agent workflow to try again.
"""
import datetime
import json
import os
import urllib.error
import urllib.request

BASE = os.environ['PANEL_BASE_URL'].rstrip('/')
PANEL_KEY = os.environ['PANEL_API_KEY']
GH_TOKEN = os.environ['GH_TOKEN']
REPO = os.environ.get('GITHUB_REPOSITORY', '')
WORKFLOW = 'deepseek-agent.yml'
REF = os.environ.get('GITHUB_REF_NAME', 'main')

# A dispatched job older than this with no worker pickup is considered stuck.
QUEUED_STALE_MIN = 60
# Agent workflow timeout is 45 minutes; allow a buffer before calling it stuck.
RUNNING_STALE_MIN = 100
# Do not keep reviving very old failures forever.
MAX_AGE_DAYS = 7

NOW = datetime.datetime.now(datetime.timezone.utc)


def call(url, token, payload=None, extra_headers=None):
    headers = {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'DeepSeek-watchdog',
    }
    if extra_headers:
        headers.update(extra_headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode('utf-8', 'replace')
        return json.loads(body) if body.strip() else {}


def age_minutes(job):
    stamp = job.get('updated_at') or job.get('created_at') or ''
    try:
        moment = datetime.datetime.fromisoformat(stamp.replace(' ', 'T'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        return (NOW - moment).total_seconds() / 60
    except (ValueError, TypeError):
        return 0


def main():
    jobs = call(BASE + '/api/v1/jobs', PANEL_KEY).get('jobs', [])
    report = {'checked': len(jobs), 'retried': [], 'redischatched': [], 'skipped_old': []}
    print('[watchdog] panel reachable, jobs listed:', len(jobs))

    for job in jobs:
        jid = str(job.get('id', ''))
        if len(jid) != 32:
            continue
        status = str(job.get('status', ''))
        age = age_minutes(job)
        if age > MAX_AGE_DAYS * 24 * 60:
            report['skipped_old'].append(jid)
            continue

        if status in ('failed', 'dispatch_failed'):
            # The panel endpoint resets status to queued before dispatching.
            try:
                call(BASE + '/api/v1/jobs/' + jid + '/retry', PANEL_KEY, payload={})
                report['retried'].append(jid)
                print('[watchdog] retried', jid, 'status:', status)
            except Exception as exc:
                print('[watchdog] retry failed', jid, exc)
            continue

        stale = ((status == 'queued' and age > QUEUED_STALE_MIN)
                 or (status == 'running' and age > RUNNING_STALE_MIN))
        if not stale:
            continue

        # A GITHUB_TOKEN is allowed to create workflow_dispatch events.
        try:
            call('https://api.github.com/repos/' + REPO + '/actions/workflows/'
                 + WORKFLOW + '/dispatches', GH_TOKEN,
                 payload={'ref': REF, 'inputs': {'job_id': jid}},
                 extra_headers={'X-GitHub-Api-Version': '2022-11-28'})
            report['redischatched'].append(jid)
            print('[watchdog] re-dispatched', jid, 'status:', status,
                  'age:', int(age), 'min')
        except Exception as exc:
            print('[watchdog] dispatch failed', jid, exc)

    print('[watchdog] summary: ' + json.dumps(report))
    with open('watchdog-report.json', 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2)


if __name__ == '__main__':
    main()
