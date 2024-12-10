import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from fastapi_request_coalesce import RequestCoalesceMiddleware

app = FastAPI()
app.add_middleware(RequestCoalesceMiddleware)


# Test endpoints
@app.post("/test")
async def test_endpoint(request: Request):
    await asyncio.sleep(0.1)  # Simulate some processing
    body = await request.json()
    return {"received": body}


@app.get("/stream")
async def stream_endpoint():
    async def generate():
        for i in range(3):
            yield f"chunk {i}\n".encode()
            await asyncio.sleep(0.1)

    return StreamingResponse(generate())


@app.get("/blacklisted")
@RequestCoalesceMiddleware.wrapper_add_one_for_all_cache_black_list(
    method="GET", path="/blacklisted"
)
async def blacklisted_endpoint():
    await asyncio.sleep(0.1)
    return {"status": "not_coalesced"}


@pytest.fixture
def client():
    return TestClient(app)


def test_request_coalescing(client):
    """Test that multiple identical requests are coalesced"""
    payload = {"test": "data"}
    responses = []

    # Send multiple concurrent requests
    with client.parallel_requests(3) as parallel:
        for _ in range(3):
            responses.append(parallel.post("/test", json=payload))

    # Check all responses are identical
    assert all(r.status_code == 200 for r in responses)
    assert all(r.json() == {"received": payload} for r in responses)


def test_different_requests_not_coalesced(client):
    """Test that different requests are not coalesced"""
    response1 = client.post("/test", json={"test": "data1"})
    response2 = client.post("/test", json={"test": "data2"})

    assert response1.json()["received"]["test"] == "data1"
    assert response2.json()["received"]["test"] == "data2"


def test_streaming_response(client):
    """Test that streaming responses are handled correctly"""
    response = client.get("/stream")
    assert response.status_code == 200
    content = b"".join(response.iter_bytes())
    expected = b"chunk 0\nchunk 1\nchunk 2\n"
    assert content == expected


def test_blacklisted_endpoint(client):
    """Test that blacklisted endpoints are not coalesced"""
    responses = []

    # Send multiple concurrent requests to blacklisted endpoint
    with client.parallel_requests(3) as parallel:
        for _ in range(3):
            responses.append(parallel.get("/blacklisted"))

    # Check all responses are successful but potentially different
    assert all(r.status_code == 200 for r in responses)
    assert all(r.json() == {"status": "not_coalesced"} for r in responses)


def test_error_handling(client):
    """Test error handling in the middleware"""

    @app.post("/error")
    async def error_endpoint():
        raise ValueError("Test error")

    response = client.post("/error")
    assert response.status_code == 500
    assert "error" in response.json()["detail"].lower()


def test_websocket_passthrough(client):
    """Test that WebSocket requests are passed through without coalescing"""

    @app.websocket("/ws")
    async def websocket_endpoint(websocket):
        await websocket.accept()
        await websocket.send_text("Hello")
        await websocket.close()

    with client.websocket_connect("/ws") as websocket:
        data = websocket.receive_text()
        assert data == "Hello"
