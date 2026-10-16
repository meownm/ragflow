"""Role capabilities and ownership must jointly authorize document mutations."""

import pytest

from business_documents.domain.access import BusinessDocumentRole, DocumentAccess, normalize_role


@pytest.mark.parametrize("owner_id", ["reader", "other-owner"])
def test_reader_can_only_read_even_when_owning_the_document(owner_id):
    access = DocumentAccess("reader", BusinessDocumentRole.READER)
    assert access.capabilities() == {"read": True, "create": False, "edit_own": False, "edit_all": False, "delete": False, "assign": False}
    assert access.permissions(owner_id) == {"read": True, "edit": False, "delete": False, "assign": False}
    assert normalize_role("READER") == BusinessDocumentRole.READER


@pytest.mark.parametrize(
    "role,create,edit_all,delete",
    [
        ("AUTHOR_EDITOR", False, False, False),
        ("AUTHOR_CREATOR", True, False, False),
        ("MODERATOR_CREATOR", True, True, False),
        ("EXTENDED_MODERATOR", True, True, True),
    ],
)
def test_existing_roles_keep_their_capabilities(role, create, edit_all, delete):
    access = DocumentAccess("owner", role)
    assert access.capabilities() == {"read": True, "create": create, "edit_own": True, "edit_all": edit_all, "delete": delete, "assign": delete}
    assert access.permissions("owner")["edit"]
    assert access.permissions("someone-else")["edit"] is edit_all


def test_superuser_keeps_administrative_access_with_a_reader_assignment():
    access = DocumentAccess("admin", "READER", is_admin=True)
    assert access.role == BusinessDocumentRole.ADMIN
    assert all(access.capabilities().values())
    assert all(access.permissions("someone-else").values())
