def make_rotation_namer(handler, rotation_folder: bool = False, min_width: int = 3):
    """Return a namer function for rotated log files.

    The namer will transform names like:
      logs/foo.jsonl.1 -> logs/foo.001.jsonl
    and if `rotation_folder` is True will place rotated files under
      logs/rotation/foo.001.jsonl

    Padding width is the greater of `min_width` and the number of digits in
    `handler.backupCount` (if present).
    """

    import os
    from pathlib import Path

    def namer(name: str) -> str:
        # Only process names that end with .<digits>
        base = str(name)
        head, sep, tail = base.rpartition(".")
        if not sep or not tail.isdigit():
            return name

        index = int(tail)
        rest = head
        rest_path = Path(rest)
        ext = rest_path.suffix
        prefix = rest[: -len(ext)] if ext else rest

        backup_count = getattr(handler, "backupCount", None) or 0
        width = max(min_width, len(str(int(backup_count))))
        index_str = f"{index:0{width}d}"

        new_filename = f"{Path(prefix).name}.{index_str}{ext}"
        parent = rest_path.parent
        if rotation_folder:
            rotation_dir = parent / "rotation"
            os.makedirs(rotation_dir, exist_ok=True)
            return str(rotation_dir / new_filename)
        return str(parent / new_filename)

    return namer
