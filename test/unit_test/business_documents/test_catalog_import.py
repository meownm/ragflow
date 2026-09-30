import hashlib

import pytest

from business_documents.domain.catalog_import import CatalogImportError, build_document_catalog


def _source(*l5_items, source_version=None):
    source = {
        "L1": [
            {
                "L1_ID": "01",
                "L1_RU": "Клиенты",
                "L2": [
                    {
                        "L2_ID": "01.01",
                        "L2_RU": "Стратегия",
                        "L3": [
                            {
                                "L3_ID": "01.01.01",
                                "L3_RU": "Жизненный цикл",
                                "L4": [
                                    {
                                        "L4_ID": "01.01.01.01",
                                        "L4_RU": "Онбординг",
                                        "L5": list(l5_items),
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    if source_version is not None:
        source["source_version"] = source_version
    return source


def _l5(item_id="01.01.01.01.01", title="Онбординг клиента", **overrides):
    return {
        "L5_ID": item_id,
        "L5_RU": title,
        "L5_EN": None,
        "L5_Description_RU": None,
        "Capability_Level": "L3",
        "Capability_Type": "Core",
        "Business_Object": "Client; Agreement",
        "BIAN_Indicative_Service_Domain": "Customer Management",
        "Description_RU": "Описание",
        "Source_IDs": "SRC-01",
        "Source_URLs": "https://example.test",
        "Owner_Function": "Клиентский блок",
        "Maturity_Target": "4 - Managed",
        "Criticality": "High",
        "Notes": None,
        **overrides,
    }


def test_builds_only_l5_records_with_source_ids_and_hierarchy():
    catalog = build_document_catalog(_source(_l5()), filename="BCM_v24 (3).json", source_sha256="a" * 64)

    assert catalog["source_id"] == "BCM_Bank"
    assert catalog["source_version"] == "v24"
    assert catalog["items"] == [
        {
            "id": "01.01.01.01.01",
            "title": "01.01.01.01.01 Онбординг клиента",
            "title_en": None,
            "description": "Описание",
            "capability_level": "L5",
            "capability_type": "Core",
            "hierarchy": {
                "L1_ID": "01",
                "L1_RU": "Клиенты",
                "L2_ID": "01.01",
                "L2_RU": "Стратегия",
                "L3_ID": "01.01.01",
                "L3_RU": "Жизненный цикл",
                "L4_ID": "01.01.01.01",
                "L4_RU": "Онбординг",
            },
            "details": {
                "source_l5_id": "01.01.01.01.01",
                "source_capability_level": "L3",
                "l5_description_ru": None,
                "business_object": "Client; Agreement",
                "bian_indicative_service_domain": "Customer Management",
                "source_ids": "SRC-01",
                "source_urls": "https://example.test",
                "owner_function": "Клиентский блок",
                "maturity_target": "4 - Managed",
                "criticality": "High",
                "notes": None,
            },
        }
    ]


def test_duplicate_source_ids_receive_stable_title_hash_suffixes():
    first_title = "Курс для физических лиц"
    second_title = "Курс для юридических лиц"
    catalog = build_document_catalog(
        _source(_l5("10.02.01.01.01", first_title), _l5("10.02.01.01.01", second_title)),
        filename="BCM_v24.json",
        source_sha256="b" * 64,
    )

    assert [item["id"] for item in catalog["items"]] == [
        f"10.02.01.01.01-{hashlib.sha256(first_title.encode()).hexdigest()[:8]}",
        f"10.02.01.01.01-{hashlib.sha256(second_title.encode()).hexdigest()[:8]}",
    ]
    assert {item["details"]["source_l5_id"] for item in catalog["items"]} == {"10.02.01.01.01"}


def test_accepts_legacy_powershell_hashtable_l5_strings_without_splitting_value_semicolons():
    item = "@{L5_ID=01.01.01.01.01; L5_RU=Онбординг; Business_Object=Client; Agreement; Notes=Строка 1\nСтрока 2}"

    catalog = build_document_catalog(_source(item, source_version="v25"), filename="catalog.json", source_sha256="c" * 64)

    assert catalog["items"][0]["details"]["business_object"] == "Client; Agreement"
    assert catalog["items"][0]["details"]["notes"] == "Строка 1\nСтрока 2"


@pytest.mark.parametrize(
    "source,filename,message",
    [
        ({}, "BCM_v24.json", "L1"),
        (_source(_l5(L5_ID=None)), "BCM_v24.json", "L5_ID"),
        (_source(_l5()), "catalog.json", "version"),
        (_source(_l5(), source_version="v24") | {"source_id": "other"}, "catalog.json", "source_id"),
    ],
)
def test_rejects_invalid_structure_and_source_metadata(source, filename, message):
    with pytest.raises(CatalogImportError, match=message):
        build_document_catalog(source, filename=filename, source_sha256="d" * 64)
