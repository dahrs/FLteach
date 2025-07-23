import re
import os
import json
import random
import datetime
import time
import logging
import threading
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dateutil import parser
from functools import partial

import telebot
import schedule
import telegramify_markdown
from telegramify_markdown import customize

from FLteach.bot.bot import IBot
from FLteach.llm.openai_api import OpenaiApi

# Configure telegramify_markdown
customize.Symbol.head_level_1 = "📌"
customize.Symbol.link = "🔗"
customize.strict_markdown = True
customize.cite_expandable = True

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')


class TelegramBot(IBot):
    """
    A simple Telegram bot class that uses the Telebot library.
    It initializes the bot with a Telegram API token (https://core.telegram.org/bots#how-do-i-create-a-bot)
    and an OpenAI API key (https://platform.openai.com/settings/organization/api-keys)
    given as arguments or from environment variables (TELEGRAM_BOT_TOKEN and OPENAI_API_KEY respectively).
    """
    def __init__(self,
                 telegram_api_key_or_path: str | None = None,
                 model_api_key_or_path: str | None = None,
                 ) -> None:
        """
        Initializes the Telegram bot with the token from environment variables.
        """
        self.telegram_api_key: str
        if telegram_api_key_or_path is not None and os.path.isfile(telegram_api_key_or_path):
            with open(telegram_api_key_or_path, 'r') as f:
                self.telegram_api_key = f.read().strip()
        elif isinstance(telegram_api_key_or_path, str):
            self.telegram_api_key = telegram_api_key_or_path
        else:
            self.telegram_api_key = os.environ.get("TELEGRAM_BOT_TOKEN", "") # Corrected env var name
            if not self.telegram_api_key:
                raise ValueError(
                    "Telegram bot token not found. Please provide it as an argument "
                    "or set the 'TELEGRAM_BOT_TOKEN' environment variable."
                )

        self.bot = self.initialize_bot()

        # User-specific state management for multi-step conversations
        # {chat_id: {'step': 'current_step_name', 'data': {key: value}}}
        self.user_states: Dict[int, Dict[str, Any]] = {}
        logging.info("User states dictionary initialized.")

        # Bot configuration and lesson data
        self.language: Optional[str] = None
        self.level: Optional[str] = None
        self.limitation: Optional[str] = None
        self.next_lesson: Optional[str] = None
        self.lesson_sections: List[str] = []  # Sections for the current lesson
        self.lesson_history: List[Dict[str, str]] = []  # LLM conversation history for current lesson
        self.abilities: List[str] = [
            "Listening comprehension", "Oral production", "Reading comprehension",
            "Written production", "Culture and Pragmatics", "Vocabulary Acquisition",
            "Grammar and syntax", "Phonetics and Pronunciation", "Orthography and Spelling",
            "Consolidation of previously learned content", "Refinement of previously learned content",
            "Discourse Analysis & Cohesion", "Practice games and exercises", "Practice weak points",
        ]
        self.learned_languages: List[str] = ['english (default)']  # Default if not set
        self.reminder_time: Optional[str] = None  # HH:MM format
        self.reminder_job = None  # schedule.Job object
        # TODO  ###################################
        self.reminded_today: Dict[int, bool] = {}  # {chat_id: True/False} to track daily reminder sent
        self.lesson_errors: List[str] = []  # Errors from user's practice
        self.seen_content: List[str] = []  # Content covered in lessons
        self.mastered: List[str] = []  # Content user has mastered

        self.model = OpenaiApi(api_key_or_path=model_api_key_or_path)
        self._register_handlers()

    def text2list(self,
                  text: str,
                  prompt_intro: str | None = None,
                  system_prompt: str = "You are a succinct element extractor, and format standardizer.",
                  ) -> List[str]:
        """
        Uses the LLM to extract a list of strings from text.
        """
        user_message_summary = ""
        prompt_intro = "Extract all listed elements and return them as a JSON array of strings" if prompt_intro is None else prompt_intro
        try:
            user_message_summary = self.model.call(
                user_prompt=f"{prompt_intro}: {text}",
                system_prompt=f"{system_prompt} Return a JSON array of strings."
            )
            user_message_summary = re.findall(r'(?<=```json).+(?=```)', user_message_summary, flags=re.DOTALL)
            return json.loads(user_message_summary[0])
        except json.JSONDecodeError as err:
            logging.error(f"Failed to decode JSON from LLM: {err}. Raw response: {user_message_summary}")
            return []  # Return empty list on failure
        except Exception as err:
            logging.error(f"Error in text2list LLM call: {err}", exc_info=True)
            return []

    def initialize_bot(self) -> telebot.TeleBot:
        """
        Initializes the Telegram bot
        """
        return telebot.TeleBot(self.telegram_api_key)

    def run(self) -> None:
        """
        Starts the bot's polling loop and the scheduler thread.
        """
        logging.info("Starting the Telegram bot polling and scheduler.")
        # Start Telegram bot polling in a separate thread
        bot_thread = threading.Thread(target=self.bot.polling, kwargs={'none_stop': True})
        bot_thread.daemon = True
        bot_thread.start()
        logging.info("Telegram bot polling thread started.")

        # Start scheduler in a separate thread
        scheduler_thread = threading.Thread(target=self._scheduler_thread)
        scheduler_thread.daemon = True
        scheduler_thread.start()
        logging.info("Scheduler thread started.")

        # Keep the main thread alive
        while True:
            time.sleep(1)

    def _scheduler_thread(self):
        """
        Function to run the schedule loop in a separate thread.
        """
        # Schedule daily reset of reminded_today flag
        schedule.every().day.at("00:00").do(self._reset_reminded_flag)
        logging.info("Scheduled daily reset of reminder flag at 00:00 UTC.")

        while True:
            schedule.run_pending()
            time.sleep(1)  # Check every second

    def _reset_reminded_flag(self):
        """
        Resets the reminded_today flag for all users at midnight.
        """
        for chat_id in self.reminded_today:
            self.reminded_today[chat_id] = False
        logging.info("Reminder flags reset for all users.")

    def _register_handlers(self):
        """
        Registers the message handlers for the bot.
        """
        self.bot.message_handler(commands=['setup'])(self._handle_setup_command)
        # TODO info handlers should not be chained to one another check and correct ######################################
        self.bot.message_handler(commands=['language'])(self._handle_language_command)
        self.bot.message_handler(commands=['level'])(self._handle_level_command)
        self.bot.message_handler(commands=['limitation'])(self._handle_limitation_command)
        self.bot.message_handler(commands=['lesson'])(self._handle_lesson_command)
        self.bot.message_handler(commands=['learned'])(self._handle_learned_command)
        self.bot.message_handler(commands=['mastered'])(self._handle_mastered_command)
        self.bot.message_handler(commands=['reminder'])(self._handle_reminder_command)
        self.bot.message_handler(commands=['new'])(self._handle_new_lesson_command)
        self.bot.message_handler(commands=['next'])(self._handle_next_section_command)
        self.bot.message_handler(commands=['more'])(self._handle_more_details_command)
        self.bot.message_handler(commands=['better'])(self._handle_better_explanation_command)
        self.bot.message_handler(commands=['question'])(self._handle_question_command)
        self.bot.message_handler(commands=['conversation'])(self._handle_conversation_command)
        self.bot.message_handler(commands=['data'])(self._handle_data_command)
        self.bot.message_handler(commands=['help', 'info', 'documentation'])(self._handle_help_command)
        self.bot.message_handler(func=lambda message: True)(self._handle_all_messages)

    # --- Setup Flow Handlers ---
    def _handle_setup_command(self, message: telebot.types.Message) -> None:
        """
        Initiates the setup flow. Asks for language.
        """
        chat_id = message.chat.id
        logging.info(f"Received /setup from chat ID: {chat_id}")
        self.user_states[chat_id] = {'step': 'awaiting_language'}
        self.lesson_history = []  # Reset history for new start
        self.bot.send_message(
            chat_id,
            "Hi! I am a Foreign language teaching agent. "
            "What language do you want to learn or practice?"
        )
        self.bot.register_next_step_handler(message, self._process_language_input)
        logging.info(f"Set next step for {chat_id} to _process_language_input.")

    def _process_language_input(self, message: telebot.types.Message) -> None:
        """
        Processes language input and asks for level.
        """
        chat_id = message.chat.id
        user_message = message.text
        if not user_message or user_message.startswith('/'):
            self.bot.send_message(chat_id, "Please provide a language. Try /language or /setup again.")
            return

        try:
            user_message_summary = self.model.call(
                user_prompt=f"This is the answer to the question 'what language do you want to learn?'. Output the "
                            f"standard name of the language along with any necessary details like regional "
                            f"specificities/level/register/etc. It must be written in said language. (use as few words "
                            f"as possible to summarize all concepts): {user_message}",
                system_prompt="You are a succinct extractor and format standardizer.",
            )
            # TODO below comment + ask for level if in /setup only##############################################
            self.language = user_message_summary # This will be overwritten by next user if not careful in multi-user setup
            # For multi-user, store in user_states: self.user_states[chat_id]['language'] = user_message_summary
            self.user_states[chat_id]['language'] = user_message_summary
            self.bot.send_message(chat_id, f"Great! You want to learn {user_message_summary}")
            logging.info(f"Language set for {chat_id}: {user_message_summary}")

            self.user_states[chat_id]['step'] = 'awaiting_level'
            self.bot.send_message(
                chat_id,
                "Can you tell me your level of proficiency in this language? "
                "If you are unsure, you can shortly explain what you can do in this language, "
                "or just ask to start from the beginning."
            )
            self.bot.register_next_step_handler(message, self._process_level_input)
            logging.info(f"Set next step for {chat_id} to _process_level_input.")
        except Exception as err:
            logging.error(f"Error processing language for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I had trouble understanding that language. Please try again.")

    def _process_level_input(self, message: telebot.types.Message) -> None:
        """
        Processes level input and asks for limitations.
        """
        chat_id = message.chat.id
        user_message = message.text
        if not user_message or user_message.startswith('/'):
            self.bot.send_message(chat_id, "Please provide your level. Try /level or /setup again.")
            return

        try:
            user_message_summary = self.model.call(
                user_prompt=f"This is the answer to the question 'what is your language proficiency/level?'. Output the "
                            f"standard name of the level along with any necessary details (use as few words as possible "
                            f"to summarize all concepts): {user_message}",
                system_prompt="You are a succinct extractor and format standardizer."
            )
            self.level = user_message_summary # This will be overwritten by next user if not careful in multi-user setup
            self.user_states[chat_id]['level'] = user_message_summary
            self.bot.send_message(chat_id, f"Understood, your level is {user_message_summary}\n\nGive me a second...")
            logging.info(f"Level set for {chat_id}: {user_message_summary}")

            # Infer mastered content based on level
            infered_master_content = self.model.call(
                user_prompt=f"Given a {self.user_states[chat_id]['level']} proficiency level in {self.user_states[chat_id]['language']}, make a list of all the content "
                            f"that a foreign language student should already know?",
                system_prompt="You are a succinct assistant."
            )
            # TODO overwritting ##########################################
            self.seen_content = [infered_master_content]  # This will be overwritten by next user if not careful
            self.user_states[chat_id]['seen_content'] = [infered_master_content]
            self._clean_mastered(chat_id)  # Ensure this operates on user-specific data if needed
            logging.info(f"Inferred seen content for {chat_id}.")

            self.user_states[chat_id]['step'] = 'awaiting_lesson_preference'
            self.bot.send_message(
                chat_id,
                "Do you have any preference to what you would like to start learning? "
                "If you do not know, you can just type 'no'."
            )
            self.bot.register_next_step_handler(message, self._process_lesson_preference_input)
            logging.info(f"Set next step for {chat_id} to _process_lesson_preference_input.")

        except Exception as err:
            logging.error(f"Error processing level for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I had trouble understanding that level. Please try again.")

    def _process_lesson_preference_input(self, message: telebot.types.Message) -> None:
        """
        Processes lesson preference and asks about optional questions.
        """
        chat_id = message.chat.id
        user_message = message.text
        if not user_message or user_message.startswith('/'):
            self.bot.send_message(chat_id, "Please provide a lesson preference or type 'no'.")
            return
        try:
            llm_output = self.model.call(
                user_prompt=f"This is the answer to the question 'about what do you want to learn?'. If it says 'no' it "
                            f"means 'start from the beginning' Output a curriculum lesson name that matches it and any "
                            f"details specified (use as few words as possible to summarize all concepts): {user_message}",
                system_prompt="You are a succinct extractor and format standardizer. "
                              "The beginner introduction class should "
                              f"cover the language writing system and general orthography."
            )
            llm_output_converted = telegramify_markdown.markdownify(
                llm_output,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.next_lesson = llm_output  # This will be overwritten by next user if not careful
            self.user_states[chat_id]['next_lesson'] = llm_output
            self.bot.send_message(chat_id, f"Okay, your initial lesson will be:\n{llm_output_converted}")
            logging.info(f"Lesson preference set for {chat_id}: {llm_output}")

            self.user_states[chat_id]['step'] = 'awaiting_optional_questions_choice'
            self.bot.send_message(
                chat_id,
                "We have a couple more questions for an optimal learning experience. Do you want to answer them? "
                "If you want to answer them type 'yes', if you want to skip them type 'no'."
            )
            self.bot.register_next_step_handler(message, self._process_optional_questions_choice)
            logging.info(f"Set next step for {chat_id} to _process_optional_questions_choice.")
        except Exception as err:
            logging.error(f"Error processing lesson preference for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I had trouble understanding that. Consider /setup again.")

    def _process_optional_questions_choice(self, message: telebot.types.Message) -> None:
        """
        Processes the choice for optional questions and either proceeds or skips.
        """
        chat_id = message.chat.id
        user_choice = message.text.lower()
        if not user_choice or user_choice.startswith('/'):
            self.bot.send_message(chat_id, "Please type 'yes' or 'no'. Or consider /setup again.")
            return

        if 'yes' in user_choice:
            self.user_states[chat_id]['step'] = 'awaiting_learned_languages'
            self.bot.send_message(
                chat_id,
                "Please list all languages you already know. "
                "If you wish to, you can specify how fluent you are in each of them."
            )
            self.bot.register_next_step_handler(message, self._process_learned_languages_input)
            logging.info(f"Set next step for {chat_id} to _process_learned_languages_input.")
        elif '/no' in user_choice or 'no' in user_choice:
            # Skip optional questions, proceed to start lesson
            self.bot.send_message(chat_id, "Okay, skipping the additional questions. Let's start your lesson!")
            logging.info(f"Skipping optional questions for {chat_id}.")
            self._start_lesson_flow(message)  # Directly call the lesson start
        else:
            self.bot.send_message(chat_id, "Invalid choice. Please type 'yes' or 'no'.")
            self.bot.register_next_step_handler(message, self._process_optional_questions_choice)  # Ask again
            logging.warning(f"Invalid optional questions choice from {chat_id}: {user_choice}. Asking again.")

    def _process_learned_languages_input(self, message: telebot.types.Message) -> None:
        """
        Processes learned languages input and asks for mastered content.
        """
        chat_id = message.chat.id
        user_message = message.text
        if not user_message or user_message.startswith('/'):
            self.bot.send_message(chat_id, "Please list your known languages or type 'None'. Try /setup again.")
            return

        try:
            lang_list = self.text2list(text=user_message,
                                       prompt_intro="This is a sentence listing languages you already know. Extract all "
                                                    "the languages mentioned.",
                                       )
            self.learned_languages = lang_list  # This will be overwritten by next user if not careful
            self.user_states[chat_id]['learned_languages'] = lang_list
            self.bot.send_message(chat_id, f"Got it. You know: {', '.join(lang_list)}.")
            logging.info(f"Learned languages set for {chat_id}: {lang_list}")

            self.user_states[chat_id]['step'] = 'awaiting_mastered_content'
            infered = ""
            current_mastered = self.user_states[chat_id].get('mastered', [])
            if current_mastered:
                infered = (f"Given your level, we inferred that you have already learned some content in this language:"
                           f" {', '.join(current_mastered)}.\n")
            self.bot.send_message(
                chat_id,
                f"{infered}Please list any other content you already know and do not wish to study again "
                f"(we will revise it later but in the meantime, we will use that content to learn new things)."
            )
            self.bot.register_next_step_handler(message, self._process_mastered_content_input)
            logging.info(f"Set next step for {chat_id} to _process_mastered_content_input.")
        except Exception as err:
            logging.error(f"Error processing learned languages for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I had trouble understanding that. Please type /learned or "
                                           "/start again.")

    def _process_mastered_content_input(self, message: telebot.types.Message) -> None:
        """
        Processes mastered content input and asks for reminder time.
        """
        chat_id = message.chat.id
        user_message = message.text
        if not user_message or user_message.startswith('/'):
            self.bot.send_message(chat_id, "Please list mastered content or type 'None'. "
                                           "Please type /mastered or /setup again.")
            return

        try:
            user_list = self.text2list(text=user_message,
                                       prompt_intro=f"This is the answer to the question 'what is the language content "
                                                    f"you studied and know?'. Extract all the content mentioned (with "
                                                    f"any necessary details). Everything must be written in "
                                                    f"{self.user_states[chat_id]['language']}",
                                       )
            self.mastered += user_list  # This will be overwritten by next user if not careful
            self.user_states[chat_id]['mastered'] = self.user_states[chat_id].get('mastered', []) + user_list
            self._clean_mastered(chat_id)  # Clean mastered content based on errors # TODO check if necessary #################################
            self.bot.send_message(chat_id, f"Acknowledged. Mastered content added.")
            logging.info(f"Mastered content set for {chat_id}.")

            self.user_states[chat_id]['step'] = 'awaiting_limitation'
            self.bot.send_message(
                chat_id,
                "Do you have any physical/cognitive/psychological/emotional/other limitations of which I should "
                "be aware? "
                "This includes but is not limited to sight impairment, hearing impairment, talking impairment, "
                "phobia, ADHD, anxiety, dyslexia, cognitive impairment, PTSD. "
                "If you are unsure, you can shortly describe what you struggle with in a learning "
                "environment or type 'no' if you have none."
            )
            self.bot.register_next_step_handler(message, self._process_limitation_input)
            logging.info(f"Set next step for {chat_id} to _process_limitation_input.")
        except Exception as err:
            logging.error(f"Error processing mastered content for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I had trouble understanding that. "
                                           "Please try /limitation or /setup again.")

    def _process_limitation_input(self, message: telebot.types.Message) -> None:
        """
        Processes limitation input and asks for lesson preference.
        """
        chat_id = message.chat.id
        user_message = message.text
        if not user_message or user_message.startswith('/'):
            self.bot.send_message(chat_id, "Please provide any limitations or type 'None'. "
                                           "Please try /limitation or /setup again.")
            return

        try:
            user_message_summary = self.model.call(
                user_prompt=f"This is the answer to the question 'do you have any limitation that restrains how a "
                            f"language class might unfold?'. Output the clinical term of any limitation(s) along with "
                            f"any necessary details (use as few words as possible to summarize all concepts): "
                            f"{user_message}",
                system_prompt="You are a succinct extractor and format standardizer."
            )
            # TODO overwritting ##########################################
            self.limitation = user_message_summary  # This will be overwritten by next user if not careful
            self.user_states[chat_id]['limitation'] = user_message_summary
            self.bot.send_message(chat_id, f"Acknowledged. Your limitations: {user_message_summary}")
            logging.info(f"Limitation set for {chat_id}: {user_message_summary}")

            self.user_states[chat_id]['step'] = 'awaiting_reminder_time'
            self.bot.send_message(
                chat_id,
                "Do you want to set a daily reminder? "
                "We will send you a message through this app, so please allow its notifications. "
                "Please say what time you would like to set the reminder (HH:MM UTC) or type 'no' to skip this step."
            )
            self.bot.register_next_step_handler(message, self._process_reminder_time_input)
            logging.info(f"Set next step for {chat_id} to _process_reminder_time_input.")
        except Exception as err:
            logging.error(f"Error processing limitation for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I had trouble understanding that limitation. Please try again.")

    def _process_reminder_time_input(self, message: telebot.types.Message) -> None:
        """
        Processes reminder time input and concludes setup, starting the lesson.
        """
        chat_id = message.chat.id
        user_message = message.text.lower()
        if not user_message or user_message.startswith('/'):
            self.bot.send_message(chat_id, "Please provide a time (HH:MM UTC) or type 'no'. "
                                           "Try /reminder or /setup again.")
            return

        try:
            user_message_summary = self.model.call(
                user_prompt=f"This is a sentence giving a time of day or saying no. If it says no, reply 'None', "
                            f"otherwise extract the given time and return it in ISO 8601 format': {user_message}",
                system_prompt="You are a succinct extractor and format standardizer."
            )
            if "None" in user_message_summary:
                self.reminder_time = None  # This will be overwritten by next user if not careful
                self.user_states[chat_id]['reminder_time'] = None
                self.bot.send_message(chat_id, "Reminder skipped.")
                logging.info(f"Reminder skipped for {chat_id}.")
            else:
                parsed_time = parser.parse(user_message_summary).strftime("%H:%M")
                self.reminder_time = parsed_time  # This will be overwritten by next user if not careful
                self.user_states[chat_id]['reminder_time'] = parsed_time
                # Schedule the reminder, job will be stored in a user-specific way if needed for cancellation
                job = schedule.every().day.at(parsed_time).do(
                    partial(self._send_reminder_callback, chat_id)
                )
                # TODO check if true ############################################################
                # Store job object if you want to cancel it later for this user
                # For simplicity, if multiple users set reminders for the same time,
                # schedule will create multiple jobs. If you need to track per-user jobs,
                # you'd need a more complex structure in self.reminders.
                # For now, let's assume reminder_job is for the current user's last set reminder.
                self.reminder_job = job  # This will be overwritten by next user if not careful
                self.bot.send_message(chat_id, f"Daily reminder set for {parsed_time} UTC!")
                logging.info(f"Reminder set for {chat_id} at {parsed_time}.")

            self.user_states[chat_id]['step'] = 'setup_complete'
            self.bot.send_message(chat_id, "Setup complete! Let's start your first lesson.")
            logging.info(f"Setup complete for {chat_id}.")
            self._start_lesson_flow(message)  # Proceed to start the lesson
        except ValueError:
            self.bot.send_message(chat_id, "Invalid time format. Please use HH:MM UTC (e.g., 14:30) or type 'no'.")
            self.bot.register_next_step_handler(message, self._process_reminder_time_input)  # Ask again
            logging.warning(f"Invalid reminder time format from {chat_id}: {user_message}. Asking again.")
        except Exception as err:
            logging.error(f"Error processing reminder time for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I had trouble setting the reminder. "
                                           "Please try /reminder or /setup again.")

    def _start_lesson_flow(self, message: telebot.types.Message) -> None:
        """
        Starts the first lesson after setup is complete.
        """
        chat_id = message.chat.id
        # Use user-specific data from user_states
        user_language = self.user_states[chat_id]['language']
        user_level = self.user_states[chat_id]['level']
        user_limitation = self.user_states[chat_id].get('limitation', 'None')
        user_learned_languages = self.user_states[chat_id].get('learned_languages', ['english (default)'])
        user_mastered = self.user_states[chat_id].get('mastered', [])
        user_seen_content = self.user_states[chat_id].get('seen_content', [])

        # Simulate initial lesson content generation
        initial_lesson_name = self.user_states[chat_id].get('next_lesson', 'a general introductory topic')
        self.bot.send_message(chat_id, f"Starting your lesson on:\n{initial_lesson_name}")
        logging.info(f"Starting lesson flow for {chat_id} on {initial_lesson_name}")

        # Directly call _handle_new_lesson_command as if user typed /new
        # We need to pass the current message to it.
        self._handle_new_lesson_command(message)

    # --- Command Handlers (can be called directly by user at any time) ---

    def _handle_language_command(self, message: telebot.types.Message) -> None:
        chat_id = message.chat.id
        self.bot.send_message(chat_id, "What language do you want to learn or practice?")
        self.bot.register_next_step_handler(message, self._process_language_input) # Reuse existing processor
        logging.info(f"User {chat_id} initiated /language command.")

    def _handle_level_command(self, message: telebot.types.Message) -> None:
        chat_id = message.chat.id
        self.bot.send_message(chat_id, "What is your language proficiency/level?")
        self.bot.register_next_step_handler(message, self._process_level_input) # Reuse existing processor
        logging.info(f"User {chat_id} initiated /level command.")

    def _handle_limitation_command(self, message: telebot.types.Message) -> None:
        chat_id = message.chat.id
        self.bot.send_message(chat_id, "Do you have any limitations I should be aware of?")
        self.bot.register_next_step_handler(message, self._process_limitation_input) # Reuse existing processor
        logging.info(f"User {chat_id} initiated /limitation command.")

    def _handle_lesson_command(self, message: telebot.types.Message) -> None:
        chat_id = message.chat.id
        self.bot.send_message(chat_id, "About what do you want to learn? (Type 'no' to start from the beginning).")
        self.bot.register_next_step_handler(message, self._process_lesson_preference_input) # Reuse existing processor
        logging.info(f"User {chat_id} initiated /lesson command.")

    def _handle_learned_command(self, message: telebot.types.Message) -> None:
        chat_id = message.chat.id
        self.bot.send_message(chat_id, "Please list all languages you already know.")
        self.bot.register_next_step_handler(message, self._process_learned_languages_input) # Reuse existing processor
        logging.info(f"User {chat_id} initiated /learned command.")

    def _handle_mastered_command(self, message: telebot.types.Message) -> None:
        chat_id = message.chat.id
        self.bot.send_message(chat_id, "Please list any other content you already know and do not wish to study again.")
        self.bot.register_next_step_handler(message, self._process_mastered_content_input) # Reuse existing processor
        logging.info(f"User {chat_id} initiated /mastered command.")

    def _handle_reminder_command(self, message: telebot.types.Message) -> None:
        chat_id = message.chat.id
        self.bot.send_message(chat_id, "What time would you like to set the daily reminder (HH:MM UTC)? Or type 'no' to skip.")
        self.bot.register_next_step_handler(message, self._process_reminder_time_input) # Reuse existing processor
        logging.info(f"User {chat_id} initiated /reminder command.")

    def _clean_mastered(self, chat_id: int) -> None:
        """
        Given the newly learned content and the lesson errors, updates the mastered content.
        Operates on user-specific data.
        """
        user_seen_content = self.user_states[chat_id].get('seen_content', [])
        user_lesson_errors = self.user_states[chat_id].get('lesson_errors', [])
        user_mastered = self.user_states[chat_id].get('mastered', [])
        user_language = self.user_states[chat_id].get('language', 'English')  # Default for LLM prompt

        try:
            user_list = self.text2list(
                text=f"STUDIED_CONTENT:{', '.join(user_seen_content)}\n\nERRORS:\n"
                     f"[{', '.join(user_lesson_errors)}]\n\nMASTERED_CONTENT:\n"
                     f"[{', '.join(user_mastered)}]",
                prompt_intro=f"Given the STUDIED_CONTENT, the ERRORS, and the MASTERED_CONTENT "
                             f"acquired in a {user_language} foreign language course, remove "
                             f"from the MASTERED_CONTENT list all content that the errors prove "
                             f"it is not mastered yet (take only into account serious errors).",
            )
            self.user_states[chat_id]['mastered'] = user_list
            logging.info(f"Mastered content cleaned for {chat_id}.")
        except Exception as err:
            logging.error(f"Error cleaning mastered content for {chat_id}: {err}", exc_info=True)

    def _send_reminder_callback(self, chat_id: int) -> None:
        """
        Send a reminder message. This is called by the scheduler.
        """
        # Ensure reminder is sent only once per day per user
        if not self.reminded_today.get(chat_id, False):
            user_data = self.user_states.get(chat_id, {})
            user_seen_content = user_data.get('seen_content', [])
            user_language = user_data.get('language', 'English')
            user_level = user_data.get('level', 'Beginner')
            user_learned_languages = user_data.get('learned_languages', ['English'])

            current_lesson = user_seen_content[-1] if len(user_seen_content) > 0 else "your last lesson"
            try:
                reminder_text = self.model.call(
                    user_prompt=f"Write a reminder message for the user reminding them to study "
                                f"{current_lesson} in {user_language}. This is the user's level: {user_level}. "
                                f"If the level that of a beginner or less, you can write it in {user_learned_languages[0]} or in "
                                f"English, if the level is above beginner write it in {user_language} but keep it "
                                f"aligned with the level of the user.",
                    system_prompt="You are a succinct teaching assistant writing a text message."
                )
                self.bot.send_message(chat_id, reminder_text)
                self.reminded_today[chat_id] = True # Mark as reminded for today
                logging.info(f"Sent reminder to chat ID {chat_id}.")
            except Exception as err:
                logging.error(f"Error sending reminder to chat ID {chat_id}: {err}", exc_info=True)
                # If bot was blocked by user, schedule.CancelJob can be returned by the job itself
                # if the exception is caught there.

    # --- Lesson Flow Handlers ---

    def _handle_new_lesson_command(self, message: telebot.types.Message) -> None:
        """
        Handles '/new' command. Generates a new lesson curriculum.
        """
        chat_id = message.chat.id
        # Ensure user data is available
        if chat_id not in self.user_states or 'language' not in self.user_states[chat_id]:
            self.bot.send_message(chat_id, "Please complete the setup first by typing /setup.")
            logging.warning(f"User {chat_id} tried /new without setup.")
            return

        user_data = self.user_states[chat_id]
        user_language = user_data['language']
        user_level = user_data['level']
        user_limitation = user_data.get('limitation', 'None')
        user_learned_languages = user_data.get('learned_languages', ['english (default)'])
        user_mastered = user_data.get('mastered', [])
        user_seen_content = user_data.get('seen_content', [])
        user_errors = user_data.get('lesson_errors', [])
        current_lesson_name = user_data.get('next_lesson', 'a general introductory topic')

        # Clear lesson history for new lesson
        self.lesson_history = []
        # Add user's command to history
        self.lesson_history.append({"role": "user", "content": "/new"})

        try:
            lesson_content_list = self.text2list(
                text=f"PREVIOUSLY SEEN CONTENT:\n{', '.join(user_seen_content)}\n\n"
                     f"MASTERED CONTENT:\n{', '.join(user_mastered)}",
                prompt_intro=f"Prepare a lesson curriculum segmented section by section"
                             f"for a lesson in {user_language} about {current_lesson_name} for a student of "
                             f"level {self.level}. Write it all in {user_language}.",
                system_prompt=f"You are a great {user_language} teacher preparing a list "
                              f"of step-by-step sections for a class.",
            )
            if not lesson_content_list:
                self.bot.send_message(chat_id, "Could not generate lesson sections. Please try again.")
                logging.error(f"LLM returned empty lesson_content_list for {chat_id}.")
                return

            self.lesson_sections = lesson_content_list # This will be overwritten by next user if not careful
            self.user_states[chat_id]['lesson_sections'] = lesson_content_list
            self.bot.send_message(chat_id, f"We are preparing your lesson.\n"
                                           f"Wait for a moment please...")
            logging.info(f"New lesson '{current_lesson_name}' started for {chat_id}.")

            # Update seen_content with the new lesson topic
            self.seen_content.append(current_lesson_name)  # This will be overwritten by next user if not careful
            self.user_states[chat_id]['seen_content'] = self.user_states[chat_id].get('seen_content', []) + [current_lesson_name]

            # Determine the next lesson topic for future use
            next_lesson_topic = self.model.call(
                user_prompt=f"You are teaching a {user_language} class in writing form. "
                            f"You must determine what is the next lesson to study, given your students have already "
                            f"learned about the following:\n{', '.join(self.user_states[chat_id]['seen_content'])}",
                system_prompt=f"You are a succinct language teacher assistant",
            )
            self.next_lesson = next_lesson_topic  # This will be overwritten by next user if not careful
            self.user_states[chat_id]['next_lesson'] = next_lesson_topic
            logging.info(f"Next lesson determined for {chat_id}: {next_lesson_topic}")

            # Move to the first section of the lesson
            self._handle_next_section_content(message)  # Call directly to send first section
            # self.bot.register_next_step_handler(message, next_step_callable) # Only register if you want to wait for user to type /next
            logging.info(f"Initiated first lesson section for {chat_id}.")

        except Exception as err:
            logging.error(f"Error handling /new lesson for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't start a new lesson. Please try again.")

    def _handle_next_section_command(self, message: telebot.types.Message) -> None:
        """
        Handles '/next' command. Proceeds to the next section of the current lesson.
        This is a direct command handler, not a next_step_handler.
        """
        chat_id = message.chat.id
        if chat_id not in self.user_states or 'lesson_sections' not in self.user_states[chat_id]:
            self.bot.send_message(chat_id, "Please start a lesson first with /new.")
            return

        self.lesson_history.append({"role": "user", "content": "/next"})  # Add to history
        self._handle_next_section_content(message)  # Call the internal function

    def _handle_next_section_content(self, message: telebot.types.Message) -> None:
        """
        Internal function to send the next lesson section content.
        """
        chat_id = message.chat.id
        user_data = self.user_states[chat_id]
        lesson_sections = user_data.get('lesson_sections', [])

        if not lesson_sections:
            self.bot.send_message(chat_id, "You've completed all sections for this lesson! "
                                           "Type /new to start a new lesson.")
            logging.info(f"All lesson sections completed for {chat_id}.")
            return

        # Use pop(0) to get the first element and remove it (queue-like behavior)
        lesson_elem = lesson_sections.pop(0)
        user_data['lesson_sections'] = lesson_sections  # Update the user's state

        # Prepare LLM prompt with user-specific data
        course_language = user_data['language']
        user_level = user_data['level']
        user_limitation = user_data.get('limitation', 'None')
        user_learned_languages = user_data.get('learned_languages', ['english (default)'])
        user_mastered = user_data.get('mastered', [])
        user_seen_content = user_data.get('seen_content', [])
        user_errors = user_data.get('lesson_errors', [])
        current_lesson_name = user_data.get('next_lesson', 'Current Topic')  # Use next_lesson as current topic

        try:
            lesson_content = self.model.call(
                user_prompt=f"You are teaching a {course_language} class for a student of "
                            f"level {self.level}. You must teach about {lesson_elem}."
                            f"If it matches the subject, you should focus on practicing one or more of the "
                            f"following abilities {random.choices(self.abilities, k=4)}.\n"
                            f"The student only knows about:"
                            f"Previous lessons: {', '.join(user_seen_content)}\n"
                            f"Mastered content: {', '.join(user_mastered)}\n",
                system_prompt=f"You are a great foreign language teacher teaching a 1-on-1 {course_language} class ",
                history=self.lesson_history  # Pass history for conversational context
                # max_tokens=3000,  # Limit the response length # TODO check if necessary to save time ##############################
            )
            self.bot.send_message(chat_id, "⏳ Halfway done...")
            lesson_content = self.model.call(
                user_prompt=f"You are adapting the following {course_language} class to some specifications. "
                            f"The level is {user_level}. "
                            f"Use emojis and simple ASCII pictures to illustrate concepts, actions and ideas. "
                            f"The lesson's content must be in {course_language}."
                            f"HOWEVER, explanations and instructions must be in English for beginner levels "
                            f"and in {course_language} for higher levels.\n"
                            f"CLASS:\n{lesson_content}\n",
                system_prompt=f"You are a foreign language teacher that uses a textual medium to teach their class. "
                              f"Use raw markdown formatting.",
                history=self.lesson_history  # Pass history for conversational context
                # max_tokens=3000,  # Limit the response length # TODO check if necessary to save time ##############################
            )
            self.bot.send_message(chat_id, "⌛ Almost done...")
            lesson_content = self.model.call(
                user_prompt=f"You are adapting the following class to your specific "
                            f"student's profile:\n"
                            f"Limitations: {user_limitation}\nOther known languages:{', '.join(user_learned_languages)}\n"
                            f"Mastered content: {', '.join(user_mastered)}\n"
                            f"Errors and weaknesses: {', '.join(user_errors)}\n"                            
                            f"You may compare to other languages the student knows: "
                            f"{', '.join(user_data.get('learned_languages', ['English']))}.\n "
                            f"CLASS:\n{lesson_content}\n",
                system_prompt=f"You are a {course_language} teacher adapting his a lesson to its student's profile."
                              f"Structure must be clean and simple, foreigner-friendly and kid-friendly. "
                              f"Use raw markdown formatting.",
                history=self.lesson_history  # Pass history for conversational context
                # max_tokens=3000,  # Limit the response length # TODO check if necessary to save time ##############################
            )
            converted_content = telegramify_markdown.markdownify(
                lesson_content,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.bot.send_message(chat_id, converted_content)
            self.lesson_history.append({"role": "system", "content": lesson_content})
            logging.info(f"Sent lesson section '{lesson_elem}' to {chat_id}.")

            # Send reminder of commands
            self.bot.send_message(chat_id, "Type '/next' to move forward, '/more' to get more details, "
                                           "'/better' for a clearer version, '/question' to ask a question, "
                                           "'/conversation' to start a conversation.")
        except Exception as err:
            logging.error(f"Error generating/sending lesson section for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't generate the next lesson section. Please try /next again.")

    def _handle_more_details_command(self, message: telebot.types.Message) -> None:
        """
        Handles '/more' command. Gets more details on the last lesson content.
        """
        chat_id = message.chat.id
        if not self.lesson_history:
            self.bot.send_message(chat_id, "There's no active lesson to get more details about. "
                                           "Please start a lesson with /new.")
            return

        self.lesson_history.append({"role": "user", "content": "/more"})
        user_data = self.user_states[chat_id]
        user_language = user_data.get('language', 'English')
        try:
            lesson_content = self.model.call(
                user_prompt=f"You are teaching a {user_language} class in writing form for a student of "
                            f"level {self.level}. "
                            f"If the student is a beginner, you can use English to explain some difficult concepts"
                            f"If the student is above beginner level, you should avoid using other "
                            f"languages apart from {user_language} but keep the structure clean and simple. "
                            f"If necessary to make comparisons or to guide their pronunciation, the student also "
                            f"speaks {', '.join(user_data.get('learned_languages', ['English']))}.\n "
                            f"Your student asks you to give more details and "
                            f"go deeper into the subject of your last lesson:\n{self.lesson_history[-2]['content']}", # Refer to previous bot message
                system_prompt=f"You are a great foreign language teacher teaching a 1-on-1 {user_language} class "
                              f"through a textual medium, such as a messaging app. Use raw markdown formatting.",
                history=self.lesson_history,  # Pass history for conversational context
            )
            converted_content = telegramify_markdown.markdownify(
                lesson_content,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.bot.send_message(chat_id, converted_content)
            self.lesson_history.append({"role": "system", "content": lesson_content})
            logging.info(f"Sent more details to {chat_id}.")
            self.bot.send_message(chat_id, "Type '/next' to move forward, '/more' to get more details, "
                                           "'/better' for a clearer version, '/question' to ask a question, "
                                           "'/conversation' to start a conversation.")
        except Exception as err:
            logging.error(f"Error handling /more for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't provide more details. Please try again.")

    def _handle_better_explanation_command(self, message: telebot.types.Message) -> None:
        """
        Handles '/better' command. Provides a clearer version of the last lesson content.
        """
        chat_id = message.chat.id
        if not self.lesson_history:
            self.bot.send_message(chat_id, "There's no active lesson to explain better. "
                                           "Please start a lesson with /new.")
            return

        self.lesson_history.append({"role": "user", "content": "/better"})
        user_data = self.user_states[chat_id]
        user_language = user_data.get('language', 'English')
        try:
            lesson_content = self.model.call(
                user_prompt=f"You are teaching a {user_language} class in writing form for a student of "
                            f"level {self.level}."
                            f"If the student is a beginner, you can use English to explain some difficult concepts"
                            f"If the student is above beginner, you should avoid using other "
                            f"languages apart from {user_language} but keep the structure clean and simple. "
                            f"If necessary to make comparisons or to guide their pronunciation, the student also "
                            f"speaks {', '.join(user_data.get('learned_languages', ['English']))}.\n "
                            f"Your student did not understand your last message."
                            f"Try to explain it in a simpler and clearer way, with more and simpler examples:\n"
                            f"{self.lesson_history[-2]['content']}", # Refer to previous bot message
                system_prompt=f"You are a great foreign language teacher teaching a 1-on-1 {user_language} class "
                              f"through a textual medium, such as a messaging app. Use raw markdown formatting.",
                history=self.lesson_history, # Pass history for conversational context
            )
            converted_content = telegramify_markdown.markdownify(
                lesson_content,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.bot.send_message(chat_id, converted_content)
            self.lesson_history.append({"role": "system", "content": lesson_content})
            logging.info(f"Sent better explanation to {chat_id}.")
            self.bot.send_message(chat_id, "Type '/next' to move forward, '/more' to get more details, "
                                           "'/better' for a clearer version, '/question' to ask a question, "
                                           "'/conversation' to start a conversation.")
        except Exception as err:
            logging.error(f"Error handling /better for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't provide a clearer explanation. Please try again.")

    def _handle_question_command(self, message: telebot.types.Message) -> None:
        """
        Handles '/question' command. Prompts user for their question.
        """
        chat_id = message.chat.id
        if not self.lesson_history:
            self.bot.send_message(chat_id, "There's no active lesson to ask a question about. "
                                           "Please start a lesson with /new.")
            return

        self.lesson_history.append({"role": "user", "content": "/question"})
        user_data = self.user_states[chat_id]
        user_language = user_data.get('language', 'English')
        try:
            please_ask_your_question = self.model.call(
                user_prompt=f"You are teaching a {user_language} class. A student has a question."
                            f"Respectfully ask the student to please ask their question."
                            f"Say it in {user_language}, {', '.join(user_data.get('learned_languages', ['English']))}",
                system_prompt=f"You are a great foreign language teacher.",
            )
            converted_please_ask_your_question = telegramify_markdown.markdownify(
                please_ask_your_question,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.bot.send_message(chat_id, converted_please_ask_your_question)
            # Register next step to get the actual question
            self.bot.register_next_step_handler(message, partial(self._process_user_question, chat_id))
            logging.info(f"Prompted {chat_id} to ask question.")
        except Exception as err:
            logging.error(f"Error prompting for question for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't prepare for your question. Please try again.")

    def _process_user_question(self, chat_id: int, message: telebot.types.Message) -> None:
        """
        Processes the user's actual question and provides an answer.
        """
        user_question = message.text
        if not user_question or user_question.startswith('/'):
            self.bot.send_message(chat_id, "That doesn't look like a valid question. Please try /question again.")
            return

        self.lesson_history.append({"role": "user", "content": user_question}) # Add actual question to history
        user_data = self.user_states[chat_id]
        user_language = user_data.get('language', 'English')
        try:
            question_answer = self.model.call(
                user_prompt=f"You are teaching a {user_language} class in writing form. Your student has a question "
                            f"about the last lesson section. Please reply and explain it in the easiest way to "
                            f"understand.\nQUESTION: {user_question}\n\n"
                            f"LESSON SECTION: {self.lesson_history[-3]['content']}", # Refer to bot's last lesson content
                system_prompt=f"You are a great foreign language teacher teaching a 1-on-1 {user_language} class "
                              f"through a textual medium, such as a messaging app. Use raw markdown formatting.",
                history=self.lesson_history, # Pass history for conversational context
            )
            converted_content = telegramify_markdown.markdownify(
                question_answer,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.bot.send_message(chat_id, converted_content)
            self.lesson_history.append({"role": "system", "content": question_answer})
            logging.info(f"Answered question for {chat_id}.")
            self.bot.send_message(chat_id, "Type '/next' to move forward, '/more' to get more details, "
                                           "'/better' for a clearer version, '/question' to ask a question, "
                                           "'/conversation' to start a conversation.")
        except Exception as err:
            logging.error(f"Error answering question for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't answer your question. Please try again.")


    def _handle_conversation_command(self, message: telebot.types.Message) -> None:
        """
        Handles '/conversation' command. Prompts user for a role or to type 'no'.
        """
        chat_id = message.chat.id
        if not self.lesson_history:
            self.bot.send_message(chat_id, "There's no active lesson to start a conversation about. "
                                           "Please start a lesson with /new.")
            return

        self.lesson_history.append({"role": "user", "content": "/conversation"})
        user_data = self.user_states[chat_id]
        user_language = user_data.get('language', 'English')
        try:
            personna_request = self.model.call(
                user_prompt=f"You are teaching a {user_language} class. A student wants to start a conversation."
                            f"Ask them if they want you to adopt a specific role. "
                            f"Say it in {user_language}, {', '.join(user_data.get('learned_languages', ['English']))}",
                system_prompt=f"You are a succinct assistant.",
            )
            self.bot.send_message(chat_id, personna_request)
            # Register next step to get the role or 'no'
            self.bot.register_next_step_handler(message, partial(self._process_conversation_role, chat_id))
            logging.info(f"Prompted {chat_id} for conversation role.")
        except Exception as err:
            logging.error(f"Error prompting for conversation role for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't start a conversation. Please try again.")

    def _process_conversation_role(self, chat_id: int, message: telebot.types.Message) -> None:
        """
        Processes the user's chosen role for conversation and starts it.
        """
        user_role_choice = message.text
        if not user_role_choice or user_role_choice.startswith('/'):
            self.bot.send_message(chat_id, "That doesn't look like a valid role. Please try /conversation again.")
            return

        user_data = self.user_states[chat_id]
        user_level = user_data.get('level', 'Beginner')
        user_language = user_data.get('language', 'English')
        try:
            persona = self.model.call(
                user_prompt=f"Come up with a persona character."
                            f"You have been asked to make the following character: {user_role_choice}."
                            f"If that says 'no' or that 'they don't care', come up with a randon persona"
                            f"You only speaks in {user_language}.",
                system_prompt=f"You are a succinct assistant that creates a persona character.",
            )
            self.lesson_history.append({"role": "user", "content": f"We will start a conversation in {user_language} "
                                                                   f"and you will play the role of "
                                                                   f"{persona}"})  # Add role choice to history
            conversation_starter = self.model.call(
                user_prompt=f"Come up with a common conversation starter in {user_language}."
                            f"You are playing the following role: {user_role_choice}\n"
                            f"Adjust your language level to the other person's level: {user_level}. "
                            f"The conversation must treat the topics of: {self.lesson_history[-3]['content']}",
                system_prompt=f"You are a person interested in starting a conversation.",
                history=self.lesson_history,  # Pass history for conversational context
            )
            converted_content = telegramify_markdown.markdownify(
                conversation_starter,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.bot.send_message(chat_id, "You have now started a conversation, to quit it type /exit.")
            self.bot.send_message(chat_id, converted_content)
            self.lesson_history.append({"role": "system", "content": conversation_starter})

            # Register next step
            self.bot.register_next_step_handler(message, partial(self._continue_conversation, chat_id))
            logging.info(f"Started conversation for {chat_id} with role '{user_role_choice}'.")
        except Exception as err:
            logging.error(f"Error starting conversation for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't start a conversation. Please try again.")

    def _continue_conversation(self, chat_id: int, message: telebot.types.Message) -> None:
        """
        Processes the user's input during an ongoing conversation.
        """
        user_role_choice = message.text
        if '/exit' in user_role_choice.lower():
            self.bot.send_message(chat_id, "You have exited the conversation. "
                                           "Type /conversation to start a new one or type /new, /next, /more, /better, "
                                           "or /question to go back to the current lesson.")
            logging.info(f"User {chat_id} exited the conversation.")
            return
        elif not user_role_choice or user_role_choice.startswith('/'):
            self.bot.send_message(chat_id, "I'm sorry, I do not understand that command. "
                                           "Please start over by typing /conversation or "
                                           "check the list of commands by typing /help.")
            return

        user_data = self.user_states[chat_id]
        user_level = user_data.get('level', 'Beginner')
        user_language = user_data.get('language', 'English')
        leitmotiv = (f" The topics the conversation should cover are: {', '.join(self.seen_content)}, "
                     f"{', '.join(self.mastered)}") if random.randint(0, 4) != 0 else ""
        try:
            conversation_follow_up = self.model.call(
                user_prompt=f"Come up with a follow-up for the conversation in {user_language}."
                            f"You are playing the following role: {user_role_choice}\n"
                            f"Adjust your language level to the other person's level: {user_level}.{leitmotiv}",
                system_prompt=f"You are a curious person, you are not excessively chatty but you are interested in "
                              f"having a conversation. Your phraseology is very natural, native and oral-like.",
                history=self.lesson_history,  # Pass history for conversational context
            )
            converted_conversation_follow_up = telegramify_markdown.markdownify(
                conversation_follow_up,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.bot.send_message(chat_id, converted_conversation_follow_up)
            self.lesson_history.append({"role": "system", "content": conversation_follow_up})

            # Register next step
            self.bot.register_next_step_handler(message, partial(self._continue_conversation, chat_id))
            logging.info(f"Started conversation for {chat_id} with role '{user_role_choice}'.")
        except Exception as err:
            logging.error(f"Error starting conversation for {chat_id}: {err}", exc_info=True)
            self.bot.send_message(chat_id, "Sorry, I couldn't start a conversation. Please try again.")

    def _handle_data_command(self, message: telebot.types.Message) -> None:
        """
        Handles the '/data' command, providing the personal data about the user.
        """
        chat_id = message.chat.id
        data_text = (
            f"Here is the data that you provided:\n\n"
            f"🔖 Language - {self.language}\n"
            f"🔖 Level - {self.level}\n"
            f"🔖 Personal limitations - {self.limitation}\n"
            f"🔖 Previously studied languages - {', '.join(self.learned_languages)}\n"
            f"🔖 Daily reminder - {self.reminder_time}\n"
            f"🔖 Mastered content - {', '.join(self.mastered)}\n"
            f"🔖 Previously studied lessons - {', '.join(self.seen_content)}\n"
        )
        self.bot.send_message(chat_id, data_text)
        logging.info(f"Sent /data to chat ID: {chat_id}")

    def _handle_help_command(self, message: telebot.types.Message) -> None:
        """
        Handles the '/help', '/info', '/documentation' command, providing a list and explanation of available commands.
        """
        chat_id = message.chat.id
        help_text = (
            "Here are the commands you can use:\n\n"
            "📌 /language - Change the language you are learning.\n"
            "📌 /level - Update your language proficiency level.\n"
            "📌 /limitation - Update any learning limitations.\n"
            "📌 /lesson - Choose a specific lesson topic.\n"
            "📌 /learned - List languages you already know.\n"
            "📌 /mastered - List content you've already mastered.\n"
            "📌 /reminder - Set or change your daily study reminder time.\n\n"
            "📌 /setup - Begin or restart the language setup (questions).\n"
            "    📌 /new or /start - Start a brand new lesson.\n"
            "    📌 /next - Move to the next section of the current lesson.\n"
            "    📌 /more - Get more details on the current lesson content.\n"
            "    📌 /better - Get a simpler explanation of the current content.\n"
            "    📌 /question - Ask a question about the current lesson.\n"
            "📌 /conversation - Start a conversation practice.\n\n"
            "    📌 /exit - Exit the conversation practice.\n\n"
            "📌 /data - Shows the data used to tailor the lessons to your needs.\n\n"
            "📌 /help or /info or /documentation - Show this list of commands."
        )
        self.bot.send_message(chat_id, help_text)
        logging.info(f"Sent /help to chat ID: {chat_id}")

    def _handle_all_messages(self, message: telebot.types.Message) -> None:
        """
        Generic handler for all other text messages not caught by specific handlers.
        """
        chat_id = message.chat.id
        user_text = message.text
        logging.info(f"Received unhandled message '{user_text}' from chat ID: {chat_id}")

        # If a next_step_handler is active, it will override this.
        # This handler only fires if no specific command or next_step_handler is waiting.
        self.bot.send_message(
            chat_id,
            f"I'm not sure how to respond to '{user_text}'. "
            "If you're in the middle of a setup, please provide the expected input. "
            "Otherwise, type /help to see all available commands."
        )

    # --- IBot Interface Implementations ---
    def send(self, chat_id: int, message_content: str) -> str:
        try:
            self.bot.send_message(chat_id, message_content)
            logging.info(f"Sent message to chat ID {chat_id}: '{message_content}'")
            return "Message sent successfully."
        except Exception as err:
            logging.error(f"Failed to send message to chat ID {chat_id}: {err}")
            return f"Failed to send message: {err}"

    def get(self) -> str:
        logging.warning("The 'get' method for TelegramBot is a placeholder as it operates via event handlers.")
        return "Telegram bot operates via event handlers, not direct 'get' calls for user input."


# Example Usage:
if __name__ == "__main__":
    try:
        # Ensure TELEGRAM_BOT_TOKEN and OPENAI_API_KEY are set as environment variables
        # For example, in your terminal before running:
        # export TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN_HERE"
        # export OPENAI_API_KEY="YOUR_OPENAI_API_KEY_HERE"

        bot_instance = TelegramBot() # Will try to load from env vars by default
        # Or pass directly:
        # bot_instance = TelegramBot(
        #     telegram_api_key_or_path="YOUR_TELEGRAM_BOT_TOKEN",
        #     model_api_key_or_path="YOUR_OPENAI_API_KEY"
        # )
        bot_instance.run()

    except ValueError as e:
        logging.error(f"Configuration Error: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)

