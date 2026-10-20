import time
import pytest
from services import resource_history as history

@pytest.fixture(autouse=True)
def database(tmp_path,monkeypatch):
    monkeypatch.setattr(history,'DB_PATH',tmp_path/'resources.db')


def test_history_persists_and_bounds_rows_and_age(monkeypatch):
    now=time.time()
    monkeypatch.setattr(history,'MAX_SAMPLES',3)
    history.append({'at':now-history.MAX_AGE-1,'memory_percent':99})
    for n in range(5): history.append({'at':now+n,'memory_percent':n})
    result=history.report(100)
    assert result['sample_count']==3
    assert [r['memory_percent'] for r in result['recent']]==[2,3,4]
    assert history.DB_PATH.exists()
    # A second reader opens SQLite independently; history is not process-local.
    assert history.report(1)['recent']==[{'at':now+4,'memory_percent':4}]


def test_snapshot_has_only_bounded_metadata():
    row=history.sample([{'kind':'messaging'},{'kind':'autowake'}])
    assert row['codex_messaging']==row['codex_autowake']==1
    assert row['anam_tree_count']>=1
    assert 0<=row['memory_percent']<=100
    assert len(row['top_private_memory'])<=8
    assert all(set(p)=={'pid','name','private_mb'} for p in row['top_private_memory'])
    history.append(row)
    assert history.report()['sample_count']==1


def test_payload_bound_rejects_unbounded_data():
    with pytest.raises(ValueError,match='size bound'):
        history.append({'at':time.time(),'unbounded':'x'*8001})


def test_history_survives_writer_process_exit():
    import json
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    setup = "import sys; from pathlib import Path; from services import resource_history as h; h.DB_PATH=Path(sys.argv[1]); "
    writer = setup + "import time,os; h.append({'at':time.time(),'marker':'persisted'}); os._exit(0)"
    reader = setup + "import json; print(json.dumps(h.report()))"
    subprocess.run([sys.executable,'-c',writer,str(history.DB_PATH)],cwd=root,check=True,timeout=30)
    result = subprocess.run([sys.executable,'-c',reader,str(history.DB_PATH)],cwd=root,check=True,capture_output=True,text=True,timeout=30)
    stored = json.loads(result.stdout)
    assert stored['sample_count'] == 1
    assert stored['recent'][0]['marker'] == 'persisted'
