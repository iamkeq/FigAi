export class Semaphore {
  private available: number;
  private readonly waiting: Array<() => void> = [];

  constructor(limit: number) {
    if (!Number.isInteger(limit) || limit < 1) throw new Error("Semaphore limit must be positive.");
    this.available = limit;
  }

  async run<T>(work: () => Promise<T>): Promise<T> {
    await this.acquire();
    try {
      return await work();
    } finally {
      this.release();
    }
  }

  private async acquire(): Promise<void> {
    if (this.available > 0) {
      this.available -= 1;
      return;
    }
    await new Promise<void>((resolve) => this.waiting.push(resolve));
  }

  private release(): void {
    const next = this.waiting.shift();
    if (next) next();
    else this.available += 1;
  }
}

export class KeyedMutex {
  private readonly tails = new Map<string, Promise<void>>();

  async run<T>(key: string, work: () => Promise<T>): Promise<T> {
    const previous = this.tails.get(key) ?? Promise.resolve();
    let release!: () => void;
    const current = new Promise<void>((resolve) => {
      release = resolve;
    });
    this.tails.set(key, current);
    await previous;
    try {
      return await work();
    } finally {
      release();
      if (this.tails.get(key) === current) this.tails.delete(key);
    }
  }
}

export class TurnCoordinator {
  private readonly threads = new KeyedMutex();
  private readonly turns: Semaphore;

  constructor(limit = 2) {
    this.turns = new Semaphore(limit);
  }

  run<T>(channelId: string, threadTs: string, work: () => Promise<T>): Promise<T> {
    return this.threads.run(`${channelId}:${threadTs}`, () => this.turns.run(work));
  }
}
