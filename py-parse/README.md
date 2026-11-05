# py-parser

Enterprise document parsing service. Single responsibility: parse a document
into structured chunks with metadata, streamed over gRPC server-streaming.

## Structure (hexagonal architecture)

```
py_parse/
├── ports/               # Interfaces (dependency inversion)
│   └── source_reader.py   # SourceReader: storage abstraction port
├── adapters/            # Concrete implementations
│   ├── file_reader.py     # file://  local filesystem
│   ├── minio_reader.py    # minio:// local/remote MinIO
│   ├── s3_reader.py       # s3://    remote S3-compatible
│   └── registry.py        # scheme → adapter routing
└── (domain/ core parsing logic — next step)
proto/
└── parse.proto           # gRPC service contract
```

## Storage abstraction (core design)

- `source` is a uniform URI: `file:///...` / `minio://...` / `s3://...`; the
  scheme selects the storage backend.
- The request carries only location; connection config (endpoints,
  credentials) is injected at adapter construction time.
- Adding a backend = write an adapter + `registry.register(scheme, reader)`.
  The contract and parsing logic are unchanged.

## Generate gRPC code

```bash
python -m grpc_tools.protoc \
  -I proto \
  --python_out=py_parse/generated \
  --grpc_python_out=py_parse/generated \
  proto/parse.proto
```

## Wiring example (for main)

```python
from py_parse.adapters import (
    FileSourceReader, MinioSourceReader, S3SourceReader, SourceReaderRegistry,
)

registry = SourceReaderRegistry()
registry.register("file", FileSourceReader())
registry.register("minio", MinioSourceReader(minio_client))  # inject connection config
registry.register("s3", S3SourceReader(s3_client))           # inject connection config

reader = registry.resolve("s3://my-bucket/doc_001.pdf")
stream = reader.read("s3://my-bucket/doc_001.pdf")
```
