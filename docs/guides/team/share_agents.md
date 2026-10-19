---
sidebar_position: 6
slug: /share_agent
sidebar_custom_props: {
  categoryIcon: LucideShare2
}
---
# Share Agent

Share an Agent with your team members.

---

When ready, you may share your Agents with your team members so that they can use them. Please note that your Agents are not shared automatically; you must manually enable sharing by selecting the corresponding **Permissions** radio button:

1. Click the intended Agent to open its editing canvas.
2. Click **Management** > **Settings** to show the **Agent settings** dialogue.
3. Change **Permissions** from **Only me** to **Team**.
4. Click **Save** to apply your changes.

*When completed, your team members will see your shared Agents.*

Sharing an Agent grants access to the application, not to another user's
conversation history. Session lists, messages, run logs, continuation and
deletion are restricted to the authenticated account that created the session,
including when the requester owns the Agent. Removing team membership blocks
subsequent requests to the shared Agent and its sessions.

The SDK's `user_id` remains an external correlation identifier. It does not
grant ownership of a session. The server records the authenticated creator
separately as `created_by` and ignores attempts to supply it from a client.
Historical sessions without recorded authorship remain stored but are excluded
from these APIs until ownership can be established from trusted evidence;
ownership is never inferred from the external `user_id`.
