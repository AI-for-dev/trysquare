// SPDX-License-Identifier: BSD-3-Clause
// judge-tool.ts - makes a judge's verdict a schema-checked tool call.
//
// There is no declarative way to get conforming JSON out of the agent: no schema
// option, no response format. Prompt discipline plus parsing is the obvious
// fallback and it is the wrong one, because a judge that answers in prose with a
// stray code fence produces an unreadable verdict, and an unreadable verdict is
// indistinguishable from a negative one unless something catches it.
//
// So the verdict is a **tool**. Its parameters are a typebox schema built from the
// metrics the scenario declared, and the agent validates the call before it ever
// reaches us. The format is therefore guaranteed by the runtime rather than by the
// model's good behaviour.
//
// What is *not* guaranteed is that the call happens at all - that still depends on
// the model. A judge that never calls the tool leaves no verdict file, and the
// harness treats that as an invalid run rather than as a false one.
//
// Static, like the agent gate: the expected metrics are read from a request file
// the harness writes next to the working directory, so this brick needs no
// configuration and cannot drift from the scenario.

import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const REQUEST = "judge-request.json";
const VERDICT = "verdict.json";

type Request = {
  metrics: string[];
  rubric?: string;
};

function request(cwd: string): Request {
  try {
    return JSON.parse(readFileSync(join(cwd, REQUEST), "utf8")) as Request;
  } catch {
    return { metrics: [] };
  }
}

export default function (pi: ExtensionAPI) {
  const cwd = process.cwd();
  const { metrics } = request(cwd);

  // Every declared metric is a required property. A call missing one fails
  // validation, so the model is told and retries, instead of us discovering the
  // hole afterwards and having to decide what a missing metric means.
  const shape: Record<string, unknown> = {};
  for (const name of metrics) {
    shape[name] = Type.Union([Type.Boolean(), Type.Number(), Type.String()], {
      description: `Verdict for "${name}". Answer from the rubric, not from impressions.`,
    });
  }

  pi.registerTool({
    name: "verdict",
    label: "Record the verdict",
    description:
      "Records your verdict for every metric the rubric defines. Call this exactly " +
      "once, when you have read the material and reached a conclusion. This is the " +
      "only way your judgement is recorded: prose outside this call is discarded.",
    promptSnippet: "Record the verdict for every metric",
    promptGuidelines: [
      "Call verdict exactly once, with every metric the rubric defines.",
      "Give a short reason for each metric, quoting what in the material decided it.",
    ],
    parameters: Type.Object({
      metrics: Type.Object(shape as never, {
        description: "One entry per metric defined by the rubric.",
      }),
      reasons: Type.Optional(
        Type.Record(Type.String(), Type.String(), {
          description: "Per metric, one sentence naming what decided it.",
        }),
      ),
    }),
    async execute(_toolCallId, params) {
      // Written where the harness reads it. Writing here rather than returning it
      // matters: the harness must not have to parse a transcript to find the
      // verdict it already validated.
      writeFileSync(
        join(cwd, VERDICT),
        JSON.stringify({ metrics: params.metrics, reasons: params.reasons ?? {} }, null, 2),
      );
      const recorded = Object.keys(params.metrics as Record<string, unknown>).length;
      return {
        content: [{ type: "text", text: `Verdict recorded for ${recorded} metrics.` }],
      };
    },
  });
}
