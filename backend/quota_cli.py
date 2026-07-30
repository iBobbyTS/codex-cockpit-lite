"""CLI entry point for one-shot quota/subsription refresh. Called by Tauri on import."""

import asyncio
import json
import sys
from pathlib import Path

from config import get_config_dir, save_meta
from quota import refresh_quota, refresh_subscription


async def main():
    if len(sys.argv) < 2:
        print("Usage: quota_cli.py <account_id>", file=sys.stderr)
        sys.exit(1)

    account_id = sys.argv[1]
    config_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else get_config_dir()

    try:
        quota = await refresh_quota(account_id, config_dir)
        sub = await refresh_subscription(account_id, config_dir)
        result = {
            "ok": True,
            "weekly_percent": quota.weekly_percent,
            "hourly_percent": quota.hourly_percent,
            "plan_type": sub.plan_type if sub else "",
            "team_name": sub.team_name if sub else "",
        }
    except Exception as e:
        result = {"ok": False, "error": str(e)}

    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
