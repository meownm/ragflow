from uuid import uuid1

import pytest
from pydantic import ValidationError

from api.utils.validation_utils import CreateDatasetReq, UpdateDatasetReq


@pytest.mark.parametrize("request_type", [CreateDatasetReq, UpdateDatasetReq])
@pytest.mark.parametrize("name", ["Я" * 65, "Я" * 128, "  Dataset  "])
def test_dataset_request_accepts_character_limits(request_type, name):
    data = {"name": name}
    if request_type is UpdateDatasetReq:
        data["dataset_id"] = uuid1().hex
    assert request_type.model_validate(data).name == name.strip()


@pytest.mark.parametrize("request_type", [CreateDatasetReq, UpdateDatasetReq])
@pytest.mark.parametrize("name", ["   ", "Я" * 129, "Dataset 🚀", "nul\x00", "surrogate\ud800"])
def test_dataset_request_rejects_invalid_names(request_type, name):
    data = {"name": name}
    if request_type is UpdateDatasetReq:
        data["dataset_id"] = uuid1().hex
    with pytest.raises(ValidationError):
        request_type.model_validate(data)
