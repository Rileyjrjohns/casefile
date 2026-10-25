"""Import-linter contract permitting stdlib imports but no third-party dependencies."""

import sys
from typing import cast

from grimp import ImportGraph
from importlinter import Contract, ContractCheck, output


class StandardLibraryOnlyContract(Contract):
    """Check core layers without maintaining a list of third-party packages."""

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        violations: list[tuple[str, str]] = []
        for importer in sorted(graph.modules):
            parts = importer.split(".")
            is_shared = parts[:2] == ["casefile", "shared"]
            is_core_layer = (
                len(parts) >= 4
                and parts[:2] == ["casefile", "features"]
                and parts[3] in {"domain", "application"}
            )
            if not (is_shared or is_core_layer):
                continue
            for imported in sorted(graph.find_modules_directly_imported_by(importer)):
                root = imported.partition(".")[0]
                if root != "casefile" and root not in sys.stdlib_module_names:
                    violations.append((importer, imported))
        return ContractCheck(kept=not violations, metadata={"violations": violations})

    def render_broken_contract(self, check: ContractCheck) -> None:
        violations = cast(list[tuple[str, str]], check.metadata["violations"])
        for importer, imported in violations:
            output.print_error(f"- {importer} -> {imported}")
