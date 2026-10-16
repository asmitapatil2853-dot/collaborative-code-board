from datetime import datetime
import os
import re
import sqlite3
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_user,
    logout_user,
    login_required,
)
from flask_socketio import SocketIO, join_room, emit
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "codeboard.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SYNCBOARD_SECRET_KEY",
    "dev-only-change-this-syncboard-secret-key",
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SYNCBOARD_HTTPS", "0") == "1"
)

csrf = CSRFProtect(app)

login_manager = LoginManager(app)
login_manager.login_view = "auth"
login_manager.login_message = "Please log in to access SyncBoard."

socketio = SocketIO(app, cors_allowed_origins="*")

ALLOWED_LANGUAGES = {
    "python", "javascript", "java", "html", "css", "sql"
}
ALLOWED_STATUSES = {
    "Pending", "Approved", "Changes Requested"
}


class User(UserMixin):
    def __init__(self, user_id, username, email):
        self.id = str(user_id)
        self.username = username
        self.email = email


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                room TEXT PRIMARY KEY,
                code TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT 'python',
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                author TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_members (
                room TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL
                    CHECK(role IN ('owner', 'editor', 'viewer')),
                created_at TEXT NOT NULL,
                PRIMARY KEY (room, user_id),
                FOREIGN KEY(room)
                    REFERENCES workspaces(room) ON DELETE CASCADE,
                FOREIGN KEY(user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # ३. Code Version History Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS code_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                code_content TEXT NOT NULL,
                saved_by TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(room) REFERENCES workspaces(room) ON DELETE CASCADE
            )
        """)


init_db()


@login_manager.user_loader
def load_user(user_id):
    try:
        numeric_id = int(user_id)
    except (TypeError, ValueError):
        return None

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, email FROM users WHERE id = ?",
            (numeric_id,),
        ).fetchone()

    if row is None:
        return None

    return User(row["id"], row["username"], row["email"])


def valid_room(room):
    return bool(
        room and re.fullmatch(r"[A-Za-z0-9_-]{2,40}", room)
    )


def get_workspace(room):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT room, code, language, updated_at
            FROM workspaces
            WHERE room = ?
            """,
            (room,),
        ).fetchone()

    return dict(row) if row else None


def get_comments(room):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, room, author, body, status, created_at
            FROM comments
            WHERE room = ?
            ORDER BY id DESC
            LIMIT 100
            """,
            (room,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_member_role(room, user_id):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT role
            FROM workspace_members
            WHERE room = ? AND user_id = ?
            """,
            (room, int(user_id)),
        ).fetchone()

    return row["role"] if row else None


def require_socket_member(room, allowed_roles=None):
    if not current_user.is_authenticated:
        emit("app_error", {"message": "Please log in first."})
        return None

    if not valid_room(room):
        emit("app_error", {"message": "Invalid workspace name."})
        return None

    role = get_member_role(room, current_user.id)

    if role is None:
        emit(
            "app_error",
            {"message": "You are not a member of this workspace."},
        )
        return None

    if allowed_roles and role not in allowed_roles:
        emit(
            "app_error",
            {"message": "You do not have permission for this action."},
        )
        return None

    return role


@app.route("/auth")
def auth():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("auth.html")


@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not re.fullmatch(r"[A-Za-z0-9_-]{3,30}", username):
        flash(
            "Username must be 3–30 characters using letters, "
            "numbers, underscores or hyphens."
        )
        return redirect(url_for("auth"))

    if (
        not email
        or len(email) > 254
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    ):
        flash("Please enter a valid email address.")
        return redirect(url_for("auth"))

    if len(password) < 8:
        flash("Password must contain at least 8 characters.")
        return redirect(url_for("auth"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users
                    (username, email, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    username,
                    email,
                    generate_password_hash(password),
                    now,
                ),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        flash("That username or email is already registered.")
        return redirect(url_for("auth"))

    login_user(User(user_id, username, email))
    flash("Account created successfully.")
    return redirect(url_for("index"))


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, username, email, password_hash
            FROM users
            WHERE username = ? COLLATE NOCASE
            """,
            (username,),
        ).fetchone()

    if row is None or not check_password_hash(
        row["password_hash"], password
    ):
        flash("Invalid username or password.")
        return redirect(url_for("auth"))

    login_user(User(row["id"], row["username"], row["email"]))
    flash("Welcome back.")
    return redirect(url_for("index"))


# 1. Logout Action
@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    flash("You have logged out.")
    return redirect(url_for("auth"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@socketio.on("join_workspace")
def on_join(data):
    if not current_user.is_authenticated:
        emit("app_error", {"message": "Please log in first."})
        return

    data = data or {}
    room = str(data.get("room", "")).strip()

    if not valid_room(room):
        emit("app_error", {"message": "Room name must be 2–40 characters."})
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        workspace = conn.execute(
            "SELECT room FROM workspaces WHERE room = ?",
            (room,),
        ).fetchone()

        if workspace is None:
            conn.execute(
                """
                INSERT INTO workspaces
                    (room, code, language, updated_at)
                VALUES (?, ?, 'python', ?)
                """,
                (room, "# Welcome! Start writing code here.", now),
            )
            conn.execute(
                """
                INSERT INTO workspace_members
                    (room, user_id, role, created_at)
                VALUES (?, ?, 'owner', ?)
                """,
                (room, int(current_user.id), now),
            )

        member = conn.execute(
            """
            SELECT role
            FROM workspace_members
            WHERE room = ? AND user_id = ?
            """,
            (room, int(current_user.id)),
        ).fetchone()

    if member is None:
        emit(
            "app_error",
            {"message": "Access denied. You are not a member of this workspace."},
        )
        return

    join_room(room)
    workspace_data = get_workspace(room)

    emit(
        "workspace_state",
        {
            "room": room,
            "code": workspace_data["code"],
            "language": workspace_data["language"],
            "updated_at": workspace_data["updated_at"],
            "comments": get_comments(room),
            "username": current_user.username,
            "role": member["role"]
        },
    )

    emit(
        "member_joined",
        {"username": current_user.username, "role": member["role"]},
        to=room,
        include_self=False,
    )


# 2. Workspace Sharing Socket Event
@socketio.on("share_workspace")
def on_share_workspace(data):
    data = data or {}
    room = str(data.get("room", "")).strip()
    target_username = str(data.get("username", "")).strip()
    role = str(data.get("role", "viewer")).lower()

    if role not in ["editor", "viewer"]:
        emit("app_error", {"message": "Invalid role selected."})
        return

    # केवळ 'owner' दुसऱ्यांना permissions देऊ शकतो
    if require_socket_member(room, {"owner"}) is None:
        emit("app_error", {"message": "Only workspace owner can invite or change roles."})
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        user_row = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (target_username,)
        ).fetchone()

        if not user_row:
            emit("app_error", {"message": f"User '{target_username}' not found."})
            return

        target_user_id = user_row["id"]

        conn.execute("""
            INSERT INTO workspace_members (room, user_id, role, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(room, user_id) DO UPDATE SET role=excluded.role
        """, (room, target_user_id, role, now))

    emit("share_success", {
        "message": f"Successfully granted '{role}' access to {target_username}."
    })


# 3. Code Version History Socket Events
@socketio.on("save_version")
def on_save_version(data):
    data = data or {}
    room = str(data.get("room", "")).strip()
    note = str(data.get("note", "Manual checkpoint")).strip()[:100]

    role = require_socket_member(room, {"owner", "editor"})
    if role is None:
        return

    workspace = get_workspace(room)
    if not workspace:
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO code_versions (room, code_content, saved_by, note, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (room, workspace["code"], current_user.username, note, now))
        version_id = cursor.lastrowid

    emit("version_saved", {
        "id": version_id,
        "note": note,
        "saved_by": current_user.username,
        "created_at": now
    }, to=room)


@socketio.on("get_versions")
def on_get_versions(data):
    data = data or {}
    room = str(data.get("room", "")).strip()

    if require_socket_member(room) is None:
        return

    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, saved_by, note, created_at
            FROM code_versions
            WHERE room = ?
            ORDER BY id DESC
            LIMIT 20
        """, (room,)).fetchall()

    versions = [dict(r) for r in rows]
    emit("versions_list", {"versions": versions})


@socketio.on("restore_version")
def on_restore_version(data):
    data = data or {}
    room = str(data.get("room", "")).strip()
    version_id = data.get("version_id")

    role = require_socket_member(room, {"owner", "editor"})
    if role is None:
        return

    with get_db() as conn:
        row = conn.execute("""
            SELECT code_content FROM code_versions WHERE id = ? AND room = ?
        """, (version_id, room)).fetchone()

        if not row:
            emit("app_error", {"message": "Version not found."})
            return

        restored_code = row["code_content"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute("""
            UPDATE workspaces
            SET code = ?, updated_at = ?
            WHERE room = ?
        """, (restored_code, now, room))

    emit("code_changed", {
        "code": restored_code,
        "language": "python",
        "updated_at": now,
        "restored_by": current_user.username
    }, to=room)


@socketio.on("code_change")
def on_code_change(data):
    data = data or {}
    room = str(data.get("room", "")).strip()

    role = require_socket_member(room, {"owner", "editor"})
    if role is None:
        return

    code = str(data.get("code", ""))[:100000]
    language = str(data.get("language", "python"))

    if language not in ALLOWED_LANGUAGES:
        language = "python"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        conn.execute(
            """
            UPDATE workspaces
            SET code = ?, language = ?, updated_at = ?
            WHERE room = ?
            """,
            (code, language, now, room),
        )

    emit(
        "code_changed",
        {
            "code": code,
            "language": language,
            "updated_at": now,
        },
        to=room,
        include_self=False,
    )


@socketio.on("add_comment")
def on_add_comment(data):
    data = data or {}
    room = str(data.get("room", "")).strip()
    body = str(data.get("body", "")).strip()[:1000]

    if require_socket_member(room) is None:
        return

    if not body:
        emit("app_error", {"message": "Please enter a review comment."})
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO comments
                (room, author, body, status, created_at)
            VALUES (?, ?, ?, 'Pending', ?)
            """,
            (room, current_user.username, body, now),
        )
        comment_id = cursor.lastrowid

    emit(
        "new_comment",
        {
            "id": comment_id,
            "room": room,
            "author": current_user.username,
            "body": body,
            "status": "Pending",
            "created_at": now,
        },
        to=room,
    )


@socketio.on("update_comment_status")
def on_update_comment_status(data):
    data = data or {}
    room = str(data.get("room", "")).strip()

    role = require_socket_member(room, {"owner", "editor"})
    if role is None:
        return

    try:
        comment_id = int(data.get("id"))
    except (TypeError, ValueError):
        return

    status = str(data.get("status", "Pending"))

    if status not in ALLOWED_STATUSES:
        return

    with get_db() as conn:
        conn.execute(
            """
            UPDATE comments
            SET status = ?
            WHERE id = ? AND room = ?
            """,
            (status, comment_id, room),
        )

    emit(
        "comment_status_changed",
        {"id": comment_id, "status": status},
        to=room,
    )


if __name__ == "__main__":
    socketio.run(app, debug=True, port=5000)