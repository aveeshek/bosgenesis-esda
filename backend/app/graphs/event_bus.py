import asyncio
from collections import defaultdict


class RunEventBus:
    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[dict]]] = defaultdict(list)

    async def publish(self, run_id: str, event: dict) -> None:
        for queue in list(self._queues.get(run_id, [])):
            await queue.put(event)

    async def subscribe(self, run_id: str):
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._queues[run_id].append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues[run_id].remove(queue)
