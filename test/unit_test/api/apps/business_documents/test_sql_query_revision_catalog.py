"""Catalog authority, bounded correction and immutable revision acceptance."""

from copy import deepcopy
import asyncio

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_revisions import ACTOR, TENANT, project as project_fixture
from test.unit_test.api.apps.business_documents.test_sql_query_specification import _snapshot, _table
from api.apps.business_documents import sql_query_revision_catalog as catalog
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Agents
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService as Revisions
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentSqlAgentProposal, BusinessDocumentSqlQueryArtifact, BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryRun
from business_documents.application.errors import ValidationError
from business_documents.sql_query.schema_changes import extend_schema
from business_documents.sql_query.schema_resolution import CatalogAccessDenied, catalog_result_from_mapping

project = project_fixture
BASE = "SELECT o.id AS id, o.amount AS amount FROM dwh.orders AS o WHERE o.status = :status_value AND o.amount >= :amount_min LIMIT :row_limit"
GROUP = "SELECT c.region AS buyer_region, SUM(o.amount) AS total FROM dwh.orders AS o LEFT JOIN dwh.customers AS c ON o.customer_id = c.id WHERE o.status = :status_value AND o.amount >= :amount_min GROUP BY c.region LIMIT :row_limit"
BAD = GROUP.replace("c.region AS buyer_region", "COALESCE(c.region, '') AS buyer_region").replace("GROUP BY c.region", "GROUP BY COALESCE(c.region, '')")
PARAMETERS = [{"name": "status_value", "type": "text", "value": "paid"}, {"name": "amount_min", "type": "decimal", "value": "50"}, {"name": "row_limit", "type": "integer", "value": 100}]


class Model:
    def __init__(self, *sqls):
        self.sqls = iter(sqls)
        self.contexts = []

    async def async_generate(self, tenant_id, _system, payload):
        assert tenant_id == TENANT
        self.contexts.append(deepcopy(payload["context"]))
        return {"sql": next(self.sqls), "parameters": deepcopy(PARAMETERS), "summary": "Requested edit"}


@pytest.fixture
def source(monkeypatch):
    root = _table("orders", "dwh.orders", ("id", "customer_id", "payer_id", "amount", "status"))
    customer = _table("customers", "dwh.customers", ("id", "region"))
    root["table_constraints"] = [
        {"constraint_type": "FOREIGN_KEY", "columns": [key], "referred_columns": ["dwh.customers.id"], "relationship_type": "MANY_TO_ONE"} for key in ("customer_id", "payer_id")
    ]
    snapshot = _snapshot()
    snapshot["original_requirements"] = "- Paid orders; preserve the amount threshold"
    snapshot["requirements"] = [{"term": "orders", "state": "CONFIRMED", "decision": "USER", "selected_table": root}]
    schema = {"schema_snapshot": snapshot, "accepted_schema": [{"entity_id": "orders", "version": root["version"], "schema_fingerprint": root["schema_fingerprint"]}]}
    entities = {table["id"]: {**table, "column_details": deepcopy(table["columns"])} for table in (root, customer)}

    class Resolver:
        async def authorize(self, *, actor_id, is_admin):
            assert actor_id == ACTOR and not is_admin

        async def visible_catalog(self, *, actor_id):
            assert actor_id == ACTOR
            return {"tables": list(entities.values())}

    async def load(actor, raw, admin, role):
        assert actor == ACTOR and not admin and role == "AUTHOR_CREATOR"
        return {"entities": [{"entity_id": identity, "lookup": {"status": "OK"}, "freshness": {"stale": False}, "entity": deepcopy(entities[identity])} for identity in raw["entity_ids"]]}

    monkeypatch.setattr(catalog, "OpenMetadataCatalogResolver", Resolver)
    monkeypatch.setattr(catalog.BusinessDocumentSqlQuerySchemaService, "load_entities", load)
    request = {
        "target": "QUERY",
        "sql": BASE,
        "parameters": deepcopy(PARAMETERS),
        "requirements": {"requirements": [{"statement": "Paid orders; preserve the amount threshold"}]},
        "schema": schema,
        "comments": [{"text": "Group by buyer via customer_id, not payer_id; keep NULL and empty strings distinct"}],
    }
    return request, entities


async def generate(request, model):
    return await catalog.propose_catalog_revision(TENANT, ACTOR, request, False, "AUTHOR_CREATOR", llm=model)


@pytest.mark.asyncio
async def test_valid_edit_stays_one_call_without_catalog_io(source, monkeypatch):
    request, _ = source
    monkeypatch.setattr(catalog, "OpenMetadataCatalogResolver", lambda: pytest.fail("A valid existing-source edit needs no discovery"))
    model = Model(BASE)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS" and len(model.contexts) == 1
    assert "verified_catalog_schema" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "replace"])
async def test_explicit_numeric_addition_or_replacement_preserves_the_requested_predicates(source, monkeypatch, operation):
    request, _ = source
    request["comments"] = [{"text": "Add amount < 50" if operation == "add" else "Replace amount >= 50 with amount < 50; preserve other conditions"}]
    before = deepcopy(request)
    sql = BASE.replace(" LIMIT", " AND o.amount < :amount_min LIMIT") if operation == "add" else BASE.replace("o.amount >= :amount_min", "o.amount < :amount_min")
    monkeypatch.setattr(catalog, "OpenMetadataCatalogResolver", lambda: pytest.fail("An existing-source filter edit needs no catalog discovery"))
    model = Model(sql)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS"
    assert result["proposal"]["sql"] == sql and result["proposal"]["parameters"] == PARAMETERS
    assert len(model.contexts) == 1 and request == before
    assert "verified_catalog_schema" not in result


@pytest.mark.asyncio
async def test_actual_c11_shape_loads_fk_target_before_one_guarded_correction(source):
    request, _ = source
    before = deepcopy(request)
    model = Model(BAD, GROUP)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS"
    assert result["proposal"]["sql"] == GROUP and len(model.contexts) == 2
    assert {item["entity_id"] for item in result["verified_catalog_schema"]["accepted_schema"]} == {"orders", "customers"}
    assert request == before
    assert model.contexts[1]["current_sql"] == BASE and model.contexts[1]["current_parameters"] == PARAMETERS
    assert model.contexts[1]["discussion"] == request["comments"]
    assert model.contexts[1]["validation_feedback"]["verified_additional_entity_ids"] == ["customers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("term", ["customers", "dwh.customers"])
async def test_contract_source_request_loads_metadata_before_changing_sql(source, term):
    request, _ = source

    class Requested(Model):
        async def async_generate(self, *args):
            response = await super().async_generate(*args)
            if len(self.contexts) == 1:
                response["requested_sources"] = [term]
            return response

    model = Requested(BASE, GROUP)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS"
    assert len(model.contexts) == 2 and "verified_catalog_schema" in result
    assert model.contexts[1]["current_sql"] == BASE


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_unrelated_source_requires_its_full_catalog_name_in_the_latest_human_edit(source, explicit):
    request, entities = source
    table = _table("archive", "archive.orders", ("id", "customer_id", "payer_id", "amount", "status"))
    entities["archive"] = {**table, "column_details": deepcopy(table["columns"])}
    request["comments"] = [
        {"text": "Previously used archive.orders"},
        {"text": "Use only archive.orders instead of the previous source; keep the filters" if explicit else "Keep the current source and filters"},
    ]
    request["catalog_source_text"] = request["comments"][-1]["text"]
    before = deepcopy(request)

    class Requested(Model):
        async def async_generate(self, *args):
            response = await super().async_generate(*args)
            if len(self.contexts) == 1:
                response["requested_sources"] = ["archive.orders"]
            return response

    model = Requested(BASE, BASE.replace("dwh.orders", "archive.orders"))
    result = await generate(request, model)
    if explicit:
        assert result["proposal"]["checks"]["status"] == "PASS" and len(model.contexts) == 2
        assert {item["entity_id"] for item in result["verified_catalog_schema"]["accepted_schema"]} == {"archive"}
        assert {item["selected_table"]["id"] for item in result["verified_catalog_schema"]["schema_snapshot"]["requirements"]} == {"archive"}
        assert catalog.query_revision_checks({**request, "schema": result["verified_catalog_schema"]}, result["proposal"])["status"] == "PASS"
    else:
        assert "verified_catalog_schema" not in result and len(model.contexts) == 1
        assert result["proposal"]["checks"]["status"] == "NEEDS_SCHEMA"
    assert request == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cached,fqn",
    [
        ("missing", "warehouse.analytics.archive.orders"),
        ("current", "warehouse.analytics.archive.orders"),
        ("old-generation", "warehouse.analytics.archive.orders"),
        ("missing", "Warehouse.Analytics.archive.orders"),
    ],
)
@pytest.mark.parametrize("direct_sql", [False, True])
async def test_explicit_source_uses_live_identity_when_projection_predates_its_creation(source, monkeypatch, cached, direct_sql, fqn):
    request, entities = source
    table = _table("archive", fqn, ("id", "customer_id", "payer_id", "amount", "status"))
    table["schema"] = "archive"
    table["column_details"] = deepcopy(table["columns"])
    entities["archive"] = table
    request["catalog_source_text"] = "Use only " + fqn
    calls = []

    class Resolver(catalog.OpenMetadataCatalogResolver):
        async def visible_catalog(self, *, actor_id):
            assert actor_id == ACTOR
            values = [] if cached == "missing" else [{**table, "id": "deleted-generation" if cached == "old-generation" else table["id"]}]
            return {"tables": values}

        async def resolve(self, *, term, actor_id, locale):
            assert (term, actor_id, locale) == (fqn, ACTOR, "ru")
            calls.append(term)
            return catalog_result_from_mapping(term, {"entities": [table]}, include_all_columns=True)

    class Requested(Model):
        async def async_generate(self, *args):
            response = await super().async_generate(*args)
            if not direct_sql and len(self.contexts) == 1:
                response["requested_sources"] = [fqn]
            return response

    monkeypatch.setattr(catalog, "OpenMetadataCatalogResolver", Resolver)
    expected = BASE.replace("dwh.orders", "archive.orders")
    model = Requested(expected if direct_sql else BASE, expected)
    before = deepcopy(request)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS"
    assert result["proposal"]["sql"] == expected
    assert len(model.contexts) == (1 if direct_sql else 2) and calls == [fqn]
    accepted = result["verified_catalog_schema"]
    assert {item["entity_id"] for item in accepted["accepted_schema"]} == {"archive"}
    assert catalog.query_revision_checks({**request, "schema": accepted}, result["proposal"])["status"] == "PASS"
    assert request == before


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["missing", "denied"])
async def test_cached_source_cannot_override_failed_live_identity_or_policy(source, monkeypatch, outcome):
    request, entities = source
    fqn = "warehouse.analytics.archive.orders"
    table = _table("archive", fqn, ("id", "customer_id", "payer_id", "amount", "status"))
    table["schema"] = "archive"
    entities["archive"] = table
    request["catalog_source_text"] = "Use only " + fqn

    class Resolver(catalog.OpenMetadataCatalogResolver):
        async def resolve(self, **_):
            if outcome == "denied":
                raise CatalogAccessDenied("Current catalog policy denies this source")
            return catalog_result_from_mapping(fqn, {"entities": []}, include_all_columns=True)

    monkeypatch.setattr(catalog, "OpenMetadataCatalogResolver", Resolver)
    model = Model(BASE.replace("dwh.orders", "archive.orders"))
    if outcome == "denied":
        with pytest.raises(CatalogAccessDenied):
            await generate(request, model)
    else:
        result = await generate(request, model)
        assert result["proposal"]["checks"]["status"] == "BLOCKED"
        assert "verified_catalog_schema" not in result
    assert len(model.contexts) == 1


@pytest.mark.asyncio
async def test_known_source_literal_error_repairs_once_without_discovery(source, monkeypatch):
    request, _ = source
    monkeypatch.setattr(catalog, "OpenMetadataCatalogResolver", lambda: pytest.fail("No new relation"))
    bad = BASE.replace(":status_value", "'paid'")
    model = Model(bad, BASE)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS" and len(model.contexts) == 2
    assert "verified_catalog_schema" not in result


@pytest.mark.asyncio
async def test_second_invalid_proposal_stays_blocked_and_never_gets_schema_authority(source):
    request, _ = source
    model = Model(BAD, BAD)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "BLOCKED" and len(model.contexts) == 2
    assert "verified_catalog_schema" not in result


@pytest.mark.asyncio
async def test_unrelated_table_is_not_approved_from_model_sql(source):
    request, entities = source
    entities["orders"]["table_constraints"] = []
    model = Model(BAD)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "BLOCKED" and len(model.contexts) == 1
    assert "verified_catalog_schema" not in result


@pytest.mark.asyncio
async def test_catalog_denial_prevents_the_corrective_model_call(source, monkeypatch):
    request, _ = source

    class Denied:
        async def authorize(self, **_):
            raise CatalogAccessDenied("Access revoked")

    monkeypatch.setattr(catalog, "OpenMetadataCatalogResolver", Denied)
    model = Model(BAD)
    with pytest.raises(CatalogAccessDenied):
        await generate(request, model)
    assert len(model.contexts) == 1 and len(request["schema"]["accepted_schema"]) == 1


@pytest.mark.asyncio
async def test_incomplete_live_columns_cannot_become_a_schema(source):
    request, entities = source
    entities["customers"]["column_details"] = []
    result = await generate(request, Model(BAD))
    assert result["proposal"]["checks"]["status"] == "BLOCKED"
    assert "verified_catalog_schema" not in result
    assert len(request["schema"]["accepted_schema"]) == 1


@pytest.mark.asyncio
async def test_stale_added_schema_is_rejected_before_correction(source, monkeypatch):
    request, _ = source
    load = catalog.BusinessDocumentSqlQuerySchemaService.load_entities

    async def stale(*args):
        result = await load(*args)
        for entity in result["entities"]:
            if entity["entity_id"] == "customers":
                entity["freshness"]["stale"] = True
        return result

    monkeypatch.setattr(catalog.BusinessDocumentSqlQuerySchemaService, "load_entities", stale)
    model = Model(BAD)
    with pytest.raises(ValidationError):
        await generate(request, model)
    assert len(model.contexts) == 1


@pytest.mark.asyncio
async def test_two_fk_neighbors_with_the_same_physical_name_are_not_guessed(source):
    request, entities = source
    first = entities["customers"]
    other = deepcopy(first)
    other["id"] = "other-customer"
    for table, prefix in ((first, "warehouse.analytics"), (other, "second.analytics")):
        table["fqn"] = prefix + ".dwh.customers"
        for key in ("columns", "column_details"):
            for column in table[key]:
                column["fqn"] = column["id"] = table["fqn"] + "." + column["name"]
    entities["other-customer"] = other
    # Both references exist in the actual visible catalog and frozen source.
    for constraint, table in zip(entities["orders"]["table_constraints"], (first, other), strict=True):
        constraint["referred_columns"] = [table["fqn"] + ".id"]
    model = Model(BAD)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "BLOCKED" and len(model.contexts) == 1
    assert "verified_catalog_schema" not in result


@pytest.mark.asyncio
async def test_unused_discovered_table_does_not_expand_the_accepted_schema(source):
    request, _ = source
    result = await generate(request, Model(BAD, BASE))
    assert result["proposal"]["checks"]["status"] == "PASS"
    assert "verified_catalog_schema" not in result


@pytest.mark.asyncio
async def test_model_cannot_inject_private_schema_authority(source):
    request, _ = source

    class Forged:
        async def async_generate(self, *_):
            return {"sql": BASE, "parameters": PARAMETERS, "summary": "Edit", "verified_catalog_schema": request["schema"]}

    with pytest.raises(ValueError, match="invalid SQL revision"):
        await generate(request, Forged())


@pytest.mark.asyncio
async def test_timeout_is_not_retried(source, monkeypatch):
    request, _ = source
    calls = []

    class Slow:
        async def async_generate(self, *_):
            calls.append(True)
            await asyncio.Event().wait()

    monkeypatch.setattr(catalog, "MODEL_TIMEOUT_SECONDS", 0.03)
    with pytest.raises(TimeoutError):
        await generate(request, Slow())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_correction_uses_the_remaining_shared_deadline(source, monkeypatch):
    request, _ = source

    class Slow(Model):
        async def async_generate(self, *args):
            response = await super().async_generate(*args)
            await asyncio.sleep(0.3)
            return response

    model = Slow(BASE.replace(":status_value", "'paid'"), BASE)
    monkeypatch.setattr(catalog, "MODEL_TIMEOUT_SECONDS", 0.5)
    with pytest.raises(TimeoutError):
        await generate(request, model)
    assert len(model.contexts) == 2


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("replace_source", [False, True])
def test_verified_worker_schema_is_pinned_only_on_accept_and_late_work_is_fenced(project, cancel, replace_source):
    old_schema = BusinessDocumentSqlQueryArtifact.get_by_id("s" * 32)
    old_payload = deepcopy(old_schema.payload)
    old_artifacts = {identity: deepcopy(BusinessDocumentSqlQueryArtifact.get_by_id(identity).payload) for identity in ("q" * 32, "c" * 32)}
    table = _table("extra", "dwh.extra", ("order_id", "region"))
    proposed = extend_schema(old_payload, [{**table, "column_details": table["columns"]}])
    if replace_source:
        proposed["schema_snapshot"]["requirements"] = [item for item in proposed["schema_snapshot"]["requirements"] if item["selected_table"]["id"] == "extra"]
        proposed["accepted_schema"] = [item for item in proposed["accepted_schema"] if item["entity_id"] == "extra"]
    BusinessDocumentSqlQueryProject.update(execution_profile_id="original-profile").where(BusinessDocumentSqlQueryProject.id == project).execute()
    old_run = BusinessDocumentSqlQueryRun.create(
        id="old-result",
        project_id=project,
        tenant_id=TENANT,
        compilation_id="c" * 32,
        profile_id="original-profile",
        profile_version=7,
        status="READY",
        columns=["order_id"],
        rows=[[42]],
        row_count=1,
        result_bytes=4,
        duration_ms=1,
    )
    old_data = deepcopy(BusinessDocumentSqlQueryRun.get_by_id(old_run.id).__data__)
    thread = Revisions.create_thread(
        TENANT,
        ACTOR,
        project,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "catalog-thread", "target": "QUERY", "anchor": {"kind": "WHOLE"}, "text": "Add the verified region"},
    )
    started = Revisions.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "catalog-job"})
    job = BusinessDocumentJobQueue.claim("catalog-worker")
    if cancel:
        Revisions.cancel_change(TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": started["state_version"], "idempotency_key": "catalog-stop"})
    sql = "SELECT o.order_id AS order_id, e.region AS region FROM dwh.order_fact AS o LEFT JOIN dwh.extra AS e ON o.order_id = e.order_id LIMIT :row_limit"
    if replace_source:
        sql = "SELECT e.order_id AS order_id, e.region AS region FROM dwh.extra AS e LIMIT :row_limit"
    Agents.complete_job(
        job,
        "catalog-worker",
        job.lease_token,
        {"proposal": {"sql": sql, "parameters": [{"name": "row_limit", "type": "integer", "value": 10}], "summary": "Region", "checks": {"status": "PASS"}}, "verified_catalog_schema": proposed},
    )
    current = Agents.get_project(TENANT, ACTOR, project)
    assert current["execution_profile_id"] == "original-profile"
    if cancel:
        assert current["artifact_ids"]["schema"] == old_schema.id and BusinessDocumentSqlAgentProposal.select().count() == 0
    else:
        assert current["artifact_ids"]["schema"] == old_schema.id
        pending = current["pending_proposal"]
        assert pending["payload"]["agent_result"]["source"]["proposed_schema"] == proposed
        accepted = Revisions.decide_change(
            TENANT, ACTOR, project, pending["id"], {"schema_version": "1", "expected_state_version": current["state_version"], "idempotency_key": "catalog-accept", "decision": "ACCEPT"}
        )
        assert accepted["artifact_ids"]["schema"] != old_schema.id
        assert accepted["execution_profile_id"] == (None if replace_source else "original-profile")
        assert BusinessDocumentSqlQueryArtifact.get_by_id(accepted["artifact_ids"]["schema"]).payload == proposed
    assert BusinessDocumentSqlQueryArtifact.get_by_id(old_schema.id).payload == old_payload
    assert {identity: BusinessDocumentSqlQueryArtifact.get_by_id(identity).payload for identity in old_artifacts} == old_artifacts
    assert BusinessDocumentSqlQueryRun.get_by_id(old_run.id).__data__ == old_data


@pytest.mark.asyncio
async def test_verified_metadata_reuses_original_sql_with_only_one_model_call(source):
    request, _ = source
    before = deepcopy(request)
    model = Model(GROUP)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS"
    assert result["proposal"]["sql"] == GROUP
    assert result["proposal"]["parameters"] == PARAMETERS
    assert len(model.contexts) == 1 and request == before
    assert {item["entity_id"] for item in result["verified_catalog_schema"]["accepted_schema"]} == {"orders", "customers"}


@pytest.mark.asyncio
async def test_correction_uses_current_error_after_metadata_is_verified(source):
    request, _ = source
    model = Model(BAD, GROUP)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS" and len(model.contexts) == 2
    reason = model.contexts[1]["validation_feedback"]["reason"]
    assert "bound parameters" in reason and "outside the accepted catalog" not in reason


@pytest.mark.asyncio
async def test_explicit_source_request_still_needs_the_actual_edit(source):
    request, _ = source

    class Requested(Model):
        async def async_generate(self, *args):
            value = await super().async_generate(*args)
            if len(self.contexts) == 1:
                value["requested_sources"] = ["dwh.customers"]
            return value

    model = Requested(GROUP, GROUP)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "PASS" and len(model.contexts) == 2
    assert "outside the accepted catalog" not in model.contexts[1]["validation_feedback"]["reason"]


@pytest.mark.asyncio
async def test_an_untrusted_cell_in_the_discussion_cannot_authorize_an_unrelated_source(source):
    request, entities = source
    table = _table("archive", "archive.orders", ("id", "customer_id", "payer_id", "amount", "status"))
    entities["archive"] = {**table, "column_details": deepcopy(table["columns"])}
    request["comments"] = [{"text": "Drill down this row\nSelected cell context: archive.orders"}]
    request["catalog_source_text"] = "Drill down this row"

    class Requested(Model):
        async def async_generate(self, *args):
            response = await super().async_generate(*args)
            response["requested_sources"] = ["archive.orders"]
            return response

    model = Requested(BASE)
    result = await generate(request, model)
    assert result["proposal"]["checks"]["status"] == "NEEDS_SCHEMA" and len(model.contexts) == 1
    assert "verified_catalog_schema" not in result


@pytest.mark.asyncio
async def test_pinned_result_access_is_rechecked_before_sending_it_to_the_model(source, monkeypatch):
    from business_documents.application.errors import PermissionDeniedError

    request, _ = source
    request["pinned_compilation_id"] = "selected-compilation"

    async def revoked(*args):
        raise PermissionDeniedError("Selected historical source was revoked")

    monkeypatch.setattr(catalog.BusinessDocumentSqlQuerySchemaService, "verify_snapshot", revoked)
    model = Model(BASE)
    with pytest.raises(PermissionDeniedError):
        await generate(request, model)
    assert not model.contexts
