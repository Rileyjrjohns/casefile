import { useEffect, useRef, useState } from "react";
import { api as defaultApi } from "./api";
import { EvidenceRecord } from "./EvidenceRecord";
import { RecommendationNotice } from "./RecommendationNotice";
import { RunProvenance, supportLabel } from "./RunProvenance";
import type {
  Case,
  CasefileApi,
  ConclusionInput,
  Recommendation,
  ReviewAction,
  Run,
  RunDetail,
  Source,
} from "./api";
import "./style.css";

const labels: Record<Recommendation, string> = {
  close: "Close the case",
  escalate: "Escalate for investigation",
  request_information: "Request more information",
};
const bankingScenarios: Record<
  string,
  { label: string; brief: string; documents: string }
> = {
  payment_fraud: {
    label: "PAYMENT DIVERSION",
    brief:
      "Verify a disputed change of supplier bank account. A genuine invoice and a familiar amount can coexist with a fraudulent payment instruction.",
    documents:
      "Approved purchase order · original invoice · changed payment instruction · independent supplier callback",
  },
  credit_document_fraud: {
    label: "DOCUMENTARY CONSISTENCY",
    brief:
      "Reconcile declared income, submitted payslips and observed salary credits. Investigate contradictions and the applicant's explanation without deciding loan eligibility.",
    documents:
      "Credit application · three payslips · three salary-credit extracts · employer response · applicant explanation",
  },
  sanctions_identity_review: {
    label: "IDENTITY DISAMBIGUATION",
    brief:
      "Determine whether a name-only screening hit concerns the same person. Compare birth dates, countries and independent identity records before recommending an outcome.",
    documents:
      "Customer identity record · fictional list biography · independent registry cross-check",
  },
};
const stateLabels = {
  PENDING: "Queued",
  GATHERING: "Gathering evidence",
  ANALYZING: "Analyzing",
  AWAITING_REVIEW: "Awaiting your review",
  CLOSED: "Review complete",
  FAILED: "Investigation failed",
};
const message = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";

function formatCost(value: string): string {
  const cost = Number(value);
  return `$${
    cost > 0 && cost < 0.0001
      ? cost.toLocaleString("en-US", { maximumSignificantDigits: 4 })
      : cost.toFixed(4)
  }`;
}

function caseLabel(item: Case): string {
  const seedLabels: Record<string, string> = {
    "00000000-0000-0000-0000-00000000044f": "CF–001",
    "00000000-0000-0000-0000-00000000044e": "CF–002",
    "00000000-0000-0000-0000-00000000044d": "CF–003",
    "00000000-0000-0000-0000-0000000003eb": "CF–004",
    "00000000-0000-0000-0000-0000000003ea": "CF–005",
    "00000000-0000-0000-0000-0000000003e9": "CF–006",
  };
  return seedLabels[item.id] ?? item.alert_id;
}

export function App({ api = defaultApi }: { api?: CasefileApi }) {
  const [cases, setCases] = useState<Case[]>([]);
  const [selected, setSelected] = useState<Case | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [choices, setChoices] = useState<Record<string, ConclusionInput>>({});
  const [decision, setDecision] = useState<Recommendation>("escalate");
  const [confirmed, setConfirmed] = useState(false);
  const [evidence, setEvidence] = useState<{
    source: Source;
    data: unknown;
  } | null>(null);
  const sourceDialog = useRef<HTMLDialogElement>(null);
  const sourceRequest = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    api
      .cases(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        setCases(items);
        setSelected(items[0] ?? null);
        setLoading(false);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(message(cause));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    setDetail(null);
    setRuns([]);
    setChoices({});
    setConfirmed(false);
    setError("");
    setEvidence(null);
    sourceRequest.current += 1;
    async function load() {
      try {
        const history = await api.runs(selected!.id, controller.signal);
        const target = selectedRunId
          ? history.find((run) => run.id === selectedRunId)
          : (history.find((run) => !["CLOSED", "FAILED"].includes(run.state)) ??
            history[0]);
        if (selectedRunId && !target)
          throw new Error("The selected investigation is no longer available.");
        const next = target
          ? await api.run(target.id, controller.signal)
          : null;
        if (controller.signal.aborted) return;
        setRuns(
          history.map((item) => (item.id === next?.run.id ? next.run : item)),
        );
        setDetail(next);
        setLoading(false);
        if (
          next &&
          ["PENDING", "GATHERING", "ANALYZING"].includes(next.run.state)
        )
          timer = setTimeout(load, 1000);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(message(cause));
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [api, selected, selectedRunId]);

  useEffect(() => {
    setChoices({});
    setConfirmed(false);
    if (detail?.run.analysis) setDecision(detail.run.analysis.recommendation);
  }, [detail?.run.id, detail?.revision, detail?.run.analysis]);

  useEffect(() => {
    const dialog = sourceDialog.current;
    if (evidence && dialog && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
  }, [evidence]);

  const run = detail?.run;
  const scenario = selected ? bankingScenarios[selected.kind] : undefined;
  const hasActiveRun = runs.some(
    (item) => !["CLOSED", "FAILED"].includes(item.state),
  );
  const conclusions = run?.analysis?.conclusions ?? [];
  const reviewed = conclusions.filter((item) => {
    const choice = choices[item.id];
    return (
      choice &&
      (choice.action === "validate" ||
        (choice.reason?.trim() &&
          (choice.action !== "correct" || choice.corrected_statement?.trim())))
    );
  }).length;
  const reviewable = run?.state === "AWAITING_REVIEW";

  function choose(id: string, action: ReviewAction) {
    setChoices((current) => ({
      ...current,
      [id]: { conclusion_id: id, action },
    }));
    setConfirmed(false);
  }
  function edit(
    id: string,
    field: "reason" | "corrected_statement",
    value: string,
  ) {
    setChoices((current) => {
      const choice = current[id];
      return choice
        ? { ...current, [id]: { ...choice, [field]: value } }
        : current;
    });
    setConfirmed(false);
  }
  async function submit() {
    if (!detail || !confirmed || reviewed !== conclusions.length || busy)
      return;
    setBusy(true);
    setError("");
    try {
      await api.review(detail.run.id, {
        expected_revision: detail.revision,
        confirmed,
        decision,
        conclusions: conclusions.map((item) => choices[item.id]!),
      });
      const next = await api.run(detail.run.id);
      setDetail(next);
      setRuns((items) =>
        items.map((item) => (item.id === next.run.id ? next.run : item)),
      );
      setConfirmed(false);
    } catch (cause) {
      setError(message(cause));
      setConfirmed(false);
    } finally {
      setBusy(false);
    }
  }
  async function showSource(source: Source) {
    if (!run) return;
    const request = ++sourceRequest.current;
    try {
      const data = await api.source(run.id, source);
      if (request === sourceRequest.current) setEvidence({ source, data });
    } catch (cause) {
      if (request === sourceRequest.current) setError(message(cause));
    }
  }
  async function promote(conclusionId: string) {
    if (!run || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.promote(run.id, conclusionId);
      setDetail(await api.run(run.id));
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }
  async function start() {
    if (!selected || busy || hasActiveRun) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.start(selected.id);
      setSelectedRunId(created.id);
      setSelected({ ...selected });
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workspace">
      <aside className="sidebar">
        <a className="brand" href="/">
          <span className="brand-icon">
            C<span>·</span>
          </span>
          casefile<span className="brand-dot">.</span>
        </a>
        <div className="workspace-label">
          <span className="live-dot" /> Fictional bank{" "}
          <span className="tag">DEMO</span>
        </div>
        <div className="queue-heading">
          <span>INVESTIGATION QUEUE</span>
          <span className="count">{cases.length}</span>
        </div>
        <nav aria-label="Cases">
          {cases.map((item) => (
            <button
              key={item.id}
              className={`case-button ${selected?.id === item.id ? "selected" : ""}`}
              onClick={() => {
                setSelectedRunId(null);
                setSelected(item);
              }}
              disabled={busy}
            >
              <span className="case-topline">
                <span>{caseLabel(item)}</span>
                <span>↗</span>
              </span>
              <strong>{item.description}</strong>
              <span className="case-customer">{item.subject_id}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="small-icon">◇</span>
          <div>
            Evidence first.
            <br />
            <strong>Human decisions, always.</strong>
          </div>
        </div>
        <div className="reviewer">
          <span className="avatar">DR</span>
          <div>
            Demo reviewer<small>Investigation team</small>
          </div>
          <span className="live-dot" />
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            Investigations <span className="muted">/</span>{" "}
            <strong>Case review</strong>
          </span>
          <span className="sandbox-label">FICTIONAL DATA · LOCAL DEMO</span>
        </header>
        <div className="content">
          {error && (
            <div role="alert" className="error">
              <strong>Action required</strong>
              <span>{error}</span>
              <button
                onClick={() => {
                  setError("");
                  if (selected) setSelected({ ...selected });
                }}
              >
                Reload case
              </button>
            </div>
          )}
          {loading && (
            <div role="status" className="empty">
              Loading investigation…
            </div>
          )}
          {!loading && !selected && (
            <div className="empty">
              <h1>Your queue is clear</h1>
              <p>Open a fictional case through the API to begin.</p>
            </div>
          )}
          {!loading && selected && (
            <>
              <div className="eyebrow">INVESTIGATION WORKSPACE</div>
              <div className="title-row">
                <div>
                  <h1>{selected.description}</h1>
                  <p className="subtitle">
                    {selected.subject_id} <span>·</span>{" "}
                    {new Date(selected.opened_at).toLocaleDateString("en-GB", {
                      day: "numeric",
                      month: "short",
                      year: "numeric",
                    })}
                  </p>
                </div>
                {run && (
                  <span className={`status state-${run.state.toLowerCase()}`}>
                    <span className="live-dot" />
                    {stateLabels[run.state]}
                  </span>
                )}
              </div>
              {scenario && (
                <section
                  className="panel scenario-brief"
                  aria-label="Scenario brief"
                >
                  <span className="tag">{scenario.label}</span>
                  <p>{scenario.brief}</p>
                  <p className="muted">
                    Evidence package: {scenario.documents}.
                  </p>
                </section>
              )}
              {run && (
                <section
                  className="panel run-history"
                  aria-label="Investigations for this case"
                >
                  <label>
                    Investigation history
                    <select
                      value={run.id}
                      disabled={busy}
                      onChange={(event) => setSelectedRunId(event.target.value)}
                    >
                      {runs.map((item, index) => (
                        <option key={item.id} value={item.id}>
                          Investigation {runs.length - index} ·{" "}
                          {stateLabels[item.state]}
                          {index === 0 ? " · Latest" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!hasActiveRun && (
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() => void start()}
                    >
                      {busy ? "Starting…" : "Start new investigation"}
                    </button>
                  )}
                </section>
              )}
              {!run && (
                <section className="panel empty">
                  <h2>Ready to investigate</h2>
                  <p>Gather the available evidence and prepare a review.</p>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => void start()}
                  >
                    Start investigation
                  </button>
                </section>
              )}
              {run && (
                <>
                  <div className="metrics">
                    <div>
                      <span>EVIDENCE CALLS</span>
                      <strong>
                        {run.usage.tool_calls}
                        <small> / {run.limits?.tool_calls ?? 5} tools</small>
                      </strong>
                    </div>
                    <div>
                      <span>FINDINGS TO REVIEW</span>
                      <strong>
                        {conclusions.length.toString().padStart(2, "0")}
                      </strong>
                    </div>
                    <div>
                      <span>ESTIMATED COST</span>
                      <strong title={`$${run.usage.estimated_cost} USD`}>
                        {formatCost(run.usage.estimated_cost)}
                        <small> USD</small>
                      </strong>
                    </div>
                  </div>
                  {run.failure_reason && (
                    <div role="alert" className="error">
                      {run.failure_reason}
                    </div>
                  )}
                  {!run.analysis && run.state !== "FAILED" && (
                    <section role="status" className="panel empty">
                      <span className="spinner" />
                      <h2>{stateLabels[run.state]}</h2>
                      <p>The investigation will stop for your review.</p>
                    </section>
                  )}
                  {run.analysis && (
                    <>
                      <section className="panel overview">
                        <div className="section-heading">
                          <h2>
                            <span className="small-icon">≋</span> Investigation
                            summary
                          </h2>
                          <span className="tag">EVIDENCE-BASED</span>
                        </div>
                        <p>{run.analysis.summary}</p>
                        <div className="recommendation">
                          <span className="small-icon">↗</span>
                          <div>
                            <small>PROPOSED RECOMMENDATION</small>
                            <strong>
                              {labels[run.analysis.recommendation]}
                            </strong>
                          </div>
                          <span className="muted">Subject to your review</span>
                        </div>
                        <RecommendationNotice
                          events={detail?.events ?? []}
                          recommendation={run.analysis.recommendation}
                        />
                        <RunProvenance events={detail?.events ?? []} />
                      </section>
                      <div className="section-heading conclusions-heading">
                        <h2>
                          Conclusions{" "}
                          <span className="count">{conclusions.length}</span>
                        </h2>
                        {reviewable && (
                          <span className="muted">
                            {reviewed} of {conclusions.length} reviewed
                          </span>
                        )}
                      </div>
                      {conclusions.length === 0 && (
                        <div className="panel empty">
                          No signals found in the available evidence. Review the
                          summary before deciding.
                        </div>
                      )}
                      {conclusions.map((item, index) => (
                        <section className="panel conclusion" key={item.id}>
                          <div className="conclusion-head">
                            <span className="signal-number">
                              {String(index + 1).padStart(2, "0")}
                            </span>
                            <p>{item.statement}</p>
                            <span className="confidence">
                              {Math.round(item.confidence.value * 100)}%{" "}
                              <small>
                                {item.sources.some((source) =>
                                  source.tool.startsWith("risk_model@"),
                                )
                                  ? "synthetic risk"
                                  : supportLabel(detail?.events ?? [])}
                              </small>
                            </span>
                          </div>
                          <div className="sources">
                            <span>SOURCES</span>
                            {item.sources.map((source) => (
                              <button
                                key={`${source.tool}:${source.record_id}`}
                                onClick={() => void showSource(source)}
                              >
                                ↗ {source.record_id}{" "}
                                <small>
                                  {source.tool.replaceAll("_", " ")}
                                </small>
                              </button>
                            ))}
                          </div>
                          {detail?.review?.conclusions
                            .filter(
                              (review) => review.conclusion_id === item.id,
                            )
                            .map((review) => (
                              <div
                                className="conclusion-review"
                                key={review.conclusion_id}
                              >
                                <strong>
                                  Human review:{" "}
                                  {review.action === "correct"
                                    ? "corrected"
                                    : review.action === "reject"
                                      ? "rejected"
                                      : "validated"}
                                </strong>
                                {review.corrected_statement && (
                                  <p>{review.corrected_statement}</p>
                                )}
                                {review.reason && (
                                  <p className="muted">{review.reason}</p>
                                )}
                                {run.state === "CLOSED" &&
                                  review.action === "correct" && (
                                    <div className="regression-candidate">
                                      {detail.references?.[item.id] ? (
                                        <a
                                          href={`/api/references/${encodeURIComponent(detail.references[item.id]!)}`}
                                        >
                                          Download candidate
                                        </a>
                                      ) : (
                                        <button
                                          disabled={busy}
                                          onClick={() => void promote(item.id)}
                                        >
                                          Save regression candidate
                                        </button>
                                      )}
                                      <p className="muted">
                                        Export this correction and its evidence
                                        for curation into a regression test. The
                                        reference suite stays unchanged.
                                      </p>
                                    </div>
                                  )}
                              </div>
                            ))}
                          {reviewable && (
                            <div className="conclusion-review">
                              <div className="review-actions">
                                {(
                                  ["validate", "correct", "reject"] as const
                                ).map((action) => (
                                  <button
                                    key={action}
                                    aria-label={`${action[0]!.toUpperCase()}${action.slice(1)} conclusion ${index + 1}`}
                                    aria-pressed={
                                      choices[item.id]?.action === action
                                    }
                                    onClick={() => choose(item.id, action)}
                                    disabled={busy}
                                  >
                                    <span>
                                      {action === "validate"
                                        ? "✓"
                                        : action === "correct"
                                          ? "✎"
                                          : "×"}
                                    </span>{" "}
                                    {action}
                                  </button>
                                ))}
                              </div>
                              {choices[item.id]?.action === "correct" && (
                                <label>
                                  Corrected statement {index + 1}
                                  <textarea
                                    aria-label={`Corrected statement ${index + 1}`}
                                    maxLength={4000}
                                    value={
                                      choices[item.id]?.corrected_statement ??
                                      ""
                                    }
                                    onChange={(event) =>
                                      edit(
                                        item.id,
                                        "corrected_statement",
                                        event.target.value,
                                      )
                                    }
                                  />
                                </label>
                              )}
                              {choices[item.id] &&
                                choices[item.id]?.action !== "validate" && (
                                  <label>
                                    Reason {index + 1}
                                    <textarea
                                      aria-label={`Reason ${index + 1}`}
                                      maxLength={2000}
                                      value={choices[item.id]?.reason ?? ""}
                                      onChange={(event) =>
                                        edit(
                                          item.id,
                                          "reason",
                                          event.target.value,
                                        )
                                      }
                                    />
                                  </label>
                                )}
                            </div>
                          )}
                        </section>
                      ))}
                      {reviewable && (
                        <section className="panel final-review">
                          <div className="section-heading">
                            <h2>Your decision</h2>
                            <span className="tag">HUMAN REVIEW</span>
                          </div>
                          <p className="muted">
                            Review every conclusion, then confirm the final
                            outcome.
                          </p>
                          <label>
                            Final recommendation
                            <select
                              value={decision}
                              onChange={(event) => {
                                setDecision(
                                  event.target.value as Recommendation,
                                );
                                setConfirmed(false);
                              }}
                            >
                              {Object.entries(labels).map(([value, label]) => (
                                <option value={value} key={value}>
                                  {label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label className="confirmation">
                            <input
                              type="checkbox"
                              checked={confirmed}
                              onChange={(event) =>
                                setConfirmed(event.target.checked)
                              }
                              disabled={busy}
                            />
                            <span>
                              I personally reviewed the conclusions and confirm
                              this decision.
                            </span>
                          </label>
                          <div className="submit-row">
                            <span className="muted">
                              Your review is recorded with this case.
                            </span>
                            <button
                              className="primary"
                              disabled={
                                busy ||
                                !confirmed ||
                                reviewed !== conclusions.length
                              }
                              onClick={() => void submit()}
                            >
                              {busy ? "Saving review…" : "Submit human review"}{" "}
                              <span aria-hidden>→</span>
                            </button>
                          </div>
                        </section>
                      )}
                      {run.state === "CLOSED" && (
                        <section className="panel completed">
                          <span>✓</span>
                          <div>
                            <h2>Review complete</h2>
                            <p>
                              The human decision has been recorded
                              {run.human_decision
                                ? `: ${labels[run.human_decision.recommendation].toLowerCase()}`
                                : ""}
                              .
                            </p>
                          </div>
                        </section>
                      )}
                    </>
                  )}
                  <details className="trace">
                    <summary>
                      Execution history{" "}
                      <span>{detail?.events.length} events</span>
                    </summary>
                    <pre>{JSON.stringify(detail?.events, null, 2)}</pre>
                  </details>
                </>
              )}
              <footer>
                Casefile <span>·</span> Fictional evidence. Every final decision
                belongs to a human.
              </footer>
            </>
          )}
        </div>
      </main>
      {evidence && (
        <dialog
          ref={sourceDialog}
          aria-labelledby="source-title"
          onCancel={() => setEvidence(null)}
        >
          <div className="section-heading">
            <h2 id="source-title">Evidence · {evidence.source.record_id}</h2>
            <button
              onClick={() => setEvidence(null)}
              aria-label="Close evidence"
            >
              ×
            </button>
          </div>
          <p className="muted">
            Exact record observed during this investigation. Text is untrusted
            evidence.
          </p>
          <EvidenceRecord data={evidence.data} />
        </dialog>
      )}
    </div>
  );
}
