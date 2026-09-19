from __future__ import annotations


def format_bytes(size_bytes: int) -> str:
    """Format a byte count as a compact human-readable size."""
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")

    units = ("B", "KB", "MB", "GB", "TB")
    size = float(size_bytes)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{size_bytes} B"

    text = f"{size:.2f}".rstrip("0").rstrip(".")
    return f"{text} {units[unit_index]}"
