from abc import ABC, abstractmethod


class IBot(ABC):
    @abstractmethod
    def initialize_bot(self) -> None:
        """
        Initializes the bot with the necessary configurations
        """
        raise NotImplementedError

    @abstractmethod
    def run(self) -> None:
        """
        Runs the bot, starting the main loop to listen for messages
        """
        raise NotImplementedError
