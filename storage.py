import json, os
from typing import Any

DATA_FILE = os.path.join(os.path.dirname(__file__), "pgub_data.json")

DEFAULT_USER = {
    "role": "player",
    "target": None,
    "amount": 0,
    "tcard_enabled": False,
    "tcard_interval": 120,
    "daily_enabled": False,
    "farm_hour": None,
    "farm_minute": None
}

class Storage:
    def __init__(self):
        self.file = DATA_FILE
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.file):
            with open(self.file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"users": {}, "_connected": {}, "sessions": []}

    def _save(self):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    # ---------- пользователи ----------
    def register_if_absent(self, user_id: int):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = DEFAULT_USER.copy()
            if int(uid) in __import__("config").GA_IDS:
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
        return self.data["users"].get(str(user_id), DEFAULT_USER.copy())

    def set_user(self, user_id: int, key: str, value: Any):
        uid = str(user_id)
        if uid in self.data["users"]:
            self.data["users"][uid][key] = value
            self._save()

    def get_role(self, user_id: int) -> str:
        return self.get_user(user_id).get("role", "player")

    def set_role(self, user_id: int, role: str):
        self.set_user(user_id, "role", role)

    def is_banned(self, user_id: int) -> bool:
        return self.get_role(user_id) == "banned"

    def count_by_roles(self):
        cnt = {"ga": 0, "admin": 0, "player": 0, "banned": 0}
        for u in self.data["users"].values():
            r = u.get("role", "player")
            if r in cnt:
                cnt[r] += 1
        return cnt

    def all_users(self):
        return list(self.data["users"].keys())

    # ---------- сессии ----------
    def add_session(self, owner_id: int, session_string: str):
        self.data["sessions"].append({
            "owner": owner_id,
            "session": session_string
        })
        self._save()

    def get_sessions(self, owner_id: int = None):
        if owner_id is not None:
            return [s for s in self.data["sessions"] if s["owner"] == owner_id]
        return self.data["sessions"]

    def delete_session(self, index: int):
        if 0 <= index < len(self.data["sessions"]):
            self.data["sessions"].pop(index)
            self._save()