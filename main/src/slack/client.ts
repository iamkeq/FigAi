export interface SlackClient {
  filesUploadV2(args: {
    channel_id: string;
    thread_ts?: string;
    filename: string;
    title?: string;
    alt_text?: string;
    file: Buffer;
  }): Promise<unknown>;
  auth: { test(args?: Record<string, never>): Promise<unknown> };
  users: { info(args: { user: string }): Promise<unknown> };
  conversations: {
    info(args: { channel: string }): Promise<unknown>;
    members(args: { channel: string; cursor?: string; limit?: number }): Promise<unknown>;
    replies(args: {
      channel: string;
      ts: string;
      limit?: number;
      cursor?: string;
    }): Promise<unknown>;
  };
  chat: {
    postMessage(args: {
      channel: string;
      text: string;
      thread_ts?: string;
      client_msg_id?: string;
      blocks?: Array<{ type: "markdown"; text: string }>;
      unfurl_links?: boolean;
      unfurl_media?: boolean;
    }): Promise<unknown>;
  };
  assistant: {
    threads: {
      setStatus(args: {
        channel_id: string;
        thread_ts: string;
        status: string;
        loading_messages?: string[];
      }): Promise<unknown>;
    };
  };
  reactions: {
    add(args: { channel: string; timestamp: string; name: string }): Promise<unknown>;
    remove(args: { channel: string; timestamp: string; name: string }): Promise<unknown>;
  };
}
