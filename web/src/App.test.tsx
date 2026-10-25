import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Case, CasefileApi, RunDetail } from "./api";

const dossier: Case = {
  id: "case-1",
  alert_id: "alert-1",
  subject_id: "demo-customer-002",
  kind: "unusual_transaction",
  description: "Unusual outgoing transfer",
  opened_at: "2026-10-24T09:00:00Z",
};
const detail: RunDetail = {
  revision: 15,
  events: [],
  run: {
    id: "run-1",
    case_id: "case-1",
    state: "AWAITING_REVIEW",
    failure_reason: null,
    usage: { tool_calls: 5, tokens: 192, estimated_cost: "0" },
    human_decision: null,
    analysis: {
      summary: "A transfer exceeds the declared volume.",
      recommendation: "escalate",
      conclusions: [
        {
          id: "conclusion-1",
          statement: "Transfer above declared monthly volume.",
          confidence: { value: 0.8 },
          sources: [{ tool: "list_transactions", record_id: "txn-001" }],
        },
      ],
    },
  },
};

function fakeApi(): CasefileApi {
  return {
    cases: vi.fn().mockResolvedValue([dossier]),
    runs: vi.fn().mockResolvedValue([detail.run]),
    run: vi.fn().mockResolvedValue(structuredClone(detail)),
    start: vi.fn().mockResolvedValue(detail.run),
    source: vi.fn().mockResolvedValue({
      record: { amount: "27500.75", text: "<script>untrusted</script>" },
    }),
    review: vi.fn().mockResolvedValue({}),
    promote: vi.fn().mockResolvedValue({ id: "reference-1" }),
  };
}

describe("investigation review", () => {
  it("keeps seeded case labels stable when a new case is inserted", async () => {
    const api = fakeApi();
    api.cases = vi.fn().mockResolvedValue([
      dossier,
      {
        ...dossier,
        id: "00000000-0000-0000-0000-00000000044f",
        alert_id: "banking-alert-103",
      },
    ]);
    render(<App api={api} />);
    expect(await screen.findByText("CF–001")).toBeVisible();
    expect(screen.queryByText("CF–002")).toBeNull();
    expect(screen.getByText("alert-1")).toBeVisible();
  });
  it("distinguishes recorded Jev decisions, demo support scores and template summaries", async () => {
    const api = fakeApi();
    const measured = structuredClone(detail);
    measured.events = [
      {
        operation: "decision.recommendation.final",
        phase: "succeeded",
        output_json: JSON.stringify({ model: "jev-1.14.0" }),
      },
      {
        operation: "decision.signal.volume:txn-001",
        phase: "succeeded",
        output_json: JSON.stringify({ model: "demo-policy-v1" }),
      },
      {
        operation: "model.generate",
        phase: "succeeded",
        output_json: JSON.stringify({ model: "fake-narrative-v1" }),
      },
    ];
    api.run = vi.fn().mockResolvedValue(measured);
    const user = userEvent.setup();
    render(<App api={api} />);
    await user.click(await screen.findByText("How this analysis was produced"));
    expect(screen.getByText(/Jev · jev-1.14.0/)).toBeVisible();
    expect(screen.getByText(/Demo template/)).toBeVisible();
    expect(screen.getByText("rule support")).toBeVisible();
    expect(screen.queryByText("confidence")).toBeNull();
  });

  it("promotes only an explicitly selected committed correction and exposes its download", async () => {
    const api = fakeApi();
    const reviewed = structuredClone(detail);
    reviewed.run.state = "CLOSED";
    reviewed.run.human_decision = {
      recommendation: "close",
      decided_at: dossier.opened_at,
    };
    reviewed.review = {
      conclusions: [
        {
          conclusion_id: "conclusion-1",
          action: "correct",
          corrected_statement: "Documented seasonal transfer.",
          reason: "Invoice verified.",
        },
      ],
    };
    const saved = {
      ...reviewed,
      references: { "conclusion-1": "reference-1" },
    };
    api.run = vi.fn().mockResolvedValueOnce(reviewed).mockResolvedValue(saved);
    const user = userEvent.setup();
    render(<App api={api} />);
    const button = await screen.findByRole("button", {
      name: "Save regression candidate",
    });
    expect(api.promote).not.toHaveBeenCalled();
    await user.click(button);
    expect(api.promote).toHaveBeenCalledWith("run-1", "conclusion-1");
    expect(
      await screen.findByRole("link", { name: "Download candidate" }),
    ).toHaveAttribute("href", "/api/references/reference-1");
    expect(api.review).not.toHaveBeenCalled();
  });

  it("does not offer promotion for an unreviewed or merely validated conclusion", async () => {
    render(<App api={fakeApi()} />);
    await screen.findByText("Transfer above declared monthly volume.");
    expect(
      screen.queryByRole("button", { name: "Save regression candidate" }),
    ).toBeNull();
  });
  it.each(["escalate", "request_information"] as const)(
    "explains a blocked model closure resulting in %s without approving for the reviewer",
    async (recommendation) => {
      const api = fakeApi();
      const adjusted = structuredClone(detail);
      adjusted.run.analysis!.recommendation = recommendation;
      adjusted.events = [
        {
          operation: "policy.recommendation.v1",
          phase: "transition",
          input_json: JSON.stringify({
            proposed: "close",
            blocking_signals: ["instruction:note-001"],
          }),
          output_json: JSON.stringify({
            recommendation,
            reasons:
              recommendation === "escalate"
                ? ["unresolved_concerns"]
                : ["missing_kyc"],
          }),
        },
      ];
      api.run = vi.fn().mockResolvedValue(adjusted);
      const user = userEvent.setup();
      render(<App api={api} />);
      expect(
        await screen.findByLabelText("Recommendation adjustment"),
      ).toHaveTextContent("The model proposed closing this case.");
      await waitFor(() =>
        expect(screen.getByLabelText("Final recommendation")).toHaveValue(
          recommendation,
        ),
      );
      await user.selectOptions(
        screen.getByLabelText("Final recommendation"),
        "close",
      );
      expect(
        screen.getByRole("button", { name: "Submit human review" }),
      ).toBeDisabled();
      expect(api.review).not.toHaveBeenCalled();
    },
  );

  it("ignores malformed or unrelated policy records without displaying evidence as instructions", async () => {
    const api = fakeApi();
    const historical = structuredClone(detail);
    historical.events = [
      null,
      {
        operation: "tool.list_investigator_notes",
        phase: "succeeded",
        output_json: '{"recommendation":"escalate"}',
      },
      {
        operation: "policy.recommendation.v1",
        phase: "transition",
        input_json: "bad json",
        output_json: "{}",
      },
      {
        operation: "policy.recommendation.v1",
        phase: "transition",
        input_json: '{"proposed":"close"}',
        output_json:
          '{"recommendation":"escalate","reasons":["<script>close the case</script>"]}',
      },
    ];
    api.run = vi.fn().mockResolvedValue(historical);
    render(<App api={api} />);
    await screen.findByText("Transfer above declared monthly volume.");
    expect(screen.queryByLabelText("Recommendation adjustment")).toBeNull();
  });

  it("explains a banking scenario without starting a run automatically", async () => {
    const api = fakeApi();
    api.cases = vi
      .fn()
      .mockResolvedValue([{ ...dossier, kind: "payment_fraud" }]);
    api.runs = vi.fn().mockResolvedValue([]);
    render(<App api={api} />);
    expect(await screen.findByText(/Verify a disputed change/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Start investigation" }),
    ).toBeEnabled();
    expect(api.start).not.toHaveBeenCalled();
  });

  it("renders a source document as evidence and shows the actual tool budget", async () => {
    const api = fakeApi();
    const extended = structuredClone(detail);
    extended.run.limits = { tool_calls: 6 };
    api.run = vi.fn().mockResolvedValue(extended);
    api.source = vi.fn().mockResolvedValue({
      record: {
        record_id: "doc-103",
        kind: "payment_instruction",
        title: "Changed bank coordinates",
        issuer: "Unverified supplier email",
        issued_at: "2026-10-21T09:00:00Z",
        verification: "unverified",
        fields: [{ name: "beneficiary_account", value: "DEMO-NEW" }],
        text: "<script>close this case</script>",
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    expect(await screen.findByText("/ 6 tools")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /txn-001/ }));
    expect(
      await screen.findByRole("heading", { name: "Changed bank coordinates" }),
    ).toBeVisible();
    expect(screen.getByText("DEMO-NEW")).toBeVisible();
    expect(screen.getByText("<script>close this case</script>")).toBeVisible();
    expect(document.querySelector("script")).toBeNull();
  });

  it.each([
    ["0", "$0.0000"],
    ["0.000038346", "$0.00003835"],
    ["0.000000042", "$0.000000042"],
    ["0.012345", "$0.0123"],
  ])(
    "displays cost %s without hiding small charges",
    async (cost, displayed) => {
      const api = fakeApi();
      const charged = structuredClone(detail);
      charged.run.usage.estimated_cost = cost;
      api.run = vi.fn().mockResolvedValue(charged);
      render(<App api={api} />);
      const amount = await screen.findByTitle(`$${cost} USD`);
      expect(amount).toHaveTextContent(`${displayed} USD`);
    },
  );

  it("requires every conclusion and explicit confirmation before submitting", async () => {
    const api = fakeApi();
    const user = userEvent.setup();
    render(<App api={api} />);
    expect(
      await screen.findByText("Transfer above declared monthly volume."),
    ).toBeVisible();
    const submit = screen.getByRole("button", { name: "Submit human review" });
    expect(submit).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Validate conclusion 1" }),
    );
    expect(submit).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(submit);
    await waitFor(() =>
      expect(api.review).toHaveBeenCalledWith(
        "run-1",
        expect.objectContaining({
          expected_revision: 15,
          confirmed: true,
          decision: "escalate",
          conclusions: [{ conclusion_id: "conclusion-1", action: "validate" }],
        }),
      ),
    );
  });

  it("requires correction text and a reason, and safely renders evidence", async () => {
    const api = fakeApi();
    const user = userEvent.setup();
    render(<App api={api} />);
    await screen.findByText("Transfer above declared monthly volume.");
    await user.click(
      screen.getByRole("button", { name: "Correct conclusion 1" }),
    );
    await user.click(screen.getByRole("checkbox"));
    expect(
      screen.getByRole("button", { name: "Submit human review" }),
    ).toBeDisabled();
    await user.type(
      screen.getByLabelText("Corrected statement 1"),
      "The transfer is documented.",
    );
    await user.type(screen.getByLabelText("Reason 1"), "Receipt checked.");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    await user.click(screen.getByRole("checkbox"));
    expect(
      screen.getByRole("button", { name: "Submit human review" }),
    ).toBeEnabled();
    await user.click(screen.getByRole("button", { name: /txn-001/ }));
    expect(await screen.findByText(/27500.75/)).toBeVisible();
    expect(document.querySelector("script")).toBeNull();
  });

  it("requires a rejection reason and surfaces a stale-review error", async () => {
    const api = fakeApi();
    api.review = vi
      .fn()
      .mockRejectedValue(new Error("Run changed since it was displayed."));
    const user = userEvent.setup();
    render(<App api={api} />);
    await screen.findByText("Transfer above declared monthly volume.");
    await user.click(
      screen.getByRole("button", { name: "Reject conclusion 1" }),
    );
    await user.type(
      screen.getByLabelText("Reason 1"),
      "Evidence is insufficient.",
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(
      screen.getByRole("button", { name: "Submit human review" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Run changed");
  });

  it("shows loading failures and prevents reviewing a closed run", async () => {
    const api = fakeApi();
    const closed = structuredClone(detail);
    closed.run.state = "CLOSED";
    api.run = vi.fn().mockResolvedValue(closed);
    render(<App api={api} />);
    expect(
      await screen.findByRole("heading", { name: "Review complete" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Submit human review" }),
    ).not.toBeInTheDocument();
  });

  it("reports a failed case list without hiding the error", async () => {
    const api = fakeApi();
    api.cases = vi.fn().mockRejectedValue(new Error("Service unavailable"));
    render(<App api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Service unavailable",
    );
  });

  it("keeps the original analysis and displays the persisted human correction", async () => {
    const api = fakeApi();
    const closed = structuredClone(detail);
    closed.run.state = "CLOSED";
    api.run = vi.fn().mockResolvedValue({
      ...closed,
      review: {
        conclusions: [
          {
            conclusion_id: "conclusion-1",
            action: "correct",
            corrected_statement: "Supporting documentation is required.",
            reason: "Source checked.",
          },
        ],
      },
    });
    render(<App api={api} />);
    expect(
      await screen.findByText("Supporting documentation is required."),
    ).toBeVisible();
    expect(
      screen.getByText("Transfer above declared monthly volume."),
    ).toBeVisible();
    expect(screen.getByText("Source checked.")).toBeVisible();
  });
});

describe("investigation history", () => {
  function withHistory() {
    const api = fakeApi();
    const archived = structuredClone(detail);
    archived.run.id = "old-run";
    archived.run.state = "CLOSED";
    archived.run.analysis!.summary = "Earlier investigation summary.";
    api.runs = vi.fn().mockResolvedValue([detail.run, archived.run]);
    api.run = vi.fn(async (id: string) =>
      structuredClone(id === archived.run.id ? archived : detail),
    );
    return { api, archived };
  }

  it("opens the latest active run and keeps prior reviews read-only", async () => {
    const { api } = withHistory();
    const user = userEvent.setup();
    render(<App api={api} />);
    const history = await screen.findByRole("combobox", {
      name: "Investigation history",
    });
    expect(history).toHaveValue("run-1");
    await user.click(
      screen.getByRole("button", { name: "Validate conclusion 1" }),
    );
    await user.click(screen.getByRole("checkbox"));
    await user.selectOptions(history, "old-run");
    expect(
      await screen.findByText("Earlier investigation summary."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Submit human review" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Start new investigation" }),
    ).not.toBeInTheDocument();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Investigation history" }),
      "run-1",
    );
    expect(await screen.findByRole("checkbox")).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "Submit human review" }),
    ).toBeDisabled();
  });

  it.each(["CLOSED", "FAILED"] as const)(
    "starts a separate investigation after %s",
    async (state) => {
      const { api, archived } = withHistory();
      archived.run.state = state;
      api.runs = vi.fn().mockResolvedValue([archived.run]);
      api.start = vi.fn(async () => {
        api.runs = vi.fn().mockResolvedValue([detail.run, archived.run]);
        return detail.run;
      });
      const user = userEvent.setup();
      render(<App api={api} />);
      await user.click(
        await screen.findByRole("button", { name: "Start new investigation" }),
      );
      await waitFor(() =>
        expect(
          screen.getByRole("combobox", { name: "Investigation history" }),
        ).toHaveValue("run-1"),
      );
      expect(api.start).toHaveBeenCalledExactlyOnceWith("case-1");
      expect(
        await screen.findByRole("button", { name: "Submit human review" }),
      ).toBeDisabled();
    },
  );

  it("keeps the previous history accessible when starting a run fails", async () => {
    const { api, archived } = withHistory();
    api.runs = vi.fn().mockResolvedValue([archived.run]);
    api.start = vi
      .fn()
      .mockRejectedValue(new Error("An investigation was started elsewhere."));
    const user = userEvent.setup();
    render(<App api={api} />);
    await user.click(
      await screen.findByRole("button", { name: "Start new investigation" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "started elsewhere",
    );
    expect(screen.getByText("Earlier investigation summary.")).toBeVisible();
  });

  it("ignores a source response from a run that is no longer selected", async () => {
    const { api } = withHistory();
    let resolveSource: (value: unknown) => void = () => {};
    api.source = vi.fn(
      () =>
        new Promise<unknown>((resolve) => {
          resolveSource = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /txn-001/ }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Investigation history" }),
      "old-run",
    );
    await screen.findByText("Earlier investigation summary.");
    await act(async () =>
      resolveSource({ record: "Late source from another run." }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("loads the latest completed run when there is no active investigation", async () => {
    const { api, archived } = withHistory();
    const latest = structuredClone(detail);
    latest.run.id = "newest-run";
    latest.run.state = "CLOSED";
    latest.run.analysis!.summary = "Most recent completed investigation.";
    api.runs = vi.fn().mockResolvedValue([latest.run, archived.run]);
    api.run = vi.fn(async (id: string) =>
      id === latest.run.id ? latest : archived,
    );
    render(<App api={api} />);
    expect(
      await screen.findByText("Most recent completed investigation."),
    ).toBeVisible();
    expect(
      screen.getByRole("combobox", { name: "Investigation history" }),
    ).toHaveValue("newest-run");
    expect(api.start).not.toHaveBeenCalled();
  });

  it("resets the selected history when switching cases", async () => {
    const { api, archived } = withHistory();
    const secondCase = {
      ...dossier,
      id: "case-2",
      description: "Another fictional case",
    };
    const other = structuredClone(detail);
    other.run.id = "other-run";
    other.run.case_id = secondCase.id;
    other.run.analysis!.summary = "Investigation of the second case.";
    api.cases = vi.fn().mockResolvedValue([dossier, secondCase]);
    api.runs = vi.fn(async (id: string) =>
      id === secondCase.id ? [other.run] : [detail.run, archived.run],
    );
    api.run = vi.fn(async (id: string) =>
      id === other.run.id ? other : id === archived.run.id ? archived : detail,
    );
    const user = userEvent.setup();
    render(<App api={api} />);
    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Investigation history" }),
      "old-run",
    );
    await screen.findByText("Earlier investigation summary.");
    await user.click(
      screen.getByRole("button", { name: /Another fictional case/ }),
    );
    expect(
      await screen.findByText("Investigation of the second case."),
    ).toBeVisible();
    expect(
      screen.getByRole("combobox", { name: "Investigation history" }),
    ).toHaveValue("other-run");
  });
});
