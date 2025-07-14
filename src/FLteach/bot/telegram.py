import os

import telebot

from FLteach.bot.bot import IBot


class TelegramBot(IBot):
    """
    A simple Telegram bot class that uses the Telebot library.
    It initializes the bot with a token from environment variables.
    """
    def __init__(self,
                 api_key_or_path: str | None = None
                 ) -> None:
        """
        Initializes the Telegram bot with the token from environment variables.
        """
        self.api_key: str
        if api_key_or_path is not None and os.path.isfile(api_key_or_path):
            self.api_key = api_key_or_path
        elif isinstance(api_key_or_path, str):
            self.api_key = api_key_or_path
        else:
            self.api_key = os.environ["OPENAI_API_KEY"]
        self.bot = self.initialize_bot()
        self.user_states = {}  # keep track of user states
        self._register_handlers()

    def initialize_bot(self) -> telebot.TeleBot:
        """
        Initializes the Telegram bot
        """
        return telebot.TeleBot(self.api_key)

    def _register_handlers(self):
        """
        Registers the message handlers for the bot.
        """
        # Register handler for '/start' and '/hello' commands
        # We pass the method object itself (self._handle_start_hello)
        self.bot.message_handler(commands=['start', 'hello'])(self._handle_start_hello)
        # Register a generic handler for all other text messages
        # 'func=lambda message: True' means this handler will process any message
        # that hasn't been handled by a more specific handler (like commands).
        self.bot.message_handler(func=lambda message: True)(self._handle_all_messages)

    def _handle_start_hello(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/start' or '/hello' commands.
        The 'message' object contains all information about the incoming message.
        """
        chat_id = message.chat.id
        user_text = message.text
        # Set the user's state to indicate we are waiting for their next input
        self.user_states[chat_id] = 'awaiting_query'

        self.bot.send_message(
            chat_id,
            "Hello there! I'm your horoscope bot. Please tell me your birth date (e.g., 'January 1, 1990') "
            "or just type anything to get a general horoscope."
        )

    def _reset_user_states(self, chat_id: int) -> None:
        # Reset the user's state after processing the input
        if chat_id in self.user_states:
            del self.user_states[chat_id]

    def _handle_query(self, message: telebot.types.Message) -> None:
        """
        Handles the free text input after the '/start' or '/hello' command.
        This function is called by _handle_all_messages when the state matches.
        """
        chat_id = message.chat.id
        user_free_text = message.text

        # Process the user's free text here
        # For demonstration, we'll just echo it and give a generic horoscope
        response_text = (
            f"Thanks for providing: '{user_free_text}'.\n\n"
            "Your general horoscope for today: Expect unexpected opportunities! "
            "Be open to new ideas and collaborations. A positive attitude will "
            "lead to great outcomes."
        )
        self.bot.send_message(chat_id, response_text)

        # Reset the user's state after processing the input
        if chat_id in self.user_states:
            del self.user_states[chat_id]  # Or self.user_states[chat_id] = 'normal'
        self._reset_user_states(chat_id)

    def _handle_help_command(self, message: telebot.types.Message) -> None:
        """
        Handles the '/help' command, providing a list and explanation of available commands.
        """
        chat_id = message.chat.id
        help_text = (
            "Here are the commands you can use:\n\n"
            "/start or /hello - Begin a new horoscope conversation.\n"
            "  After this, you can type your birth date or any text to get a horoscope.\n\n"
            "/help - Show this list of commands."
            # Add more commands here as your bot grows
        )
        self.bot.send_message(chat_id, help_text)


    def _handle_all_messages(self, message: telebot.types.Message) -> None:
        """
        Handles all other text messages that are not specific commands.
        """
        user_text = message.text  # This is how you retrieve the text written by the user!
        chat_id = message.chat.id
        current_state = self.user_states.get(chat_id)
        if current_state == 'awaiting_query':
            self._handle_query(message)

        self.bot.send_message(chat_id,
                              f"You said: '{user_text}'. I'm still learning, but I can tell you "
                              "your horoscope if you type /start or /hello!")

    def send(self, chat_id: int, message_content: str) -> None:
        """
        Sends a message from the user in the telegram chat
        """
        self.bot.send_message(chat_id, message_content)

    def get(self) -> str:
        """
        The 'get' method for TelegramBot is a placeholder as it operates via event handlers.
        """
        pass

    def run(self) -> None:
        """
        Starts the bot's polling loop. This makes the bot listen for incoming messages.
        The 'none_stop=True' argument ensures the bot keeps running even if there are errors.
        """
        self.bot.polling(none_stop=True)


if __name__ == "__main__":
    bot_instance = TelegramBot()
    bot_instance.run()
