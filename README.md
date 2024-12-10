# FastAPI Request Coalesce Middleware

A FastAPI middleware that coalesces identical concurrent requests to reduce server load and improve performance.

## Features

- Automatically combines identical concurrent requests
- Supports streaming responses
- Configurable blacklist for endpoints that shouldn't be coalesced
- WebSocket passthrough
- Thread-safe implementation
- Comprehensive error handling

## Installation

```bash
pip install fastapi-request-coalesce
```

## Usage

```python
from fastapi import FastAPI
from fastapi_request_coalesce import RequestCoalesceMiddleware

app = FastAPI()
app.add_middleware(RequestCoalesceMiddleware)
```

### Blacklisting Endpoints

You can exclude specific endpoints from request coalescing using the decorator:

```python
@app.get("/no-coalesce")
@RequestCoalesceMiddleware.wrapper_add_one_for_all_cache_black_list(method="GET", path="/no-coalesce")
async def no_coalesce():
    return {"status": "not_coalesced"}
```

## How It Works

1. When multiple identical requests arrive concurrently, only the first request is processed
2. Subsequent identical requests wait for the result of the first request
3. All requests receive the same response
4. Request identity is determined by:
   - URL path
   - HTTP method
   - Query parameters
   - Request body (for POST/PUT requests)

## Requirements

- Python 3.7+
- FastAPI
- Starlette

## Testing

```bash
pip install -r requirements.txt
pytest
```

## License

This project is licensed under the terms of the MIT license.
