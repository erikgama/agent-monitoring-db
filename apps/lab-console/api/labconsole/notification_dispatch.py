"""Fixed fixture adapter executed inside the existing Notification virtualenv."""

import json
import os
import sys
from importlib import import_module
from pathlib import Path


def main() -> int:
    if (
        sys.argv[1:] != ["--send"]
        or os.environ.get("NOTIFICATION_DELIVERY_ENABLED") != "true"
    ):
        print('{"status":"failed","error_code":"delivery_not_enabled"}')
        return 2
    try:
        # Notification alone owns credential resolution, validation and SMTP.
        runtime = import_module("notification.runtime")
        alert = json.loads(Path("fixtures/connection-warning.json").read_text())
        result = runtime.build_dispatcher().dispatch(alert)
    except Exception:
        print('{"status":"failed","error_code":"notification_runtime_failed"}')
        return 2
    print(json.dumps(result), flush=True)
    return 0 if result.get("status") == "sent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
