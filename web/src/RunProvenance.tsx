function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function recordedOutputs(events: unknown[]) {
  const outputs: { operation: string; model: string }[] = [];
  for (const event of events) {
    if (
      !record(event) ||
      typeof event.operation !== "string" ||
      event.phase !== "succeeded" ||
      typeof event.output_json !== "string"
    )
      continue;
    try {
      const output: unknown = JSON.parse(event.output_json);
      if (record(output) && typeof output.model === "string") {
        outputs.push({ operation: event.operation, model: output.model });
      }
    } catch {
      // A malformed historical trace cannot supply provenance.
    }
  }
  return outputs;
}

function modelLabel(model: string): string {
  if (model === "demo-policy-v1") return "Demo rules";
  if (model.startsWith("fake-narrative")) return "Demo template";
  if (model.startsWith("risk_model@")) return "Local synthetic risk";
  if (model.startsWith("jev-synthetic")) return "Jev protocol fixture";
  if (model.startsWith("jev-")) return `Jev · ${model}`;
  return `Model · ${model}`;
}

export function supportLabel(events: unknown[]): string {
  const scores = recordedOutputs(events).filter(({ operation }) =>
    operation.startsWith("decision.signal."),
  );
  if (!scores.length) return "recorded support";
  return scores.every(({ model }) => model === "demo-policy-v1")
    ? "rule support"
    : "model support";
}

export function RunProvenance({ events }: { events: unknown[] }) {
  const outputs = recordedOutputs(events);
  const roles = [
    ["Recommendation", "decision.recommendation."],
    ["Priority", "decision.priority."],
    ["Finding support", "decision.signal."],
    ["Risk estimate", "decision.risk."],
    ["Summary", "model.generate"],
  ] as const;
  return (
    <details className="provenance">
      <summary>How this analysis was produced</summary>
      <dl>
        {roles.map(([label, prefix]) => {
          const models = [
            ...new Set(
              outputs
                .filter(({ operation }) => operation.startsWith(prefix))
                .map(({ model }) => modelLabel(model)),
            ),
          ];
          return models.length ? (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{models.join(", ")}</dd>
            </div>
          ) : null;
        })}
      </dl>
      {!outputs.length && (
        <p>Provider details are unavailable in this historical trace.</p>
      )}
      <p>
        Support scores describe how a finding is supported by the returned
        evidence. They are not probabilities of fraud. Synthetic risk estimates
        come from a model trained on fictional data.
      </p>
      <p>
        These labels reflect this investigation's recorded responses. Review
        rules may constrain a proposed closure; your decision remains final.
      </p>
    </details>
  );
}
