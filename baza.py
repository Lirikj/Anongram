import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).with_name('users.db')

USER_COLUMNS = {
    'user_id',
    'username',
    'name',
    'premium',
    'premium_expiry',
    'user_emoji',
    'custom_emoji',
    'ghost_mode',
    'link',
    'custom_link',
}

USER_SETTINGS_COLUMNS = {
    'user_id',
    'main_message_id',
    'main_thread_id',
    'notice_message_id',
}

CHAT_REQUEST_COLUMNS = {
    'id',
    'requester_id',
    'owner_id',
    'owner_message_id',
    'requester_message_id',
    'status',
    'created_at',
    'updated_at',
}

CONVERSATION_COLUMNS = {
    'id',
    'owner_id',
    'guest_id',
    'emoji',
    'owner_thread_id',
    'guest_thread_id',
    'owner_status_message_id',
    'guest_status_message_id',
    'request_id',
    'status',
    'created_at',
    'closed_at',
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row['name'] for row in conn.execute(f'PRAGMA table_info({table_name})')}


def init_db() -> None:
    with _connect() as conn:
        if not _table_exists(conn, 'users'):
            conn.execute(
                '''
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    name TEXT,
                    premium INTEGER,
                    premium_expiry TEXT,
                    user_emoji TEXT DEFAULT '💌',
                    custom_emoji TEXT,
                    ghost_mode INTEGER,
                    link TEXT,
                    custom_link TEXT
                )
                '''
            )
        else:
            missing = USER_COLUMNS - _table_columns(conn, 'users')
            alter_map = {
                'username': 'ALTER TABLE users ADD COLUMN username TEXT',
                'name': 'ALTER TABLE users ADD COLUMN name TEXT',
                'premium': 'ALTER TABLE users ADD COLUMN premium INTEGER',
                'premium_expiry': 'ALTER TABLE users ADD COLUMN premium_expiry TEXT',
                'user_emoji': "ALTER TABLE users ADD COLUMN user_emoji TEXT DEFAULT '💌'",
                'custom_emoji': 'ALTER TABLE users ADD COLUMN custom_emoji TEXT',
                'ghost_mode': 'ALTER TABLE users ADD COLUMN ghost_mode INTEGER',
                'link': 'ALTER TABLE users ADD COLUMN link TEXT',
                'custom_link': 'ALTER TABLE users ADD COLUMN custom_link TEXT',
            }
            for column in missing:
                conn.execute(alter_map[column])

        if not _table_exists(conn, 'ratings'):
            conn.execute(
                '''
                CREATE TABLE ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER,
                    rater_id INTEGER,
                    rating INTEGER,
                    UNIQUE(target_id, rater_id)
                )
                '''
            )

        conn.execute(
            '''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    main_message_id INTEGER,
                    main_thread_id INTEGER,
                    notice_message_id INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
                '''
        )

        missing_user_settings = USER_SETTINGS_COLUMNS - _table_columns(conn, 'user_settings')
        alter_settings_map = {
            'main_message_id': 'ALTER TABLE user_settings ADD COLUMN main_message_id INTEGER',
            'main_thread_id': 'ALTER TABLE user_settings ADD COLUMN main_thread_id INTEGER',
            'notice_message_id': 'ALTER TABLE user_settings ADD COLUMN notice_message_id INTEGER',
        }
        for column in missing_user_settings:
            if column == 'user_id':
                continue
            conn.execute(alter_settings_map[column])

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                target_user_id INTEGER NOT NULL,
                owner_user_id INTEGER NOT NULL,
                chooser_chat_id INTEGER,
                chooser_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            '''
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS chat_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                owner_message_id INTEGER,
                requester_message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )

        missing_chat_requests = CHAT_REQUEST_COLUMNS - _table_columns(conn, 'chat_requests')
        alter_chat_requests_map = {
            'owner_message_id': 'ALTER TABLE chat_requests ADD COLUMN owner_message_id INTEGER',
            'requester_message_id': 'ALTER TABLE chat_requests ADD COLUMN requester_message_id INTEGER',
            'status': "ALTER TABLE chat_requests ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
            'created_at': 'ALTER TABLE chat_requests ADD COLUMN created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP',
            'updated_at': 'ALTER TABLE chat_requests ADD COLUMN updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP',
        }
        for column in missing_chat_requests:
            if column in {'id', 'requester_id', 'owner_id'}:
                continue
            conn.execute(alter_chat_requests_map[column])

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                guest_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                owner_thread_id INTEGER NOT NULL,
                guest_thread_id INTEGER NOT NULL,
                request_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT
            )
            '''
        )

        missing_conversations = CONVERSATION_COLUMNS - _table_columns(conn, 'conversations')
        alter_conversations_map = {
            'owner_status_message_id': 'ALTER TABLE conversations ADD COLUMN owner_status_message_id INTEGER',
            'guest_status_message_id': 'ALTER TABLE conversations ADD COLUMN guest_status_message_id INTEGER',
            'request_id': 'ALTER TABLE conversations ADD COLUMN request_id INTEGER',
        }
        for column in missing_conversations:
            if column in {'id', 'owner_id', 'guest_id', 'emoji', 'owner_thread_id', 'guest_thread_id', 'status', 'created_at', 'closed_at'}:
                continue
            conn.execute(alter_conversations_map[column])

        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_conversations_owner_guest ON conversations(owner_id, guest_id, status)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_conversations_owner_thread ON conversations(owner_id, owner_thread_id, status)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_conversations_guest_thread ON conversations(guest_id, guest_thread_id, status)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_conversations_request_id ON conversations(request_id)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_chat_requests_owner_status ON chat_requests(owner_id, status)'
        )


def upsert_user(user_id: int, username: Optional[str], name: str) -> bool:
    with _connect() as conn:
        exists = conn.execute(
            'SELECT 1 FROM users WHERE user_id = ?',
            (user_id,),
        ).fetchone() is not None
        conn.execute(
            '''
            INSERT INTO users (user_id, username, name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                name = excluded.name
            ''',
            (user_id, username, name),
        )
        conn.execute(
            'INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)',
            (user_id,),
        )
        return not exists


def get_user(user_id: int):
    with _connect() as conn:
        return conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()


def get_main_message_id(user_id: int) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute(
            'SELECT main_message_id FROM user_settings WHERE user_id = ?',
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return row['main_message_id']


def get_main_thread_id(user_id: int) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute(
            'SELECT main_thread_id FROM user_settings WHERE user_id = ?',
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return row['main_thread_id']


def get_notice_message_id(user_id: int) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute(
            'SELECT notice_message_id FROM user_settings WHERE user_id = ?',
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return row['notice_message_id']


def set_main_message_id(user_id: int, message_id: Optional[int]) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            INSERT INTO user_settings (user_id, main_message_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                main_message_id = excluded.main_message_id
            ''',
            (user_id, message_id),
        )


def set_main_thread_id(user_id: int, thread_id: Optional[int]) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            INSERT INTO user_settings (user_id, main_thread_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                main_thread_id = excluded.main_thread_id
            ''',
            (user_id, thread_id),
        )


def set_notice_message_id(user_id: int, message_id: Optional[int]) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            INSERT INTO user_settings (user_id, notice_message_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                notice_message_id = excluded.notice_message_id
            ''',
            (user_id, message_id),
        )


def set_user_state(
    user_id: int,
    state: str,
    target_user_id: int,
    owner_user_id: int,
    chooser_chat_id: Optional[int] = None,
    chooser_message_id: Optional[int] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            INSERT INTO user_states (
                user_id,
                state,
                target_user_id,
                owner_user_id,
                chooser_chat_id,
                chooser_message_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                state = excluded.state,
                target_user_id = excluded.target_user_id,
                owner_user_id = excluded.owner_user_id,
                chooser_chat_id = excluded.chooser_chat_id,
                chooser_message_id = excluded.chooser_message_id,
                created_at = CURRENT_TIMESTAMP
            ''',
            (user_id, state, target_user_id, owner_user_id, chooser_chat_id, chooser_message_id),
        )


def get_user_state(user_id: int):
    with _connect() as conn:
        return conn.execute(
            'SELECT * FROM user_states WHERE user_id = ?',
            (user_id,),
        ).fetchone()


def clear_user_state(user_id: int) -> None:
    with _connect() as conn:
        conn.execute('DELETE FROM user_states WHERE user_id = ?', (user_id,))


def create_chat_request(requester_id: int, owner_id: int) -> tuple[int, bool]:
    with _connect() as conn:
        existing = conn.execute(
            '''
            SELECT id FROM chat_requests
            WHERE requester_id = ? AND owner_id = ? AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            ''',
            (requester_id, owner_id),
        ).fetchone()
        if existing is not None:
            conn.execute(
                '''
                UPDATE chat_requests
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (existing['id'],),
            )
            return int(existing['id']), False

        cursor = conn.execute(
            '''
            INSERT INTO chat_requests (requester_id, owner_id)
            VALUES (?, ?)
            ''',
            (requester_id, owner_id),
        )
        return int(cursor.lastrowid), True


def get_pending_chat_request_between(first_user_id: int, second_user_id: int):
    with _connect() as conn:
        return conn.execute(
            '''
            SELECT * FROM chat_requests
            WHERE (
                (requester_id = ? AND owner_id = ?)
                OR
                (requester_id = ? AND owner_id = ?)
            )
            AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            ''',
            (first_user_id, second_user_id, second_user_id, first_user_id),
        ).fetchone()


def get_chat_request(request_id: int):
    with _connect() as conn:
        return conn.execute(
            'SELECT * FROM chat_requests WHERE id = ?',
            (request_id,),
        ).fetchone()


def get_latest_chat_request_between(first_user_id: int, second_user_id: int, statuses: Optional[tuple[str, ...]] = None):
    params = [first_user_id, second_user_id, second_user_id, first_user_id]
    status_clause = ''

    if statuses:
        placeholders = ', '.join('?' for _ in statuses)
        status_clause = f'AND status IN ({placeholders})'
        params.extend(statuses)

    query = f'''
        SELECT * FROM chat_requests
        WHERE (
            (requester_id = ? AND owner_id = ?)
            OR
            (requester_id = ? AND owner_id = ?)
        )
        {status_clause}
        ORDER BY id DESC
        LIMIT 1
    '''
    with _connect() as conn:
        return conn.execute(query, params).fetchone()


def set_chat_request_message(request_id: int, owner_message_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            UPDATE chat_requests
            SET owner_message_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (owner_message_id, request_id),
        )


def set_chat_request_requester_message(request_id: int, requester_message_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            UPDATE chat_requests
            SET requester_message_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (requester_message_id, request_id),
        )


def update_chat_request_status(request_id: int, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            UPDATE chat_requests
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (status, request_id),
        )


def get_active_conversation(first_user_id: int, second_user_id: int):
    with _connect() as conn:
        return conn.execute(
            '''
            SELECT * FROM conversations
            WHERE (
                (owner_id = ? AND guest_id = ?)
                OR
                (owner_id = ? AND guest_id = ?)
            )
            AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            ''',
            (first_user_id, second_user_id, second_user_id, first_user_id),
        ).fetchone()


def create_conversation(
    owner_id: int,
    guest_id: int,
    emoji: str,
    owner_thread_id: int,
    guest_thread_id: int,
    owner_status_message_id: Optional[int] = None,
    guest_status_message_id: Optional[int] = None,
    request_id: Optional[int] = None,
) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            '''
            INSERT INTO conversations (
                owner_id,
                guest_id,
                emoji,
                owner_thread_id,
                guest_thread_id,
                owner_status_message_id,
                guest_status_message_id,
                request_id,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
            ''',
            (
                owner_id,
                guest_id,
                emoji,
                owner_thread_id,
                guest_thread_id,
                owner_status_message_id,
                guest_status_message_id,
                request_id,
            ),
        )
        return int(cursor.lastrowid)


def get_conversation_by_thread(user_id: int, thread_id: int, active_only: bool = True):
    status_clause = "AND status = 'active'" if active_only else ''
    query = f'''
        SELECT * FROM conversations
        WHERE (
            (owner_id = ? AND owner_thread_id = ?)
            OR
            (guest_id = ? AND guest_thread_id = ?)
        )
        {status_clause}
        ORDER BY id DESC
        LIMIT 1
    '''
    with _connect() as conn:
        return conn.execute(query, (user_id, thread_id, user_id, thread_id)).fetchone()


def close_conversation(conversation_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            '''
            UPDATE conversations
            SET status = 'closed', closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            ''',
            (conversation_id,),
        )
