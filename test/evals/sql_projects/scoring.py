"""Independent acceptance scoring for synthetic SQL project dialogues."""

from collections import Counter
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from functools import cache

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify

from test.evals.sql_projects.corpus import result_errors
from test.evals.sql_projects.golden import Turn
from test.evals.sql_projects.discriminators import PACK_ID, oracle_for
from test.evals.sql_projects.schema import qualification_schema


def response_kind(response):
    if not isinstance(response, Mapping):
        return "INVALID"
    return response.get("response_kind") or ("PROPOSAL" if isinstance(response.get("proposal"), Mapping) else "INVALID")


def parameter_errors(bindings, expected):
    if not isinstance(bindings, list) or any(not isinstance(item, Mapping) or set(item) != {"name", "type", "value"} for item in bindings):
        return ["Invalid typed parameter shape"]
    names = [item["name"] for item in bindings]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        return ["Duplicate or invalid parameter name"]
    actual = {item["name"]: item for item in bindings}
    errors = []
    # Existing project bind names are stable. New binds may use another valid
    # name: semantic scoring does not require the oracle's SQL spelling.
    for name, kind, value in expected.parameters:
        if name not in {"paid", "date_from", "date_to", "row_limit"}:
            continue
        try:
            matches = name in actual and _parameter_signature(actual[name]["type"], actual[name]["value"]) == _parameter_signature(kind, value)
        except (ValueError, TypeError, ArithmeticError):
            matches = False
        if not matches:
            errors.append(f"Existing parameter changed or missing: {name}")
    try:
        if Counter(_parameter_signature(item["type"], item["value"]) for item in bindings) != Counter(_parameter_signature(kind, value) for _, kind, value in expected.parameters):
            errors.append("Typed parameter values differ")
    except (ValueError, TypeError, ArithmeticError):
        errors.append("Invalid typed parameter value")
    return errors


def _parameter_signature(kind, value):
    if kind == "decimal":
        if isinstance(value, bool):
            raise ValueError("Boolean is not a decimal")
        value = Decimal(str(value))
        if not value.is_finite():
            raise ValueError("Decimal must be finite")
        sign, digits, exponent = value.as_tuple()
        digits = list(digits)
        if not any(digits):
            value = [0, [0], 0]
        else:
            while digits[-1] == 0:
                digits.pop()
                exponent += 1
            value = [sign, digits, exponent]
    elif kind == "datetime":
        if not isinstance(value, str):
            raise ValueError("Datetime has a different type")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError("Golden time boundaries need an explicit timezone")
        value = value.astimezone(timezone.utc).isoformat()
    elif kind in {"integer_list", "text_list"}:
        if not isinstance(value, list) or not value or any(type(item) is not (int if kind == "integer_list" else str) for item in value):
            raise ValueError("Invalid typed list")
        value = sorted(set(value))
    elif kind == "integer" and type(value) is not int:
        raise ValueError("Integer has a different type")
    elif kind == "text" and not isinstance(value, str):
        raise ValueError("Text has a different type")
    return json.dumps([kind, value], ensure_ascii=False, sort_keys=True)


def physical_entities(sql):
    statements = sqlglot.parse(sql, read="postgres")
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError("Expected one read-only query")
    query = normalize_identifiers(statements[0], dialect="postgres")
    return frozenset(source.name for scope in traverse_scope(query) for _, source in scope.selected_sources.values() if isinstance(source, exp.Table))


@cache
def _qualification_schema():
    return qualification_schema()


def _output_position(expression, projections):
    matches = []
    if isinstance(expression, exp.Literal) and expression.is_int:
        index = int(expression.this) - 1
        if 0 <= index < len(projections):
            matches = [index]
    elif isinstance(expression, exp.Column) and not expression.table:
        matches = [index for index, projection in enumerate(projections) if projection.alias_or_name == expression.name]
    if not matches:
        matches = [index for index, projection in enumerate(projections) if projection.unalias().unnest() == expression]
    if len(matches) != 1:
        raise ValueError("ORDER BY cannot be resolved to one projected output")
    return matches[0]


def ordered_output(sql):
    """Resolve outer ORDER BY to output positions, including PostgreSQL defaults.

    Fixture ordering is an explicit contract, not the incidental row order of a
    sequential scan. Alias names, ordinal spelling and table aliases may vary.
    All authored sort keys are projected; other expressions need a new oracle.
    """
    query = qualify(sqlglot.parse_one(sql, read="postgres"), dialect="postgres", schema=_qualification_schema(), quote_identifiers=False, identify=False)
    order = query.args.get("order")
    if order is None:
        return ()
    projections = query.selects
    keys = []
    index = 0
    while index < len(order.expressions):
        item = order.expressions[index]
        expression = item.this.unnest()
        negated = isinstance(expression, exp.Not)
        null_test = expression.this.unnest() if negated else expression
        if isinstance(null_test, exp.Is) and isinstance(null_test.expression, exp.Null) and index + 1 < len(order.expressions):
            following = order.expressions[index + 1]
            try:
                null_position = _output_position(null_test.this.unnest(), projections)
                next_position = _output_position(following.this.unnest(), projections)
            except ValueError:
                pass
            else:
                if null_position == next_position:
                    # A paired boolean null key determines null placement;
                    # the following key determines the non-null value order.
                    keys.append((next_position, bool(following.args.get("desc")), bool(item.args.get("desc")) != negated))
                    index += 2
                    continue
        keys.append((_output_position(expression, projections), bool(item.args.get("desc")), bool(item.args.get("nulls_first"))))
        index += 1
    return tuple(keys)


def score_turn(turn: Turn, response, *, result=None, discriminator_results=None):
    """Score all hard axes; semantic explanation accuracy needs explicit review."""
    errors = []
    actual_kind = response_kind(response)
    if actual_kind != turn.kind:
        return [f"Response kind differs: expected {turn.kind}, got {actual_kind}"]
    if turn.kind != "PROPOSAL":
        allowed = {"response_kind", "answer", "questions"} if turn.kind == "CLARIFICATION" else {"response_kind", "answer"}
        if set(response) - allowed or not isinstance(response.get("answer"), str) or not response["answer"].strip():
            errors.append("Dialogue response is empty or contains a mutation")
        if turn.kind == "CLARIFICATION" and not response.get("questions"):
            errors.append("Clarification has no questions")
        return errors
    expected = turn.query
    proposal = response["proposal"]
    if proposal.get("checks", {}).get("status") != "PASS":
        errors.append("Production guard did not pass")
    if not isinstance(proposal.get("summary"), str) or not proposal["summary"].strip():
        errors.append("Change summary is missing")
    errors.extend(parameter_errors(proposal.get("parameters"), expected))
    try:
        if physical_entities(proposal.get("sql")) != expected.entities:
            errors.append("Physical source entities differ")
        required_order = ordered_output(expected.oracle.sql)
        if required_order and ordered_output(proposal.get("sql")) != required_order:
            errors.append("Explicit output ordering differs")
    except (sqlglot.errors.SqlglotError, ValueError, TypeError, AttributeError):
        errors.append("Query AST cannot be scored")
    if result is None:
        errors.append("Real PostgreSQL result is missing")
    else:
        errors.extend(result_errors(result, expected.oracle))
    if not isinstance(discriminator_results, Mapping) or PACK_ID not in discriminator_results:
        errors.append("Independent boundary dataset result is missing")
    else:
        errors.extend(f"{PACK_ID}: {error}" for error in result_errors(discriminator_results[PACK_ID], oracle_for(expected.oracle)))
    return errors
