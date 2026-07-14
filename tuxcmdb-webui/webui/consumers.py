from __future__ import annotations

import asyncio
import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .services import latest_audit_marker


class UpdatesConsumer(AsyncWebsocketConsumer):
    group_name = "tuxcmdb_updates"
    poll_interval_seconds = 2.0

    async def connect(self) -> None:
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        self._stop_event = asyncio.Event()
        self._audit_marker = await sync_to_async(latest_audit_marker, thread_sensitive=True)()
        self._watch_task = asyncio.create_task(self._watch_audit_updates())

    async def disconnect(self, close_code: int) -> None:
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if hasattr(self, "_stop_event"):
            self._stop_event.set()
        watch_task = getattr(self, "_watch_task", None)
        if watch_task is not None:
            watch_task.cancel()
            try:
                await watch_task
            except asyncio.CancelledError:
                pass

    async def ui_update(self, event: dict) -> None:
        await self.send(
            text_data=json.dumps(
                {
                    "entity": event.get("entity", "unknown"),
                    "action": event.get("action", "changed"),
                    "ref": event.get("ref", ""),
                    "ts": event.get("ts"),
                }
            )
        )

    async def _watch_audit_updates(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self.poll_interval_seconds)
            latest_marker = await sync_to_async(latest_audit_marker, thread_sensitive=True)()
            if latest_marker <= self._audit_marker:
                continue

            self._audit_marker = latest_marker
            await self.send(
                text_data=json.dumps(
                    {
                        "entity": "all",
                        "action": "changed",
                        "ref": "audit-log",
                        "ts": None,
                    }
                )
            )