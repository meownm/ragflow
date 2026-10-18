"""Recoverable, fenced execution of workspace document proposals."""

import asyncio
import logging
import threading
import time

from .errors import SourceWorkspaceError
from .processing import ProcessingError
from .linked_runtime import build_linked_documents


def run_job(service, job):
    async def execute_with_lease():
        async def monitor():
            loop = asyncio.get_running_loop()
            renew_at = loop.time() + 15
            while True:
                await asyncio.sleep(1)
                await asyncio.to_thread(service.repo.check_lease, job["id"], job["lease_token"])
                if loop.time() >= renew_at:
                    await asyncio.to_thread(service.repo.lease, job["id"], job["lease_token"])
                    renew_at = loop.time() + 15

        execution = asyncio.create_task(service.execute(job))
        watcher = asyncio.create_task(monitor())
        try:
            done, _ = await asyncio.wait({execution, watcher}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in (execution, watcher):
                task.cancel()
            await asyncio.gather(execution, watcher, return_exceptions=True)

    try:
        with asyncio.Runner() as runner:
            runner.run(execute_with_lease())
    except Exception as error:
        if isinstance(error, (SourceWorkspaceError, ProcessingError)):
            details = {"code": error.code, "message": error.message}
        elif isinstance(error, TimeoutError):
            details = {"code": "MODEL_TIMEOUT", "message": "Модель не завершила подготовку за отведённое время"}
        else:
            logging.exception("Linked document job failed")
            details = {"code": "PREPARATION_FAILED", "message": "Не удалось подготовить правки. Проверьте настройку модели и повторите запрос"}
        try:
            service.repo.finish_job(job["id"], job["lease_token"], error=details)
        except SourceWorkspaceError:
            pass


def start_linked_document_worker(stop_event):
    def work():
        service = build_linked_documents()
        scan_after = 0
        while not stop_event.is_set():
            try:
                job = service.repo.claim()
                if job:
                    run_job(service, job)
                    continue
                # Only explicitly automatic links cause model work without a button click.
                if time.monotonic() >= scan_after:
                    scan_after = time.monotonic() + 60
                    for workspace_id, document_id in service.repo.automatic_targets():
                        if stop_event.is_set():
                            break
                        try:
                            document = service.repo.document(workspace_id, document_id)
                            actor = document["responsible_id"] or document["owner_id"]

                            with asyncio.Runner() as runner:
                                runner.run(service.inspect_automatic(actor, workspace_id, document_id))
                        except SourceWorkspaceError:
                            logging.info("Automatic document inspection requires user action", exc_info=True)
            except Exception:
                logging.exception("Linked document worker iteration failed")
            stop_event.wait(2)

    thread = threading.Thread(target=work, name="source-document-worker", daemon=True)
    thread.start()
    return thread
