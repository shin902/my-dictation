from __future__ import annotations

import json
import urllib.request
from typing import Any


def json_request(url: str, payload: dict, api_key: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)
