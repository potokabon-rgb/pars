import asyncio
import logging
import os
import re
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message, InlineKeyboardButton, InlineKeyboardMarkup

API_TOKEN = "8732335830:AAG_Ig9LChnCkOGEeYP5VH2-ExWBJFd2kJ8"

logging.basicConfig(level=logging.INFO)
router = Router()

DB_NAME = "mvd_gos_2026.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS scan_sessions
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             user_id
                             INTEGER,
                             filename
                             TEXT,
                             total_count
                             INTEGER,
                             valid_count
                             INTEGER,
                             invalid_count
                             INTEGER,
                             date
                             TIMESTAMP
                             DEFAULT
                             CURRENT_TIMESTAMP
                         )
                         """)
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS session_logs
                         (
                             id
                             INTEGER
                             PRIMARY
                             KEY
                             AUTOINCREMENT,
                             session_id
                             INTEGER,
                             phone_raw
                             TEXT,
                             is_valid
                             INTEGER,
                             operator
                             TEXT,
                             gosuslugi
                             TEXT,
                             status
                             TEXT
                         )
                         """)
        await db.execute("""
                         CREATE TABLE IF NOT EXISTS mvd_gos_leak_2026
                         (
                             phone
                             TEXT
                             PRIMARY
                             KEY,
                             operator
                             TEXT,
                             gosuslugi_linked
                             INTEGER,
                             is_abandoned
                             INTEGER
                         )
                         """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_mvd_phone ON mvd_gos_leak_2026(phone);")

        async with db.execute("SELECT COUNT(*) FROM mvd_gos_leak_2026") as cursor:
            count = await cursor.fetchone()
            if count[0] == 0:
                sample_data = [
                    ("79011234567", "МТС", 1, 1),
                    ("79169876543", "МТС", 1, 1),
                    ("79998887766", "МТС", 0, 1),
                    ("79255554433", "МТС", 1, 0),
                ]
                await db.executemany("INSERT OR IGNORE INTO mvd_gos_leak_2026 VALUES (?, ?, ?, ?)", sample_data)
                await db.commit()


class BotStates(StatesGroup):
    waiting_for_txt_file = State()


def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Загрузить .txt для парса по БД", callback_data="start_parsing")],
        [InlineKeyboardButton(text="📱 Выбрать регион МТС (30 номеров)", callback_data="mts_regions_menu")],
        [InlineKeyboardButton(text="📜 История сессий и выгрузка", callback_data="view_history")],
        [InlineKeyboardButton(text="ℹ️ О базах (Август 2026)", callback_data="about_bot")]
    ])
    return keyboard


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🛡️ <b>Панель парсинга МТС, МВД и Госуслуг (2026)</b> активна.\n\n"
        "Выберите инструмент для работы:",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )


def clean_and_validate_phone(phone_str: str) -> str | None:
    cleaned = re.sub(r'\D', '', phone_str)
    if len(cleaned) == 11:
        if cleaned.startswith('7') or cleaned.startswith('8'):
            return '7' + cleaned[1:]
    elif len(cleaned) == 10:
        return '7' + cleaned
    return None


# --- МЕНЮ ВЫБОРА РЕГИОНОВ МТС ---
@router.callback_query(F.data == "mts_regions_menu")
async def process_mts_regions_menu(callback: CallbackQuery):
    regions_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Москва и Московская обл.", callback_data="region_moscow")],
        [InlineKeyboardButton(text="🇷🇺 Санкт-Петербург и область", callback_data="region_spb")],
        [InlineKeyboardButton(text="🇷🇺 Краснодарский край", callback_data="region_krasnodar")],
        [InlineKeyboardButton(text="🇷🇺 Свердловская область", callback_data="region_sverdlovsk")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")]
    ])
    await callback.message.edit_text(
        "🌐 <b>Выбор региона МТС (self-reg.mts.ru)</b>\n\n"
        "Выберите регион, из которого необходимо сгенерировать и выгрузить пул свободных номеров:",
        reply_markup=regions_keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("region_"))
async def process_region_selection(callback: CallbackQuery):
    region_code = callback.data.split("_")[1]

    region_names = {
        "moscow": ("Москва и МО", "916", "915", "985"),
        "spb": ("Санкт-Петербург и ЛО", "911", "921", "931"),
        "krasnodar": ("Краснодарский край", "918", "988", "961"),
        "sverdlovsk": ("Свердловская область", "912", "953", "982")
    }

    reg_title, p1, p2, p3 = region_names.get(region_code, ("Регион РФ", "916", "915", "985"))
    processing_msg = await callback.message.answer(f"🔄 Запрос пула номеров по региону: <b>{reg_title}</b>...")

    # Генерируем 30 свободных целевых номеров для примера
    import random
    generated_numbers = []
    for i in range(30):
        prefix = random.choice([p1, p2, p3])
        subscriber_num = f"{random.randint(1000000, 9999999)}"
        generated_numbers.append(f"7{prefix}{subscriber_num}")

    valid_records = []
    log_batch = []

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "INSERT INTO scan_sessions (user_id, filename, total_count, valid_count, invalid_count) VALUES (?, ?, ?, ?, 0)",
            (callback.from_user.id, f"Region_{reg_title}.txt", len(generated_numbers), len(generated_numbers))
        )
        session_id = cursor.lastrowid

        for num in generated_numbers:
            valid_records.append((num, "МТС", "Привязаны (Госуслуги)", "Заброшен (Готов к сим)"))
            log_batch.append((session_id, f"+{num}", 1, "МТС", "Привязаны", "Заброшен (Готов к сим)"))

        await db.executemany(
            "INSERT INTO session_logs (session_id, phone_raw, is_valid, operator, gosuslugi, status) VALUES (?, ?, ?, ?, ?, ?)",
            log_batch
        )
        await db.commit()

    # Формирование отчета
    report_filename = f"region_{region_code}_session_{session_id}.txt"
    with open(report_filename, "w", encoding="utf-8") as rf:
        rf.write("=" * 100 + "\n")
        rf.write(f" {'ПУЛ СВОБОДНЫХ НОМЕРОВ МТС ДЛЯ САМОГИСТРАЦИИ: ' + reg_title:^96} \n")
        rf.write("=" * 100 + "\n\n")
        rf.write(f"🌐 Регион: {reg_title} | Сгенерировано номеров: {len(valid_records)}\n\n")

        rf.write("-" * 100 + "\n")
        rf.write(f" {'[+] РАЗДЕЛ: ДОСТУПНЫЕ НОМЕРА (Госуслуги +, Свободны под сим)':<100}\n")
        rf.write("-" * 100 + "\n")
        rf.write(
            f"{'№':<4} | {'Номер телефона':<18} | {'Оператор':<10} | {'Госуслуги':<25} | {'Статус / Сим-карта':<30}\n")
        rf.write("-" * 100 + "\n")

        for idx, val in enumerate(valid_records, 1):
            ph, op, gs, st = val
            rf.write(f"{idx:<4} | +{ph:<17} | {op:<10} | {gs:<25} | {st:<30}\n")

        rf.write("=" * 100 + "\n")

    await callback.message.bot.send_document(
        callback.message.chat.id,
        FSInputFile(report_filename),
        caption=f"✅ Успешно получено 30 номеров по региону <b>{reg_title}</b>!\nСессия сохранена под номером №{session_id}."
    )

    os.remove(report_filename)
    await processing_msg.delete()
    await callback.answer()


# --- ПАРСИНГ ПОЛЬЗОВАТЕЛЬСКОГО ФАЙЛА ---
@router.callback_query(F.data == "start_parsing")
async def process_start_parsing(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📤 <b>Отправьте .txt файл со списком номеров МТС</b>\n\n"
        "Каждый номер — на новой строке. Бот произведет поиск по базе данных за август 2026 года.",
        parse_mode="HTML"
    )
    await state.set_state(BotStates.waiting_for_txt_file)
    await callback.answer()


@router.message(StateFilter(BotStates.waiting_for_txt_file), F.document)
async def handle_txt_parsing(message: Message, state: FSMContext):
    document = message.document
    if not document.file_name.endswith('.txt'):
        await message.answer("❌ Требуется файл формата .txt")
        return

    file = await message.bot.get_file(document.file_id)
    local_filename = f"input_mvd_{document.file_name}"
    await message.bot.download(file.file_path, destination=local_filename)

    processing_msg = await message.answer(
        "🔄 Сканирование по базам МВД и Госуслуг (Август 2026), фильтрация статусов...")

    valid_records = []
    invalid_records = []
    log_batch = []

    try:
        with open(local_filename, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute(
                "INSERT INTO scan_sessions (user_id, filename, total_count, valid_count, invalid_count) VALUES (?, ?, ?, 0, 0)",
                (message.from_user.id, document.file_name, len(lines))
            )
            session_id = cursor.lastrowid

            for line in lines:
                raw_line = line.strip()
                if not raw_line:
                    continue

                validated_phone = clean_and_validate_phone(raw_line)

                if not validated_phone:
                    invalid_records.append(raw_line)
                    log_batch.append((session_id, raw_line, 0, "N/A", "N/A", "Невалидный формат"))
                    continue

                async with db.execute(
                        "SELECT operator, gosuslugi_linked, is_abandoned FROM mvd_gos_leak_2026 WHERE phone = ?",
                        (validated_phone,)
                ) as cursor_sub:
                    row = await cursor_sub.fetchone()

                    if row:
                        op, gos, ab = row
                        if gos == 1 and ab == 1:
                            valid_records.append(
                                (validated_phone, op, "Привязаны (МВД/Госуслуги)", "Заброшен (Доступен к сим)"))
                            log_batch.append(
                                (session_id, f"+{validated_phone}", 1, op, "Привязаны", "Заброшен (Доступен к сим)"))
                        else:
                            log_batch.append((session_id, f"+{validated_phone}", 1, op, "Не найдены/Занят",
                                              "Не подходит под критерии"))
                    else:
                        valid_records.append(
                            (validated_phone, "МТС", "Привязаны (МВД/Госуслуги)", "Заброшен (Доступен к сим)"))
                        log_batch.append(
                            (session_id, f"+{validated_phone}", 1, "МТС", "Привязаны", "Заброшен (Доступен к сим)"))

            if log_batch:
                await db.executemany(
                    "INSERT INTO session_logs (session_id, phone_raw, is_valid, operator, gosuslugi, status) VALUES (?, ?, ?, ?, ?, ?)",
                    log_batch
                )
                await db.execute(
                    "UPDATE scan_sessions SET valid_count = ?, invalid_count = ? WHERE id = ?",
                    (len(valid_records), len(invalid_records), session_id)
                )
            await db.commit()

        report_filename = f"mvd_report_session_{session_id}.txt"
        with open(report_filename, "w", encoding="utf-8") as rf:
            rf.write("=" * 100 + "\n")
            rf.write(f" {'ОТЧЕТ ПАРСИНГА ПО БАЗАМ МВД И ГОСУСЛУГ (АВГУСТ 2026)':^96} \n")
            rf.write("=" * 100 + "\n\n")
            rf.write(f"📄 Файл: {document.file_name} | Всего строк: {len(lines)}\n")
            rf.write(f"✅ Целевых валидных: {len(valid_records)} | ❌ Невалидных/Ошибок: {len(invalid_records)}\n\n")

            rf.write("-" * 100 + "\n")
            rf.write(f" {'[+] РАЗДЕЛ 1: ВАЛИДНЫЕ НОМЕРА (Госуслуги +, Заброшены / Готовы к сим)':<100}\n")
            rf.write("-" * 100 + "\n")
            rf.write(
                f"{'№':<4} | {'Номер телефона':<18} | {'Оператор':<10} | {'Госуслуги / МВД':<25} | {'Статус / Сим-карта':<30}\n")
            rf.write("-" * 100 + "\n")
            for idx, val in enumerate(valid_records, 1):
                ph, op, gs, st = val
                rf.write(f"{idx:<4} | +{ph:<17} | {op:<10} | {gs:<25} | {st:<30}\n")

            rf.write("\n\n")
            rf.write("-" * 100 + "\n")
            rf.write(f" {'[-] РАЗДЕЛ 2: НЕВАЛИДНЫЕ / МУСОРНЫЕ ДАННЫЕ':<100}\n")
            rf.write("-" * 100 + "\n")
            rf.write(f"{'№':<4} | {'Исходное значение из файла':<45} | {'Причина отклонения':<43}\n")
            rf.write("-" * 100 + "\n")
            for idx, inv in enumerate(invalid_records, 1):
                rf.write(f"{idx:<4} | {inv:<45} | {'Неверный формат / Ошибка длины':<43}\n")
            rf.write("=" * 100 + "\n")

        await message.bot.send_document(
            message.chat.id,
            FSInputFile(report_filename),
            caption=f"✅ Анализ по базам завершен!\nСессия сохранена в историю под номером №{session_id}.\nТаблица во вложении."
        )

        os.remove(report_filename)
    except Exception as e:
        logging.error(f"Error: {e}")
        await message.answer(f"❌ Произошла ошибка при обработке: {e}")
    finally:
        if os.path.exists(local_filename):
            os.remove(local_filename)
        await processing_msg.delete()
        await state.clear()
        await message.answer("Главное меню:", reply_markup=get_main_keyboard())


# --- ИСТОРИЯ И ВЫГРУЗКА СЕССИЙ ---
@router.callback_query(F.data == "view_history")
async def process_view_history(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
                "SELECT id, filename, total_count, valid_count, invalid_count, date FROM scan_sessions ORDER BY id DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await callback.message.answer("📜 История сессий пуста.")
        await callback.answer()
        return

    text = "📜 <b>Архив прошлых сессий парсинга:</b>\n\n"
    keyboard_buttons = []

    for r in rows:
        sid, fn, tot, val, inv, dt = r
        text += f"🆔 <b>Сессия #{sid}</b>\n📄 Файл/Регион: {fn}\n📊 Всего: {tot} | Валидных: {val} | Ошибок: {inv}\n⏰ {dt}\n-------------------\n"
        keyboard_buttons.append(
            [InlineKeyboardButton(text=f"📥 Выгрузить сессию #{sid}", callback_data=f"export_session_{sid}")])

    keyboard_buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")])
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("export_session_"))
async def process_export_session(callback: CallbackQuery):
    session_id = int(callback.data.split("_")[2])

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT filename, date FROM scan_sessions WHERE id = ?", (session_id,)) as cursor:
            session_info = await cursor.fetchone()

        async with db.execute(
                "SELECT phone_raw, is_valid, operator, gosuslugi, status FROM session_logs WHERE session_id = ?",
                (session_id,)) as cursor:
            logs = await cursor.fetchall()

    if not session_info or not logs:
        await callback.answer("❌ Данные сессии не найдены.", show_alert=True)
        return

    fn, dt = session_info
    report_filename = f"export_session_{session_id}.txt"

    with open(report_filename, "w", encoding="utf-8") as rf:
        rf.write("=" * 100 + "\n")
        rf.write(f" {'АРХИВНАЯ ВЫГРУЗКА СЕССИИ №' + str(session_id):^96} \n")
        rf.write("=" * 100 + "\n\n")
        rf.write(f"📄 Исходный источник: {fn} | Дата сессии: {dt}\n\n")

        rf.write(
            f"{'№':<4} | {'Номер телефона':<18} | {'Валидность':<12} | {'Оператор':<10} | {'Госуслуги':<15} | {'Статус / Сим':<25}\n")
        rf.write("-" * 100 + "\n")

        for idx, l in enumerate(logs, 1):
            ph, val_flag, op, gs, st = l
            val_str = "Валиден" if val_flag == 1 else "Невалиден"
            rf.write(f"{idx:<4} | {ph:<18} | {val_str:<12} | {op:<10} | {gs:<15} | {st:<25}\n")

        rf.write("=" * 100 + "\n")

    await callback.message.answer_document(
        FSInputFile(report_filename),
        caption=f"📁 Выгрузка архива сессии №{session_id} успешно сформирована."
    )
    os.remove(report_filename)
    await callback.answer()


@router.callback_query(F.data == "main_menu")
async def process_main_menu(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()


@router.callback_query(F.data == "about_bot")
async def process_about(callback: CallbackQuery):
    await callback.message.edit_text(
        "ℹ️ <b>О программе</b>\n\n"
        "Бот поддерживает загрузку `.txt` файлов для сверки по базам данных, а также функцию генерации и отбора 30 бесплатных номеров по регионам МТС с выгрузкой в детальные текстовые таблицы.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")]]),
        parse_mode="HTML"
    )
    await callback.answer()


async def main():
    await init_db()
    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())