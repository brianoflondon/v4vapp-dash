import argparse
import os

import uvicorn

from v4vapp_dash.config import get_settings
from v4vapp_dash.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="v4vapp-dash")
    parser.add_argument("--config", default=os.getenv("DASH_CONFIG"))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    if args.config:
        os.environ["DASH_CONFIG"] = args.config
    get_settings.cache_clear()
    settings = get_settings()
    setup_logging(settings)
    host = args.host or settings.dash_bind
    port = args.port or settings.dash_port
    from v4vapp_dash.main import create_app

    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        log_config=None,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
