"""Dimension-order JSON-stat decoding retained from the source ETL.

The original flat-index division method is retained, with dense/sparse values,
status flags, explicit missing records, and dimension validation added.
"""
import math
import numbers


def jsonstat_rows(data, *, include_missing=False, max_cells=2_000_000):
    ids, sizes = data["id"], data["size"]
    if len(ids) != len(sizes) or len(set(ids)) != len(ids) or not ids:
        raise ValueError("Invalid dimension IDs/sizes")
    if any(type(n) is not int or n < 1 for n in sizes):
        raise ValueError("Dimension sizes must be positive integers")
    total = math.prod(sizes)
    if total > max_cells:
        raise ValueError("Dataset exceeds configured cell limit")
    dims = []
    for dim, size in zip(ids, sizes):
        idx = data["dimension"][dim]["category"]["index"]
        if isinstance(idx, dict):
            if sorted(idx.values()) != list(range(size)):
                raise ValueError(f"Invalid category index for {dim}")
            ordered = [k for k, _ in sorted(idx.items(), key=lambda kv: kv[1])]
        elif isinstance(idx, list):
            ordered = idx
        else:
            raise ValueError("Unsupported category index")
        if len(ordered) != size or len(set(ordered)) != size:
            raise ValueError(f"Category count/uniqueness mismatch for {dim}")
        dims.append(ordered)
    values = data.get("value", {})
    statuses = data.get("status", {})
    # JSON-stat scalar status strings apply to every cell, including missing cells.
    if isinstance(statuses, str):
        statuses = [statuses] * total
    for container in [values, statuses]:
        if not isinstance(container, (dict, list)):
            raise ValueError("Values/status must be a sparse object or dense list")
        if isinstance(container, list) and len(container) != total:
            raise ValueError("Dense array length differs from dimension product")
        if isinstance(container, dict) and any(not str(k).isdigit() or int(k) >= total for k in container):
            raise ValueError("Sparse index outside dataset")
    def cell(container, index):
        return container[index] if isinstance(container, list) else container.get(str(index))
    rows = []
    for flat in range(total):
        coords, rem = [], flat
        for size in reversed(sizes):
            coords.append(rem % size)
            rem //= size
        coords.reverse()
        value = cell(values, flat)
        if value is not None and (isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value)):
            raise ValueError("Non-finite or non-numeric dataset value")
        if value is None and not include_missing:
            continue
        row = {ids[i]: dims[i][coords[i]] for i in range(len(ids))}
        row.update(value=value, status=cell(statuses, flat), is_missing=value is None)
        rows.append(row)
    return rows
