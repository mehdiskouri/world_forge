"""Project package: persistent data model + ``ProjectService``.

Phase 2 introduces the on-disk Forge project format (see
``AGENT/ARCHITECTURE.md`` §3 and ``AGENT/dev_phases/phase2.md``). This
sub-package owns:

* :mod:`.schemas` — every Pydantic model that touches disk.
* :mod:`.schema_export` — JSON-Schema export + ``forge-schema-export`` CLI.

Stages C, F land additional modules (``service.py``, ``history.py``,
``locks.py``) in subsequent Phase 2 PRs.
"""
