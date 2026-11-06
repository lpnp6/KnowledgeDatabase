# py-parser

Enterprise document parsing service. Single responsibility: parse a document
into structured chunks with metadata, streamed over gRPC server-streaming.

Document extraction is delegated to **MinerU** (running as a separate HTTP
service); this package owns the chunking logic and the gRPC contract.

## Structure (hexagonal architecture)

```
py_parse/
├── ports/                 # Interfaces (dependency inversion)
│   ├── source_reader.py     # SourceReader: storage abstraction
│   └── document_extractor.py  # DocumentExtractor: PDF bytes → content_list
├── adapters/              # Concrete implementations
│   ├── mineru_client.py     # HTTP client over a local mineru-api service
│   ├── file_reader.py       # file://  local filesystem
│   ├── minio_reader.py      # minio://
│   ├── s3_reader.py         # s3://
│   └── registry.py          # scheme → reader routing
├── domain/                # Core parsing logic (depends only on ports)
│   ├── models.py            # Chunk / ChunkMetadata / DocMeta / ParseOutput
│   ├── chunker.py           # content_list → chunks (heading grouping + packing)
│   └── parser.py            # orchestrator: read → extract → chunk
└── ...
proto/
└── parse.proto             # gRPC service contract
```

## MinerU service (prerequisite)

py-parser does **not** bundle MinerU; it talks to a MinerU HTTP service.

### Option 1: Docker

```bash
docker compose -f docker/compose.yaml up -d
curl http://localhost:8000/health     # → ok
```

### Option 2: pip (bare metal)

```bash
# 1. Isolated env + GPU deps (see requirements-mineru.txt header for CUDA notes)
python -m venv ~/mineru-env && source ~/mineru-env/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu118   # driver 535 → cu118
pip install -r requirements-mineru.txt

# 2. Confirm GPU is used (False = torch was installed as CPU build)
python -c "import torch; print(torch.cuda.is_available())"

# 3. Download models + start the API
export MINERU_MODEL_SOURCE=modelscope
mineru-models-download
mineru-api --host 0.0.0.0 --port 8000
```

See `requirements-mineru.txt` and `docker/compose.yaml` for full details.

## Run the parser (end-to-end check)

```bash
python scripts/test_parse.py /path/to/doc.pdf \
  --doc-id sample \
  --mineru-url http://localhost:8000 \
  --output-dir out
```

This prints an extraction report (block-type counts, detected headings), a
chunking report (token distribution, samples), and persists chunks as JSONL to
`out/sample.jsonl`.

## Run the gRPC service (Docker)

`docker/compose.yaml` starts both the MinerU service and the py-parse gRPC
server, wired together over the compose network:

```bash
docker compose -f docker/compose.yaml up -d
```

- gRPC endpoint: `localhost:50051` (service `parse.v1.ParseService`)
- Health probe: standard gRPC health service (`grpc-health-probe` compatible)
- `MINERU_URL` is set to `http://mineru-api:8000` inside the compose network

Start the server directly (no Docker) with `python -m py_parse`; see
`py_parse/config.py` for the env vars (`GRPC_PORT`, `MINERU_URL`, storage
credentials).

Test the gRPC contract end-to-end:

```bash
python scripts/grpc_client.py file:///path/to/doc.pdf --doc-id sample
```

## Storage abstraction

- `source` is a uniform URI: `file:///...` / `minio://...` / `s3://...`; the
  scheme selects the storage backend.
- The request carries only location; connection config (endpoints,
  credentials) is injected at adapter construction time.
- Adding a backend = write an adapter + `registry.register(scheme, reader)`.

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
from py_parse.adapters import FileSourceReader, MineruClient, SourceReaderRegistry
from py_parse.domain.parser import DocumentParser

extractor = MineruClient(base_url="http://localhost:8000")
parser = DocumentParser(extractor)

registry = SourceReaderRegistry()
registry.register("file", FileSourceReader())
# ...

stream = registry.resolve("file:///data/docs/doc_001.pdf").read(...)
out = parser.parse(stream, doc_id="doc_001")
```
