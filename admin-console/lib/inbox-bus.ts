/**
 * Postgres LISTEN/NOTIFY bus for the admin-console inbox + orders SSE.
 *
 * Singleton pg.Client per Vercel instance fans `inbox_event` notifications
 * out to all in-memory subscribers via an EventEmitter, so N open SSE
 * panes cost one DB connection.
 *
 * IMPORTANT — DATABASE_URL_LISTEN must point to a SESSION-MODE Postgres
 * URL (Supabase: "Session pooler" connection string, port 5432, or the
 * direct connection). Supavisor's transaction-mode URL — what Prisma
 * uses — silently strips `LISTEN`, so notifications never arrive AND
 * the connection stays held, competing with the bot's connection pool
 * on the upstream database. If unset, this bus falls back to
 * DATABASE_URL, which is fine for local dev (direct Postgres) but
 * wrong for any environment fronted by a transaction-mode pooler.
 */
import { EventEmitter } from "node:events";
import { Client } from "pg";

export type InboxEvent =
  | {
      type: "message";
      business_id: string;
      whatsapp_id: string;
      message_id: number;
      role: string;
      ts: number;
    }
  | {
      type: "attachment";
      business_id: string;
      whatsapp_id: string;
      message_id: number;
      attachment_id: string;
    }
  | {
      type: "agent";
      business_id: string;
      whatsapp_id: string;
      agent_enabled: boolean;
    }
  | {
      type: "order";
      business_id: string;
      order_id: string;
      /** Present on direct order updates; absent on order_items propagation. */
      status?: string;
      /** "INSERT" | "UPDATE" for orders, "ITEM_INSERT" | "ITEM_UPDATE" | "ITEM_DELETE" for order_items. */
      op: string;
    };

export type InboxEventType = InboxEvent["type"];

type SubscriberFilter = {
  businessId: string;
  /** Only matches events whose payload has this whatsapp_id. */
  whatsappId?: string;
  /** Restrict to a subset of event types. Omit to receive all types. */
  eventTypes?: readonly InboxEventType[];
};

type Subscriber = {
  filter: SubscriberFilter;
  handler: (event: InboxEvent) => void;
};

const CHANNEL = "inbox_event";
const DISCONNECT_GRACE_MS = 30_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_CAP_MS = 30_000;

class InboxBus {
  private emitter = new EventEmitter();
  private client: Client | null = null;
  private subscribers = 0;
  private connecting: Promise<void> | null = null;
  private disconnectTimer: NodeJS.Timeout | null = null;
  private reconnectAttempt = 0;

  constructor() {
    // Subscribers can each register; bumping the limit keeps the warning quiet
    // for an admin tool with N concurrent open inboxes per Vercel instance.
    this.emitter.setMaxListeners(0);
  }

  subscribe(
    filter: Subscriber["filter"],
    handler: Subscriber["handler"],
  ): () => void {
    if (this.disconnectTimer) {
      clearTimeout(this.disconnectTimer);
      this.disconnectTimer = null;
    }

    this.subscribers += 1;
    void this.ensureConnected();

    const allowedTypes = filter.eventTypes
      ? new Set<InboxEventType>(filter.eventTypes)
      : null;

    const wrapped = (event: InboxEvent) => {
      if (event.business_id !== filter.businessId) return;
      if (allowedTypes && !allowedTypes.has(event.type)) return;
      // Only events that carry a whatsapp_id field can match this filter.
      if (filter.whatsappId) {
        if (
          !("whatsapp_id" in event) ||
          event.whatsapp_id !== filter.whatsappId
        )
          return;
      }
      handler(event);
    };
    this.emitter.on(CHANNEL, wrapped);

    return () => {
      this.emitter.off(CHANNEL, wrapped);
      this.subscribers = Math.max(0, this.subscribers - 1);
      if (this.subscribers === 0) {
        this.scheduleDisconnect();
      }
    };
  }

  private async ensureConnected(): Promise<void> {
    if (this.client) return;
    if (this.connecting) return this.connecting;

    this.connecting = this.connectWithRetry();
    try {
      await this.connecting;
    } finally {
      this.connecting = null;
    }
  }

  private async connectWithRetry(): Promise<void> {
    while (true) {
      try {
        await this.connect();
        this.reconnectAttempt = 0;
        return;
      } catch (err) {
        // No subscribers left; abandon the reconnect loop.
        if (this.subscribers === 0) return;
        this.reconnectAttempt += 1;
        const delay = Math.min(
          RECONNECT_BASE_MS * 2 ** (this.reconnectAttempt - 1),
          RECONNECT_CAP_MS,
        );
        console.error(
          `[inbox-bus] connect failed (attempt ${this.reconnectAttempt}); retrying in ${delay}ms`,
          err,
        );
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  private async connect(): Promise<void> {
    const connectionString =
      process.env.DATABASE_URL_LISTEN || process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error(
        "DATABASE_URL_LISTEN (or DATABASE_URL) must be set for the inbox bus",
      );
    }

    const client = new Client({ connectionString });
    client.on("notification", (msg) => {
      if (msg.channel !== CHANNEL || !msg.payload) return;
      try {
        const event = JSON.parse(msg.payload) as InboxEvent;
        this.emitter.emit(CHANNEL, event);
      } catch (err) {
        console.error("[inbox-bus] invalid payload", msg.payload, err);
      }
    });
    client.on("error", (err) => {
      console.error("[inbox-bus] pg client error", err);
      this.handleDisconnect(client);
    });
    client.on("end", () => {
      this.handleDisconnect(client);
    });

    await client.connect();
    await client.query(`LISTEN ${CHANNEL}`);
    console.log(`[inbox-bus] LISTEN ${CHANNEL} active`);
    this.client = client;
  }

  private handleDisconnect(client: Client) {
    if (this.client !== client) return;
    this.client = null;
    if (this.subscribers > 0) {
      // Live subscribers — reconnect.
      void this.ensureConnected();
    }
  }

  private scheduleDisconnect() {
    if (this.disconnectTimer) clearTimeout(this.disconnectTimer);
    this.disconnectTimer = setTimeout(() => {
      this.disconnectTimer = null;
      if (this.subscribers === 0 && this.client) {
        const client = this.client;
        this.client = null;
        client.removeAllListeners();
        client.end().catch((err) => {
          console.error("[inbox-bus] error ending client", err);
        });
      }
    }, DISCONNECT_GRACE_MS);
  }
}

declare global {
  var __inboxBus: InboxBus | undefined;
}

export const inboxBus: InboxBus = globalThis.__inboxBus ?? new InboxBus();
if (process.env.NODE_ENV !== "production") {
  globalThis.__inboxBus = inboxBus;
}
