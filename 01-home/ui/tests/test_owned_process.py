"""Real Windows subprocess tests, using only children created by these tests."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import psutil
import pytest
from services.owned_process import owned_popen, close_owned_process

pytestmark = pytest.mark.skipif(os.name!='nt',reason='Windows Job Object integration')
ROOT = Path(__file__).resolve().parents[1]
CHILD = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"

def wait_gone(pid):
    end=time.monotonic()+5
    while time.monotonic()<end:
        if not psutil.pid_exists(pid): return
        time.sleep(.05)
    pytest.fail(f'Owned test descendant {pid} survived')


def test_root_crash_reaps_descendants_and_preserves_other_messaging_process():
    warm=owned_popen([sys.executable,'-c','import time; time.sleep(60)'])
    root=owned_popen([sys.executable,'-c',CHILD],stdout=subprocess.PIPE,text=True)
    try:
        child=int(root.stdout.readline())
        root.kill();root.wait(timeout=5)
        wait_gone(child)
        assert warm.poll() is None
        assert close_owned_process(root)
    finally:
        close_owned_process(root);close_owned_process(warm)
        root.stdout.close()


def test_owner_crash_closes_job_without_running_cleanup_code():
    code = ('import subprocess,sys,time,json; from services.owned_process import owned_popen; '
        f'p=owned_popen([sys.executable,"-c",{CHILD!r}],stdout=subprocess.PIPE,text=True); '
        'child=int(p.stdout.readline()); print(json.dumps([p.pid,child]),flush=True); time.sleep(60)')
    owner=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,stdout=subprocess.PIPE,text=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    ids=[]
    try:
        ids=json.loads(owner.stdout.readline())
        owner.kill();owner.wait(timeout=5)
        for pid in ids: wait_gone(pid)
    finally:
        if owner.poll() is None: owner.kill()
        owner.wait(timeout=5);owner.stdout.close()
        for pid in ids:
            try: psutil.Process(pid).kill()
            except psutil.NoSuchProcess: pass


def test_explicit_close_terminates_live_tree():
    root=owned_popen([sys.executable,'-c',CHILD],stdout=subprocess.PIPE,text=True)
    try:
        child=int(root.stdout.readline())
        assert close_owned_process(root)
        wait_gone(child)
        assert root.poll() is not None
    finally:
        close_owned_process(root);root.stdout.close()
