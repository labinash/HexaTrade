import asyncio
import logging

from app.services.engine import AppRuntime, run_cycle

log = logging.getLogger("hextrade.loop")


async def market_loop(runtime: AppRuntime) -> None:
    while True:
        try:
            await asyncio.to_thread(run_cycle, runtime)
        except Exception:
            log.exception("cycle error")
        await asyncio.sleep(2)
