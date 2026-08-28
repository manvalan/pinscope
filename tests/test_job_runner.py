"""Local worker pid file so a restarted API can see a dead subprocess."""

from backend.services import job_runner


def test_local_state_failed_when_pid_file_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(job_runner.settings, "data_dir", tmp_path)
    pid_path = tmp_path / "workers" / "proj.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("99999999")
    assert job_runner.get_execution_state("local/projects/proj") == "failed"
