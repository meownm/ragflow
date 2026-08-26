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

import asyncio

from api.db.services.file_service import FileService
from common.misc_utils import thread_pool_exec


async def upload_agent_files(tenant_id: str, file_objs: list, url: str | None = None):
    if len(file_objs) == 1:
        return await thread_pool_exec(FileService.upload_info, tenant_id, file_objs[0], url)

    return await asyncio.gather(*(thread_pool_exec(FileService.upload_info, tenant_id, file_obj) for file_obj in file_objs))
