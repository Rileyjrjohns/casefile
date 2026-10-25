import type { Recommendation } from "./api";

const reasons: Record<string, string> = {
  unresolved_concerns: "Unresolved concerns remain.",
  missing_kyc: "The KYC profile is missing.",
  evidence_gaps: "Required evidence is incomplete.",
};

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function RecommendationNotice({
  events,
  recommendation,
}: {
  events: unknown[];
  recommendation: Recommendation;
}) {
  if (recommendation === "close") return null;
  for (const event of [...events].reverse()) {
    if (
      !record(event) ||
      event.operation !== "policy.recommendation.v1" ||
      event.phase !== "transition" ||
      typeof event.input_json !== "string" ||
      typeof event.output_json !== "string"
    )
      continue;
    try {
      const input: unknown = JSON.parse(event.input_json);
      const output: unknown = JSON.parse(event.output_json);
      if (
        !record(input) ||
        input.proposed !== "close" ||
        !record(output) ||
        output.recommendation !== recommendation ||
        !Array.isArray(output.reasons) ||
        !output.reasons.length ||
        !output.reasons.every(
          (reason: unknown) =>
            typeof reason === "string" && Object.hasOwn(reasons, reason),
        )
      )
        continue;
      const messages = output.reasons.map((reason: string) => reasons[reason]);
      return (
        <aside
          className="recommendation-notice"
          aria-label="Recommendation adjustment"
        >
          <strong>Closure blocked by review rules</strong>
          <p>
            The model proposed closing this case. {messages.join(" ")} The
            investigator retains the final decision.
          </p>
        </aside>
      );
    } catch {
      // Historical or malformed events cannot introduce a reviewer instruction.
    }
  }
  return null;
}
