"""inbox_event NOTIFY triggers for conversations / attachments / agent settings

Revision ID: d4f8a2b1c5e7
Revises: c3f7a9b1e4d2
Create Date: 2026-05-02

Adds Postgres LISTEN/NOTIFY plumbing so the admin-console inbox can drop
client-side polling and switch to SSE pushes. Three AFTER triggers all
publish to the same `inbox_event` channel with a `type` discriminator so
a single LISTEN covers the entire inbox feed:

    {type:'message',    business_id, whatsapp_id, message_id, role, ts}
    {type:'attachment', business_id, whatsapp_id, message_id, attachment_id}
    {type:'agent',      business_id, whatsapp_id, agent_enabled}

Payloads are intentionally tiny — the SSE handler refetches the affected
scope from Postgres before pushing to clients, so we don't leak full row
data through pg_notify (which has an 8000-byte hard cap).

The `conversation_attachments` payload joins back to `conversations` to
resolve business_id / whatsapp_id so the SSE filter logic doesn't need a
secondary lookup. If the parent message is missing (cascade-delete race)
the trigger silently no-ops.

Provider-side: this revision is purely additive. It does not alter
existing tables or constraints. Safe to deploy ahead of the consumer.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "d4f8a2b1c5e7"
down_revision: Union[str, Sequence[str], None] = "c3f7a9b1e4d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_inbox_message() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify(
                'inbox_event',
                json_build_object(
                    'type',        'message',
                    'business_id', NEW.business_id,
                    'whatsapp_id', NEW.whatsapp_id,
                    'message_id',  NEW.id,
                    'role',        NEW.role,
                    'ts',          extract(epoch FROM NEW.timestamp)
                )::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_inbox_attachment() RETURNS trigger AS $$
        DECLARE
            parent_business_id uuid;
            parent_whatsapp_id varchar(50);
        BEGIN
            SELECT business_id, whatsapp_id
              INTO parent_business_id, parent_whatsapp_id
              FROM conversations
              WHERE id = NEW.conversation_id;

            IF parent_business_id IS NULL THEN
                RETURN NEW;
            END IF;

            PERFORM pg_notify(
                'inbox_event',
                json_build_object(
                    'type',          'attachment',
                    'business_id',   parent_business_id,
                    'whatsapp_id',   parent_whatsapp_id,
                    'message_id',    NEW.conversation_id,
                    'attachment_id', NEW.id
                )::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_inbox_agent() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify(
                'inbox_event',
                json_build_object(
                    'type',          'agent',
                    'business_id',   NEW.business_id,
                    'whatsapp_id',   NEW.whatsapp_id,
                    'agent_enabled', NEW.agent_enabled
                )::text
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS conversations_notify_inbox ON conversations;
        CREATE TRIGGER conversations_notify_inbox
            AFTER INSERT ON conversations
            FOR EACH ROW EXECUTE FUNCTION notify_inbox_message();
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS conversation_attachments_notify_inbox ON conversation_attachments;
        CREATE TRIGGER conversation_attachments_notify_inbox
            AFTER INSERT ON conversation_attachments
            FOR EACH ROW EXECUTE FUNCTION notify_inbox_attachment();
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS conversation_agent_settings_notify_inbox ON conversation_agent_settings;
        CREATE TRIGGER conversation_agent_settings_notify_inbox
            AFTER INSERT OR UPDATE OF agent_enabled ON conversation_agent_settings
            FOR EACH ROW EXECUTE FUNCTION notify_inbox_agent();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS conversation_agent_settings_notify_inbox ON conversation_agent_settings")
    op.execute("DROP TRIGGER IF EXISTS conversation_attachments_notify_inbox ON conversation_attachments")
    op.execute("DROP TRIGGER IF EXISTS conversations_notify_inbox ON conversations")
    op.execute("DROP FUNCTION IF EXISTS notify_inbox_agent()")
    op.execute("DROP FUNCTION IF EXISTS notify_inbox_attachment()")
    op.execute("DROP FUNCTION IF EXISTS notify_inbox_message()")
