"""Import-linter contracts keeping domain, application and shared free of dependencies."""

import sys
from typing import cast

from grimp import ImportGraph
from importlinter import Contract, ContractCheck, output

# Standard-library modules that reach the network, other processes or an embedded database.
# Core layers describe behaviour; adapters perform I/O, so these stay out of domain,
# application and shared even though they ship with Python.
IO_STANDARD_LIBRARY_MODULES = frozenset(
    {
        "ftplib",
        "http",
        "imaplib",
        "multiprocessing",
        "nntplib",
        "poplib",
        "smtplib",
        "socket",
        "socketserver",
        "sqlite3",
        "ssl",
        "subprocess",
        "telnetlib",
        "urllib",
        "xmlrpc",
    }
)


def core_layer_modules(graph: ImportGraph) -> list[str]:
    """Modules of `shared` and of every feature's `domain` and `application` packages."""
    selected: list[str] = []
    for module in sorted(graph.modules):
        parts = module.split(".")
        is_shared = parts[:2] == ["casefile", "shared"]
        is_core_layer = (
            len(parts) >= 4
            and parts[:2] == ["casefile", "features"]
            and parts[3] in {"domain", "application"}
        )
        if is_shared or is_core_layer:
            selected.append(module)
    return selected


def render_violations(check: ContractCheck) -> None:
    violations = cast(list[tuple[str, str]], check.metadata["violations"])
    for importer, imported in violations:
        output.print_error(f"- {importer} -> {imported}")


class StandardLibraryOnlyContract(Contract):
    """Check core layers without maintaining a list of third-party packages."""

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        violations: list[tuple[str, str]] = []
        for importer in core_layer_modules(graph):
            for imported in sorted(graph.find_modules_directly_imported_by(importer)):
                root = imported.partition(".")[0]
                if root != "casefile" and root not in sys.stdlib_module_names:
                    violations.append((importer, imported))
        return ContractCheck(kept=not violations, metadata={"violations": violations})

    def render_broken_contract(self, check: ContractCheck) -> None:
        render_violations(check)


class NoIoStandardLibraryContract(Contract):
    """Reject standard-library modules that perform I/O in the core layers."""

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        violations: list[tuple[str, str]] = []
        for importer in core_layer_modules(graph):
            for imported in sorted(graph.find_modules_directly_imported_by(importer)):
                if imported.partition(".")[0] in IO_STANDARD_LIBRARY_MODULES:
                    violations.append((importer, imported))
        return ContractCheck(kept=not violations, metadata={"violations": violations})

    def render_broken_contract(self, check: ContractCheck) -> None:
        render_violations(check)
