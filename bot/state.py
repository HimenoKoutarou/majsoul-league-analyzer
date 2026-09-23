"""机器人消费游标持久化。"""
import json
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.event_cursor = 0
        self.subscriptions: set[str] | None = None
        self.last_digest_date: str | None = None
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.event_cursor = max(0, int(data.get("event_cursor", 0)))
            last_digest_date = data.get("last_digest_date")
            self.last_digest_date = str(last_digest_date) if last_digest_date else None
            if "subscriptions" in data:
                self.subscriptions = {
                    str(group_id) for group_id in data.get("subscriptions") or []
                    if str(group_id).strip()
                }
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            self.event_cursor = 0
            self.subscriptions = None
            self.last_digest_date = None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        data = {"event_cursor": self.event_cursor}
        if self.last_digest_date:
            data["last_digest_date"] = self.last_digest_date
        if self.subscriptions is not None:
            data["subscriptions"] = sorted(self.subscriptions)
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        temporary.replace(self.path)

    def target_groups(self, configured_groups: tuple[str, ...]) -> tuple[str, ...]:
        """未自定义订阅时使用环境变量；一旦操作过订阅则以持久化列表为准。"""
        groups = self.subscriptions if self.subscriptions is not None else configured_groups
        return tuple(sorted({str(group_id) for group_id in groups if str(group_id).strip()}))

    def subscribe(self, group_id: str, configured_groups: tuple[str, ...]) -> bool:
        if self.subscriptions is None:
            self.subscriptions = set(configured_groups)
        before = len(self.subscriptions)
        self.subscriptions.add(str(group_id))
        self.save()
        return len(self.subscriptions) != before

    def digest_sent(self, day: str) -> bool:
        return self.last_digest_date == day

    def mark_digest_sent(self, day: str):
        self.last_digest_date = day
        self.save()

    def unsubscribe(self, group_id: str, configured_groups: tuple[str, ...]) -> bool:
        if self.subscriptions is None:
            self.subscriptions = set(configured_groups)
        before = len(self.subscriptions)
        self.subscriptions.discard(str(group_id))
        self.save()
        return len(self.subscriptions) != before
