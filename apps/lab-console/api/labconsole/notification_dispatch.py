"""Fixed fixture adapter executed inside the existing Notification virtualenv."""

import json
import os
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path


def _smtp_test_alert() -> dict[str, object]:
    alert = json.loads(Path("fixtures/connection-warning.json").read_text())
    alert["detected_at"] = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    alert["environment"] = "smtp-test"
    alert["title"] = "TESTE SMTP — sem incidente real"
    alert["summary"] = (
        "Mensagem de teste enviada pelo operador para verificar a entrega SMTP. "
        "Nenhum incidente de banco foi detectado por este comando."
    )
    alert["findings"] = [
        {
            "check_id": "smtp.delivery_test",
            "metric": "smtp_delivery_test",
            "observed_value": "teste",
            "evidence": {"synthetic": True},
            "affected_objects": ["smtp-test"],
        }
    ]
    alert["dedupe_key"] = "smtp-test|connections|delivery"
    return alert


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
        alert = _smtp_test_alert()
        result = runtime.build_dispatcher().dispatch(alert)
    except Exception:
        print('{"status":"failed","error_code":"notification_runtime_failed"}')
        return 2
    print(json.dumps(result), flush=True)
    return 0 if result.get("status") == "sent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
