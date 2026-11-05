"""py-parser: enterprise document parsing service.

Single responsibility: turn a document (PDF/Word/scanned) into structured
chunks with metadata, streamed out over gRPC server-streaming. It does not
embed, index, search, or answer questions.

Hexagonal architecture:
  - domain    core parsing logic (depends only on ports)
  - ports     interfaces (SourceReader, etc.)
  - adapters  concrete implementations (file/minio/s3 storage, gRPC server)
"""
