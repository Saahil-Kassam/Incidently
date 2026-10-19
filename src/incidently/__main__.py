"""Start Incidently: ``python -m incidently`` (or the ``incidently`` command).

Serves the web app on http://127.0.0.1:8000 and opens it in the browser.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

from . import __version__
from .config import ConfigError, Settings
from .llm import LLMError, create_llm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="incidently", description="Practise troubleshooting enterprise network incidents."
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    parser.add_argument("--version", action="version", version=f"incidently {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
        llm = create_llm(settings)
    except (ConfigError, LLMError) as exc:
        print(f"incidently: {exc}", file=sys.stderr)
        return 2

    # Imported here so `--help` and `--version` stay fast.
    import uvicorn

    from .web.app import LOCAL_HOSTS, create_app

    hosts = LOCAL_HOSTS if args.host in LOCAL_HOSTS else [*LOCAL_HOSTS, args.host]
    app = create_app(settings, llm, allowed_hosts=hosts)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}/"
    mode = "offline mode (placeholder output)" if settings.fake_llm else f"model {settings.model}"
    print(f"Incidently {__version__} running at {url}  ({mode}). Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
