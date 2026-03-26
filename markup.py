from typing import Optional

from telebot import types


class StyledInlineKeyboardButton(types.InlineKeyboardButton):
    def __init__(self, *args, style: Optional[str] = None, icon_custom_emoji_id: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.style = style
        self.icon_custom_emoji_id = icon_custom_emoji_id

    def to_dict(self):
        data = super().to_dict()
        if self.icon_custom_emoji_id is not None:
            data['icon_custom_emoji_id'] = self.icon_custom_emoji_id
        if self.style is not None:
            data['style'] = self.style
        return data


def action_choice_markup(target_user_id: int, owner_user_id: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        StyledInlineKeyboardButton(
            text='Сообщение',
            callback_data=f'msg:{target_user_id}:{owner_user_id}',
            style='success',
        ),
        StyledInlineKeyboardButton(
            text='Чат',
            callback_data=f'chat:{target_user_id}:{owner_user_id}',
            style='primary',
        ),
    )
    return markup


def incoming_message_markup(target_user_id: int, owner_user_id: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        StyledInlineKeyboardButton(
            text='Ответить',
            callback_data=f'reply:{target_user_id}:{owner_user_id}',
            style='success',
        ),
        StyledInlineKeyboardButton(
            text='Чат',
            callback_data=f'chat:{target_user_id}:{owner_user_id}',
            style='primary',
        ),
    )
    return markup


def send_more_markup(target_user_id: int, owner_user_id: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        StyledInlineKeyboardButton(
            text='Отправить ещё',
            callback_data=f'again:{target_user_id}:{owner_user_id}',
            style='primary',
        )
    )
    return markup


def main_menu_markup(link: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        StyledInlineKeyboardButton(
            text='Скопировать ссылку',
            copy_text=types.CopyTextButton(link),
            style='primary',
        )
    )
    return markup


def chat_request_markup(request_id: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        StyledInlineKeyboardButton(
            text='Принять',
            callback_data=f'approve:{request_id}',
            style='success',
        ),
        StyledInlineKeyboardButton(
            text='Отклонить',
            callback_data=f'decline:{request_id}',
            style='danger',
        ),
    )
    return markup
