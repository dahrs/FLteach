import os
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
            self.api_key = api_key_or_path
        elif isinstance(api_key_or_path, str):
            self.api_key = api_key_or_path
        else:
            self.api_key = os.environ["OPENAI_API_KEY"]
        self.model_id = model_id
        self.client = OpenAI(api_key=self.api_key)
        return

    def call(self,
             system_prompt: str,
             user_prompt: str,
             history: 
             ) -> str:
        # prepare the model input
        messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

        completion = self.client.chat.completions.create(
            model=self.model_id,
            messages=messages,
        )
        content = completion.choices[0].message.content
        return content
