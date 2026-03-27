from html import escape
import atexit
import fcntl
import os
import sys
import time
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

from telebot import TeleBot, apihelper, types, util

from baza import (
    clear_user_state,
    close_conversation,
    create_chat_request,
    create_conversation,
    get_active_conversation,
    get_chat_request,
    get_conversation_by_thread,
    get_latest_chat_request_between,
    get_main_message_id,
    get_main_thread_id,
    get_notice_message_id,
    get_pending_chat_request_between,
    get_user,
    get_user_state,
    init_db,
    set_chat_request_message,
    set_chat_request_requester_message,
    set_main_message_id,
    set_main_thread_id,
    set_notice_message_id,
    set_user_state,
    update_chat_request_status,
    upsert_user,
)
from config import bot
from criptography import decode, encode, encode_topic_code
from markup import (
    action_choice_markup,
    chat_request_markup,
    incoming_message_markup,
    main_menu_markup,
    send_more_markup,
)


HANDLED_CONTENT_TYPES = util.content_type_media + ['venue']
CONTENT_LABELS = {
    'audio': 'Аудио',
    'animation': 'GIF',
    'contact': 'Контакт',
    'dice': 'Кубик',
    'document': 'Документ',
    'game': 'Игра',
    'gift': 'Подарок',
    'location': 'Локация',
    'photo': 'Фото',
    'poll': 'Опрос',
    'sticker': 'Стикер',
    'story': 'История',
    'text': 'Сообщение',
    'unique_gift': 'Уникальный подарок',
    'venue': 'Место',
    'video': 'Видео',
    'video_note': 'Видео-кружок',
    'voice': 'Голосовое',
}
CAPTIONABLE_ONE_OFF_TYPES = {
    'animation',
    'audio',
    'document',
    'photo',
    'video',
    'voice',
}
BOT_USERNAME: Optional[str] = None
INSTANCE_LOCK = None


def sync_user(tg_user: types.User) -> bool:
    name_parts = [tg_user.first_name or '']
    if tg_user.last_name:
        name_parts.append(tg_user.last_name)
    full_name = ' '.join(part for part in name_parts if part).strip() or tg_user.username or str(tg_user.id)
    return upsert_user(tg_user.id, tg_user.username, full_name)


def get_bot_username() -> str:
    global BOT_USERNAME
    if BOT_USERNAME:
        return BOT_USERNAME
    me = bot.get_me()
    BOT_USERNAME = me.username or 'AnongramBot'
    return BOT_USERNAME


def build_link(user_id: int) -> str:
    return f'https://t.me/{get_bot_username()}?start={encode(user_id)}'


def extract_start_token(raw_text: Optional[str]) -> Optional[str]:
    if not raw_text:
        return None

    text = raw_text.strip()
    if not text:
        return None

    if text.startswith('/start'):
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) == 2 and parts[1].strip() else None

    if 't.me/' in text or 'tg://resolve' in text:
        try:
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            values = query.get('start')
            if values and values[0].strip():
                return values[0].strip()
        except Exception:
            return None

    if all(ch.isalnum() or ch in '-_' for ch in text) and len(text) <= 64:
        return text

    return None


def normalize_pair(
    first_user_id: int,
    first_thread_id: int,
    second_user_id: int,
    second_thread_id: int,
) -> Tuple[int, int, int, int]:
    if first_user_id <= second_user_id:
        return first_user_id, first_thread_id, second_user_id, second_thread_id
    return second_user_id, second_thread_id, first_user_id, first_thread_id


def system_text(title: str, body: str, emoji: str = '🤖') -> str:
    return f'{emoji} <b>{escape(title)}</b>\n<blockquote>{escape(body)}</blockquote>'


def send_root_notice(user_id: int, title: str, body: str, emoji: str = '🧭') -> int:
    notice_message_id = replace_main_message(user_id, get_notice_message_id(user_id), system_text(title, body, emoji))
    set_notice_message_id(user_id, notice_message_id)
    return notice_message_id


def action_prompt_text() -> str:
    return (
        '🧭 <b>Анонимное сообщение</b>\n<blockquote>'
        'Ты открыл чужую анонимную ссылку. '
        'Можешь сразу отправить одно анонимное сообщение или запросить отдельный анонимный чат. '
        'Если просто пришлёшь следующее сообщение без нажатия кнопки, я отправлю его как обычное анонимное сообщение.</blockquote>'
    )


def waiting_message_text(is_reply: bool) -> str:
    if is_reply:
        return system_text(
            'Жду ответ',
            'Пришли ответ. Я передам его анонимно, не раскрывая тебя, и сохраню форматирование.',
            '↩️',
        )
    return system_text(
        'Жду сообщение',
        'Пришли анонимное сообщение. Поддерживаются текст, фото, видео, стикеры, GIF, голосовые и другое.',
        '🟢',
    )


def delivery_confirmation_text() -> str:
    return system_text(
        'Сообщение доставлено',
        'Если нужно, можно сразу отправить ещё одно.',
        '✅',
    )


def request_sent_text(is_already_pending: bool) -> str:
    if is_already_pending:
        return system_text(
            'Запрос уже ждёт решения',
            'Новый запрос не создавался. Когда собеседник ответит, я обновлю эту карточку.',
            '⏳',
        )
    return system_text(
        'Запрос отправлен',
        'Когда собеседник примет решение, я обновлю эту карточку.',
        '📨',
    )


def is_message_not_modified_error(error: apihelper.ApiTelegramException) -> bool:
    description = (getattr(error, 'description', '') or '').lower()
    return 'message is not modified' in description


def safe_delete_message(chat_id: int, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        bot.delete_message(chat_id, message_id)
    except apihelper.ApiTelegramException:
        pass


def clear_root_notice(user_id: int) -> None:
    safe_delete_message(user_id, get_notice_message_id(user_id))
    set_notice_message_id(user_id, None)


def resolve_link_entry(user_id: int, token: str) -> bool:
    try:
        owner_id = decode(token)
    except Exception:
        return False

    owner = get_user(owner_id)
    if owner is None:
        clear_prompt_state(user_id)
        send_root_notice(
            user_id,
            'Ссылка неактивна',
            'Владелец этой ссылки ещё не запускал бота, поэтому написать ему пока нельзя.',
            emoji='⚠️',
        )
        return True

    if owner_id == user_id:
        clear_prompt_state(user_id)
        send_root_notice(
            user_id,
            'Себе писать нельзя',
            'Открой закреплённое сообщение со своей ссылкой и поделись ей с другим аккаунтом, если хочешь проверить сценарий.',
            emoji='🚫',
        )
        return True

    clear_root_notice(user_id)
    send_root_notice(
        user_id,
        'Ты на анонимной ссылке',
        'Можешь отправить одно анонимное сообщение сразу или открыть анонимный чат кнопками ниже.',
        emoji='👋',
    )
    send_action_prompt(user_id, owner_id, owner_id)
    return True


def topic_log(action: str, chat_id: int, thread_id: int, error: Optional[apihelper.ApiTelegramException] = None) -> None:
    if error is None:
        print(f'TOPIC_OK action={action} chat_id={chat_id} thread_id={thread_id}', flush=True)
        return
    print(
        f'TOPIC_ERR action={action} chat_id={chat_id} thread_id={thread_id} '
        f'description={getattr(error, "description", "")}',
        flush=True,
    )


def is_missing_topic_error(error: apihelper.ApiTelegramException) -> bool:
    description = (getattr(error, 'description', '') or '').lower()
    markers = (
        'already closed',
        'message thread not found',
        'message thread is not found',
        'message thread is invalid',
        'forum topic not found',
        'topic not found',
        'topic was deleted',
        'thread not found',
    )
    return any(marker in description for marker in markers)


def reopen_topic_if_present(chat_id: int, thread_id: int) -> bool:
    last_error: Optional[apihelper.ApiTelegramException] = None
    for attempt in range(2):
        try:
            bot.reopen_forum_topic(chat_id, thread_id)
            topic_log('reopen', chat_id, thread_id)
            return True
        except apihelper.ApiTelegramException as error:
            description = (getattr(error, 'description', '') or '').lower()
            if is_missing_topic_error(error):
                topic_log('reopen-missing', chat_id, thread_id, error)
                return True
            if 'not closed' in description or 'already open' in description:
                topic_log('reopen-skip', chat_id, thread_id, error)
                return True
            last_error = error
            if attempt == 0:
                time.sleep(0.35)

    if last_error is not None:
        topic_log('reopen-failed', chat_id, thread_id, last_error)
    return False


def close_topic_if_present(chat_id: int, thread_id: int) -> bool:
    try:
        bot.close_forum_topic(chat_id, thread_id)
        topic_log('close', chat_id, thread_id)
        return True
    except apihelper.ApiTelegramException as error:
        description = (getattr(error, 'description', '') or '').lower()
        if is_missing_topic_error(error):
            topic_log('close-missing', chat_id, thread_id, error)
            return True
        if 'already closed' in description:
            topic_log('close-skip', chat_id, thread_id, error)
            return True
        topic_log('close-failed', chat_id, thread_id, error)
        return False


def delete_topic_if_present(chat_id: int, thread_id: int) -> bool:
    last_error: Optional[apihelper.ApiTelegramException] = None
    for attempt in range(3):
        try:
            bot.delete_forum_topic(chat_id, thread_id)
            topic_log('delete', chat_id, thread_id)
            return True
        except apihelper.ApiTelegramException as error:
            if is_missing_topic_error(error):
                topic_log('delete-missing', chat_id, thread_id, error)
                return True
            last_error = error

            description = (getattr(error, 'description', '') or '').lower()
            topic_log(f'delete-attempt-{attempt + 1}', chat_id, thread_id, error)

            if attempt == 0 and ('closed' in description or 'reopen' in description):
                reopen_topic_if_present(chat_id, thread_id)
            elif attempt == 1 and ('open' in description or 'delete' in description):
                close_topic_if_present(chat_id, thread_id)
                reopen_topic_if_present(chat_id, thread_id)

            if attempt < 2:
                time.sleep(0.6)

    if last_error is not None:
        topic_log('delete-failed', chat_id, thread_id, last_error)
    return False


def close_conversation_topics(conversation) -> tuple[bool, bool]:
    owner_deleted = False
    guest_deleted = False

    for attempt in range(3):
        if not owner_deleted:
            owner_deleted = delete_topic_if_present(conversation['owner_id'], conversation['owner_thread_id'])
        if not guest_deleted:
            guest_deleted = delete_topic_if_present(conversation['guest_id'], conversation['guest_thread_id'])

        if owner_deleted and guest_deleted:
            break

        if attempt < 2:
            time.sleep(0.6)

    return owner_deleted, guest_deleted


def cleanup_legacy_main_thread(user_id: int) -> None:
    legacy_thread_id = get_main_thread_id(user_id)
    if not legacy_thread_id:
        return

    if delete_topic_if_present(user_id, legacy_thread_id):
        set_main_thread_id(user_id, None)


def send_main_message(
    user_id: int,
    text: str,
    *,
    reply_markup=None,
    disable_notification: bool = False,
    disable_web_page_preview: bool = False,
) -> types.Message:
    cleanup_legacy_main_thread(user_id)
    return bot.send_message(
        chat_id=user_id,
        text=text,
        reply_markup=reply_markup,
        disable_notification=disable_notification,
        disable_web_page_preview=disable_web_page_preview,
    )


def update_main_message(
    user_id: int,
    message_id: Optional[int],
    text: str,
    *,
    reply_markup=None,
    disable_web_page_preview: bool = False,
) -> bool:
    if not message_id:
        return False

    try:
        bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        return True
    except apihelper.ApiTelegramException as error:
        return is_message_not_modified_error(error)


def replace_main_message(
    user_id: int,
    message_id: Optional[int],
    text: str,
    *,
    reply_markup=None,
    disable_web_page_preview: bool = False,
) -> int:
    if update_main_message(
        user_id,
        message_id,
        text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    ):
        return int(message_id)

    sent = send_main_message(
        user_id,
        text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )
    safe_delete_message(user_id, message_id)
    return sent.message_id


def send_to_main(
    user_id: int,
    title: str,
    body: str,
    *,
    emoji: str = '🤖',
    reply_markup=None,
    disable_notification: bool = False,
) -> types.Message:
    return send_main_message(
        user_id,
        system_text(title, body, emoji),
        reply_markup=reply_markup,
        disable_notification=disable_notification,
    )


def send_raw_to_main(
    user_id: int,
    text: str,
    *,
    reply_markup=None,
    disable_web_page_preview: bool = False,
) -> types.Message:
    return send_main_message(
        user_id,
        text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )


def main_card_text(user_id: int) -> str:
    return (
        '🧷 <b>Anongram</b>\n\n'
        'Твоя анонимная ссылка:\n'
        f'{build_link(user_id)}\n\n'
        '<b>Как это работает?</b>\n'
        '1. Отправь эту ссылку человеку.\n'
        '2. Он сможет написать тебе анонимно.\n'
        '3. Когда придёт сообщение, ты сможешь ответить или открыть анонимный чат.\n\n'
        '<b>Важно:</b>\n'
        '• сюда приходят анонимные сообщения и запросы на чат\n'
        '• отдельные диалоги открываются в тредах\n'
        '• команда /stop закрывает такой тред у обоих\n'
        '• если чат очищен, просто нажми /start и я снова покажу ссылку\n\n'
        'Кнопка ниже помогает быстро скопировать ссылку.'
    )


def ensure_main_card(user_id: int, *, force_new: bool = False) -> int:
    cleanup_legacy_main_thread(user_id)
    text = main_card_text(user_id)
    reply_markup = main_menu_markup(build_link(user_id))
    existing_message_id = get_main_message_id(user_id)

    if existing_message_id:
        if update_main_message(
            user_id,
            existing_message_id,
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        ):
            return int(existing_message_id)
        set_main_message_id(user_id, None)

    sent = send_raw_to_main(
        user_id,
        text,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    set_main_message_id(user_id, sent.message_id)
    return int(sent.message_id)


def trim_text(value: str, limit: int = 700) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + '…'


def message_preview(message: types.Message) -> str:
    if message.content_type == 'text' and message.text:
        return trim_text(message.text)
    if getattr(message, 'caption', None):
        return trim_text(message.caption)
    return CONTENT_LABELS.get(message.content_type, message.content_type.capitalize())


def incoming_notice_text(message: types.Message, is_reply: bool) -> str:
    title = 'Анонимный ответ' if is_reply else 'Новое анонимное сообщение'
    preview = escape(message_preview(message))
    return f'💌 <b>{title}</b>\n<blockquote>{preview}</blockquote>'


def safe_remove_markup(chat_id: int, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except apihelper.ApiTelegramException:
        pass


def clear_prompt_state(user_id: int) -> None:
    state = get_user_state(user_id)
    if state is not None:
        chooser_chat_id = state['chooser_chat_id']
        chooser_message_id = state['chooser_message_id']
        if chooser_chat_id and chooser_message_id:
            safe_remove_markup(chooser_chat_id, chooser_message_id)
    clear_user_state(user_id)


def set_prompt_state(user_id: int, state: str, target_user_id: int, owner_user_id: int, chooser_message_id: int) -> None:
    set_user_state(
        user_id=user_id,
        state=state,
        target_user_id=target_user_id,
        owner_user_id=owner_user_id,
        chooser_chat_id=user_id,
        chooser_message_id=chooser_message_id,
    )


def send_action_prompt(user_id: int, target_user_id: int, owner_user_id: int) -> int:
    clear_root_notice(user_id)
    current_state = get_user_state(user_id)
    current_message_id = current_state['chooser_message_id'] if current_state is not None else None
    prompt_message_id = replace_main_message(
        user_id,
        current_message_id,
        action_prompt_text(),
        reply_markup=action_choice_markup(target_user_id, owner_user_id),
    )
    set_prompt_state(user_id, 'choose_action', target_user_id, owner_user_id, prompt_message_id)
    return prompt_message_id


def send_delivery_confirmation(
    user_id: int,
    target_user_id: int,
    owner_user_id: int,
    prompt_message_id: Optional[int] = None,
) -> None:
    clear_root_notice(user_id)
    replace_main_message(
        user_id,
        prompt_message_id,
        delivery_confirmation_text(),
        reply_markup=send_more_markup(target_user_id, owner_user_id),
    )


def pending_request_owner_text() -> str:
    return system_text(
        'Запрос на анонимный чат',
        'Собеседник предлагает перейти в отдельный анонимный чат.',
        '💬',
    )


def request_approved_text(is_requester: bool) -> str:
    if is_requester:
        return system_text(
            'Запрос принят',
            'Собеседник согласился. Я открыл отдельный тред.',
            '🤝',
        )
    return system_text(
        'Запрос принят',
        'Я открыл отдельный тред. Дальше общение пойдёт там.',
        '🤝',
    )


def request_closed_text(is_requester: bool) -> str:
    if is_requester:
        return system_text(
            'Тред закрыт',
            'Этот анонимный диалог закрыт для обеих сторон. Если захочешь открыть новый, отправь ещё один запрос.',
            '🔒',
        )
    return system_text(
        'Тред закрыт',
        'Этот анонимный диалог закрыт для обеих сторон. Если нужно открыть новый, собеседник должен отправить новый запрос.',
        '🔒',
    )


def request_declined_text(is_requester: bool) -> str:
    if is_requester:
        return system_text(
            'Запрос отклонён',
            'Собеседник пока не готов перейти в чат.',
            '🛑',
        )
    return system_text(
        'Запрос отклонён',
        'Чат не открыт.',
        '🛑',
    )


def request_existing_chat_text() -> str:
    return system_text(
        'Диалог уже открыт',
        'У вас уже есть активный анонимный диалог.',
        '🔁',
    )


def replace_requester_card(
    request,
    fallback_user_id: int,
    fallback_message_id: Optional[int],
    text: str,
    *,
    reply_markup=None,
) -> int:
    clear_root_notice(request['requester_id'] if request is not None else fallback_user_id)
    requester_message_id = request['requester_message_id'] if request is not None else None
    message_id = replace_main_message(
        request['requester_id'] if request is not None else fallback_user_id,
        requester_message_id or fallback_message_id,
        text,
        reply_markup=reply_markup,
    )
    if request is not None:
        set_chat_request_requester_message(request['id'], message_id)
    return message_id


def replace_owner_request_card(request, text: str, *, reply_markup=None) -> int:
    clear_root_notice(request['owner_id'])
    owner_message_id = request['owner_message_id'] if request is not None else None
    message_id = replace_main_message(
        request['owner_id'],
        owner_message_id,
        text,
        reply_markup=reply_markup,
    )
    set_chat_request_message(request['id'], message_id)
    return message_id


def sync_request_cards(
    request,
    *,
    owner_text: str,
    requester_text: str,
    owner_markup=None,
    requester_markup=None,
) -> None:
    replace_owner_request_card(request, owner_text, reply_markup=owner_markup)
    replace_requester_card(
        request,
        request['requester_id'],
        request['requester_message_id'],
        requester_text,
        reply_markup=requester_markup,
    )


def finalize_request_as_opened(request) -> None:
    update_chat_request_status(request['id'], 'approved')
    conversation, created = open_or_reuse_conversation(
        request['owner_id'],
        request['requester_id'],
        request_id=request['id'],
    )
    if created:
        sync_request_cards(
            request,
            owner_text=request_approved_text(is_requester=False),
            requester_text=request_approved_text(is_requester=True),
        )
        return

    sync_request_cards(
        request,
        owner_text=request_existing_chat_text(),
        requester_text=request_existing_chat_text(),
    )


def copy_anonymous_message(
    from_message: types.Message,
    to_user_id: int,
    message_thread_id: Optional[int] = None,
    caption: Optional[str] = None,
    reply_markup=None,
) -> int:
    result = bot.copy_message(
        chat_id=to_user_id,
        from_chat_id=from_message.chat.id,
        message_id=from_message.message_id,
        caption=caption,
        parse_mode='HTML' if caption is not None else None,
        reply_markup=reply_markup,
        message_thread_id=message_thread_id,
    )
    return result.message_id


def notify_incoming_message(
    recipient_id: int,
    sender_id: int,
    owner_user_id: int,
    source_message: types.Message,
) -> None:
    is_reply = sender_id == owner_user_id
    notice_text = incoming_notice_text(source_message, is_reply=is_reply)
    notice_markup = incoming_message_markup(sender_id, owner_user_id)

    if source_message.content_type == 'text':
        bot.send_message(
            recipient_id,
            notice_text,
            reply_markup=notice_markup,
        )
        return

    if source_message.content_type in CAPTIONABLE_ONE_OFF_TYPES:
        copy_anonymous_message(
            source_message,
            recipient_id,
            caption=notice_text,
            reply_markup=notice_markup,
        )
        return

    bot.send_message(
        recipient_id,
        notice_text,
        reply_markup=notice_markup,
    )
    copy_anonymous_message(source_message, recipient_id)


def deliver_one_off(source_message: types.Message, target_user_id: int, owner_user_id: int) -> None:
    notify_incoming_message(
        recipient_id=target_user_id,
        sender_id=source_message.from_user.id,
        owner_user_id=owner_user_id,
        source_message=source_message,
    )
    state = get_user_state(source_message.from_user.id)
    prompt_message_id = state['chooser_message_id'] if state is not None else None
    send_delivery_confirmation(source_message.chat.id, target_user_id, owner_user_id, prompt_message_id)
    safe_delete_message(source_message.chat.id, source_message.message_id)


def participant_threads(conversation, chat_id: int) -> Tuple[int, int, int]:
    if chat_id == conversation['owner_id']:
        return conversation['owner_thread_id'], conversation['guest_thread_id'], conversation['guest_id']
    return conversation['guest_thread_id'], conversation['owner_thread_id'], conversation['owner_id']


def announce_chat_opened(user_id: int, thread_id: int, topic_code: str) -> Optional[int]:
    message = bot.send_message(
        user_id,
        system_text(
            f'Диалог {topic_code} открыт',
            'Это отдельный анонимный тред. Сообщения собеседника будут приходить ниже. Если захочешь закончить диалог, отправь /stop.',
            '🤝',
        ),
        message_thread_id=thread_id,
    )
    return message.message_id


def update_thread_status_message(user_id: int, thread_id: int, message_id: Optional[int], text: str) -> None:
    if not message_id:
        bot.send_message(user_id, text, message_thread_id=thread_id)
        return

    try:
        bot.edit_message_text(
            text=text,
            chat_id=user_id,
            message_id=message_id,
        )
    except apihelper.ApiTelegramException:
        bot.send_message(user_id, text, message_thread_id=thread_id)


def create_private_topics(first_user_id: int, second_user_id: int, topic_code: str):
    first_topic = bot.create_forum_topic(first_user_id, topic_code)
    try:
        second_topic = bot.create_forum_topic(second_user_id, topic_code)
    except apihelper.ApiTelegramException:
        delete_topic_if_present(first_user_id, first_topic.message_thread_id)
        raise
    return first_topic, second_topic


def open_or_reuse_conversation(first_user_id: int, second_user_id: int, request_id: Optional[int] = None):
    existing = get_active_conversation(first_user_id, second_user_id)
    if existing is not None:
        return existing, False

    topic_code = encode_topic_code(first_user_id, second_user_id)
    first_topic, second_topic = create_private_topics(first_user_id, second_user_id, topic_code)
    owner_id, owner_thread_id, guest_id, guest_thread_id = normalize_pair(
        first_user_id,
        first_topic.message_thread_id,
        second_user_id,
        second_topic.message_thread_id,
    )
    owner_status_message_id = announce_chat_opened(first_user_id, first_topic.message_thread_id, topic_code)
    guest_status_message_id = announce_chat_opened(second_user_id, second_topic.message_thread_id, topic_code)
    create_conversation(
        owner_id=owner_id,
        guest_id=guest_id,
        emoji=topic_code,
        owner_thread_id=owner_thread_id,
        guest_thread_id=guest_thread_id,
        owner_status_message_id=(owner_status_message_id if owner_id == first_user_id else guest_status_message_id),
        guest_status_message_id=(guest_status_message_id if guest_id == second_user_id else owner_status_message_id),
        request_id=request_id,
    )
    conversation = get_active_conversation(first_user_id, second_user_id)
    return conversation, True


def request_chat(
    requester_id: int,
    approver_id: int,
    requester_message_id: Optional[int] = None,
) -> None:
    existing = get_active_conversation(requester_id, approver_id)
    if existing is not None:
        replace_main_message(
            requester_id,
            requester_message_id,
            request_existing_chat_text(),
        )
        return

    pending_request = get_pending_chat_request_between(requester_id, approver_id)
    if pending_request is not None:
        if pending_request['requester_id'] == approver_id and pending_request['owner_id'] == requester_id:
            if requester_message_id and requester_message_id != pending_request['owner_message_id']:
                safe_remove_markup(requester_id, requester_message_id)
            finalize_request_as_opened(pending_request)
            return

        replace_owner_request_card(
            pending_request,
            pending_request_owner_text(),
            reply_markup=chat_request_markup(pending_request['id']),
        )
        requester_card_id = replace_requester_card(
            pending_request,
            requester_id,
            requester_message_id,
            request_sent_text(is_already_pending=True),
        )
        if requester_message_id and requester_message_id != requester_card_id:
            safe_remove_markup(requester_id, requester_message_id)
        return

    request_id, _ = create_chat_request(requester_id, approver_id)
    request = get_chat_request(request_id)
    replace_owner_request_card(
        request,
        pending_request_owner_text(),
        reply_markup=chat_request_markup(request_id),
    )
    replace_requester_card(
        request,
        requester_id,
        requester_message_id,
        request_sent_text(is_already_pending=False),
    )


def resolve_chat_action(
    current_user_id: int,
    target_user_id: int,
    requester_message_id: Optional[int] = None,
) -> None:
    request_chat(current_user_id, target_user_id, requester_message_id)


def handle_main_content(message: types.Message) -> None:
    state = get_user_state(message.from_user.id)
    token = extract_start_token(message.text) if message.content_type == 'text' else None

    if state is None:
        if token and resolve_link_entry(message.from_user.id, token):
            safe_delete_message(message.chat.id, message.message_id)
            return
        safe_delete_message(message.chat.id, message.message_id)
        return

    if token and resolve_link_entry(message.from_user.id, token):
        safe_delete_message(message.chat.id, message.message_id)
        return

    deliver_one_off(message, state['target_user_id'], state['owner_user_id'])
    clear_user_state(message.from_user.id)


def handle_topic_content(message: types.Message) -> None:
    conversation = get_conversation_by_thread(message.chat.id, message.message_thread_id, active_only=True)
    if conversation is None:
        return

    my_thread_id, target_thread_id, target_user_id = participant_threads(conversation, message.chat.id)
    if my_thread_id != message.message_thread_id:
        return

    copy_anonymous_message(message, target_user_id, message_thread_id=target_thread_id)


def get_request_for_conversation(conversation):
    request_id = conversation['request_id'] if 'request_id' in conversation.keys() else None
    if request_id:
        request = get_chat_request(request_id)
        if request is not None:
            return request

    return get_latest_chat_request_between(
        conversation['owner_id'],
        conversation['guest_id'],
        statuses=('approved', 'closed'),
    )


def sync_closed_request_cards(conversation) -> None:
    request = get_request_for_conversation(conversation)
    if request is None:
        return

    update_chat_request_status(request['id'], 'closed')
    sync_request_cards(
        request,
        owner_text=request_closed_text(is_requester=False),
        requester_text=request_closed_text(is_requester=True),
    )


def sync_closed_topic_messages(conversation) -> None:
    topic_code = conversation['emoji']
    closed_text = system_text(
        f'Диалог {topic_code} закрыт',
        'Этот анонимный тред закрыт для обеих сторон. Если захотите продолжить общение позже, нужно открыть новый чат.',
        '🔒',
    )

    update_thread_status_message(
        conversation['owner_id'],
        conversation['owner_thread_id'],
        conversation['owner_status_message_id'],
        closed_text,
    )
    update_thread_status_message(
        conversation['guest_id'],
        conversation['guest_thread_id'],
        conversation['guest_status_message_id'],
        closed_text,
    )


@bot.message_handler(commands=['start'])
def start(message: types.Message) -> None:
    sync_user(message.from_user)
    clear_prompt_state(message.from_user.id)
    clear_root_notice(message.chat.id)

    token = extract_start_token(message.text)
    if token is not None:
        if not resolve_link_entry(message.from_user.id, token):
            send_root_notice(message.chat.id, 'Ссылка не сработала', 'Похоже, что этот deep link повреждён или устарел.', emoji='⚠️')
        safe_delete_message(message.chat.id, message.message_id)
        return

    ensure_main_card(message.chat.id)
    safe_delete_message(message.chat.id, message.message_id)


@bot.message_handler(commands=['stop'])
def stop_chat(message: types.Message) -> None:
    sync_user(message.from_user)
    if not getattr(message, 'is_topic_message', False) or not getattr(message, 'message_thread_id', None):
        safe_delete_message(message.chat.id, message.message_id)
        return

    conversation = get_conversation_by_thread(message.chat.id, message.message_thread_id, active_only=False)
    if conversation is None:
        safe_delete_message(message.chat.id, message.message_id)
        return

    safe_delete_message(message.chat.id, message.message_id)
    owner_closed, guest_closed = close_conversation_topics(conversation)

    if conversation['status'] != 'closed':
        close_conversation(conversation['id'])

    sync_closed_request_cards(conversation)
    sync_closed_topic_messages(conversation)

    cleanup_complete = owner_closed and guest_closed
    owner_id = conversation['owner_id']
    guest_id = conversation['guest_id']

    if cleanup_complete:
        return

    if not owner_closed:
        send_root_notice(
            owner_id,
            'Тред ещё открыт',
            'Telegram не закрыл этот тред с первого раза. Если он ещё активен, открой его и отправь /stop ещё раз.',
            emoji='⚠️',
        )
    if not guest_closed:
        send_root_notice(
            guest_id,
            'Тред ещё открыт',
            'Telegram не закрыл этот тред с первого раза. Если он ещё активен, открой его и отправь /stop ещё раз.',
            emoji='⚠️',
        )


@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call: types.CallbackQuery) -> None:
    sync_user(call.from_user)
    data = (call.data or '').split(':')
    action = data[0]

    if action in {'msg', 'reply', 'chat', 'again'} and len(data) == 3:
        target_user_id = int(data[1])
        owner_user_id = int(data[2])

        if action == 'again':
            prompt_message_id = replace_main_message(
                call.from_user.id,
                call.message.message_id,
                action_prompt_text(),
                reply_markup=action_choice_markup(target_user_id, owner_user_id),
            )
            set_prompt_state(call.from_user.id, 'choose_action', target_user_id, owner_user_id, prompt_message_id)
            bot.answer_callback_query(call.id)
            return

        if action == 'msg':
            prompt_message_id = replace_main_message(
                call.from_user.id,
                call.message.message_id,
                waiting_message_text(is_reply=False),
            )
            set_prompt_state(call.from_user.id, 'send_message', target_user_id, owner_user_id, prompt_message_id)
            bot.answer_callback_query(call.id)
            return

        if action == 'reply':
            prompt_message_id = replace_main_message(
                call.from_user.id,
                call.message.message_id,
                waiting_message_text(is_reply=True),
            )
            set_prompt_state(call.from_user.id, 'send_message', target_user_id, owner_user_id, prompt_message_id)
            bot.answer_callback_query(call.id)
            return

        if action == 'chat':
            clear_prompt_state(call.from_user.id)
            resolve_chat_action(
                call.from_user.id,
                target_user_id,
                requester_message_id=call.message.message_id,
            )
            bot.answer_callback_query(call.id)
            return

    if action in {'approve', 'decline'} and len(data) == 2:
        request_id = int(data[1])
        request = get_chat_request(request_id)
        if request is None:
            bot.answer_callback_query(call.id, 'Запрос уже недоступен.', show_alert=True)
            return

        if request['owner_id'] != call.from_user.id:
            bot.answer_callback_query(call.id, 'Этот запрос адресован не вам.', show_alert=True)
            return

        if request['status'] != 'pending':
            safe_remove_markup(call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id, 'Запрос уже обработан.', show_alert=True)
            return

        if action == 'decline':
            update_chat_request_status(request_id, 'declined')
            sync_request_cards(
                request,
                owner_text=request_declined_text(is_requester=False),
                requester_text=request_declined_text(is_requester=True),
            )
            bot.answer_callback_query(call.id)
            return

        finalize_request_as_opened(request)
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)


@bot.message_handler(
    func=lambda message: not (message.content_type == 'text' and message.text and message.text.startswith('/')),
    content_types=HANDLED_CONTENT_TYPES,
)
def handle_content(message: types.Message) -> None:
    sync_user(message.from_user)

    if getattr(message, 'is_topic_message', False) and getattr(message, 'message_thread_id', None):
        handle_topic_content(message)
        return

    handle_main_content(message)


def warn_topic_management_policy(bot_instance: TeleBot) -> None:
    try:
        me = bot_instance.get_me()
    except apihelper.ApiTelegramException:
        return

    if getattr(me, 'allows_users_to_create_topics', False):
        print(
            'WARNING: users can still create/delete private topics for this bot. '
            'Disable it in @BotFather Mini App.'
        )


def bootstrap(bot_instance: TeleBot) -> None:
    init_db()
    warn_topic_management_policy(bot_instance)
    try:
        bot_instance.set_my_commands(
            [
                types.BotCommand('start', 'Показать меню и свою ссылку'),
                types.BotCommand('stop', 'Закрыть текущий тред у обоих'),
            ]
        )
    except apihelper.ApiTelegramException:
        pass


def acquire_instance_lock() -> None:
    global INSTANCE_LOCK
    lock_path = os.path.join(os.path.dirname(__file__), 'anongram.lock')
    lock_file = open(lock_path, 'w')

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Anongram уже запущен в другом процессе. Второй экземпляр завершён.', flush=True)
        sys.exit(0)

    lock_file.write(str(os.getpid()))
    lock_file.flush()
    INSTANCE_LOCK = lock_file

    def _cleanup_lock() -> None:
        try:
            lock_file.seek(0)
            lock_file.truncate()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass

    atexit.register(_cleanup_lock)


if __name__ == '__main__':
    acquire_instance_lock()
    bootstrap(bot)
    bot.infinity_polling(skip_pending=True, allowed_updates=util.update_types)
