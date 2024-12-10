"""Main module."""
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
import logging
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestCoalesceMiddleware(BaseHTTPMiddleware):
    _instance: Optional["RequestCoalesceMiddleware"] = None
    black_list: List[Tuple[str, str]] = list()

    def __new__(cls, *args: Any, **kwargs: Any) -> 'RequestCoalesceMiddleware':
        if cls._instance is None:
            cls._instance = super().__new__(cls)

            # 将初始化代码移到这里，确保只初始化一次
            cls._instance.result_futures: Dict[str, asyncio.Future] = dict()  # type: ignore
            cls._instance.interface_counters: Dict[str, int] = dict()  # type: ignore
            cls._instance.global_lock = asyncio.Lock()

        return cls._instance

    @classmethod
    def add_one_for_all_cache_black_list(cls, method: str, path: str) -> None:
        cls.black_list.append((method, path))
        logging.info(f"Add '{path}' to OneForAllCacheMiddleware black list!")

    @classmethod
    def wrapper_add_one_for_all_cache_black_list(cls, method: str, path: str) -> Callable:  # type: ignore

        def wrapper(func: Callable) -> Callable:  # type: ignore
            cls.add_one_for_all_cache_black_list(method=method, path=path)
            return func

        return wrapper

    @asynccontextmanager
    async def interface_session(self, interface_key: str) -> AsyncIterator[None]:
        try:
            async with self.global_lock:
                if interface_key not in self.interface_counters:
                    self.interface_counters[interface_key] = 0
                self.interface_counters[interface_key] += 1
            yield
        finally:
            async with self.global_lock:
                self.interface_counters[interface_key] -= 1
                if self.interface_counters[interface_key] == 0:
                    if interface_key in self.result_futures:
                        future = self.result_futures.pop(interface_key)
                        if not future.done():
                            future.cancel()

    @staticmethod
    async def _generate_interface_key(request: Request) -> str:
        try:
            body_content = await request.body()
            body_dict = json.loads(body_content)
            body_content = json.dumps(body_dict, sort_keys=True)
        except Exception:
            body_content = ""

        return hashlib.md5("|".join([
            request.url.path,
            request.method,
            str(request.query_params),
            body_content,
        ]).encode()).hexdigest()

    async def _get_or_create_future(self, interface_key: str) -> Tuple[bool, asyncio.Future]:  # type: ignore
        async with self.global_lock:
            is_producer = False
            if interface_key not in self.result_futures or self.result_futures[interface_key].done():
                self.result_futures[interface_key] = asyncio.Future()
                is_producer = True
            return is_producer, self.result_futures[interface_key]

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 不处理 websocket 请求
        if request.scope["type"].lower() == "websocket":
            return await call_next(request)

        # 黑名单内的 API，不进行请求
        if (request.method, request.url.path) in self.black_list:
            return await call_next(request)

        try:
            # 读取并存储请求体, 使用自定义的方法来设置请求体
            body = await request.body()
            request_copy = Request(request.scope, receive=request.receive)
            await self._set_request_receive(request_copy, body=body)

            # 生成接口唯一标识
            # 进行请求，并同步第一次的请求结果至同一时间内的其他同一接口的请求
            interface_key = await self._generate_interface_key(request_copy)
            async with self.interface_session(interface_key):

                # 判断是否为 第一次请求，第一次请求为生产者，需要负责真正执行
                is_producer, future = await self._get_or_create_future(interface_key)
                if is_producer:
                    try:
                        future.set_result(await self._wrapper_response(response=await call_next(request_copy)))
                    except Exception as e:
                        future.set_exception(e)

                # 处理结束后，返回结果
                try:
                    return await self._wrapper_response(response=await future)
                except asyncio.CancelledError:
                    raise HTTPException(status_code=500, detail="Request cancelled")
                except Exception as e:
                    raise HTTPException(status_code=500, detail=str(e))

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

    @staticmethod
    async def _set_request_receive(request: Request, body: bytes) -> None:

        async def receive() -> Dict[str, Any]:
            return {"type": "http.request", "body": body}

        request._receive = receive

    @staticmethod
    async def _wrapper_response(response: Response) -> Response:
        """
        封装返回 Response

            - StreamingResponse： 需要处理 body_iterator，否则会导致 N - 1 个返回产生错误

        :param response:
        :return:
        """
        if isinstance(response, StreamingResponse):
            if isinstance(response, WrapperStreamingResponse):
                return response.new()
            else:
                content = [section async for section in response.body_iterator]
                return WrapperStreamingResponse(response=response, content=content)
        else:
            return response


class WrapperStreamingResponse(StreamingResponse):  # type: ignore

    def __init__(self, response: StreamingResponse, content: List[bytes]) -> None:
        super().__init__(
            content=iter(content),
            status_code=response.status_code,
            headers=response.headers,
            media_type=response.media_type,
            background=response.background,
        )
        self.content = content

    def new(self) -> StreamingResponse:
        return StreamingResponse(
            content=iter(self.content),
            status_code=self.status_code,
            headers=self.headers,
            media_type=self.media_type,
            background=self.background,
        )
