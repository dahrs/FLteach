import os
from typing import Sequence, Tuple

from openai import OpenAI

from FLteach.llm.llm import ILargeLanguageModel


class OpenaiApi(ILargeLanguageModel):
    def __init__(self,
                 model_id: str = "gpt-4o-mini",
                 api_key_or_path: str | None = None) -> None:
        """
        Initializes the openai api class with the due model id and api key
        """
        if api_key_or_path is not None and os.path.isfile(api_key_or_path):
            self.api_key = open(api_key_or_path).read()
        elif isinstance(api_key_or_path, str):
            self.api_key = api_key_or_path
        else:
            self.api_key = os.environ["OPENAI_API_KEY"]
        self.model_id = model_id
        self.client = OpenAI(api_key=self.api_key)
        return

    def call(self,
             user_prompt: str,
             system_prompt: str = "You are a helpful assistant.",
             history: Sequence[Tuple[str, str]] | None = None,
             ) -> str:
        history = [] if history is None else history
        conversation_history = []
        for turn in history:
            conversation_history.append({"role": "user", "content": turn[0]})
            conversation_history.append({"role": "assistant", "content": turn[1]})
        # prepare the model input
        messages = [
            {"role": "system", "content": system_prompt},
            *conversation_history,
            {"role": "user", "content": user_prompt}
        ]

        completion = self.client.chat.completions.create(
            model=self.model_id,
            messages=messages,
        )
        content = completion.choices[0].message.content
        return content
