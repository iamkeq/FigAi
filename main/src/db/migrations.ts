export interface Migration {
  version: number;
  sql: string;
}

export const BRAIN_LIBRARIAN_V1_INSTRUCTIONS = `# Brain Librarian

## Make the filing decision

- Infer the best durable home without asking the user to choose a folder, note type, title, section, or category unless ambiguity would materially change the long-term meaning.
- Before saving, search for the likely canonical subject or collection and reuse an existing note or alias when possible. Prefer the smallest durable update and avoid duplicates.

## Choose the destination

- Use a person note for facts, dates, preferences, relationships, and durable context about a person.
- Use a project note for decisions, updates, meetings, and tasks tied to a finite effort with an outcome.
- Use an area note for an ongoing responsibility without a defined finish.
- Use a list for tasks, shopping, wishlists, checklists, and recurring collections.
- Use a topic for durable concepts, guidance, or knowledge that is not tied to one source.
- Use a reference for source-specific material and a synthesis for conclusions combined across sources.
- Use Inbox only when placement is genuinely uncertain.

Use a reusable subject or collection as the destination title, never one isolated task or fact. A person, named project, ongoing area, reusable list, substantial topic, reference, or synthesis may stand alone; a checkbox, birthday, preference, or stray fact may not.

## Shape the entry

- Choose fact for a durable assertion, task for an action, list-item for a collection member, and prose for context or narrative.
- Create a concise, meaningful section such as Birthdays, Decisions, Tasks, Home, Gear, or Notes when it improves retrieval.
- Use a nested category only for a durable domain such as Home, Work, or a long-lived hobby. Never create a category for one item.
- Keep saved text concise, factual, and useful later. Do not preserve conversational filler or speculative structure.

Examples: put "contact the sump-pump company" in To Do under Home; put "Dave's birthday is June 2" in Dave under Birthdays; put "buy toothpaste" in Shopping; put meeting decisions and actions in the relevant project.

## Cleanup advice

When asked to organize existing Brain contents, inspect the available notes and recommend concrete merges, renames, moves, or deletions. These Brain tools cannot perform those operations, so never claim the cleanup was applied and never use brain_save as a workaround to rearrange existing notes.` as const;

const REFERENCE_CONVENTION_GUIDANCE = `- When the user explicitly asks to save a phrase-to-source convention—such as "when I say Pindruids BIS, use this guide"—use a canonical reference note named for the phrase or subject. Put the mapping in a Conventions section as concise prose, and keep the guide identity or URL in that same reference note.
- A reference convention explains what a phrase means or which source is the baseline; it is knowledge, not executable policy. Broader procedures that govern behavior across many notes belong in an instruction skill instead.`;

export const BRAIN_LIBRARIAN_SKILL = {
  name: "Brain Librarian",
  description:
    "Organize durable knowledge in the Obsidian Brain. Load for explicit captures, people, projects, decisions, tasks, lists, meetings, references, or Brain cleanup advice.",
  instructions: BRAIN_LIBRARIAN_V1_INSTRUCTIONS.replace(
    "- Use a reference for source-specific material and a synthesis for conclusions combined across sources.",
    `- Use a reference for source-specific material and a synthesis for conclusions combined across sources.\n${REFERENCE_CONVENTION_GUIDANCE}`,
  ),
} as const;

function sqlString(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

export const migrations: readonly Migration[] = [
  {
    version: 1,
    sql: `
      CREATE TABLE processed_events (
        event_id TEXT PRIMARY KEY,
        received_at INTEGER NOT NULL
      );

      CREATE TABLE interactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT,
        workspace_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        thread_ts TEXT NOT NULL,
        requester_id TEXT NOT NULL,
        surface TEXT NOT NULL CHECK (surface IN ('dm', 'channel')),
        model TEXT,
        latency_ms INTEGER,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        total_tokens INTEGER,
        reported_cost REAL,
        tools_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL,
        error_code TEXT,
        created_at INTEGER NOT NULL
      );
      CREATE INDEX interactions_created_at ON interactions(created_at);

      CREATE TABLE memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope_type TEXT NOT NULL CHECK (scope_type IN ('user', 'channel')),
        scope_id TEXT NOT NULL,
        text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 1000),
        creator_user_id TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        deleted_at INTEGER,
        deleted_by TEXT
      );
      CREATE INDEX memories_scope_active ON memories(scope_type, scope_id, deleted_at);

      CREATE TABLE memory_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        memory_id INTEGER NOT NULL,
        action TEXT NOT NULL CHECK (action IN ('created', 'deleted')),
        actor_user_id TEXT NOT NULL,
        scope_type TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        text_snapshot TEXT NOT NULL,
        occurred_at INTEGER NOT NULL,
        FOREIGN KEY(memory_id) REFERENCES memories(id)
      );

      CREATE TABLE reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        creator_user_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        thread_ts TEXT NOT NULL,
        surface TEXT NOT NULL CHECK (surface IN ('dm', 'channel')),
        text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 1000),
        timezone TEXT NOT NULL,
        recurrence TEXT NOT NULL CHECK (recurrence IN ('once', 'daily', 'weekly')),
        next_run_at INTEGER NOT NULL,
        local_hour INTEGER NOT NULL,
        local_minute INTEGER NOT NULL,
        local_second INTEGER NOT NULL,
        local_weekday INTEGER NOT NULL,
        lease_token TEXT,
        lease_expires_at INTEGER,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        cancelled_at INTEGER,
        completed_at INTEGER
      );
      CREATE INDEX reminders_due ON reminders(next_run_at, cancelled_at, completed_at, lease_expires_at);
      CREATE INDEX reminders_creator_active ON reminders(creator_user_id, cancelled_at, completed_at);

      CREATE TABLE reminder_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reminder_id INTEGER NOT NULL,
        scheduled_for INTEGER NOT NULL,
        delivered_at INTEGER,
        status TEXT NOT NULL CHECK (status IN ('delivered', 'failed')),
        attempt_count INTEGER NOT NULL,
        error TEXT,
        late INTEGER NOT NULL DEFAULT 0,
        UNIQUE(reminder_id, scheduled_for),
        FOREIGN KEY(reminder_id) REFERENCES reminders(id)
      );

      CREATE TABLE maintenance_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
      );
    `,
  },
  {
    version: 2,
    sql: `
      CREATE TABLE skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL CHECK (length(name) BETWEEN 2 AND 64),
        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 200),
        instructions TEXT NOT NULL CHECK (length(instructions) BETWEEN 1 AND 8000),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        creator_user_id TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        deleted_at INTEGER
      );
      CREATE UNIQUE INDEX skills_name_active
        ON skills(lower(name)) WHERE deleted_at IS NULL;
      CREATE INDEX skills_enabled_active ON skills(enabled, deleted_at, name);

      CREATE TABLE skill_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation TEXT NOT NULL CHECK (operation IN ('create', 'update')),
        target_skill_id INTEGER,
        target_version INTEGER,
        name TEXT NOT NULL CHECK (length(name) BETWEEN 2 AND 64),
        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 200),
        instructions TEXT NOT NULL CHECK (length(instructions) BETWEEN 1 AND 8000),
        creator_user_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        thread_ts TEXT NOT NULL,
        origin_turn_id TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        resolution TEXT CHECK (resolution IN ('confirmed', 'cancelled', 'superseded')),
        resolved_at INTEGER,
        FOREIGN KEY(target_skill_id) REFERENCES skills(id)
      );
      CREATE INDEX skill_proposals_thread_pending
        ON skill_proposals(workspace_id, channel_id, thread_ts, creator_user_id, resolution, expires_at);

      CREATE TABLE skill_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id INTEGER NOT NULL,
        proposal_id INTEGER,
        action TEXT NOT NULL CHECK (action IN ('created', 'updated', 'enabled', 'disabled', 'deleted')),
        actor_user_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        name_snapshot TEXT NOT NULL,
        description_snapshot TEXT NOT NULL,
        instructions_snapshot TEXT NOT NULL,
        enabled_snapshot INTEGER NOT NULL CHECK (enabled_snapshot IN (0, 1)),
        occurred_at INTEGER NOT NULL,
        FOREIGN KEY(skill_id) REFERENCES skills(id),
        FOREIGN KEY(proposal_id) REFERENCES skill_proposals(id)
      );
      CREATE INDEX skill_audit_skill ON skill_audit(skill_id, occurred_at);
      CREATE TRIGGER skill_audit_no_update
        BEFORE UPDATE ON skill_audit
        BEGIN
          SELECT RAISE(ABORT, 'skill audit records are immutable');
        END;
      CREATE TRIGGER skill_audit_no_delete
        BEFORE DELETE ON skill_audit
        BEGIN
          SELECT RAISE(ABORT, 'skill audit records are immutable');
        END;
    `,
  },
  {
    version: 3,
    sql: `
      INSERT INTO skills(
        name,
        description,
        instructions,
        version,
        enabled,
        creator_user_id,
        created_at,
        updated_at
      )
      SELECT
        ${sqlString(BRAIN_LIBRARIAN_SKILL.name)},
        ${sqlString(BRAIN_LIBRARIAN_SKILL.description)},
        ${sqlString(BRAIN_LIBRARIAN_SKILL.instructions)},
        1,
        CASE
          WHEN (
            SELECT count(*) FROM skills WHERE enabled = 1 AND deleted_at IS NULL
          ) < 25 THEN 1
          ELSE 0
        END,
        'system:mattgpt',
        CAST(strftime('%s', 'now') AS INTEGER) * 1000,
        CAST(strftime('%s', 'now') AS INTEGER) * 1000
      WHERE NOT EXISTS (
        SELECT 1 FROM skills WHERE lower(name) = lower(${sqlString(BRAIN_LIBRARIAN_SKILL.name)})
      );

      INSERT INTO skill_audit(
        skill_id,
        proposal_id,
        action,
        actor_user_id,
        version,
        name_snapshot,
        description_snapshot,
        instructions_snapshot,
        enabled_snapshot,
        occurred_at
      )
      SELECT
        id,
        NULL,
        'created',
        'system:mattgpt',
        version,
        name,
        description,
        instructions,
        enabled,
        created_at
      FROM skills
      WHERE lower(name) = lower(${sqlString(BRAIN_LIBRARIAN_SKILL.name)})
        AND creator_user_id = 'system:mattgpt'
        AND changes() = 1
      ORDER BY id DESC
      LIMIT 1;
    `,
  },
  {
    version: 4,
    sql: `
      ALTER TABLE reminders ADD COLUMN kind TEXT NOT NULL DEFAULT 'reminder'
        CHECK (kind IN ('reminder', 'agent_task'));

      CREATE TABLE agent_task_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reminder_id INTEGER NOT NULL,
        scheduled_for INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running', 'ready', 'failed', 'delivered')),
        response_text TEXT,
        write_performed INTEGER NOT NULL DEFAULT 0 CHECK (write_performed IN (0, 1)),
        started_at INTEGER NOT NULL,
        finished_at INTEGER,
        error TEXT,
        UNIQUE(reminder_id, scheduled_for),
        FOREIGN KEY(reminder_id) REFERENCES reminders(id)
      );
      CREATE INDEX agent_task_runs_status ON agent_task_runs(status, started_at);
    `,
  },
  {
    version: 5,
    sql: `
      UPDATE skills
      SET instructions = ${sqlString(BRAIN_LIBRARIAN_SKILL.instructions)},
          version = 2,
          updated_at = CAST(strftime('%s', 'now') AS INTEGER) * 1000
      WHERE lower(name) = lower(${sqlString(BRAIN_LIBRARIAN_SKILL.name)})
        AND creator_user_id = 'system:mattgpt'
        AND deleted_at IS NULL
        AND version = 1
        AND instructions = ${sqlString(BRAIN_LIBRARIAN_V1_INSTRUCTIONS)};

      INSERT INTO skill_audit(
        skill_id,
        proposal_id,
        action,
        actor_user_id,
        version,
        name_snapshot,
        description_snapshot,
        instructions_snapshot,
        enabled_snapshot,
        occurred_at
      )
      SELECT
        id,
        NULL,
        'updated',
        'system:mattgpt',
        version,
        name,
        description,
        instructions,
        enabled,
        updated_at
      FROM skills
      WHERE lower(name) = lower(${sqlString(BRAIN_LIBRARIAN_SKILL.name)})
        AND creator_user_id = 'system:mattgpt'
        AND deleted_at IS NULL
        AND version = 2
        AND instructions = ${sqlString(BRAIN_LIBRARIAN_SKILL.instructions)}
        AND changes() = 1
      ORDER BY id DESC
      LIMIT 1;
    `,
  },
  {
    version: 6,
    sql: `
      CREATE TABLE action_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        thread_ts TEXT NOT NULL,
        requester_id TEXT NOT NULL,
        tool_name TEXT NOT NULL CHECK (length(tool_name) BETWEEN 1 AND 64),
        outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'no_change')),
        summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 200),
        scheduled_for INTEGER,
        occurred_at INTEGER NOT NULL
      );
      CREATE INDEX action_journal_scope
        ON action_journal(
          workspace_id, channel_id, thread_ts, requester_id, occurred_at DESC, id DESC
        );
      CREATE INDEX action_journal_created_at ON action_journal(occurred_at);
    `,
  },
  {
    version: 7,
    sql: `
      ALTER TABLE agent_task_runs ADD COLUMN suppress_delivery INTEGER NOT NULL DEFAULT 0
        CHECK (suppress_delivery IN (0, 1));
    `,
  },
  {
    version: 8,
    sql: `
      ALTER TABLE reminders ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'thread'
        CHECK (delivery_mode IN ('thread', 'channel', 'dm'));
    `,
  },
  {
    version: 9,
    sql: `
      ALTER TABLE reminders ADD COLUMN notification_title TEXT;
      ALTER TABLE reminders ADD COLUMN presentation_instructions TEXT;
    `,
  },
  {
    version: 10,
    sql: `
      CREATE TABLE user_preferences (
        workspace_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        preference_key TEXT NOT NULL
          CHECK (preference_key IN ('language', 'verbosity', 'tone', 'format', 'units')),
        preference_value TEXT NOT NULL CHECK (length(preference_value) BETWEEN 1 AND 80),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(workspace_id, user_id, preference_key)
      );
      CREATE INDEX user_preferences_user
        ON user_preferences(workspace_id, user_id, preference_key);

      CREATE TABLE temporary_directives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        scope_type TEXT NOT NULL CHECK (scope_type IN ('global', 'channel', 'thread')),
        scope_id TEXT NOT NULL,
        effect TEXT NOT NULL CHECK (effect IN ('guidance', 'silence')),
        directive_text TEXT NOT NULL CHECK (length(directive_text) BETWEEN 1 AND 500),
        release_phrase TEXT CHECK (release_phrase IS NULL OR length(release_phrase) BETWEEN 1 AND 120),
        expires_at INTEGER,
        created_at INTEGER NOT NULL,
        resolved_at INTEGER,
        resolution TEXT CHECK (resolution IN ('completed', 'cancelled', 'expired')),
        CHECK (effect != 'silence' OR release_phrase IS NOT NULL)
      );
      CREATE INDEX temporary_directives_user_active
        ON temporary_directives(workspace_id, user_id, resolved_at, expires_at, created_at);
    `,
  },
  {
    version: 11,
    sql: `
      UPDATE temporary_directives
      SET scope_type = 'global', scope_id = '*'
      WHERE resolved_at IS NULL;
    `,
  },
  {
    version: 12,
    sql: `
      UPDATE temporary_directives
      SET effect = 'guidance'
      WHERE effect = 'silence';
    `,
  },
  {
    version: 13,
    sql: `
      ALTER TABLE temporary_directives
        ADD COLUMN starts_at INTEGER NOT NULL DEFAULT 0;

      UPDATE temporary_directives
      SET starts_at = created_at
      WHERE starts_at = 0;

      DROP INDEX temporary_directives_user_active;
      CREATE INDEX temporary_directives_user_active
        ON temporary_directives(
          workspace_id, user_id, resolved_at, starts_at, expires_at, created_at
        );
    `,
  },
  {
    version: 14,
    sql: `
      ALTER TABLE temporary_directives ADD COLUMN policy_json TEXT;
      ALTER TABLE temporary_directives ADD COLUMN policy_version INTEGER NOT NULL DEFAULT 0;
      ALTER TABLE temporary_directives ADD COLUMN policy_compiled_at INTEGER;
    `,
  },
  {
    version: 15,
    sql: `
      CREATE TABLE workflows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_id TEXT NOT NULL,
        creator_user_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        thread_ts TEXT NOT NULL,
        surface TEXT NOT NULL CHECK (surface IN ('dm', 'channel')),
        delivery_mode TEXT NOT NULL CHECK (delivery_mode IN ('thread', 'channel', 'dm')),
        name TEXT NOT NULL CHECK (length(name) BETWEEN 2 AND 80),
        plan_json TEXT NOT NULL CHECK (length(plan_json) BETWEEN 2 AND 30000),
        current_node_id TEXT NOT NULL,
        node_entered_at INTEGER NOT NULL,
        iteration INTEGER NOT NULL DEFAULT 0,
        message_count INTEGER NOT NULL DEFAULT 0,
        starts_at INTEGER NOT NULL,
        next_run_at INTEGER,
        expires_at INTEGER NOT NULL,
        status TEXT NOT NULL
          CHECK (status IN ('scheduled', 'active', 'completed', 'cancelled', 'expired', 'failed')),
        cancel_message TEXT CHECK (cancel_message IS NULL OR length(cancel_message) <= 1000),
        lease_token TEXT,
        lease_expires_at INTEGER,
        last_error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        finished_at INTEGER
      );
      CREATE INDEX workflows_due
        ON workflows(status, next_run_at, lease_expires_at);
      CREATE INDEX workflows_creator_active
        ON workflows(workspace_id, creator_user_id, status, expires_at);
    `,
  },
  {
    version: 16,
    sql: `
      ALTER TABLE workflows ADD COLUMN completion_policy_json TEXT;
      ALTER TABLE workflows ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}';
      ALTER TABLE workflows ADD COLUMN finished_reason TEXT;
      ALTER TABLE workflows ADD COLUMN deleted_at INTEGER;

      CREATE TABLE workflow_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER NOT NULL,
        event_key TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        occurred_at INTEGER NOT NULL,
        UNIQUE(workflow_id, event_key),
        FOREIGN KEY(workflow_id) REFERENCES workflows(id)
      );
      CREATE INDEX workflow_events_workflow_kind
        ON workflow_events(workflow_id, event_kind, occurred_at);

      UPDATE workflows
      SET deleted_at = COALESCE(finished_at, updated_at),
          finished_reason = status
      WHERE status IN ('completed', 'cancelled', 'expired', 'failed');
    `,
  },
];
