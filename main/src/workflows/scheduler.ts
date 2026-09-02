import type { WorkflowRepository } from "../db/workflows.ts";
import { errorMessage, log } from "../logger.ts";
import type { Clock } from "../types.ts";
import { systemClock } from "../types.ts";
import type { WorkflowEngine } from "./engine.ts";

const MAX_IDLE_WAIT_MS = 60_000;

export class WorkflowScheduler {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private polling = false;
  private wakePending = false;
  private stopped = true;
  private unsubscribe: (() => void) | null = null;

  constructor(
    private readonly workflows: WorkflowRepository,
    private readonly engine: WorkflowEngine,
    private readonly clock: Clock = systemClock,
  ) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.unsubscribe = this.workflows.onChange(() => this.wake());
    this.schedule(0);
  }

  async stop(): Promise<void> {
    this.stopped = true;
    this.unsubscribe?.();
    this.unsubscribe = null;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    while (this.polling) await Bun.sleep(5);
  }

  wake(): void {
    if (this.stopped) return;
    if (this.polling) {
      this.wakePending = true;
      return;
    }
    this.schedule(0);
  }

  async poll(): Promise<void> {
    if (this.polling || this.stopped) return;
    this.polling = true;
    this.wakePending = false;
    try {
      const now = this.clock.now().getTime();
      const due = this.workflows.leaseDue(now);
      for (const workflow of due) await this.engine.handleDue(workflow.id, now);
    } catch (error) {
      log("error", "workflow_scheduler_failed", { error: errorMessage(error) });
    } finally {
      this.polling = false;
      if (!this.stopped) this.schedule(this.wakePending ? 0 : this.delayUntilNext());
    }
  }

  private delayUntilNext(): number {
    const now = this.clock.now().getTime();
    const next = this.workflows.nextDueAt(now);
    if (next === null) return MAX_IDLE_WAIT_MS;
    return Math.min(MAX_IDLE_WAIT_MS, Math.max(0, next - now));
  }

  private schedule(delay: number): void {
    if (this.stopped) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.poll();
    }, delay);
  }
}
