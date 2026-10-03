import asyncio
import logging

from app.db.session import session_factory
from app.services.historical_backtest import run_queued_paper
from app.services.historical_import import run_queued_import

log = logging.getLogger("hextrade.historical.loop")


async def historical_loop(runtime) -> None:
    """Import and backdated paper jobs. Separate from the live market loop."""
    while True:
        try:
            await asyncio.to_thread(_tick, runtime)
        except Exception:
            log.exception("historical worker error")
        await asyncio.sleep(1)


def _tick(runtime) -> None:
    run_queued_import(runtime)
    db = session_factory()()
    try:
        run_queued_paper(db)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("historical paper worker error")
    finally:
        db.close()
