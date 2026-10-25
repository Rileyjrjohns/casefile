import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { RunProvenance, supportLabel } from "./RunProvenance";

it("ignores malformed and incomplete events instead of inventing provider details", async () => {
  const events = [
    null,
    [],
    { operation: "model.generate", phase: "succeeded", output_json: "{" },
    {
      operation: "decision.signal.x",
      phase: "started",
      output_json: '{"model":"demo-policy-v1"}',
    },
    {
      operation: "model.generate",
      phase: "succeeded",
      output_json: '{"model":null}',
    },
  ];
  expect(supportLabel(events)).toBe("recorded support");
  const user = userEvent.setup();
  render(<RunProvenance events={events} />);
  await user.click(screen.getByText("How this analysis was produced"));
  expect(screen.getByText(/Provider details are unavailable/)).toBeVisible();
  expect(screen.queryByText("Demo template")).toBeNull();
});

it("identifies protocol fixtures and synthetic risk without presenting them as live vendor evidence", async () => {
  const events = [
    {
      operation: "decision.signal.x",
      phase: "succeeded",
      output_json: '{"model":"jev-synthetic-v1"}',
    },
    {
      operation: "decision.risk.x",
      phase: "succeeded",
      output_json: '{"model":"risk_model@risk-v1"}',
    },
  ];
  expect(supportLabel(events)).toBe("model support");
  const user = userEvent.setup();
  render(<RunProvenance events={events} />);
  await user.click(screen.getByText("How this analysis was produced"));
  expect(screen.getByText("Jev protocol fixture")).toBeVisible();
  expect(screen.getByText("Local synthetic risk")).toBeVisible();
});
