"""Deterministic process-tree and memory-ranking checks for resource snapshots."""

from types import SimpleNamespace

from services import resource_history as history


_MB = 2**20


def test_sample_excludes_reused_missing_and_cyclic_parent_links(monkeypatch):
    def process(pid, parent_pid, created, megabytes, name="node.exe", *, private=True):
        memory = SimpleNamespace(vms=megabytes * _MB, rss=(10 - megabytes) * _MB)
        if private:
            memory.private = megabytes * _MB
        return SimpleNamespace(info={
            "pid": pid,
            "ppid": parent_pid,
            "name": name,
            "create_time": created,
            "memory_info": memory,
        })

    processes = [
        process(10, 1, 100, 1, "PYTHON.EXE"),
        process(11, 10, 110, 2, "Claude.exe", private=False),
        process(12, 11, 120, 3),
        # PID 99 was reused after process 13 started. It is not 13's parent.
        process(13, 99, 130, 4),
        process(99, 10, 140, 5),
        process(14, 999, 200, 6),
        process(15, 16, 300, 7),
        process(16, 15, 300, 8),
        process(17, 17, 400, 9, None),
        # Inaccessible metrics must not enter totals or ancestry walks.
        process(18, 10, None, 1),
        process(19, 10, 150, 1),
    ]
    processes[-1].info["memory_info"] = None
    monkeypatch.setattr(history.os, "getpid", lambda: 10)
    monkeypatch.setattr(
        history.psutil, "process_iter", lambda attrs, ad_value: iter(processes)
    )
    monkeypatch.setattr(history.psutil, "virtual_memory", lambda: SimpleNamespace(
        percent=50, available=1024 * _MB, total=2048 * _MB
    ))
    monkeypatch.setattr(history.psutil, "boot_time", lambda: 10.0)
    monkeypatch.setattr(history.time, "time", lambda: 500.0)
    monkeypatch.setattr(history, "_commit", lambda: {"commit_mb": 512.0})

    result = history.sample([
        {"kind": "messaging"}, {"kind": "messaging"}, {"kind": "autowake"}
    ])

    assert result["process_count"] == 9
    assert result["anam_tree_count"] == 4
    assert result["anam_tree_private_mb"] == 11.0
    assert result["process_counts"]["python.exe"] == 1
    assert result["process_counts"]["claude.exe"] == 1
    assert result["process_counts"]["node.exe"] == 6
    assert result["codex_messaging"] == 2
    assert result["codex_autowake"] == 1
    assert result["at"] == 500.0
    assert result["boot_at"] == 10.0
    assert result["available_mb"] == 1024.0
    assert result["physical_total_mb"] == 2048.0
    assert result["commit_mb"] == 512.0
    assert [row["pid"] for row in result["top_private_memory"]] == [
        17, 16, 15, 14, 99, 13, 12, 11
    ]
    assert [row["pid"] for row in result["top_resident_memory"]] == [
        10, 11, 12, 13, 99, 14, 15, 16
    ]
    assert result["top_private_memory"][-1]["private_mb"] == 2.0
    assert result["top_resident_memory"][0]["resident_mb"] == 9.0


def test_sample_handles_empty_process_snapshot(monkeypatch):
    monkeypatch.setattr(history.psutil, "process_iter", lambda attrs, ad_value: iter(()))
    monkeypatch.setattr(history.psutil, "virtual_memory", lambda: SimpleNamespace(
        percent=0, available=1024 * _MB, total=1024 * _MB
    ))
    monkeypatch.setattr(history.psutil, "boot_time", lambda: 10.0)
    monkeypatch.setattr(history, "_commit", lambda: {})

    result = history.sample()

    assert result["process_count"] == 0
    assert result["anam_tree_count"] == 0
    assert result["anam_tree_private_mb"] == 0
    assert all(count == 0 for count in result["process_counts"].values())
    assert result["top_private_memory"] == []
    assert result["top_resident_memory"] == []
    assert result["codex_messaging"] == result["codex_autowake"] == 0
