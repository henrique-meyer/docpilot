# Docker

An image is a stacked filesystem plus a start command. A container is a running process made from that image.

## Local

```bash
docker compose up --build
```

The API listens on port 8000. Jaeger collects traces and shows them at <http://localhost:16686>. Compose points the API at Jaeger's OTLP port. Azurite stores uploaded PDFs in the `sources` container on port 10000. The connection string is Azurite's published development account, and it wins over an account URL. To use Foundry or Search from Compose, add `env_file` under the `api` service, pointing at `.env` or at a filled file outside this checkout. Copy `.env.example` to that file first. The file stays out of git.

## Azure

The container app exports traces to the Application Insights resource already in the template. The Log Analytics workspace caps ingestion at 0.1 GB a day. `DOCPILOT_OTEL_SAMPLE_RATIO` defaults to 1. Set it below 1 if that cap gets close. No second telemetry resource is required.
