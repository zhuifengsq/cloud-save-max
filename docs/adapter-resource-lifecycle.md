# Adapter resource ownership

`AdapterFactory.create_adapter()` returns a separate `AdapterLease` for each caller.
The underlying adapter and its initialization state are shared by cache key; lease
identity is not shared. Adapter attributes and calls are forwarded. `lease.close()`
releases only that caller's borrow and is idempotent. A method already running
holds an independent pin until it returns, including when another thread closes
the caller's lease.

The cache owns each `AdapterResource` until LRU eviction, configuration cache
clear, or application shutdown. Removing that owner retires the resource: idle
transports close immediately, while borrowed resources close when the last lease
or running operation finishes. Cache invalidation increments a generation, so a
slow constructor cannot restore entries invalidated during login. Shutdown also
disables cache insertion until application startup; late background work can
finish without leaving new cached sessions behind.

Use `with adapter_scope():` around an entire operation, or `@scoped_adapters` for
a synchronous operation. Every factory lease created within that scope is
released in `finally`, including errors. The ASGI middleware covers HTTP response
streaming and response background work. Task execution and prepared task engines
have their own scopes. `AccountManager` and `DatabaseAccountManager` also support
`close()` and context managers, including releasing old leases on reload. A
legacy caller outside a scope should explicitly close its lease; a weak finalizer
is a compatibility fallback whose timing depends on garbage collection.

An owner intentionally spanning scopes, such as an authentication challenge,
uses `retained = lease.retain()`. That independent unscoped lease must be closed
by its owner on expiry, deletion, eviction, or shutdown. Never pass a scoped lease
to work that outlives its scope without retaining it first.

Concrete adapters close the owned `_session`, `auth_session`, or `_client` handles
through the base adapter. Guangya's client closes its own session. Quark and UC
use request-local transports and own no persistent session. Resource close errors
are logged while remaining handles continue to close. Adding another owned
transport requires updating the explicit `OWNED_RESOURCE_FIELDS` contract.

Deterministic shutdown retires every cached owner; it deliberately does not close
resources still borrowed by active operations or retained challenges. Those
owners must finish/release before their transports can close safely. The tests
exercise concurrent close, eviction, invalidation during construction, scoped
exception cleanup, ASGI response lifetime, retained leases, late shutdown work,
and closure of local sessions for all registered adapters without network calls.

Interactive authentication has a separate bounded registry. Application shutdown
disables new session insertion and retires outstanding sessions before retiring
the factory cache. Per-session active-operation accounting protects retained
transports until an in-progress challenge returns; retirement does not wait on
another session's operation lock. A late background challenge is rejected and its
newly retained lease released, rather than repopulating the stopped registry.
