"""Which indexed code a service node is made of.

A service and its code are the same thing until one repository is deployed more than once.
These tests pin the indirection that separates them, and in particular that it changes
nothing for a repository deployed once, which is every repository that does not declare
deployments.
"""
from __future__ import annotations

from mind1.codebase import CODEBASE, codebase_of, interface_prefix, symbol_prefix
from mind1.store.models import KnowledgeObject
from mind1.store.sqlite import SqliteStore


def _obj(store, gid: str, payload: dict | None = None) -> None:
    otype, _, local = gid.partition(":")
    store.upsert_object(KnowledgeObject(otype, local, local, None, "unknown", "static",
                                        "index", None, None, payload or {}))


def test_a_service_is_its_own_codebase_by_default():
    s = SqliteStore(":memory:")
    _obj(s, "service:orders", {CODEBASE: "orders"})
    assert codebase_of(s, "service:orders") == "orders"


def test_an_index_built_before_this_existed_still_resolves():
    """No payload key, so the slug is the answer. This is what makes the change need no
    re-index."""
    s = SqliteStore(":memory:")
    _obj(s, "service:orders", {"root": "/repos/orders"})
    assert codebase_of(s, "service:orders") == "orders"


def test_a_deployment_points_at_the_codebase_it_is_made_of():
    s = SqliteStore(":memory:")
    _obj(s, "service:orders_api", {CODEBASE: "orders"})
    _obj(s, "service:orders_worker", {CODEBASE: "orders"})
    assert codebase_of(s, "service:orders_api") == "orders"
    assert codebase_of(s, "service:orders_worker") == "orders"


def test_a_library_resolves_the_same_way():
    s = SqliteStore(":memory:")
    _obj(s, "library:commonutils", {CODEBASE: "commonutils"})
    assert codebase_of(s, "library:commonutils") == "commonutils"
    # and by bare slug, since callers hold ids of several shapes
    assert codebase_of(s, "commonutils") == "commonutils"


def test_an_unknown_slug_falls_back_to_itself_rather_than_failing():
    """Callers use this to build an id prefix. Raising here would turn a missing service
    into an exception inside a read that should return `not_found`."""
    s = SqliteStore(":memory:")
    assert codebase_of(s, "service:nosuchthing") == "nosuchthing"


def test_the_prefixes_are_the_ids_the_loaders_write():
    """Guards the one thing that would silently return nothing: a prefix that does not
    match what `loaders.interfaces` and `loaders.callgraph` actually wrote."""
    from mind1.loaders import callgraph as cg
    from mind1.loaders import interfaces as iface
    assert symbol_prefix("orders") == cg.node_prefix("orders")
    assert interface_prefix("orders") == iface.node_prefix("orders")


def test_list_interfaces_reads_through_the_codebase():
    from mind1.service.relationships import RelationshipService

    s = SqliteStore(":memory:")
    _obj(s, "service:orders_api", {CODEBASE: "orders"})
    _obj(s, "interface:orders:get-v1-orders",
         {"type": "rest", "method": "GET", "path": "/v1/orders", "handler": "list_orders"})
    s.commit()

    out = RelationshipService(s, s).list_interfaces("service:orders_api")

    assert out["status"] == "ok"
    assert [i["path"] for i in out["interfaces"]] == ["/v1/orders"]


def test_two_deployments_of_one_codebase_see_the_same_interfaces():
    """Until a deployment narrows its routes by path, each owns all of them. The point of
    the indirection is that they share one copy rather than holding three."""
    from mind1.service.relationships import RelationshipService

    s = SqliteStore(":memory:")
    _obj(s, "service:orders_api", {CODEBASE: "orders"})
    _obj(s, "service:orders_worker", {CODEBASE: "orders"})
    _obj(s, "interface:orders:get-v1-orders",
         {"type": "rest", "method": "GET", "path": "/v1/orders", "handler": "list_orders"})
    s.commit()

    svc = RelationshipService(s, s)
    api = svc.list_interfaces("service:orders_api")
    worker = svc.list_interfaces("service:orders_worker")

    assert api["interfaces"] == worker["interfaces"]
    assert len(s.list_objects("interface")) == 1, "one stored copy, not one per deployment"


def test_indexing_records_the_codebase(tmp_path):
    """The pointer has to be written, or every lookup silently falls back to the slug and
    the indirection does nothing."""
    from mind1.indexing import index_repo

    repo = tmp_path / "orders"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("def handler():\n    return 1\n")
    store = SqliteStore(str(tmp_path / "x.db"))
    index_repo(repo, store)

    svc = store.get("service:orders")
    assert svc is not None
    assert svc.payload[CODEBASE] == "orders"
