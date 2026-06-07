"""DM pairing + authorization for the gateway.

Unknown users are denied by default; they can request an 8-char pairing code that
the operator approves with `aegis pairing approve <platform> <code>`. Per-platform
or global allow-all can be enabled via env (e.g. TELEGRAM_ALLOW_ALL_USERS=1).
"""

from __future__ import annotations

import json
import os
import secrets
import string

from .. import config as cfg
from ..util import atomic_write, now_iso, read_text

_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"


def _path():
    return cfg.sub("pairing.json")


class PairingStore:
    def _load(self) -> dict:
        raw = read_text(_path())
        if not raw.strip():
            return {"approved": {}, "pending": {}}
        try:
            data = json.loads(raw)
            data.setdefault("approved", {})
            data.setdefault("pending", {})
            return data
        except json.JSONDecodeError:
            return {"approved": {}, "pending": {}}

    def _save(self, data: dict) -> None:
        atomic_write(_path(), json.dumps(data, indent=2))
        try:
            os.chmod(_path(), 0o600)
        except OSError:
            pass

    def is_authorized(self, platform: str, user_id: str | None, user_name: str | None = None) -> bool:
        if user_id is None:
            return True  # channels without a user concept (e.g. cli)
        if os.environ.get("GATEWAY_ALLOW_ALL_USERS") in ("1", "true", "True"):
            return True
        if os.environ.get(f"{platform.upper()}_ALLOW_ALL_USERS") in ("1", "true", "True"):
            return True
        allowed = os.environ.get(f"{platform.upper()}_ALLOWED_USERS", "")
        allowed_set = {u.strip() for u in allowed.split(",") if u.strip()}
        names = {user_id}
        if user_name:
            names.update({user_name, f"@{user_name.lstrip('@')}"})
        if names & allowed_set:
            return True
        return user_id in self._load()["approved"].get(platform, [])

    def request_code(self, platform: str, user_id: str) -> str:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        data = self._load()
        data["pending"].setdefault(platform, {})[code] = {"user_id": user_id, "ts": now_iso()}
        self._save(data)
        return code

    def approve(self, platform: str, code_or_user: str) -> bool:
        data = self._load()
        pend = data["pending"].get(platform, {})
        user_id = None
        if code_or_user in pend:
            user_id = pend.pop(code_or_user)["user_id"]
        else:
            user_id = code_or_user  # approve a raw user id directly
        approved = data["approved"].setdefault(platform, [])
        if user_id not in approved:
            approved.append(user_id)
        self._save(data)
        return True

    def revoke(self, platform: str, user_id: str) -> bool:
        data = self._load()
        approved = data["approved"].get(platform, [])
        if user_id in approved:
            approved.remove(user_id)
            self._save(data)
            return True
        return False

    def list(self) -> dict:
        return self._load()


def cmd_pairing(args, config) -> int:
    store = PairingStore()
    action = getattr(args, "action", None) or "list"
    if action == "approve":
        if not args.platform or not args.code:
            print("usage: aegis pairing approve <platform> <code-or-userid>")
            return 1
        store.approve(args.platform, args.code)
        print(f"approved on {args.platform}")
        return 0
    if action == "revoke":
        if not args.platform or not args.code:
            print("usage: aegis pairing revoke <platform> <userid>")
            return 1
        print("revoked" if store.revoke(args.platform, args.code) else "not found")
        return 0
    data = store.list()
    print("approved:")
    for plat, users in data["approved"].items():
        print(f"  {plat}: {', '.join(users) or '(none)'}")
    print("pending:")
    for plat, codes in data["pending"].items():
        for code, info in codes.items():
            print(f"  {plat}: {code} -> {info['user_id']}")
    return 0
