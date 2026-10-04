from aiogram.fsm.state import State, StatesGroup


class AddAccount(StatesGroup):
    name = State()


class EditAccount(StatesGroup):
    rename = State()


class UploadStrategy(StatesGroup):
    waiting_file = State()


class StrategyParam(StatesGroup):
    value = State()


class NewBot(StatesGroup):
    name = State()
    symbols = State()
    timeframes = State()


class EditBot(StatesGroup):
    symbols = State()
    timeframes = State()
    param = State()


class RiskEdit(StatesGroup):
    value = State()
    new_profile = State()


class PositionInput(StatesGroup):
    sl = State()
    tp = State()
    partial = State()


class UserInput(StatesGroup):
    add_id = State()
    remove_id = State()
