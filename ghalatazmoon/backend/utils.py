import json
from flask import request


def paginate_args():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        page_size = 20
    return page, page_size


def paginated_response(rows_total, rows_page, page, page_size):
    return {
        "count": rows_total,
        "page": page,
        "page_size": page_size,
        "num_pages": (rows_total + page_size - 1) // page_size if page_size else 1,
        "results": rows_page,
    }


def parse_json_field(value, default=None):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def mistake_to_public(m: dict) -> dict:
    m = dict(m)
    m["tags"] = parse_json_field(m.get("tags"), [])
    m["is_premium"] = bool(m.get("is_premium")) if "is_premium" in m else None
    return m
