"""Real-process restart matrix: demo/fixture only, private dirs and random ports."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest
from pbdesk.services.client_lifecycle import stop_generation
from pbdesk.services.core_settings import RESTART_RECORD
from pbdesk.services.lifecycle import CoreStartupError
from test_coupled_lifecycle import isolated, eventually


def verified_health(health):
    try:
        return health()
    except CoreStartupError:
        # A graceful socket close can race a health read; only the test waiter
        # retries the read. Product recovery still requires connection refusal.
        return None


def restart(request, instance, request_id='restart-process'):
    payload = {'expected_instance_id': instance, 'request_id': request_id}
    for _ in range(20):
        response = request('POST', '/api/v1/core/restart', json=payload)
        if response.status_code != 409 or 'active' not in response.json().get('detail', ''):
            break
        # A definitive busy rejection has not initiated restart.
        time.sleep(.05)
    assert response.status_code == 202, response.text
    return response.json()


def test_graceful_restart_same_owned_web_survives_and_leases_recover(isolated):
    settings, lease, health, request, children = isolated
    desktop = lease()
    old = desktop.instance_id
    generation = stop_generation(settings)
    old_process = children[0]
    updated = request('PATCH', '/api/v1/core/settings', json={'expected_instance_id': old, 'notifications_enabled': False})
    assert updated.status_code == 200 and not updated.json()['notifications']['requested']
    web = request('POST', '/api/v1/web', json={'enabled': True}).json()
    assert web['enabled'] and web['ownership'] == 'owned', web
    # Inspect the exact old core's child relationship, without sending signals or
    # discovering a PID to kill. Product code never uses /proc for ownership.
    child_ids = {pid for task in Path(f'/proc/{old_process.pid}/task').iterdir()
                 for pid in (task / 'children').read_text().split()}
    web_ids = [pid for pid in child_ids if b'pbdesk.web' in Path(f'/proc/{pid}/cmdline').read_bytes()]
    assert len(web_ids) == 1
    web_pid = web_ids[0]
    web_start = Path(f'/proc/{web_pid}/stat').read_text().split()[21]
    assert restart(request, old)['instance_id'] == old
    old_process.wait(timeout=8)
    assert old_process.returncode == 0
    replacement = eventually(lambda: (value := verified_health(health)) and value['instance_id'] != old and value,
                             timeout=15)
    eventually(lambda: desktop.instance_id == replacement['instance_id'])
    adopted = eventually(lambda: (value := request('GET', '/api/v1/web').json())['enabled'] and value)
    assert adopted['instance_id'] == web['instance_id'] and adopted['ownership'] == 'adopted'
    assert Path(f'/proc/{web_pid}/stat').read_text().split()[21] == web_start
    assert stop_generation(settings) == generation
    assert not (settings.state_dir / RESTART_RECORD).exists()
    config = request('GET', '/api/v1/core/settings').json()
    assert config['instance_id'] == replacement['instance_id']
    assert not config['notifications']['requested']
    eventually(lambda: request('GET', '/api/v1/core/settings').json()['lifecycle']['desktop_clients'] == 1)
    assert request('GET', '/api/v1/capabilities').json()['provider_state'] == 'demo'


@pytest.mark.parametrize('mode', ['demo-standalone', 'fixture-coupled'])
def test_original_launch_profile_wins_over_default_client_recovery(isolated, tmp_path, mode):
    settings, lease, health, request, children = isolated
    command = [sys.executable, '-m', 'pbdesk.cli', '--foreground']
    if mode == 'demo-standalone':
        command += ['--demo', '--standalone']
    else:
        fixture = tmp_path / 'fixture.json'
        fixture.write_text(json.dumps([{'iden': 'restart-fixture-only', 'type': 'note', 'title': 'fixture',
            'body': 'fixture-only preserved event', 'created': 1704067200}]))
        command += ['--fixture', str(fixture)]
    old_process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    original = eventually(lambda: verified_health(health))
    desktop = lease()
    # This client normally launches demo mode, deliberately unlike the fixture
    # profile; its recovery races the helper under the shared launcher lock.
    generation = desktop.generation
    restart(request, original['instance_id'])
    old_process.wait(timeout=8)
    replacement = eventually(lambda: (value := verified_health(health)) and value['instance_id'] != original['instance_id'] and value,
                             timeout=15)
    eventually(lambda: desktop.instance_id == replacement['instance_id'])
    state = request('GET', '/api/v1/core/settings').json()
    assert state['lifecycle']['mode'] == ('standalone' if mode.endswith('standalone') else 'coupled')
    assert state['lifecycle']['renew_seconds'] == .15
    assert not state['notifications']['requested']
    assert stop_generation(settings) == generation
    items = request('GET', '/api/v1/items').json()['items']
    if mode == 'fixture-coupled':
        assert [item['source_id'] for item in items] == ['stored:restart-fixture-only']
    else:
        desktop.close()
        time.sleep(1.6)
        assert verified_health(health)['instance_id'] == replacement['instance_id']


def test_standalone_restart_helper_holds_no_lease(isolated):
    settings, lease, health, request, children = isolated
    old_process = subprocess.Popen([sys.executable, '-m', 'pbdesk.cli', '--demo', '--standalone'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    original = eventually(lambda: verified_health(health))
    generation = stop_generation(settings)
    restart(request, original['instance_id'])
    old_process.wait(timeout=8)
    replacement = eventually(lambda: (value := verified_health(health)) and value['instance_id'] != original['instance_id'] and value,
                             timeout=15)
    state = request('GET', '/api/v1/core/settings').json()
    assert state['lifecycle']['mode'] == 'standalone'
    assert state['lifecycle']['desktop_clients'] == state['lifecycle']['web_clients'] == 0
    assert stop_generation(settings) == generation
    time.sleep(1.6)
    assert verified_health(health)['instance_id'] == replacement['instance_id']


def test_stop_all_after_restart_acceptance_prevents_all_revival(isolated):
    settings, lease, health, request, children = isolated
    desktop = lease()
    old_generation = desktop.generation
    old = desktop.instance_id
    restart(request, old)
    # Stop All is valid while restarting; its generation barrier wins over the
    # helper and all old desktop/web lease recovery contenders.
    response = request('POST', '/api/v1/shutdown')
    assert response.status_code == 200
    eventually(lambda: verified_health(health) is None)
    time.sleep(2)
    assert verified_health(health) is None
    assert stop_generation(settings) != old_generation
    assert not (settings.state_dir / RESTART_RECORD).exists()
    fresh = lease()
    assert fresh.generation != old_generation and fresh.instance_id != old
