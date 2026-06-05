"""moni.db — SQLite schema, migrate, connection."""

from .connection import close_db, get_db, get_db_path, set_db_path
import moni.db.migrate as _migrate_mod
# Re-export the function for backward compat (`from moni.db import migrate`).
# But we DON'T assign it to `migrate` here because that would shadow the
# submodule attribute (Python would resolve `moni.db.migrate` to the function,
# not the module).  Instead expose the function as `migrate_fn`.
migrate_fn = _migrate_mod.migrate
# Also keep the submodule accessible as `migrate` (the canonical way to
# access the module is `moni.db.migrate` or `from moni.db import migrate`
# which Python will resolve to the submodule now since we don't shadow it).
__all__ = ["close_db", "get_db", "get_db_path", "set_db_path", "migrate_fn", "migrate"]
