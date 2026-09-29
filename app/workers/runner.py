import asyncio
import logging
from app.workers.order_worker import order_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def main():
    logging.info("Starting standalone TangLike Background Worker...")
    try:
        await order_worker.run_loop()
    except (KeyboardInterrupt, SystemExit):
        order_worker.stop()
        logging.info("TangLike Background Worker stopped.")

if __name__ == "__main__":
    asyncio.run(main())
