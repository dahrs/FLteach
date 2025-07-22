import os
import json
import random
from abc import ABC
from typing import List
from dateutil import parser
from datetime import datetime
from functools import partial

import telebot
import schedule
import telegramify_markdown
from telegramify_markdown import customize

from FLteach.bot.bot import IBot
from FLteach.llm.openai_api import OpenaiApi

customize.Symbol.head_level_1 = "📌"
customize.Symbol.link = "🔗"
customize.strict_markdown = True  # to use __underline__ as underline, set it to False, or it will be converted to bold
customize.cite_expandable = True  # to enable expandable citation, set it to True.


class TelegramBot(IBot, ABC):
    # TODO: make TEACHER BOT class that inherits from this one
    """
    A simple Telegram bot class that uses the Telebot library.
    It initializes the bot with a token from environment variables.
    """
    def __init__(self,
                 telegram_api_key_or_path: str | None = None,
                 model_api_key_or_path: str | None = None,
                 ) -> None:
        """
        Initializes the Telegram bot with the token from environment variables.
        """
        self.api_key: str
        if telegram_api_key_or_path is not None and os.path.isfile(telegram_api_key_or_path):
            self.api_key = open(telegram_api_key_or_path).read()
        elif isinstance(telegram_api_key_or_path, str):
            self.api_key = telegram_api_key_or_path
        else:
            self.api_key = os.environ["OPENAI_API_KEY"]
        self.bot = self.initialize_bot()
        self.logging = {}  # keep track of user states
        self.language = None
        self.level = None
        self.limitation = None
        self.next_lesson = None
        self.lesson_sections = []
        self.lesson_history = []
        self.abilities = [
            "Listening comprehension",
            "Oral production",
            "Reading comprehension",
            "Written production",
            "Reading comprehension",
            "Culture and Pragmatics",
            "Vocabulary Acquisition",
            "Grammar and syntax",
            "Phonetics and Pronunciation",
            "Orthography and Spelling",
            "Consolidation of previously learned content",
            "Refinement of previously learned content",
            "Discourse Analysis & Cohesion",
            "Practice games and exercises",
            "Practice weak points",
        ]
        self.learned_languages = ['english (default)']
        self.reminder = datetime.now().strftime("%H:%M")
        self.reminder_job = None
        self.reminded = False
        self.lesson_errors = []
        self.seen_content = []
        self.mastered = []
        self.setup_step = True
        self.model = OpenaiApi(api_key_or_path=model_api_key_or_path)
        self._register_handlers()

    def text2list(self,
                  text: str,
                  prompt_intro: str | None = None,
                  system_prompt: str = "You are a succinct element extractor, and format standardizer.",
                  ) -> List[str]:
        prompt_intro = "Extract all listed elements and return them as a JSON array of strings" if prompt_intro is None else prompt_intro
        user_message_summary = self.model.call(
            user_prompt=f"{prompt_intro}: {text}",
            system_prompt=f"{system_prompt} You must return a JSON array of strings."
        )
        return json.loads(user_message_summary)

    def initialize_bot(self) -> telebot.TeleBot:
        """
        Initializes the Telegram bot
        """
        return telebot.TeleBot(self.api_key)

    def run(self) -> None:
        """
        Starts the bot's polling loop. This makes the bot listen for incoming messages.
        """
        print("Starting the Telegram bot.")
        self.bot.polling(none_stop=True)

    def _register_handlers(self):
        """
        Registers the message handlers for the bot.
        """
        # Register handler for '/start' commands
        self.bot.message_handler(commands=['start', 'restart'])(self._handle_start)
        # Register handler for '/language' commands
        self.bot.message_handler(commands=['language'])(self._handle_language)
        # Register handler for '/level' commands
        self.bot.message_handler(commands=['level'])(self._handle_level)
        # Register handler for '/limitation' commands
        self.bot.message_handler(commands=['limitation'])(self._handle_limitation)
        # Register handler for '/lesson' commands
        self.bot.message_handler(commands=['lesson'])(self._handle_lesson)
        # Register handler for '/learned' commands
        self.bot.message_handler(commands=['learned'])(self._handle_learned)
        # Register handler for '/mastered' commands
        self.bot.message_handler(commands=['mastered'])(self._handle_mastered)
        # Register handler for '/reminder' commands
        self.bot.message_handler(commands=['reminder'])(self._handle_reminder)
        # Register handlers for the lesson commands
        # Register handler for '/new' commands
        self.bot.message_handler(commands=['new'])(self._handle_new)
        # Register handler for '/next' commands
        partial_callable = partial(self._handle_next, self.lesson_sections)
        self.bot.message_handler(commands=['next'])(partial_callable)
        # Register handler for '/more' commands
        self.bot.message_handler(commands=['more'])(self._handle_more)
        # Register handler for '/better' commands
        self.bot.message_handler(commands=['better'])(self._handle_better)
        # Register handler for '/question' commands
        self.bot.message_handler(commands=['question'])(self._handle_question)
        # Register handler for '/conversation' commands
        self.bot.message_handler(commands=['conversation'])(self._handle_conversation)
        # Register handler for '/help' or '/info' or '/documentation' commands
        self.bot.message_handler(commands=['/help', '/info', '/documentation'])(self._handle_help_command)
        # Register a generic handler for all other text messages
        # 'func=lambda message: True' means this handler will process any message
        # that hasn't been handled by a more specific handler (like commands).
        self.bot.message_handler(func=lambda message: True)(self._handle_all_messages)

    def _handle_language(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/language' command.
        """
        chat_id = message.chat.id
        user_message = message.text
        user_message_summary = self.model.call(
            user_prompt=f"This is the answer to the question 'what language do you want to learn?'. Output the "
                        f"standard name of the language along with any necessary details like regional "
                        f"specificities/level/register/etc. and translate it in said language (use as few words "
                        f"as possible to summarize all concepts): {user_message}",
            system_prompt="You are a succinct extractor and format standardizer.",

        )
        self.logging[chat_id] = f'setting_language_to_{user_message_summary}'
        self.language = user_message_summary

    def _handle_level(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/level' command.
        """
        chat_id = message.chat.id
        user_message = message.text
        user_message_summary = self.model.call(
            user_prompt=f"This is the answer to the question 'what is your language proficiency/level?'. Output the "
                        f"standard name of the level along with any necessary details (use as few words as possible "
                        f"to summarize all concepts): {user_message}",
            system_prompt="You are a succinct extractor and format standardizer."
        )
        self.logging[chat_id] = f'setting_level_to_{user_message_summary}'
        self.level = user_message_summary
        infered_master_content = self.model.call(
            user_prompt=f"Given a {self.level} proficiency level in {self.language}, make a list of all the content "
                        f"that a foreign language student should already know?",
            system_prompt="You are a succinct assistant."
        )
        self.seen_content = [infered_master_content]
        self._clean_mastered()

    def _handle_limitation(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/limitation' command.
        """
        chat_id = message.chat.id
        user_message = message.text
        user_message_summary = self.model.call(
            user_prompt=f"This is the answer to the question 'do you have any limitation that restrains how a language "
                        f"class might unfold?'. Output the clinical term of any limitation along with any necessary "
                        f"details (use as few words as possible to summarize all concepts): {user_message}",
            system_prompt="You are a succinct extractor and format standardizer."
        )
        self.logging[chat_id] = f'setting_limitation_to_{user_message_summary}'
        self.limitation = user_message_summary

    def _handle_lesson(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/lesson' command.
        """
        chat_id = message.chat.id
        user_message = message.text
        user_message_summary = self.model.call(
            user_prompt=f"This is the answer to the question 'about what do you want to learn?'. If it says 'no' it "
                        f"means 'start from the beginning' Output a curriculum lesson name that matches it and any "
                        f"details specified (use as few words as possible to summarize all concepts): {user_message}",
            system_prompt="You are a succinct extractor and format standardizer."
        )
        self.logging[chat_id] = f'setting_lesson_to_{user_message_summary}'
        self.next_lesson = user_message_summary
        if self.setup_step is False:
            self.bot.register_next_step_handler(message, self._handle_lesson_class)

    def _handle_learned(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/learned' command.
        """
        chat_id = message.chat.id
        user_message = message.text
        lang_list = self.text2list(text=user_message,
                                   prompt_intro="This is a sentence listing languages you already know. Extract all "
                                                "the languages mentioned.",
                                   )
        self.logging[chat_id] = f'setting_learned_languages_to_[{", ".join(lang_list)}]'
        self.learned_languages = lang_list

    def _handle_mastered(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/mastered' command.
        """
        chat_id = message.chat.id
        user_message = message.text
        user_list = self.text2list(text=user_message,
                                   prompt_intro=f"This is the answer to the question 'what is the language content "
                                                f"you studied and know?'. Extract all the content mentioned (with "
                                                f"any necessary details). If not already done, translate them to "
                                                f"{self.language}",
                                   )
        self.logging[chat_id] = f'setting_mastered_content'
        self.mastered += user_list

    def _clean_mastered(self) -> None:
        """
        Given the newly learned content and the lesson errors, updates the mastered content.
        """
        user_list = self.text2list(text=f"STUDIED_CONTENT:{', '.join(self.seen_content)}\n\nERRORS:\n"
                                        f"[{', '.join(self.lesson_errors)}]\n\nMASTERED_CONTENT:\n"
                                        f"[{', '.join(self.mastered)}]",
                                   prompt_intro=f"Given the STUDIED_CONTENT, the ERRORS, and the MASTERED_CONTENT "
                                                f"acquired in a {self.language} foreign language course, remove "
                                                f"from the MASTERED_CONTENT list all content that the errors prove "
                                                f"it is not mastered yet (take only into account serious errors).",
                                   )
        self.mastered = user_list

    def _send_reminder(self, chat_id: int) -> None:
        """
        Send a reminder message.
        """
        # send only 1 reminder per day
        if not self.reminded:
            current_lesson = self.seen_content[-1] if len(self.seen_content) > 0 else ""
            reminder_text = self.model.call(
                user_prompt=f"Write a reminder message for the user reminding them to study "
                            f"{current_lesson} in {self.language}. This is the user's level: {self.level}. "
                            f"If the level is too low, you can write it in {self.learned_languages[0]} or in "
                            f"English, otherwise write it in {self.language} but keep it aligned with the level "
                            f"of the user.",
                system_prompt="You are a succinct teaching assistant writing a text message."
            )
            self.bot.send_message(chat_id, reminder_text)
            self.logging[chat_id] = f'sending_reminder_at_{self.reminder}'

    def _set_reminded(self) -> None:
        """
        Set the reminded variable to False every day at midnight
        """
        self.reminded = False

    def _handle_reminder(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/reminder' command.
        """
        # TODO: take into account the timezone of the user
        chat_id = message.chat.id
        user_message = message.text
        user_message_summary = self.model.call(
            user_prompt=f"This is a sentence giving a time of day or saying no. If it says no, reply 'None', "
                        f"otherwise extract the given time and return it in ISO 8601 format': {user_message}",
            system_prompt="You are a succinct extractor and format standardizer."
        )
        if "None" in user_message_summary:
            self.reminder = None
        else:
            self.reminder = parser.parse(user_message_summary).strftime("%H:%M")
            self.reminder_job = schedule.every().day.at(self.reminder).do(self._send_reminder)

        self.logging[chat_id] = f'setting_reminder_to_{user_message_summary}'

    def _handle_start(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/start' and '/restart' commands.
        """
        self.language = None
        chat_id = message.chat.id
        self.logging[chat_id] = 'start'
        # ask for language
        self.bot.send_message(
            chat_id,
            "Hi! I am a Foreign language teaching agent."
            "What language do you want to learn or practice?"
        )
        # save language to the user state
        self.bot.register_next_step_handler(message, self._handle_language)
        # ask for level
        self.bot.send_message(
            chat_id,
            "Can you tell me your level of proficiency in this language?"
            "If you are unsure, you can shortly explain what you can do in this language,"
            "or just ask to start from the beginning."
        )
        # save level to the user state
        self.bot.register_next_step_handler(message, self._handle_level)
        # ask for limitations
        self.bot.send_message(
            chat_id,
            "Do you have any physical/cognitive/psychological/emotional/other limitations of which I should be "
            "aware?"
            "If you are unsure, you can shortly describe what you struggle with in a learning environment"
        )
        # save limitation to the user state
        self.bot.register_next_step_handler(message, self._handle_limitation)
        # ask for lesson
        self.bot.send_message(
            chat_id,
            "Do you have any preference to what you would like to start learning?"
            "If you do not know, you can just type '/no'."
        )
        # save lesson to the user state
        self.setup_step = True
        self.bot.register_next_step_handler(message, self._handle_lesson)
        self.setup_step = False
        # ask for optional questions
        self.bot.send_message(
            chat_id,
            "We have a couple more questions for an optimal learning experience. Do you want to answer them?"
            "If you want to answer them type '/yes', if you want to skip them type /no"
        )
        user_text = message.text
        if '/yes' in user_text.lower() or 'yes' in user_text.lower():
            # ask for learned languages
            self.bot.send_message(
                chat_id,
                "Please list all languages you already know." 
                "If you wish to, you can specify how fluent you are in each of them."
            )
            # save learned languages to the user state
            self.bot.register_next_step_handler(message, self._handle_learned)
            # ask for already mastered content
            infered = ""
            if self.mastered:
                infered = (f"Given you level, we inferred that you have already learned some content in this language:"
                           f" {', '.join(self.mastered)}.\n")
            self.bot.send_message(
                chat_id,
                f"{infered}Please list any other content you already know and do not wish to study again "
                f"(we might still reuse to learn other lessons and we revise it later)."
            )
            # save mastered to the user state
            self.bot.register_next_step_handler(message, self._handle_mastered)
            # ask for reminder time
            self.bot.send_message(
                chat_id,
                "Do you want to set a daily reminder?" 
                "We will send you a message through this app, so please allow its notifications."
                "Please say what time you would like to set the reminder or type '/no' to skip this step."
            )
            # save reminder time to the user state
            self.bot.register_next_step_handler(message, self._handle_reminder)
        # start a lesson to the user state
        self.bot.register_next_step_handler(message, self._handle_lesson_class)

    def _handle_new(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/new' command.
        """
        self.reminded = True
        prev_lessons = self.seen_content[:-1] if len(self.seen_content) < 11 else self.seen_content[-11:-1]
        current_lesson = self.seen_content[-1] if len(self.seen_content) > 0 else ""
        self.lesson_history.append({"role": "user",
                                    "content":
                                        f"/new\nStudent's profile:\nLevel: {self.level}\n Limitations: "
                                        f"{self.limitation}\nOther known languages:{self.learned_languages}\n"
                                        f"Current lesson: {current_lesson}\nPrevious lessons: {", ".join(prev_lessons)}"
                                        f"\nMastered content: {', '.join(self.mastered)}\n"
                                        f"Errors and weaknesses: {', '.join(self.lesson_errors)}\n"}),
        chat_id = message.chat.id
        lesson_content_list = self.text2list(text=f"PREVIOUSLY SEEN CONTENT:\n{', '.join(self.seen_content)}\n\n"
                                                  f"MASTERED CONTENT:\n{', '.join(self.mastered)}",
                                             prompt_intro=f"Prepare a lesson curriculum segmented section by section"
                                                          f"for a lesson in {self.language} about "
                                                          f"{self.next_lesson}. Write it all in {self.language}.",
                                             system_prompt=f"You are a great {self.language} teacher preparing a list "
                                                           f"of step-by-step sections for a class.",
                                             )
        self.lesson_sections = lesson_content_list
        self.bot.send_message(chat_id, f"New lesson started!:\n{self.next_lesson}.")
        self.logging[chat_id] = f'new_lesson_{self.next_lesson}'
        # find out the next lesson
        self.seen_content.append(self.next_lesson)
        next_lesson = self.model.call(
            user_prompt=f"You are teaching a {self.language} class in writing form. "
                        f"You must determine what is the next lesson to study, given your students have already "
                        f"learned about the following:\n{', '.join(self.seen_content)}",
            system_prompt=f"You are a succinct language teacher assistant",
        )
        self.next_lesson = next_lesson
        # set lesson history anew
        self.lesson_history = []
        # move to the first section of the lesson
        next_step_callable = partial(self._handle_next, self.lesson_sections)
        self.bot.register_next_step_handler(message, next_step_callable)

    def _handle_next(self, lesson_sections: List[str], message: telebot.types.Message) -> None:
        """
        Handles messages that are '/next' command.
        """
        self.reminded = True
        self.lesson_history.append({"role": "user", "content": "/next"})
        chat_id = message.chat.id
        if len(lesson_sections) == 0:
            self.bot.register_next_step_handler(message, self._handle_new)
        else:
            prev_lessons = self.seen_content[:-1] if len(self.seen_content) < 11 else self.seen_content[-11:-1]
            current_lesson = self.seen_content[-1] if len(self.seen_content) > 0 else ""
            lesson_elem = lesson_sections.pop()
            lesson_content = self.model.call(
                user_prompt=f"You are teaching a {self.language} class in writing form. "
                            f"You must teach about {lesson_elem}.\nYou must avoid using other languages apart from "
                            f"{self.language}, if necessary to ensure their understanding or to guide their "
                            f"pronunciation, the student also speaks {', '.join(self.learned_languages)}."
                            f"If it matches the subject, you should focus on practicing one or more of the "
                            f"following abilities {random.choices(self.abilities, k=4)}.\n"
                            f"Whenever possible, you should add little side notes and details about mnemotecnics, "
                            f"different orthography, origin stories, interesting facts, and cultural references.\n"
                            f"Take into account the student's profile:\nLevel: {self.level}\n"
                            f"Limitations: {self.limitation}\nOther known languages:{self.learned_languages}\n"
                            f"Current lesson: {current_lesson}\nPrevious lessons: {", ".join(prev_lessons)}\n"
                            f"Mastered content: {', '.join(self.mastered)}\n"
                            f"Errors and weaknesses: {', '.join(self.lesson_errors)}\n",
                system_prompt=f"You are a great foreign language teacher teaching a 1-on-1 {self.language} class "
                              f"through a textual medium, such as a messaging app. Use raw markdown formatting.",
            )
            converted_content = telegramify_markdown.markdownify(
                lesson_content,
                max_line_length=None,
                normalize_whitespace=False
            )
            self.bot.send_message(chat_id, converted_content)
            self.lesson_history.append({"role": "system", "content": lesson_content})
            self.logging[chat_id] = f'new_lesson_section_{lesson_elem}'
            # send reminder of commands
            self.bot.send_message(chat_id, "Type '/next' to move forward, '/more' to get more details, "
                                           "'/better' for a clearer version, '/question' to ask a question, "
                                           "'/conversation' to start a conversation.")

    def _handle_more(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/more' command.
        """
        self.reminded = True
        self.lesson_history.append({"role": "user", "content": "/more"})
        chat_id = message.chat.id
        lesson_content = self.model.call(
            user_prompt=f"You are teaching a {self.language} class in writing form. You must avoid using other "
                        f"languages apart from {self.language}, if necessary to ensure their understanding or "
                        f"to guide their pronunciation, the student also speaks "
                        f"{', '.join(self.learned_languages)}.\n Your student ask you to give more details and "
                        f"go deeper into the subject of your last lesson:\n{self.lesson_history[-1]['content']}",
            system_prompt=f"You are a great foreign language teacher teaching a 1-on-1 {self.language} class "
                          f"through a textual medium, such as a messaging app. Use raw markdown formatting.",
            # history=self.lesson_history,
            )
        converted_content = telegramify_markdown.markdownify(
            lesson_content,
            max_line_length=None,
            normalize_whitespace=False
        )
        self.bot.send_message(chat_id, converted_content)
        self.lesson_history.append({"role": "system", "content": lesson_content})
        current_section = self.logging[chat_id-1].split(f'new_lesson_section_')[1] if chat_id-1 in self.logging else ""
        self.logging[chat_id] = f'deepening_of_lesson_section_{current_section}'
        # send reminder of commands
        self.bot.send_message(chat_id, "Type '/next' to move forward, '/more' to get more details, "
                                       "'/better' for a clearer version, '/question' to ask a question, "
                                       "'/conversation' to start a conversation.")

    def _handle_better(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/better' command.
        """
        self.reminded = True
        self.lesson_history.append({"role": "user", "content": "/better"})
        chat_id = message.chat.id
        lesson_content = self.model.call(
            user_prompt=f"You are teaching a {self.language} class in writing form. You must avoid using other "
                        f"languages apart from {self.language}, if necessary to ensure their understanding or "
                        f"to guide their pronunciation, the student also speaks "
                        f"{', '.join(self.learned_languages)}.\n Your student did not understand your last message."
                        f"Try to explain it in a simpler and clearer way, with more and simpler examples:\n"
                        f"{self.lesson_history[-1]['content']}",
            system_prompt=f"You are a great foreign language teacher teaching a 1-on-1 {self.language} class "
                          f"through a textual medium, such as a messaging app. Use raw markdown formatting.",
            # history=self.lesson_history,
        )
        converted_content = telegramify_markdown.markdownify(
            lesson_content,
            max_line_length=None,
            normalize_whitespace=False
        )
        self.bot.send_message(chat_id, converted_content)
        self.lesson_history.append({"role": "system", "content": lesson_content})
        current_section = self.logging[chat_id-1].split(f'new_lesson_section_')[1] if chat_id-1 in self.logging else ""
        self.logging[chat_id] = f'rephrasing_of_lesson_section_{current_section}'
        # send reminder of commands
        self.bot.send_message(chat_id, "Type '/next' to move forward, '/more' to get more details, "
                                       "'/better' for a clearer version, '/question' to ask a question, "
                                       "'/conversation' to start a conversation.")

    def _handle_question(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/question' command.
        """
        self.reminded = True
        self.lesson_history.append({"role": "user", "content": "/better"})
        chat_id = message.chat.id
        please_ask_your_question = self.model.call(
            user_prompt=f"You are teaching a {self.language} class. A student has a question."
                        f"Respectfully ask the student to please ask their question."
                        f"Say it in {self.language}, {', '.join(self.learned_languages)}.",
            system_prompt=f"You are a great foreign language teacher.",
        )
        self.bot.send_message(chat_id, please_ask_your_question)
        user_text = message.text
        question_answer = self.model.call(
            user_prompt=f"You are teaching a {self.language} class in writing form. Your student has a question about"
                        f"the last lesson section. Please reply and explain it in the easiest way to understand"
                        f".\nQUESTION: {user_text}\n\n"
                        f"LESSON SECTION: {self.lesson_history[-1]['content']}",
            system_prompt=f"You are a great foreign language teacher teaching a 1-on-1 {self.language} class "
                          f"through a textual medium, such as a messaging app. Use raw markdown formatting.",
            # history=self.lesson_history,
        )
        converted_content = telegramify_markdown.markdownify(
            question_answer,
            max_line_length=None,
            normalize_whitespace=False
        )
        self.bot.send_message(chat_id, converted_content)
        self.lesson_history.append({"role": "system", "content": question_answer})
        self.logging[chat_id] = f'answer_question_{user_text}'
        # send reminder of commands
        self.bot.send_message(chat_id, "Type '/next' to move forward, '/more' to get more details, "
                                       "'/better' for a clearer version, '/question' to ask a question, "
                                       "'/conversation' to start a conversation.")

    def _handle_conversation_starter(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/conversation' command.
        """
        self.reminded = True
        self.lesson_history.append({"role": "user", "content": "/conversation"})
        chat_id = message.chat.id
        personna_request = "If you want me to adopt a specific role, describe it, otherwise, you can type '/no'."
        self.bot.send_message(chat_id, personna_request)
        self.lesson_history.append({"role": "system", "content": personna_request})
        user_text = message.text
        self.lesson_history.append({"role": "user", "content": user_text})
        conversation_starter = self.model.call(
            user_prompt=f"Come up with a common conversation starter in {self.language}.",
            system_prompt=f"You are a acting as a '{user_text}' character that only speaks in {self.language}. "
                          f"If your character is 'no' that means your character is your own as an LLM agent.",
            # history=self.lesson_history,
        )
        self.bot.send_message(chat_id, conversation_starter)
        self.lesson_history.append({"role": "system", "content": conversation_starter})
        self.logging[chat_id] = f'conversation'
        # register conversation follow-up
        self.bot.register_next_step_handler(message, self._handle_conversation)

    def _handle_conversation(self, message: telebot.types.Message) -> None:
        """
        Handles messages that are '/conversation' command.
        """
        self.reminded = True
        self.lesson_history.append({"role": "user", "content": "/conversation"})
        chat_id = message.chat.id
        self.bot.send_message(chat_id, "If you want me to adopt a specific role, describe it, otherwise, you can "
                                       "type '/no'.")
        user_text = message.text
        conversation_follow_up = self.model.call(
            user_prompt=user_text,
            system_prompt=f"You are a acting as character and only speak in {self.language} using simple vocabulary "
                          f"and syntax for the {self.level} level.",
            history=self.lesson_history,
        )
        self.bot.send_message(chat_id, conversation_follow_up)
        self.lesson_history.append({"role": "system", "content": conversation_follow_up})
        self.logging[chat_id] = f'conversation'
        # register conversation follow-up
        self.bot.register_next_step_handler(message, self._handle_conversation)

    def _handle_lesson_class(self, message: telebot.types.Message) -> None:
        """
        Handles the lesson class, which is the main part of the bot's functionality.
        """
        self.reminded = True
        self.next_lesson = f"basics of learning {self.language}" if not self.next_lesson else self.next_lesson
        # register a new lesson and make the list of sections
        self.bot.register_next_step_handler(message, self._handle_new)

    # def _reset_logging(self, chat_id: int) -> None:
    #     # Reset the user's state after processing the input
    #     if chat_id in self.logging:
    #         del self.logging[chat_id]

    def _handle_help_command(self, message: telebot.types.Message) -> None:
        """
        Handles the '/help' or '/info' or '/documentation' commands,
        providing a list and explanation of available commands.
        """
        chat_id = message.chat.id
        help_text = (
            "Here are the commands you can use:\n\n"
            "/start or /restart - Begin a new course in a new language (this will delete all your information).\n"
            "/language - Set or change the language you want to learn.\n"
            "/level - Set or change your proficiency level in the language.\n"
            "/limitation - Set or change any limitations that might affect your learning.\n"
            "/lesson - Set or change the next lesson you want to learn.\n"
            "/learned - List the other languages you already know.\n"
            "/mastered - List the content you have already mastered in the language.\n"
            "/reminder - Set a daily reminder for your lessons.\n"
            "/new - Start a new lesson with a new subject.\n"
            "/next - Move to the next section of the current lesson.\n"
            "/more - Ask for more details or examples about the current lesson section.\n"
            "/better - Ask for a simpler or clearer explanation of the current lesson section.\n"
            "/question - Ask a question about the current lesson section.\n"
            "/conversation - Start a conversation in the language you are learning.\n"
            "/help or /info or /documentation - Show this list of commands."
        )
        self.bot.send_message(chat_id, help_text)

    # def _handle_query(self, message: telebot.types.Message) -> None:
    #     """
    #     Handles the free text input after the '/start' command.
    #     This function is called by _handle_all_messages when the state matches.
    #     """
    #     chat_id = message.chat.id
    #     user_free_text = message.text
    #
    #     # Process the user's free text here
    #     # For demonstration, we'll just echo it and give a generic horoscope
    #     response_text = (
    #         "Your general horoscope for today: Expect unexpected opportunities! "
    #         "Be open to new ideas and collaborations. A positive attitude will "
    #         "lead to great outcomes."
    #     )
    #     self.bot.send_message(chat_id, response_text)
    #
    #     # Reset the user's state after processing the input
    #     if chat_id in self.logging:
    #         del self.logging[chat_id]  # Or self.logging[chat_id] = 'normal'
    #     self._reset_logging(chat_id)

    def _handle_all_messages(self, message: telebot.types.Message) -> None:
        """
        Handles all other text messages that are not specific commands.
        """
        user_text = message.text
        chat_id = message.chat.id
        current_state = self.logging.get(chat_id)
        if current_state == 'conversation':
            self._handle_conversation_starter(message)

        self.bot.send_message(chat_id,
                              f"I am sorry, I do not understand the command '{user_text}'."
                              "Please type '/help' to see the list of all possible commands.")


if __name__ == "__main__":
    bot_instance = TelegramBot(
        telegram_api_key_or_path="telegram_api_key.txt",
        model_api_key_or_path="openai_api_key.txt",
    )
    bot_instance._handle_start()
