from __future__ import annotations

import json

from channels.generic.websocket import AsyncWebsocketConsumer


class UpdatesConsumer(AsyncWebsocketConsumer):
    group_name = "tuxcmdb_updates"

    async def connect(self) -> None:
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

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