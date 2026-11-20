"""Return the operating system's current local time."""

from datetime import datetime


def run(parameters: dict, context: dict) -> dict:
    return {"hour": datetime.now().strftime("%H h %M")}
