# ADR 027: No I/O standard-library modules in the core layers

Status: accepted.

## Context

[ADR 001](./0001-feature-first-hexagonal-architecture.md) says the domain and
application depend only on the standard library and `shared`. The import-linter
contract that enforces it (`StandardLibraryOnlyContract`) rejects third-party
packages by comparing import roots with `sys.stdlib_module_names`. That
accepts every standard-library module, including `socket`, `ssl`,
`subprocess`, `urllib`, `http` and `sqlite3`. A use case could therefore open a
connection or spawn a process while the contract stayed green, which defeats
the point of keeping side effects in adapters.

## Decision

- **Add a second contract type, `no-io-stdlib`**, implemented by
  `quality.contracts.NoIoStandardLibraryContract` and declared as "Domain,
  application and shared stay free of I/O standard-library modules". It covers
  the same modules as the third-party contract: `casefile.shared` and each
  feature's `domain` and `application`. The existing contract is unchanged, so
  nothing is loosened.
- **Forbidden roots:** `socket`, `socketserver`, `ssl`, `subprocess`,
  `multiprocessing`, `urllib`, `http`, `xmlrpc`, `ftplib`, `smtplib`, `imaplib`,
  `poplib`, `nntplib`, `telnetlib` and `sqlite3`. A submodule import such as
  `urllib.request` or `from http import client` is matched by its root. Imports
  under `TYPE_CHECKING` count, as they already do for the other contracts.
- **Deliberately allowed:** `asyncio` (the worker uses `asyncio.sleep` and the
  ports are `async`), `pathlib` (pure path values), `json`, `re`, `datetime`,
  `decimal`, `uuid` and similar. They do not reach the network, another
  process or a database. Adapters and `bootstrap` remain free to use every
  module.
- **Checked against the code first.** No module in the three layers imports a
  forbidden root today, so no code changed. Meta-tests in
  `tests/test_architecture.py` write each forbidden import (plain, submodule,
  `from` form and under `TYPE_CHECKING`) into a temporary package for `domain`,
  `application` and `shared` and require the contract to break; they also check
  that adapters and bootstrap may still import them and that pure modules stay
  allowed.

## Alternatives considered

- Extend the existing contract with a list parameter: one class would carry two
  policies and its broken-contract name would stop saying why it failed.
- Allow-list the standard-library modules core layers may import: precise, but
  every new `itertools` or `functools` import would need a configuration change.
- Forbid `asyncio` too: it would force a clock-like port for sleeping in the
  worker without removing any real I/O capability from the core.

## Consequences

The architecture rule in ADR 001 now holds for side effects as well as for
vendors. The list is a deny-list: a module that performs I/O but is missing from
it (for example `ctypes`) is not caught, and dynamic imports are still out of
reach for static analysis. Extending the list means adding a name to
`IO_STANDARD_LIBRARY_MODULES` and a case to the meta-tests.
