#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

REST_API_MAX_PAGE_SIZE = 100


def validate_rest_api_page_size(page_size: int) -> int:
    """Validate REST API page_size values against the public maximum."""
    if page_size > REST_API_MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be less than or equal to {REST_API_MAX_PAGE_SIZE}")
    return page_size


def validate_rest_list_args(args, *, orderby_fields, default_page: int, default_page_size: int) -> tuple[int, int, str]:
    """Validate list inputs before the caller invokes its storage service."""
    values = []
    for field, default in (("page", default_page), ("page_size", default_page_size)):
        try:
            value = int(args.get(field, default))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{field} must be an integer") from exc
        if value < 0:
            raise ValueError(f"{field} must be greater than or equal to 0")
        values.append(value)
    page, page_size = values
    validate_rest_api_page_size(page_size)
    orderby = args.get("orderby", "create_time")
    if orderby not in orderby_fields:
        raise ValueError(f"Invalid orderby field: {orderby}")
    return page, page_size, orderby
