from dataclasses import replace

from xbb import sources, storage
from xbb import __main__ as cli
from xbb.config import Config


def test_backfill_source_dispatches_registered_adapter_without_x_cap(monkeypatch, capsys):
    calls = []

    class Adapter:
        def is_configured(self, cfg):
            return True

        def is_connected(self, con):
            return True

        def backfill(self, con, cfg, *, incremental, max_total):
            calls.append((incremental, max_total))
            return 3

    class Connection:
        def close(self):
            pass

    cfg = replace(Config.from_env(), database_url="postgresql://u:p@localhost/dev")
    monkeypatch.setattr(cli.Config, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(storage, "init_db", lambda dsn, tenant: None)
    monkeypatch.setattr(storage, "connect", lambda dsn, tenant: Connection())
    monkeypatch.setitem(sources.REGISTRY, "fake", Adapter())
    assert cli.main(["backfill", "--source", "fake"]) == 0
    assert calls == [(True, None)]
    assert "3 new bookmark(s)" in capsys.readouterr().out


def test_backfill_rejects_invalid_source_syntax(capsys):
    assert cli.main(["backfill", "fake"]) == 2
    assert "Usage:" in capsys.readouterr().err
