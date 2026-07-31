// SPDX-License-Identifier: BSD-3-Clause
// agent-gate.ts - makes the scenario's subagents the only ones that can run.
//
// Dropping agent definitions into the clone's `.pi/agents/` does not activate
// them, and the failure is worse than an inactive brick. Two facts, both checked
// in the library's source rather than assumed:
//
//   1. `loadAgents` defaults to scope "user", and the subagent tool exposes
//      `scope` as a **tool parameter the model chooses**. There is no
//      environment variable and no setting to force it.
//   2. The tool passes `builtin: true` hardcoded. So a model that omits `scope`
//      does not get "no agents" - it gets the nine agents shipped with the
//      library, and **none of them declares a model**, so each inherits the
//      operator's defaultProvider/defaultModel/defaultThinkingLevel.
//
// A cell injecting `explorer` and `tester` would therefore have measured `scout`
// and `coder`, on the operator's personal settings, without one line of warning.
//
// This gate closes both holes. It forces the scope, and it refuses any agent the
// scenario did not inject - because forcing the scope alone would leave the nine
// shipped agents reachable by name.
//
// It is **static**: the permitted set is read from the directory the scenario
// filled, so the gate agrees with the scenario without configuration, codegen or
// a second place to keep in sync.
//
// This is a hook guaranteeing what an instruction would merely suggest, which is
// the same mechanism the course teaches, applied to the measuring tool itself.

import { readdirSync } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const AGENT_DIR = ".pi/agents";

// Every parameter of the subagent tool that names an agent. Missing one would
// leave a way in: `route` takes candidates, `reduce` takes reduceWith, `chain`
// and `loop` take steps.
const NAME_FIELDS = ["agent", "steps", "candidates", "reduceWith"] as const;

function permitted(cwd: string): string[] {
  try {
    return readdirSync(join(cwd, AGENT_DIR))
      .filter((f) => f.endsWith(".md"))
      .map((f) => f.slice(0, -3))
      .sort();
  } catch {
    return [];
  }
}

function requested(input: Record<string, unknown>): string[] {
  const names: string[] = [];
  for (const field of NAME_FIELDS) {
    const value = input[field];
    if (typeof value === "string") names.push(value);
    else if (Array.isArray(value)) {
      for (const v of value) if (typeof v === "string") names.push(v);
    }
  }
  return names;
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName !== "subagent") return;

    const cwd = ctx?.cwd ?? process.cwd();
    const allowed = permitted(cwd);

    // 1. The scope stops being the model's choice.
    //
    // `event.input` is mutable and mutations affect the actual execution, which
    // is what makes this possible without patching the library.
    const input = event.input as Record<string, unknown>;
    input.scope = "project";

    // 2. Only what the scenario injected may run.
    if (allowed.length === 0) {
      return {
        block: true,
        reason:
          `No agent definition is present in ${AGENT_DIR}. This scenario declares ` +
          `subagents but none reached the clone, so any delegation here would run ` +
          `an agent the experiment did not choose.`,
      };
    }

    const asked = requested(input);
    const unknown = asked.filter((name) => !allowed.includes(name));
    if (unknown.length > 0) {
      return {
        block: true,
        reason:
          `Unknown agent: ${unknown.join(", ")}. Available agents: ` +
          `${allowed.join(", ")}. Only these are part of this experiment; the ` +
          `library's built-in agents are deliberately out of reach because they ` +
          `declare no model and would inherit this machine's defaults.`,
      };
    }
  });
}
