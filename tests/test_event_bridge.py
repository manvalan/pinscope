"""GCS event log: a new run must not replay a prior pipeline_complete."""

from backend.services.event_bridge import GCSEventBroker


def test_clear_history_wipes_terminal_events(tmp_path):
    from backend.services.storage import LocalStorageBackend

    storage = LocalStorageBackend(tmp_path)
    broker = GCSEventBroker(storage, "local")
    broker.publish("p1", "step_update", {"stage": "validation"})
    broker.publish("p1", "pipeline_complete", {"summary": {}})

    prefix = "users/local/projects/p1/events/"
    assert len(storage.list_prefix(prefix)) == 2

    broker.clear_history("p1")
    assert storage.list_prefix(prefix) == []

    broker.publish("p1", "step_update", {"stage": "bom_parse"})
    keys = storage.list_prefix(prefix)
    assert len(keys) == 1
    assert keys[0].endswith("0000000000.json")
    assert storage.read_json(keys[0])["event"] == "step_update"
