"""Очередь обработки: лекции обрабатываются строго по одной (видеопамяти на две не хватит).
После перезапуска сервера недообработанные лекции продолжаются с последнего сохранённого этапа."""
import queue
import threading
import traceback

from . import config, db, pipeline, storage
from .ml.index import INDEX


class Worker:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.cancelled: set[str] = set()
        self.current: str | None = None
        self._thread = threading.Thread(target=self._run, name="lecture-worker", daemon=True)

    def start(self) -> None:
        for r in db.query("SELECT id FROM lectures WHERE status IN ('queued', 'processing') ORDER BY created_at"):
            words = (config.LECTURES_DIR / r["id"] / "words.json").exists()
            self.enqueue(r["id"], "index" if words else None)
        self._thread.start()

    def enqueue(self, lecture_id: str, from_stage: str | None = None) -> None:
        self.cancelled.discard(lecture_id)
        db.update_lecture(lecture_id, status="queued", stage="В очереди", error=None)
        self.q.put((lecture_id, from_stage))

    def queue_position(self, lecture_id: str) -> int:
        ids = [item[0] for item in list(self.q.queue)]
        return ids.index(lecture_id) + 1 if lecture_id in ids else 0

    def delete(self, lecture_id: str) -> None:
        """Удаление; если лекция сейчас обрабатывается — остановится на ближайшей границе этапов."""
        self.cancelled.add(lecture_id)
        if self.current != lecture_id:
            _remove(lecture_id)

    def _run(self) -> None:
        while True:
            lecture_id, from_stage = self.q.get()
            if lecture_id in self.cancelled:
                continue
            self.current = lecture_id
            try:
                pipeline.safe_process(lecture_id, lambda: lecture_id in self.cancelled, from_stage)
            except pipeline.Cancelled:
                _remove(lecture_id)
            except Exception:
                traceback.print_exc()
            finally:
                self.current = None


def _remove(lecture_id: str) -> None:
    db.delete_lecture_rows(lecture_id)
    storage.remove(lecture_id)
    INDEX.reload()


WORKER = Worker()
