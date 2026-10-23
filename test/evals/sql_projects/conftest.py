"""Own asyncio resources without application bootstrap or unit-only setup."""

from test.asyncio_policy import event_loop_policy as event_loop_policy
from test.asyncio_policy import owned_policy_loop as owned_policy_loop
