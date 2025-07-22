from FLteach.bot.telegram import TelegramBot

if __name__ == "__main__":
    # Example usage of the TelegramBot
    bot = TelegramBot(
        telegram_api_key_or_path="./bot/telegram_api_key.txt",
        model_api_key_or_path="./llm/api_key.txt",
    )
    bot.run()
