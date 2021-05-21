"""Github hook server.

This is a simple HTTP server handling Github Webhooks requests to
build the doc when needed.

It needs a GH_SECRET environment variable to be able to receive hooks
on `/hook/github`.

Its logging can be configured by giving a yaml file path to the
`--logging-config` argument.

By default the loglevel is `DEBUG` on `stderr`, the default config can
be found in the code so one can bootstrap a different config from it.
"""

from pathlib import Path
import argparse
import asyncio
import logging.config
import os

from aiohttp import web
from gidgethub import sansio
import yaml

__version__ = "0.0.1"

DEFAULT_LOGGING_CONFIG = """
---

version: 1
disable_existing_loggers: false
formatters:
  normal:
    format: '%(asctime)s - %(levelname)s - %(message)s'
handlers:
  stderr:
    class: logging.StreamHandler
    stream: ext://sys.stderr
    level: DEBUG
    formatter: normal
loggers:
  build_docs_server:
    level: DEBUG
    handlers: [stderr]
  aiohttp.access:
    level: DEBUG
    handlers: [stderr]
  aiohttp.client:
    level: DEBUG
    handlers: [stderr]
  aiohttp.internal:
    level: DEBUG
    handlers: [stderr]
  aiohttp.server:
    level: DEBUG
    handlers: [stderr]
  aiohttp.web:
    level: DEBUG
    handlers: [stderr]
  aiohttp.websocket:
    level: DEBUG
    handlers: [stderr]
"""

logger = logging.getLogger("build_docs_server")


async def version(request):
    return web.json_response(
        {
            "name": "docs.python.org Github handler",
            "version": __version__,
            "source": "https://github.com/python/docsbuild-scripts",
        }
    )


async def child_waiter(app):
    while True:
        try:
            status = os.waitid(os.P_ALL, 0, os.WNOHANG | os.WEXITED)
            logger.debug("Child completed with status %s", str(status))
        except ChildProcessError:
            await asyncio.sleep(600)


async def start_child_waiter(app):
    app["child_waiter"] = asyncio.ensure_future(child_waiter(app))


async def stop_child_waiter(app):
    app["child_waiter"].cancel()


async def hook(request):
    body = await request.read()
    event = sansio.Event.from_http(
        request.headers, body, secret=os.environ.get("GH_SECRET")
    )
    if event.event != "push":
        logger.debug(
            "Received a %s event, nothing to do.", event.event
        )
        return web.Response()
    touched_files = (
        set(event.data["head_commit"]["added"])
        | set(event.data["head_commit"]["modified"])
        | set(event.data["head_commit"]["removed"])
    )
    if not any("Doc" in touched_file for touched_file in touched_files):
        logger.debug("No documentation file modified, ignoring.")
        return web.Response()  # Nothing to do
    branch = event.data["ref"].split("/")[-1]
    logger.debug("Forking a build for branch %s", branch)
    pid = os.fork()
    if pid == 0:
        os.execl(
            "/usr/bin/env",
            "/usr/bin/env",
            "python",
            "build_docs.py",
            "--branch",
            branch,
        )
    else:
        return web.Response()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", help="Unix socket to listen for connections.")
    parser.add_argument("--port", help="Local port to listen for connections.")
    parser.add_argument(
        "--logging-config",
        help="yml file containing a Python logging dictconfig, see README.md",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    logging.config.dictConfig(
        yaml.load(
            Path(args.logging_config).read_text()
            if args.logging_config
            else DEFAULT_LOGGING_CONFIG,
            Loader=yaml.SafeLoader,
        )
    )
    app = web.Application()
    app.on_startup.append(start_child_waiter)
    app.on_cleanup.append(stop_child_waiter)
    app.add_routes(
        [
            web.get("/", version),
            web.post("/hooks/github", hook),
        ]
    )
    web.run_app(app, path=args.path, port=args.port)


if __name__ == "__main__":
    main()
