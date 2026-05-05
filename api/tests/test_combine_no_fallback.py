from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_SOURCE_HANDLER_PATH = Path(__file__).resolve().parents[1] / "app" / "combine" / "source_handler.py"
_SPEC = spec_from_file_location("combine_source_handler_test", _SOURCE_HANDLER_PATH)
_MODULE = module_from_spec(_SPEC)
assert _SPEC is not None and _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
SourceHandler = _MODULE.SourceHandler


def test_get_top_reports_does_not_fall_back_from_elo(monkeypatch):
    handler = SourceHandler("/tmp/missing.db", "/tmp")

    monkeypatch.setattr(handler, "get_top_reports_by_elo", lambda limit: [])
    monkeypatch.setattr(handler, "get_top_reports_by_single_doc", lambda limit: ["single"])

    assert handler.get_top_reports(limit=2, prefer_elo=True) == []


def test_get_top_reports_does_not_fall_back_from_single_doc(monkeypatch):
    handler = SourceHandler("/tmp/missing.db", "/tmp")

    monkeypatch.setattr(handler, "get_top_reports_by_elo", lambda limit: ["elo"])
    monkeypatch.setattr(handler, "get_top_reports_by_single_doc", lambda limit: [])

    assert handler.get_top_reports(limit=2, prefer_elo=False) == []
