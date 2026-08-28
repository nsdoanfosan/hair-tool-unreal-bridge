"""Small, process-safe storage for shared Hair Tool material profiles."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid


REGISTRY_SCHEMA = "htue.profile-registry.v1"
REGISTRY_VERSION = 1
LOCK_STALE_SECONDS = 30.0


class RegistryError(RuntimeError):
    pass


class RegistryLockError(RegistryError):
    pass


class ProfileConflictError(RegistryError):
    def __init__(self, fields):
        self.fields = tuple(sorted(str(field) for field in fields))
        super().__init__("Profile fields changed in another Blender: " + ", ".join(self.fields))


def empty_registry():
    return {
        "schema": REGISTRY_SCHEMA,
        "version": REGISTRY_VERSION,
        "profiles": {},
    }


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def values_hash(values):
    return hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_registry(data):
    if not isinstance(data, dict):
        raise RegistryError("Profile registry root must be a JSON object")
    if data.get("schema") != REGISTRY_SCHEMA:
        raise RegistryError(f"Unsupported profile registry schema: {data.get('schema')!r}")
    if int(data.get("version", 0)) != REGISTRY_VERSION:
        raise RegistryError(f"Unsupported profile registry version: {data.get('version')!r}")
    if not isinstance(data.get("profiles"), dict):
        raise RegistryError("Profile registry has no profiles object")
    return data


def load_registry(path):
    path = Path(path)
    if not path.is_file():
        return empty_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"Could not read profile registry {path}: {exc}") from exc
    return validate_registry(data)


def _lock_path(path):
    path = Path(path)
    return path.with_name(path.name + ".lock")


@contextmanager
def registry_lock(path, stale_seconds=LOCK_STALE_SECONDS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(path)
    token = f"{os.getpid()}:{uuid.uuid4()}"
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
        except OSError:
            age = 0.0
        if age <= float(stale_seconds):
            raise RegistryLockError(f"Profile registry is busy: {lock_path}")
        try:
            lock_path.unlink()
        except OSError as exc:
            raise RegistryLockError(f"Stale profile lock could not be removed: {exc}") from exc
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RegistryLockError(f"Profile registry became busy: {lock_path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            if lock_path.read_text(encoding="utf-8") == token:
                lock_path.unlink()
        except OSError:
            pass


def save_registry(path, data):
    path = Path(path)
    validate_registry(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise RegistryError(f"Could not save profile registry {path}: {exc}") from exc
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def merge_changed_values(remote_values, base_values, local_values, changed_fields):
    remote_values = dict(remote_values or {})
    base_values = dict(base_values or {})
    local_values = dict(local_values or {})
    changed_fields = {str(field) for field in changed_fields}
    conflicts = []
    merged = dict(remote_values)
    missing = object()
    for field in sorted(changed_fields):
        local_value = local_values.get(field, missing)
        if local_value is missing:
            continue
        base_value = base_values.get(field, missing)
        remote_value = remote_values.get(field, missing)
        remote_changed = remote_value != base_value
        if remote_changed and local_value != remote_value:
            conflicts.append(field)
            continue
        merged[field] = local_value
    if conflicts:
        raise ProfileConflictError(conflicts)
    return merged


def profile_record(profile_id, display_name, revision, values, source=None):
    record = {
        "profile_id": str(profile_id),
        "display_name": str(display_name),
        "revision": int(revision),
        "content_hash": values_hash(values),
        "updated_at": utc_now(),
        "values": dict(values),
    }
    if source:
        record["source"] = dict(source)
    return record
