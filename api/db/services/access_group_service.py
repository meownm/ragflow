#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#

from peewee import IntegrityError

from api.db.db_models import (
    DB,
    AccessGroup,
    AccessGroupDataset,
    AccessGroupSection,
    AccessGroupUser,
    Knowledgebase,
    User,
)
from api.db.services.navigation_visibility_service import NAVIGATION_SECTIONS, get_visible_sections
from common.misc_utils import get_uuid
from common.constants import StatusEnum
from common.time_utils import current_timestamp, datetime_format
from datetime import datetime


BLUEPRINT_SECTIONS = {
    "dataset_api": "dataset",
    "document_api": "dataset",
    "chunk_api": "dataset",
    "chat_api": "chat",
    "search_api": "search",
    "source_workbench_api": "search",
    "agent_api": "agent",
    "bot_api": "agent",
    "memory_api": "memory",
    "openmetadata_api": "catalog",
    "business_document_api": "business_documents",
    "technical_specifications_api": "technical_specifications",
    "openai_api": "chat",
    "dify_retrieval_api": "search",
    "file_api": "file_manager",
    "file_commit_api": "file_manager",
    "file2document_api": "file_manager",
}


def sections_for_request(blueprint, route):
    path = route.removeprefix("/api/v1").removeprefix("/v1")
    if blueprint == "document_api" and path == "/documents/upload":
        return ("dataset", "chat")
    if blueprint in {"backward_compat", "backward_compat_legacy_v1"} and path in {
        "/file/upload_info",
        "/document/upload_info",
    }:
        return ("dataset", "chat")
    if blueprint in {"backward_compat", "backward_compat_legacy_v1"}:
        if path == "/document/get/<doc_id>":
            return ("dataset", "chat", "search", "agent")
        prefix = path.strip("/").split("/", 1)[0]
        if prefix in {"chats", "chats_openai"}:
            return ("chat",)
        if prefix == "sessions":
            return ("chat", "search")
        if prefix in {"agents", "agents_openai"} or path.startswith("/document/download/"):
            return ("agent",)
        if prefix in {"datasets", "document"}:
            return ("dataset",)
        if prefix == "file":
            return ("file_manager",)
    # Technical Specifications and Source Workbench share project identity and
    # membership. Search, source selection and processing stay search-only.
    if blueprint == "source_workbench_api" and path in {
        "/source-workspaces",
        "/source-workspaces/<workspace_id>",
    }:
        return ("search", "business_documents", "technical_specifications")
    if blueprint == "dataset_api" and path in {
        "/datasets/choices",
        "/datasets/metadata/flattened",
        "/datasets/tags/aggregation",
    }:
        return ("dataset", "chat", "search", "agent")
    if blueprint == "chat_api" and path in {"/chat/mindmap", "/chat/recommendation"}:
        return ("chat", "search")
    # Source-file readers are shared; dataset management remains dataset-only.
    if blueprint == "document_api" and path in {
        "/thumbnails",
        "/documents/<doc_id>/preview",
        "/documents/images/<image_id>",
        "/documents/<document_id>",
    }:
        return ("dataset", "chat", "search", "agent")
    if blueprint == "bot_api" and path.startswith("/searchbots/"):
        return ("search",)
    if blueprint == "bot_api" and path.startswith("/chatbots/"):
        return ("chat",)
    if blueprint == "business_document_api":
        if path == "/business-documents/capabilities":
            return ("business_documents", "document_constructor", "sql_projects")
        if path in {
            "/business-documents/sql-query/schema/resolve",
            "/business-documents/sql-query/schema/entities",
            "/business-documents/sql-query/compile",
            "/business-documents/sql-query/plan",
        }:
            return ("document_constructor", "sql_projects")
        if path.startswith("/business-documents/sql-query/"):
            return ("sql_projects",)
    section = BLUEPRINT_SECTIONS.get(blueprint)
    return (section,) if section else ()


class AccessGroupValidationError(ValueError):
    pass


def _normalize_ids(value, field):
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise AccessGroupValidationError(f"{field} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise AccessGroupValidationError(f"{field} must not contain duplicates")
    return value


def validate_group_payload(payload, *, partial=False):
    if not isinstance(payload, dict):
        raise AccessGroupValidationError("JSON object is required")
    result = {}
    if not partial or "name" in payload:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
            raise AccessGroupValidationError("name must be a non-empty string up to 128 characters")
        result["name"] = name.strip()
    if not partial or "description" in payload:
        description = payload.get("description", "")
        if not isinstance(description, str) or len(description) > 2000:
            raise AccessGroupValidationError("description must be a string up to 2000 characters")
        result["description"] = description.strip()
    for field in ("user_ids", "dataset_ids", "sections"):
        if not partial or field in payload:
            result[field] = _normalize_ids(payload.get(field, []), field)
    if "sections" in result:
        unknown = sorted(set(result["sections"]) - set(NAVIGATION_SECTIONS))
        if unknown:
            raise AccessGroupValidationError(f"Unknown sections: {', '.join(unknown)}")
        selected = set(result["sections"])
        result["sections"] = [item for item in NAVIGATION_SECTIONS if item in selected]
    return result


class AccessGroupService:
    @staticmethod
    def _validate_references(payload, *, group_id=None):
        if "user_ids" in payload:
            found = {row.id for row in User.select(User.id).where(User.id.in_(payload["user_ids"]))}
            missing = sorted(set(payload["user_ids"]) - found)
            if missing:
                raise AccessGroupValidationError(f"Unknown user IDs: {', '.join(missing)}")
        if "dataset_ids" in payload:
            found = {row.id for row in Knowledgebase.select(Knowledgebase.id).where(Knowledgebase.id.in_(payload["dataset_ids"]))}
            missing = set(payload["dataset_ids"]) - found
            if missing and group_id is not None:
                # Saved forms may still contain this group's now-deleted datasets.
                previous = {
                    row.dataset_id for row in AccessGroupDataset.select(AccessGroupDataset.dataset_id).where((AccessGroupDataset.group_id == group_id) & AccessGroupDataset.dataset_id.in_(missing))
                }
                missing -= previous
            if missing:
                raise AccessGroupValidationError(f"Unknown dataset IDs: {', '.join(sorted(missing))}")
            payload["dataset_ids"] = [dataset_id for dataset_id in payload["dataset_ids"] if dataset_id in found]

    @staticmethod
    @DB.connection_context()
    def list_groups():
        groups = list(AccessGroup.select().order_by(AccessGroup.name.asc()).dicts())
        group_ids = [group["id"] for group in groups]
        users = {group_id: [] for group_id in group_ids}
        datasets = {group_id: [] for group_id in group_ids}
        sections = {group_id: [] for group_id in group_ids}
        if group_ids:
            for row in AccessGroupUser.select().where(AccessGroupUser.group_id.in_(group_ids)).dicts():
                users[row["group_id"]].append(row["user_id"])
            for row in AccessGroupDataset.select().where(AccessGroupDataset.group_id.in_(group_ids)).dicts():
                datasets[row["group_id"]].append(row["dataset_id"])
            for row in AccessGroupSection.select().where(AccessGroupSection.group_id.in_(group_ids)).dicts():
                sections[row["group_id"]].append(row["section"])
        for group in groups:
            group["user_ids"] = sorted(users[group["id"]])
            group["dataset_ids"] = sorted(datasets[group["id"]])
            selected = set(sections[group["id"]])
            group["sections"] = [item for item in NAVIGATION_SECTIONS if item in selected]
        return groups

    @staticmethod
    @DB.connection_context()
    def options():
        users = list(User.select(User.id, User.email, User.nickname, User.is_superuser).order_by(User.email.asc()).dicts())
        datasets = list(
            Knowledgebase.select(Knowledgebase.id, Knowledgebase.name, Knowledgebase.tenant_id).where(Knowledgebase.status == StatusEnum.VALID.value).order_by(Knowledgebase.name.asc()).dicts()
        )
        return {"users": users, "datasets": datasets, "sections": list(NAVIGATION_SECTIONS)}

    @staticmethod
    def _replace_links(group_id, payload):
        mappings = (
            ("user_ids", AccessGroupUser, "user_id"),
            ("dataset_ids", AccessGroupDataset, "dataset_id"),
            ("sections", AccessGroupSection, "section"),
        )
        for field, model, target in mappings:
            if field not in payload:
                continue
            model.delete().where(model.group_id == group_id).execute()
            values = payload[field]
            if values:
                model.insert_many([{"group_id": group_id, target: value} for value in values]).execute()

    @classmethod
    @DB.connection_context()
    def create(cls, payload):
        data = validate_group_payload(payload)
        cls._validate_references(data)
        group_id = get_uuid()
        now = current_timestamp()
        with DB.atomic():
            try:
                AccessGroup.create(
                    id=group_id,
                    name=data["name"],
                    description=data["description"],
                    create_time=now,
                    update_time=now,
                    create_date=datetime_format(datetime.now()),
                    update_date=datetime_format(datetime.now()),
                )
            except IntegrityError as error:
                raise AccessGroupValidationError("A group with this name already exists") from error
            cls._replace_links(group_id, data)
        return next(group for group in cls.list_groups() if group["id"] == group_id)

    @classmethod
    @DB.connection_context()
    def update(cls, group_id, payload):
        data = validate_group_payload(payload, partial=True)
        values = {key: value for key, value in data.items() if key in {"name", "description"}}
        values["update_time"] = current_timestamp()
        values["update_date"] = datetime_format(datetime.now())
        with DB.atomic():
            if AccessGroup.get_or_none(AccessGroup.id == group_id) is None:
                raise LookupError("Access group not found")
            cls._validate_references(data, group_id=group_id)
            try:
                AccessGroup.update(values).where(AccessGroup.id == group_id).execute()
            except IntegrityError as error:
                raise AccessGroupValidationError("A group with this name already exists") from error
            cls._replace_links(group_id, data)
            AccessGroupDataset.delete().where((AccessGroupDataset.group_id == group_id) & AccessGroupDataset.dataset_id.not_in(Knowledgebase.select(Knowledgebase.id))).execute()
        return next(group for group in cls.list_groups() if group["id"] == group_id)

    @staticmethod
    @DB.connection_context()
    def delete(group_id):
        with DB.atomic():
            for model in (AccessGroupUser, AccessGroupDataset, AccessGroupSection):
                model.delete().where(model.group_id == group_id).execute()
            deleted = AccessGroup.delete().where(AccessGroup.id == group_id).execute()
        if not deleted:
            raise LookupError("Access group not found")

    @staticmethod
    @DB.connection_context()
    def effective_policy(user_id):
        group_ids = [row.group_id for row in AccessGroupUser.select(AccessGroupUser.group_id).where(AccessGroupUser.user_id == user_id)]
        if not group_ids:
            return None
        dataset_ids = {row.dataset_id for row in AccessGroupDataset.select(AccessGroupDataset.dataset_id).where(AccessGroupDataset.group_id.in_(group_ids))}
        sections = {row.section for row in AccessGroupSection.select(AccessGroupSection.section).where(AccessGroupSection.group_id.in_(group_ids))}
        return {"dataset_ids": dataset_ids, "sections": sections}

    @classmethod
    def has_section_access(cls, user, sections):
        return bool(set(cls.accessible_sections(user)).intersection(sections))

    @classmethod
    def accessible_sections(cls, user):
        if not user:
            return []
        if getattr(user, "is_superuser", False):
            return list(NAVIGATION_SECTIONS)
        return cls.visible_sections(user)

    @classmethod
    def visible_sections(cls, user):
        policy = cls.effective_policy(user.id) if user else None
        if policy is None:
            return get_visible_sections()
        return [section for section in NAVIGATION_SECTIONS if section in policy["sections"]]

    @classmethod
    def allowed_dataset_ids(cls, user_id):
        policy = cls.effective_policy(user_id)
        return None if policy is None else policy["dataset_ids"]
