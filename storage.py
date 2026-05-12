import json
import os
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DATA_FILE = os.path.join(DATA_DIR, "pgub_data.json")

from config import GA_IDS

DEFAULT_USER = {
    "role": "player",
    "connected": False,
    "session_string": None,
    "target": None,
    "amount": 0,
    "tcard_enabled": False,
    "tcard_interval": 120,
    "daily_enabled": False,
    "autofarm_enabled": False
}

class Storage:
    def __init__(self):
        self.file = DATA_FILE
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.file):
            with open(self.file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"users": {}, "_connected": {}}

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    # ---------- пользователи ----------
    def register_if_absent(self, user_id: int):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = DEFAULT_USER.copy()
            if int(uid) in GA_IDS:
                self.data["users"][uid]["role"] = "ga"
            self._save()

    def set_connection(self, user_id: int, bc_id: str):
        self.data["_connected"][str(user_id)] = bc_id
        self._save()

    def remove_connection(self, user_id: int):
        self.data["_connected"].pop(str(user_id), None)
        self._save()

    def is_connected(self, user_id: int) -> bool:
        return str(user_id) in self.data["_connected"]

    def get_user(self, user_id: int):
        uid = str(user_id)
        user = self.data["users"].get(uid, DEFAULT_USER.copy())
        # Гарантия роли для фиксированных ГА
        if int(uid) in GA_IDS:
            user["role"] = "ga"
        return user

    def set_user(self, user_id: int, key: str, value: Any):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = DEFAULT_USER.copy()
            if int(uid) in GA_IDS:
                self.data["users"][uid]["role"] = "ga"
        self.data["users"][uid][key] = value
        self._save()

    def get_role(self, user_id: int) -> str:
        if int(user_id) in GA_IDS:
            return "ga"
        return self.get_user(user_id).get("role", "player")

    def set_role(self, user_id: int, role: str):
        # Не даём изменить роль фиксированных ГА
        if int(user_id) in GA_IDS and role != "ga":
            return
        self.set_user(user_id, "role", role)

    def is_banned(self, user_id: int) -> bool:
        return self.get_role(user_id) == "banned"

    def count_by_roles(self):
        cnt = {"ga": 0, "admin": 0, "player": 0, "banned": 0}
        for uid, u in self.data["users"].items():
            r = "ga" if int(uid) in GA_IDS else u.get("role", "player")
            if r in cnt:
                cnt[r] += 1
        return cnt

    def all_users(self):
        return list(self.data["users"].keys())

    def get_all_sessions(self):
        """Возвращает список (user_id, session_string) для всех connected пользователей."""
        sessions = []
        for uid, u in self.data["users"].items():
            if u.get("connected") and u.get("session_string"):
                sessions.append((int(uid), u["session_string"]))
        return sessions

    def remove_session(self, user_id: int):
        self.set_user(user_id, "connected", False)
        self.set_user(user_id, "session_string", None)