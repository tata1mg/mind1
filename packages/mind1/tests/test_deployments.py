"""One repository deployed several times, each deployment a node callers can address.

The property these pin: a repository that declares deployments produces one copy of the
code and several nodes over it, and an edge lands on exactly one of those nodes rather
than fanning out across all of them.
"""
from __future__ import annotations

import json

import pytest
from mind1.codebase import CODEBASE
from mind1.manifest import ManifestError, load_manifest, resolve_services
from mind1.resolve import resolve
from mind1.store.models import KnowledgeObject
from mind1.store.sqlite import SqliteStore

GOOD = {"deployments": [
    {"name": "routing_api", "default": True, "aliases": ["routing"]},
    {"name": "routing_engine"},
]}


def _manifest(tmp_path, raw: dict):
    (tmp_path / "mind1.json").write_text(json.dumps(raw))
    return tmp_path


# --- manifest -------------------------------------------------------------
def test_deployments_parse(tmp_path):
    m = load_manifest(_manifest(tmp_path, GOOD))
    assert [d.name for d in m.deployments] == ["routing_api", "routing_engine"]
    assert m.deployments[0].default is True and m.deployments[0].aliases == ["routing"]
    assert m.deployments[1].default is False


@pytest.mark.parametrize("raw, message", [
    ({"deployments": [{"name": "a"}, {"name": "b"}]}, "must be marked 'default: true'"),
    ({"deployments": [{"name": "a", "default": True},
                      {"name": "b", "default": True}]}, "only one deployment can be default"),
    ({"deployments": [{"name": "a", "default": True}, {"name": "a"}]}, "duplicate deployment"),
    ({"deployments": [{"default": True}]}, "missing 'name'"),
    ({"deployments": [{"name": "a", "default": True, "aliases": ["x"]},
                      {"name": "b", "aliases": ["x"]}]}, "claimed by both"),
    ({"deployments": [{"name": "a", "default": True, "aliases": ["b"]},
                      {"name": "b"}]}, "also a deployment name"),
    ({"deployments": [{"name": "a", "default": True}],
      "services": [{"name": "s"}]}, "Declaring both"),
])
def test_a_manifest_that_would_build_a_wrong_graph_is_refused(tmp_path, raw, message):
    """Every one of these makes the graph wrong rather than incomplete, and a wrong graph
    gives no sign of it. No default means edges with no host evidence have no owner."""
    with pytest.raises(ManifestError, match=message):
        load_manifest(_manifest(tmp_path, raw))


def test_a_deployments_repo_is_indexed_once(tmp_path):
    """One spec, at the repository root. Two specs would index the code twice and write
    both copies into the same .context directory."""
    specs = resolve_services(_manifest(tmp_path, GOOD), default_name="routing")
    assert len(specs) == 1
    assert specs[0].path == "." and [d.name for d in specs[0].deployments] == [
        "routing_api", "routing_engine"]


def test_a_repo_without_deployments_is_unchanged(tmp_path):
    specs = resolve_services(tmp_path, default_name="orders")
    assert len(specs) == 1 and specs[0].deployments == []


# --- indexing -------------------------------------------------------------
def _repo(tmp_path, raw=GOOD):
    repo = tmp_path / "routing"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("def handler():\n    return 1\n")
    (repo / "mind1.json").write_text(json.dumps(raw))
    return repo


def test_indexing_writes_one_codebase_and_a_node_per_deployment(tmp_path):
    from mind1.indexing import index_repo

    store = SqliteStore(str(tmp_path / "x.db"))
    index_repo(_repo(tmp_path), store)

    assert [o.object_id for o in store.list_objects("service")] == [
        "routing_api", "routing_engine"]
    assert [o.object_id for o in store.list_objects("codebase")] == ["routing"]
    assert store.get("service:routing") is None, "the codebase is not itself a node"
    for name in ("routing_api", "routing_engine"):
        assert store.get(f"service:{name}").payload[CODEBASE] == "routing"


def test_the_symbols_are_stored_once_under_the_codebase(tmp_path):
    from mind1.indexing import index_repo

    store = SqliteStore(str(tmp_path / "x.db"))
    index_repo(_repo(tmp_path), store)

    syms = store.list_objects("code_symbol")
    assert syms, "the repository has code"
    assert {s.id.split(":")[1] for s in syms} == {"routing"}


def test_a_deployment_can_be_read_back_to_source(tmp_path):
    """`get_source` resolves a symbol's slug to a root. With deployments that slug names
    no service, which is why the codebase gets an object of its own."""
    from mind1.indexing import index_repo
    from mind1.service.source import SourceService

    store = SqliteStore(str(tmp_path / "x.db"))
    index_repo(_repo(tmp_path), store)
    assert SourceService(store, store)._root_of("routing")


# --- resolution -----------------------------------------------------------
def _svc(store, slug, *, codebase=None, default=False, aliases=(), outbound=(), ident=None,
         bindings=()):
    store.upsert_object(KnowledgeObject(
        "service", slug, slug, None, "unknown", "static", "index", None, None,
        {CODEBASE: codebase or slug, "default_deployment": default,
         "aliases": list(aliases), "outbound": list(outbound),
         "identity": ident or {}, "host_bindings": list(bindings)}))


def _iface(store, codebase, method, path):
    gid = f"{codebase}:{method.lower()}-{path.strip('/').replace('/', '-')}"
    store.upsert_object(KnowledgeObject(
        "interface", gid, gid, None, "unknown", "static", "index", None, None,
        {"type": "rest", "method": method, "path": path, "handler": "h"}))


def _fleet(tmp_path, host=""):
    s = SqliteStore(str(tmp_path / "r.db"))
    _svc(s, "caller", outbound=[{"verb": "GET", "path": "/v1/routes", "host": host}])
    _svc(s, "routing_api", codebase="routing", default=True, aliases=["routing"])
    _svc(s, "routing_engine", codebase="routing")
    _iface(s, "routing", "GET", "/v1/routes")
    s.commit()
    return s


def _calls(store):
    return {e.to_id: e.metadata for e in store.edges_of("service:caller", "out")}


def test_a_path_served_by_two_deployments_makes_one_edge_not_two(tmp_path):
    """Both deployments own every route until a path selector narrows them. Emitting an
    edge to each would turn an exact match into an ambiguous one across the fleet."""
    s = _fleet(tmp_path)
    resolve(s, s, s)
    s.commit()

    out = _calls(s)
    assert list(out) == ["service:routing_api"]
    assert out["service:routing_api"]["confidence"] == "high"


def test_the_caller_s_host_picks_the_deployment(tmp_path):
    s = _fleet(tmp_path, host="routing-engine")
    resolve(s, s, s)
    s.commit()

    assert list(_calls(s)) == ["service:routing_engine"]


def test_an_alias_picks_the_deployment_the_repository_name_does_not(tmp_path):
    """`routing` is both the codebase and an alias of the default, so it must land on a
    node rather than on the codebase, which is not one."""
    s = _fleet(tmp_path, host="routing")
    resolve(s, s, s)
    s.commit()

    assert list(_calls(s)) == ["service:routing_api"]


def test_a_config_key_naming_a_deployment_picks_it(tmp_path):
    """The caller's own config is the evidence that survives when the request path is
    shared by every deployment."""
    s = SqliteStore(str(tmp_path / "r.db"))
    _svc(s, "caller", outbound=[{"verb": "GET", "path": "/v1/routes", "host": ""}],
         bindings=[{"key": "ROUTING_ENGINE.HOST", "value": "http://localhost:1",
                    "host": "localhost", "port": "1"}])
    _svc(s, "routing_api", codebase="routing", default=True, aliases=["routing"])
    _svc(s, "routing_engine", codebase="routing")
    _iface(s, "routing", "GET", "/v1/routes")
    s.commit()

    resolve(s, s, s)
    s.commit()

    assert list(_calls(s)) == ["service:routing_engine"]


def test_deployments_of_one_codebase_do_not_contest_their_own_port(tmp_path):
    """They read one config, so they all report the same listen port. Counting them as
    rival claimants would make every such port look contested."""
    s = SqliteStore(str(tmp_path / "r.db"))
    _svc(s, "routing_api", codebase="routing", default=True, ident={"port": "4000"})
    _svc(s, "routing_engine", codebase="routing", ident={"port": "4000"})
    _svc(s, "caller", bindings=[{"key": "SOMETHING.HOST", "value": "http://localhost:4000",
                                "host": "localhost", "port": "4000"}])
    s.commit()

    resolve(s, s, s)
    s.commit()

    assert list(_calls(s)) == ["service:routing_api"], "the port still identifies the code"


def test_a_fleet_with_no_deployments_resolves_exactly_as_before(tmp_path):
    s = SqliteStore(str(tmp_path / "r.db"))
    _svc(s, "caller", outbound=[{"verb": "GET", "path": "/v1/orders", "host": ""}])
    _svc(s, "orders")
    _iface(s, "orders", "GET", "/v1/orders")
    s.commit()

    resolve(s, s, s)
    s.commit()

    out = _calls(s)
    assert list(out) == ["service:orders"] and out["service:orders"]["confidence"] == "high"
