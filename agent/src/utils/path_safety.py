"""Path confinement helpers shared by vault/workspace file operations.

Use `resolve_within` instead of string `startswith` checks: a sibling path
whose name starts with the root (e.g. `/data/vault_fake` vs `/data/vault`)
must not be accepted, and symlinks pointing outside the root must be rejected.
"""

from pathlib import Path


def resolve_within(root: Path, candidate: Path) -> Path:
    """Resolve `candidate` and ensure it stays inside `root`.

    Args:
        root: directory the result must be confined to.
        candidate: path to resolve (may not exist yet).

    Returns:
        The fully resolved path.

    Raises:
        ValueError: if the resolved path escapes `root`.
    """
    root_resolved = root.resolve()
    resolved = candidate.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError("Path escapes root %s: %s" % (root, candidate))
    return resolved
