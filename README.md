# fastapi-starter

FastAPI starter template with structured logging, per-request IDs, an asyncio
background scheduler, and a demo items CRUD to build from.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env        # then edit .env with your values
uvicorn main:app --reload --port 8000
```

Docs: http://127.0.0.1:8000/docs · Health: http://127.0.0.1:8000/health

## Layout

```
├── main.py                # FastAPI app, lifespan, asyncio scheduler, middleware wiring
├── api/v1/                # versioned HTTP routes
│   └── demo.py            #   example items CRUD — replace with your resources
├── core/                  # cross-cutting concerns
│   ├── config.py          #   pydantic-settings (.env)
│   ├── logger.py          #   stdlib logger + req_id ContextVar + adapter
│   └── middleware.py      #   RequestIdMiddleware
├── agents/                # agent runtime
│   ├── base.py            #   BaseAgent ABC
│   ├── roles/             #   planner / executor / reviewer
│   ├── orchestrators/     #   chat / task flow coordination
│   ├── tools/             #   db / search / notification tools
│   └── memory/            #   vector + conversation stores
├── services/              # business logic (called by routes)
├── repositories/          # data access layer
├── models/                # pydantic request/response models
├── utils/                 # constants and helpers
└── tests/
```

## Cross-cutting features

### Request id + logging
Every request gets a `req_id` (`X-Request-ID` header value, or a generated
hex UUID) stored in a `ContextVar` and auto-injected into every log line.
Log output is plain text:

```
2026-05-29 12:00:00 INFO     [a3f1c9...] api.v1.demo:22 item.created
```

Use the logger anywhere:

```python
from core.logger import get_logger
logger = get_logger(__name__)
logger.info("thing happened", extra={"key": "value"})
```

### Background scheduler
A 24-hour periodic task is wired into the FastAPI lifespan via
`asyncio.create_task`. Add your logic to `scheduled_data_fetch()` in `main.py`:

```python
async def scheduled_data_fetch() -> None:
    pass  # TODO: add periodic task logic here
```

### Middleware order

`add_middleware` adds outward, so requests hit them last-added-first:

```
RequestIdMiddleware  →  CORSMiddleware  →  route
```

## Configuration

Settings are loaded from `.env` via `pydantic-settings`. Key variables:

| Variable           | Default          | Description                  |
|--------------------|------------------|------------------------------|
| `APP_NAME`         | `fastapi-starter`| OpenAPI title                |
| `APP_VERSION`      | `0.1.0`          | OpenAPI version              |
| `LOG_LEVEL`        | `INFO`           | Root log level               |
| `CORS_ORIGINS`     | `["*"]`          | Allowed CORS origins         |
| `DATABASE_URL`     | `""`             | Database connection string   |
| `EXTERNAL_API_URL` | `""`             | Downstream service base URL  |
| `EXTERNAL_API_KEY` | `""`             | Downstream service API key   |
