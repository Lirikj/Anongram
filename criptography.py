import base64
import hashlib
from typing import Tuple


SECRET = 0x5A3C9E7F1B2D4C68
TOPIC_LABEL_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
TOPIC_EMOJI_ALPHABET = [
    '😀', '😃', '😄', '😁', '😆', '😅', '😂', '🤣',
    '😊', '😇', '🙂', '🙃', '😉', '😍', '🥰', '😘',
    '😋', '😎', '🤩', '🥳', '🤖', '👻', '👽', '🤠',
    '😺', '😸', '😹', '😻', '😼', '🙈', '🙉', '🙊',
    '💌', '💎', '🔒', '🔑', '🧩', '🎯', '🎲', '🎮',
    '🎨', '🎧', '🎭', '🎪', '🚀', '🛸', '🌙', '⭐',
    '🌈', '⚡', '🔥', '🌊', '🍀', '🌸', '🍓', '🍉',
    '🍒', '🥝', '🍪', '🧁', '🍿', '☕', '🍯',
]


def encode(user_id: int) -> str:
    x = user_id ^ SECRET
    return base64.urlsafe_b64encode(x.to_bytes(8, 'big')).decode().rstrip('=')


def decode(token: str) -> int:
    padded = token + '=' * (-len(token) % 4)
    x = int.from_bytes(base64.urlsafe_b64decode(padded), 'big')
    return x ^ SECRET


def normalize_topic_users(first_user_id: int, second_user_id: int) -> Tuple[int, int]:
    if first_user_id <= second_user_id:
        return first_user_id, second_user_id
    return second_user_id, first_user_id


def build_topic_digest(first_user_id: int, second_user_id: int, length: int) -> bytes:
    left_user_id, right_user_id = normalize_topic_users(first_user_id, second_user_id)
    payload = f'{left_user_id}:{right_user_id}'.encode()
    key = SECRET.to_bytes(8, 'big', signed=False)
    return hashlib.blake2s(payload, key=key, digest_size=length).digest()


def encode_topic_label(first_user_id: int, second_user_id: int, length: int = 4) -> str:
    digest = build_topic_digest(first_user_id, second_user_id, length)
    alphabet_size = len(TOPIC_LABEL_ALPHABET)
    return ''.join(TOPIC_LABEL_ALPHABET[byte % alphabet_size] for byte in digest)


def encode_topic_emoji(first_user_id: int, second_user_id: int, length: int = 3) -> str:
    digest = build_topic_digest(first_user_id, second_user_id, length)
    alphabet_size = len(TOPIC_EMOJI_ALPHABET)
    return ''.join(TOPIC_EMOJI_ALPHABET[byte % alphabet_size] for byte in digest)


def encode_topic_code(first_user_id: int, second_user_id: int) -> str:
    return f'{encode_topic_label(first_user_id, second_user_id)} {encode_topic_emoji(first_user_id, second_user_id)}'
