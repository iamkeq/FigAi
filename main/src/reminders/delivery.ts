export interface SlackDeliveryTarget {
  channel: string;
  threadTs?: string;
  mentionCreator: boolean;
}

interface Deliverable {
  delivery_mode: "thread" | "channel" | "dm";
  creator_user_id: string;
  channel_id: string;
  thread_ts: string;
  surface: "dm" | "channel";
}

export function slackDeliveryTarget(reminder: Deliverable): SlackDeliveryTarget {
  if (reminder.delivery_mode === "dm") {
    return { channel: reminder.creator_user_id, mentionCreator: false };
  }
  if (reminder.delivery_mode === "channel") {
    return { channel: reminder.channel_id, mentionCreator: true };
  }
  return {
    channel: reminder.channel_id,
    threadTs: reminder.thread_ts,
    mentionCreator: reminder.surface === "channel",
  };
}
