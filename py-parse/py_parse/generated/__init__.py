"""Generated gRPC/protobuf bindings for `proto/parse.proto` (package parse.v1).

Re-generate with:

    python -m grpc_tools.protoc \\
      -I proto \\
      --python_out=py_parse/generated \\
      --grpc_python_out=py_parse/generated \\
      proto/parse.proto

Note: the generated `parse_pb2_grpc.py` is patched to use a relative import
(`from . import parse_pb2`) so this directory behaves as a normal package.
"""

from .parse_pb2 import (  # noqa: F401
    ChunkMetadata,
    DocMeta,
    ParseChunk,
    ParseRequest,
    ParseResponse,
    ParseResult,
)
from .parse_pb2_grpc import (  # noqa: F401
    ParseService,
    ParseServiceServicer,
    ParseServiceStub,
    add_ParseServiceServicer_to_server,
)

__all__ = [
    "ChunkMetadata",
    "DocMeta",
    "ParseChunk",
    "ParseRequest",
    "ParseResponse",
    "ParseResult",
    "ParseService",
    "ParseServiceServicer",
    "ParseServiceStub",
    "add_ParseServiceServicer_to_server",
]
