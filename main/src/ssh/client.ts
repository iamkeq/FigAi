import { spawn } from "node:child_process";
import type { SshHostConnection } from "../config.ts";

export interface SshCommandResult {
  exitCode: number | null;
  timedOut: boolean;
  stdout: string;
  stderr: string;
  stdoutTruncated: boolean;
  stderrTruncated: boolean;
}

const MAX_OUTPUT_CHARACTERS = 20_000;
const COMMAND_TIMEOUT_MS = 30_000;
const CONNECT_TIMEOUT_SECONDS = 10;

function bounded(chunks: Buffer[]): { text: string; truncated: boolean } {
  const joined = Buffer.concat(chunks).toString("utf8");
  if (joined.length <= MAX_OUTPUT_CHARACTERS) return { text: joined, truncated: false };
  return { text: joined.slice(0, MAX_OUTPUT_CHARACTERS), truncated: true };
}

/**
 * Runs a command on one explicitly configured host alias via the system `ssh` binary.
 * The command is passed as a single argv element after `--`, never through a local shell,
 * so this process cannot be manipulated by shell metacharacters in the command text; the
 * remote host's own shell still interprets it, which is inherent to running arbitrary
 * commands over SSH.
 */
export class SshClient {
  constructor(private readonly hosts: ReadonlyMap<string, SshHostConnection>) {}

  aliases(): string[] {
    return [...this.hosts.keys()].sort();
  }

  run(hostAlias: string, command: string): Promise<SshCommandResult> {
    const target = this.hosts.get(hostAlias);
    if (!target) throw new Error(`No SSH host is configured for "${hostAlias}".`);
    const args = [
      "-o",
      "BatchMode=yes",
      "-o",
      "StrictHostKeyChecking=yes",
      "-o",
      `ConnectTimeout=${CONNECT_TIMEOUT_SECONDS}`,
      "-p",
      String(target.port),
      ...(target.keyPath ? ["-i", target.keyPath] : []),
      `${target.user}@${target.host}`,
      "--",
      command,
    ];
    return new Promise((resolvePromise, reject) => {
      const child = spawn("ssh", args, { stdio: ["ignore", "pipe", "pipe"] });
      const stdoutChunks: Buffer[] = [];
      const stderrChunks: Buffer[] = [];
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        child.kill("SIGKILL");
      }, COMMAND_TIMEOUT_MS);
      child.stdout.on("data", (chunk: Buffer) => stdoutChunks.push(chunk));
      child.stderr.on("data", (chunk: Buffer) => stderrChunks.push(chunk));
      child.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        const stdout = bounded(stdoutChunks);
        const stderr = bounded(stderrChunks);
        resolvePromise({
          exitCode: code,
          timedOut,
          stdout: stdout.text,
          stderr: stderr.text,
          stdoutTruncated: stdout.truncated,
          stderrTruncated: stderr.truncated,
        });
      });
    });
  }
}
