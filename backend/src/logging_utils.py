import logging
import os
from pathlib import Path
from threading import Lock


_CONFIG_LOCK = Lock()


class CappedErrorFileHandler(logging.Handler):
    """Append-only error log file that drops oldest bytes once max size is reached."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        super().__init__(level=logging.ERROR)
        self.path = path
        self.max_bytes = max(1024, int(max_bytes))
        self._io_lock = Lock()

    @staticmethod
    def _trim_partial_first_line(payload: bytes) -> bytes:
        if not payload:
            return payload
        idx = payload.find(b"\n")
        if idx != -1 and idx + 1 < len(payload):
            return payload[idx + 1 :]
        return payload

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = f"{self.format(record)}\n"
            encoded = message.encode("utf-8", errors="replace")
            if not encoded:
                return
            if len(encoded) > self.max_bytes:
                encoded = self._trim_partial_first_line(encoded[-self.max_bytes :])
            with self._io_lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("ab+") as fh:
                    fh.seek(0, os.SEEK_END)
                    current_size = fh.tell()
                    if current_size + len(encoded) <= self.max_bytes:
                        fh.write(encoded)
                        return
                    keep_bytes = max(0, self.max_bytes - len(encoded))
                    tail = b""
                    if keep_bytes > 0 and current_size > 0:
                        read_bytes = min(keep_bytes, current_size)
                        fh.seek(-read_bytes, os.SEEK_END)
                        tail = fh.read(read_bytes)
                    updated = self._trim_partial_first_line(tail + encoded)
                    if len(updated) > self.max_bytes:
                        updated = updated[-self.max_bytes :]
                    fh.seek(0)
                    fh.truncate(0)
                    fh.write(updated)
        except Exception:
            self.handleError(record)


def configure_capped_error_file_logging(path_value: str, max_bytes: int) -> None:
    path = (path_value or "").strip()
    if not path:
        return
    target = Path(path)
    handler = CappedErrorFileHandler(target, max_bytes=max_bytes)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    with _CONFIG_LOCK:
        for existing in root.handlers:
            if isinstance(existing, CappedErrorFileHandler) and existing.path == target:
                return
        root.addHandler(handler)
