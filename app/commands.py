from aiogram.types import BotCommand, BotCommandScopeDefault

EN = [
    BotCommand(command="start",     description="Start"),
    BotCommand(command="journal",   description="New journal entry"),
    BotCommand(command="stats",     description="Stats"),
    BotCommand(command="remind",    description="Create reminder"),
    BotCommand(command="premium",   description="Premium"),
    BotCommand(command="meditation",description="Meditation timer"),
    BotCommand(command="music",     description="My music / playlist"),
]

RU = [
    BotCommand(command="start",     description="Начать"),
    BotCommand(command="journal",   description="Новая запись"),
    BotCommand(command="stats",     description="Статистика"),
    BotCommand(command="remind",    description="Создать напоминание"),
    BotCommand(command="premium",   description="Премиум"),
    BotCommand(command="meditation",description="Медитация (таймер)"),
    BotCommand(command="music",     description="Моя музыка / плейлист"),
]

UK = [
    BotCommand(command="start",     description="Почати"),
    BotCommand(command="journal",   description="Створити запис"),
    BotCommand(command="stats",     description="Статистика"),
    BotCommand(command="remind",    description="Створити нагадування"),
    BotCommand(command="premium",   description="Преміум"),
    BotCommand(command="meditation",description="Медитація (таймер)"),
    BotCommand(command="music",     description="Моя музика / плейлист"),
]

async def setup_bot_commands(bot):
    await bot.delete_my_commands(scope=BotCommandScopeDefault())
    try:
        await bot.delete_my_commands(language_code="ru")
    except:
        pass
    try:
        await bot.delete_my_commands(language_code="uk")
    except:
        pass
    await bot.set_my_commands(EN)
    await bot.set_my_commands(RU, language_code="ru")
    await bot.set_my_commands(UK, language_code="uk")
