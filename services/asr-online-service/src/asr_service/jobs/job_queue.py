from queue import Empty, Queue


class JobQueue:
    def __init__(self) -> None:
        self._queue: Queue[str] = Queue()

    def put(self, job_id: str) -> None:
        self._queue.put(job_id)

    def get(self, timeout: float = 0.2) -> str | None:
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None

    def task_done(self) -> None:
        self._queue.task_done()
