from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from supplymargin.config import settings
from supplymargin.database import SessionLocal, init_database
from supplymargin.job_runner import execute_run
from supplymargin.models import Run

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("supplymargin.worker")
running = True


def _stop(_signum, _frame) -> None:
    global running
    running = False


def claim_one() -> str | None:
    with SessionLocal() as db:
        query = (
            select(Run)
            .where(Run.status == "queued")
            .order_by(Run.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        run = db.scalar(query)
        if run is None:
            return None
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()
        return run.id


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    init_database()
    with SessionLocal() as db:
        db.execute(
            update(Run)
            .where(
                Run.status == "running",
                Run.started_at
                < datetime.now(timezone.utc) - timedelta(hours=2),
            )
            .values(status="queued", started_at=None, error_message=None)
        )
        db.commit()
    logger.info("SupplyMargin worker started")
    while running:
        run_id = claim_one()
        if run_id is None:
            time.sleep(settings.worker_poll_seconds)
            continue
        logger.info("Processing run %s", run_id)
        with SessionLocal() as db:
            execute_run(db, run_id)
    logger.info("SupplyMargin worker stopped")


if __name__ == "__main__":
    main()
