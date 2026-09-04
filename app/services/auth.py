import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Literal

UserRole = Literal["triage_officer", "reviewer", "auditor"]
SESSION_COOKIE_NAME = "minwon_demo_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 210_000


@dataclass(frozen=True)
class DemoUser:
    username: str
    display_name: str
    role: UserRole
    salt: str
    password_hash: str


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    display_name: str
    role: UserRole
    csrf_token: str
    expires_at: int

    @property
    def role_label(self) -> str:
        return {
            "triage_officer": "분류 담당",
            "reviewer": "검토 승인",
            "auditor": "감사 조회",
        }[self.role]

    @property
    def can_triage(self) -> bool:
        return self.role in {"triage_officer", "reviewer"}

    @property
    def can_review(self) -> bool:
        return self.role == "reviewer"

    @property
    def can_write(self) -> bool:
        return self.can_triage

    @property
    def permissions(self) -> list[str]:
        permissions = ["complaint:read", "audit:read"]
        if self.can_triage:
            permissions.extend(
                [
                    "complaint:create",
                    "complaint:reprocess",
                    "location:confirm",
                    "duplicate:review",
                ]
            )
        if self.can_review:
            permissions.append("complaint:approve")
        return permissions


_DEMO_USERS = (
    DemoUser(
        username="triage.demo",
        display_name="분류 담당자",
        role="triage_officer",
        salt="seongnam-triage.demo",
        password_hash="3dee97092f399391c3ca54efd514ff93c7b7d0530f28e45b31c62e34e8351352",
    ),
    DemoUser(
        username="review.demo",
        display_name="검토 책임자",
        role="reviewer",
        salt="seongnam-review.demo",
        password_hash="569859a68af582bd35079d6888dcbe9039e61b3f25ea9decc5d53a5cc3ade51a",
    ),
    DemoUser(
        username="audit.demo",
        display_name="감사 열람자",
        role="auditor",
        salt="seongnam-audit.demo",
        password_hash="ea7fbe32f28040c1f8e1256cdd97d9c047ff0613d01c8b5480cfa5486b347449",
    ),
)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class AuthManager:
    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._users = {user.username: user for user in _DEMO_USERS}

    @property
    def demo_users(self) -> tuple[DemoUser, ...]:
        return _DEMO_USERS

    @staticmethod
    def _password_digest(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            PBKDF2_ITERATIONS,
        ).hex()

    def authenticate(self, username: str, password: str) -> DemoUser | None:
        normalized = username.strip().lower()
        user = self._users.get(normalized)
        salt = user.salt if user else "seongnam-unknown-demo-user"
        expected = user.password_hash if user else "0" * 64
        actual = self._password_digest(password, salt)
        if user is None or not hmac.compare_digest(actual, expected):
            return None
        return user

    def create_session_token(self, user: DemoUser) -> str:
        payload = {
            "csrf": secrets.token_urlsafe(24),
            "exp": int(time.time()) + SESSION_TTL_SECONDS,
            "role": user.role,
            "sub": user.username,
        }
        encoded = _b64encode(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        )
        signature = _b64encode(hmac.new(self._secret, encoded.encode("ascii"), "sha256").digest())
        return f"{encoded}.{signature}"

    def read_session_token(self, token: str | None) -> AuthenticatedUser | None:
        if not token:
            return None
        try:
            encoded, supplied_signature = token.split(".", maxsplit=1)
            expected_signature = _b64encode(
                hmac.new(self._secret, encoded.encode("ascii"), "sha256").digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                return None
            payload = json.loads(_b64decode(encoded))
            username = payload["sub"]
            role = payload["role"]
            csrf_token = payload["csrf"]
            expires_at = payload["exp"]
            if not all(
                (
                    isinstance(username, str),
                    isinstance(role, str),
                    isinstance(csrf_token, str),
                    isinstance(expires_at, int),
                )
            ):
                return None
            user = self._users.get(username)
            if user is None or user.role != role or expires_at <= int(time.time()):
                return None
            return AuthenticatedUser(
                username=user.username,
                display_name=user.display_name,
                role=user.role,
                csrf_token=csrf_token,
                expires_at=expires_at,
            )
        except (
            binascii.Error,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return None


def get_authenticated_user(request: object) -> AuthenticatedUser:
    state = getattr(request, "state", None)
    user = getattr(state, "current_user", None)
    if not isinstance(user, AuthenticatedUser):
        raise PermissionError("authentication_required")
    return user


def require_role(user: AuthenticatedUser, *roles: UserRole) -> None:
    if user.role not in roles:
        raise PermissionError("insufficient_role")


def require_csrf(user: AuthenticatedUser, supplied_token: str | None) -> None:
    if not supplied_token or not hmac.compare_digest(user.csrf_token, supplied_token):
        raise PermissionError("invalid_csrf_token")
