"""Real TZ exchange scenario and production text codec over disposable memory."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from urllib.parse import urlsplit

from api.document_exchange.content import content_hash
from api.technical_specifications.application import Specifications
from api.technical_specifications.domain import SpecificationError, fail
from test.fixtures.technical_specifications.eva_codec import eva_roundtrip
from test.playwright.helpers.technical_specification_api import ACTOR, DETAIL, SpecificationApi


class CodecSpecificationApi(SpecificationApi):
    def __init__(self):
        super().__init__()
        api = self

        class Repository:
            def access(self, actor, workspace, capability="read"):
                assert (actor, workspace) == (ACTOR, "workspace")

            def get(self, actor, workspace, document):
                self.access(actor, workspace)
                assert document == "doc"
                return deepcopy(api.document)

            def change(self, actor, workspace, document, expected, mutate, reason, *, latest=False):
                value = self.get(actor, workspace, document)
                if not latest and expected != value["version"]:
                    fail("VERSION_CONFLICT", "ТЗ изменилось", 409)
                mutate(value)
                if value["sections"] != api.document["sections"]:
                    value["revision"] += 1
                value["version"] += 1
                api.document = value
                api.revisions[value["revision"]] = deepcopy(value)
                return deepcopy(value)

        class Eva:
            remote = ""
            version = 1

            def publish_connected_page(self, actor, binding, source, name):
                self.remote = eva_roundtrip(source)
                self.version += 1
                return self.read_connected_page(actor, binding)

            def read_connected_page(self, actor, binding):
                return {**binding, "remote_version": str(self.version), "remote_content_hash": content_hash(self.remote)}, self.remote

        class AI:
            async def verify(self, *args, **kwargs):
                return {"valid": True, "issues": []}

        async def run(method, *args, **kwargs):
            return method(*args, **kwargs)

        self.eva = Eva()
        self.service = Specifications(Repository(), AI(), run, self.eva)

    def route(self, route):
        path = urlsplit(route.request.url).path
        operation = {DETAIL + "/publish-eva": self.service.publish_eva, DETAIL + "/pull": self.service.pull, DETAIL + "/apply": self.service.apply}.get(path)
        if route.request.method != "POST" or operation is None:
            return super().route(route)
        body = route.request.post_data_json
        self.requests.append(("POST", path, body))
        # Playwright's sync callback already has an event loop. Run the actual
        # async application scenario in one owned thread, without live clients.
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                value = executor.submit(lambda: asyncio.run(operation(ACTOR, "workspace", "doc", body))).result(timeout=10)
        except SpecificationError as error:
            return self.reject(route, error.code, error.message, error.status)
        return self.respond(route, value)
