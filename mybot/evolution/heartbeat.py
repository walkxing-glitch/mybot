"""HeartbeatLoop — async tick loop for evolution subsystems."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from mybot.config import HeartbeatConfig

logger = logging.getLogger(__name__)


class HeartbeatLoop:
    def __init__(
        self,
        *,
        config: HeartbeatConfig,
        on_tick: Callable[[], Awaitable[Any]],
    ) -> None:
        self._config = config
        self._on_tick = on_tick
        self._busy = False
        self._running = False

    def set_busy(self, busy: bool) -> None:
        self._busy = busy

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        if not self._config.enabled:
            return
        self._running = True
        logger.info(
            "Heartbeat started, interval=%ss", self._config.interval_seconds
        )
        while self._running:
            await asyncio.sleep(self._config.interval_seconds)
            if not self._running:
                break
            if self._busy:
                logger.debug("Heartbeat tick skipped — agent busy")
                continue
            try:
                await self._on_tick()
            except Exception:
                logger.exception("Heartbeat tick failed")
        logger.info("Heartbeat stopped")
