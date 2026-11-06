"""Entrypoint: `python -m py_parse` starts the gRPC ParseService."""
from __future__ import annotations

import logging

from .config import Settings
from .grpc_server import serve


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    serve(Settings.from_env())


if __name__ == "__main__":
    main()
