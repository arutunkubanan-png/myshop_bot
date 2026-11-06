import asyncio
import os


from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
TOKEN = "9054630723:AAHcSHP69klnk9hCxu46mCDC0lMQmgJnZUc"



bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! 👋 Это торговая площадка.\n"
        "Здесь с появятся товары."
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
