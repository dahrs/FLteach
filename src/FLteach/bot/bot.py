from abc import ABC, abstractmethod


class IBot(ABC):
    @abstractmethod
    def send(self, chat_id: int, message_content: str) -> str:
        """
        Sends a message from the user in the chat
        """
        raise NotImplementedError

    @abstractmethod
    def get(self) -> str:
        """
        Gets the reply from the bot in the chat
        """
        raise NotImplementedError