from abc import ABC, abstractmethod
from ui.i18n import I18n

class BaseStrategy(ABC):
    def __init__(self, name_key, desc_key):
        self._name_key = name_key
        self._desc_key = desc_key

    @property
    def name(self):
        return I18n.get(self._name_key)
    
    @property
    def description(self):
        return I18n.get(self._desc_key)

    @abstractmethod
    def filter(self, context):
        """
        Execute strategy logic.
        :param context: Dict containing 'screening_data' DataFrame with merged daily+financial data
        :return: Filtered DataFrame
        """
        pass
