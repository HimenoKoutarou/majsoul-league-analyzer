"""启动独立机器人进程：python -m bot。"""
import asyncio
import logging

from .application import BotApplication


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(BotApplication().run())


if __name__ == "__main__":
    main()
