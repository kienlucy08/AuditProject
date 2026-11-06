from dataclasses import dataclass, field


@dataclass
class Record:
    """One user as seen on one platform at snapshot time."""
    key: str                    # stable id on that platform
    email: str                  # lowercased; used to match people across platforms
    name: str = ""
    status: str = "active"      # active | inactive
    attrs: dict = field(default_factory=dict)   # tracked extras: role, user type, ...
    reason: str = ""            # reason supplied by the source itself, if any
    last_login: str = ""        # 'YYYY-MM-DD HH:MM:SS' UTC, '' if never/unknown (not a tracked change)
    inactive_since: str = ""    # optional: when an already-inactive user was deactivated (used to backfill at baseline)


@dataclass
class Event:
    platform: str
    key: str
    email: str
    name: str
    event_type: str             # added | readded | removed | deactivated | reactivated | changed
    field: str = ""
    old_value: str = ""
    new_value: str = ""
    reason: str = ""
    reason_source: str = ""     # '' | source | manual
