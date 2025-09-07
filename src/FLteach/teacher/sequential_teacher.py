import re
import json
import random
import logging
from typing import List, Dict, Any, Optional

from FLteach.llm.openai_api import OpenaiApi

# Configure logging for this module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')


class LLMService:
    """
    A service class that handles all interactions with the language model.
    """
    def __init__(self, model_api_key_or_path: str | None = None) -> None:
        self.model = OpenaiApi(api_key_or_path=model_api_key_or_path)

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

    def get_language_summary(self, user_message: str) -> str:
        """
        Summarizes and standardizes the language from a user's message.
        """
        return self.model.call(
            user_prompt=f"This is the answer to the question 'what language do you want to learn?'. Output the "
                        f"standard name of the language along with any necessary details like regional "
                        f"specificities/level/register/etc. It must be written in said language. (use as few words "
                        f"as possible to summarize all concepts): {user_message}",
            system_prompt="You are a succinct extractor and format standardizer.",
        )

    def get_level_summary(self, user_message: str) -> str:
        """
        Summarizes and standardizes the user's proficiency level.
        """
        return self.model.call(
            user_prompt=f"This is the answer to the question 'what is your language proficiency/level?'. Output the "
                        f"standard name of the level along with any necessary details (use as few words as possible "
                        f"to summarize all concepts): {user_message}",
            system_prompt="You are a succinct extractor and format standardizer."
        )

    def infer_seen_content(self, level: str, language: str) -> List[str]:
        """
        Infers content a student should already know based on their level.
        """
        infered_content = self.model.call(
            user_prompt=f"Given a {level} proficiency level in {language}, make a list of all the content "
                        f"that a foreign language student should already know?",
            system_prompt="You are a succinct assistant."
        )
        # Assuming the LLM returns a single string with a list-like format
        return [infered_content]

    def get_lesson_name(self, user_message: str) -> str:
        """
        Determines the lesson name from a user's preference.
        """
        return self.model.call(
            user_prompt=f"This is the answer to the question 'about what do you want to learn?'. If it says 'no' it "
                        f"means 'start from the beginning' Output a curriculum lesson name that matches it and any "
                        f"details specified (use as few words as possible to summarize all concepts): {user_message}",
            system_prompt="You are a succinct extractor and format standardizer. "
                          "The beginner introduction class should "
                          f"cover the language writing system and general orthography."
        )

    def is_beginner_level(self, user_level: str, lesson_history: List[Dict[str, str]]) -> bool:
        """
        Checks if the user's level is a low beginner level.
        """
        beginner_level_bool_str = self.model.call(
            user_prompt=f"Return 'True' if the following level is very low beginner level and a foreign language "
                        f"student cannot be expected to read the language yet: {user_level}, otherwise return 'False'.",
            system_prompt=f"You are a succinct assistant that replies using boolean values only.",
            history=lesson_history
        )
        return 'true' in beginner_level_bool_str.lower()

    def lesson_maker(self,
                     course_language: str,
                     user_limitation: str,
                     lesson_elem: str,
                     instruction_language: str,
                     user_seen_content: List,
                     user_mastered: List,
                     user_level: str,
                     revision_bool: bool,
                     lesson_history: List[Dict[str, str]],
                     ) -> str:
        """
        Generates lesson content for a student using the LLM.
        """
        lesson_content = ""
        # previous content revision
        if revision_bool:
            revision_content = self.text2list(", ".join(user_seen_content + user_mastered),
                                              f"Extract from the list all the topics that can be useful "
                                              f"as a base to learn about {lesson_elem}")
            revision_content = revision_content if revision_content is not None else user_seen_content + user_mastered
            lesson_content = self.model.call(
                user_prompt=f"You are teaching a {course_language} class."
                            f"The instructions and explanations must be foreigner-friendly/kid-friendly and "
                            f"written in a version of {instruction_language} so simple, a 7 years old should be able "
                            f"to read it. "
                            f"Do not write direct translations of the content, you may use emojis to illustrate."
                            f"As a way to start the class, make "
                            f"a very short, simple, and clear introductory revision of previously seen content: "
                            f"{random.choice(revision_content)}",
                system_prompt=f"You are a succinct  foreign language teacher teaching a 1-on-1 {course_language} "
                              f"class to your student (LEVEL: {user_level}, LIMITATION: {user_limitation}).",
                history=lesson_history
            )
            lesson_content += "\n\n"
        # new content presentation
        content_presentation = self.model.call(
            user_prompt=f"You are teaching a {course_language} class. The topic of the class is {lesson_elem}. "
                        f"The instructions and explanations must be foreigner-friendly/kid-friendly and "
                        f"written in a version of {instruction_language} so simple, a 7 years old should be able "
                        f"to read it. "
                        f"The lesson's new content must be in {course_language}."
                        f"Do not write direct translations of the content, you may use emojis to illustrate. "
                        f"Make a simple and clear explanation of the topic and a schematic "
                        f"presentation of the content. Do not make practice exercises, someone else is in charge of "
                        f"those. Instead of figures, add emojis and simple ASCII pictures to ease the semantic "
                        f"understanding by illustrating concepts, actions, persons, etc.",
            system_prompt=f"You are a succinct  foreign language teacher teaching a 1-on-1 {course_language} class"
                          f" to your student (LEVEL: {user_level}, LIMITATION: {user_limitation}).",
            history=lesson_history
        )
        lesson_content += f"{content_presentation}\n\n"
        # add exercises
        lesson_content += self.model.call(
            user_prompt=f"You are teaching a {course_language} class. The topic of the class is {lesson_elem}. "
                        f"The instructions and explanations must be foreigner-friendly/kid-friendly and "
                        f"written in a simple version of {instruction_language}. The content to exercise must be "
                        f"in {course_language}. "
                        f"Make 4 exercises that practice each of the skills: Listening comprehension, Oral production, "
                        f"Reading comprehension, Written production. "
                        f"The exercises must practice the following content: {content_presentation}",
            system_prompt=f"You are a succinct foreign language teacher teaching a 1-on-1 {course_language} class through a "
                          f"text messaging app for (LEVEL: {user_level}, LIMITATION: {user_limitation}).",
            history=lesson_history
        )
        lesson_content += "\n\n"
        return lesson_content
