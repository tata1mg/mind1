"""Which indexed code a service node is made of.

A service node and the code behind it are usually the same thing, so for most of mind1's
life the service slug was also the key every symbol and interface id was filed under:
`code_symbol:<slug>:...`, `interface:<slug>:...`.

That stops being true as soon as one repository is deployed more than once (#12). Those
deployments are separate nodes in the mesh, they are addressed by different names, and
callers depend on one of them rather than on all of them. They are not separate code. Each
is a view of one checkout, and copying a repository's symbols once per deployment would
multiply the index and make `find_symbol` answer the same question several times over.

So the two ideas are separated here. A service records which codebase it is made of, and
anything that walks from a service to its code asks for that first. A repository that is
deployed once records itself, which is why this changes no ids and needs no re-index: for
every repository that does not declare deployments, the codebase and the service slug are
the same string.
"""
from __future__ import annotations

#: Payload key on a service or library object naming the codebase it is built from.
CODEBASE = "codebase"


def codebase_of(knowledge, service_id: str) -> str:
    """The slug that `service_id`'s symbols and interfaces are filed under.

    Falls back to the service's own slug, which keeps indexes built before this existed
    readable, and is also the right answer for every repository with one deployment.
    """
    slug = service_id.split(":", 1)[-1]
    obj = knowledge.get(service_id) if ":" in service_id else None
    if obj is None:
        for kind in ("service", "library"):
            obj = knowledge.get(f"{kind}:{slug}")
            if obj is not None:
                break
    if obj is None:
        return slug
    return str((obj.payload or {}).get(CODEBASE) or slug)


def symbol_prefix(codebase: str) -> str:
    return f"code_symbol:{codebase}:"


def interface_prefix(codebase: str) -> str:
    return f"interface:{codebase}:"
