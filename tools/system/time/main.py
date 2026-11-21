"""Return the operating system's current local time to the Rust workflow engine."""

import json
from datetime import datetime
import sys


def run(parameters: dict, context: dict) -> dict:
    return {"hour": datetime.now().strftime("%H h %M")}


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps(run(payload.get("parameters", {}), payload.get("context", {}))))
