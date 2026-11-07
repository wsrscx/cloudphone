from __future__ import annotations

import asyncio

import uvicorn

from .api import ServiceContext, create_admin_app, create_public_app
from .config import AppConfig


async def serve() -> None:
    config = AppConfig.load()
    context = ServiceContext.build(config)
    public_app = create_public_app(context)
    admin_app = create_admin_app(context)
    tls = bool(config.tls_cert and config.tls_key)
    public_server = uvicorn.Server(
        uvicorn.Config(
            public_app,
            host="0.0.0.0",
            port=config.public_port,
            ssl_certfile=str(config.tls_cert) if tls else None,
            ssl_keyfile=str(config.tls_key) if tls else None,
            ws_max_size=512 * 1024,
            log_level="info",
        )
    )
    admin_server = uvicorn.Server(
        uvicorn.Config(admin_app, host=config.admin_host, port=config.admin_port, log_level="info")
    )
    public_server.install_signal_handlers = lambda: None
    admin_server.install_signal_handlers = lambda: None
    await asyncio.gather(public_server.serve(), admin_server.serve())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
