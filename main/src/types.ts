export type Surface = "dm" | "channel";
export type Recurrence = "once" | "daily" | "weekly";
export type ScheduleKind = "reminder" | "agent_task";
export type ScheduleDelivery = "thread" | "channel" | "dm";

export interface RuntimeContext {
  workspaceId: string;
  botUserId: string;
  requesterId: string;
  requesterName: string;
  surface: Surface;
  channelId: string;
  threadTs: string;
  turnId: string;
  timezone: string;
  isOwner: boolean;
  participantIds?: ReadonlySet<string>;
  participantNames?: ReadonlyMap<string, string>;
}

export interface SlackFile {
  id: string;
  name: string;
  mimetype: string;
  size: number;
  url_private_download?: string;
  url_private?: string;
}

export interface ThreadMessage {
  ts: string;
  user?: string;
  bot_id?: string;
  text: string;
  files?: SlackFile[];
}

export interface Clock {
  now(): Date;
}

export const systemClock: Clock = { now: () => new Date() };
