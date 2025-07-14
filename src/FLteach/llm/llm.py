from abc import ABC, abstractmethod


class ILargeLanguageModel(ABC):
    @abstractmethod
    def call(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError
