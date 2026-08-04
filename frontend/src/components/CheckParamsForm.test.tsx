// Schema-driven check params editor (#293): the form an analyst uses to author
// or edit every check. It is fully controlled, so these tests wrap it in the
// same harness its real parents use (ChecksTable / ChecksTab / ContractTab):
// params state + live `validateParams` errors.
//
// Covered: the right fields render per check type, invalid input surfaces an
// inline (screen-reader announced) error, the "Effective params" preview tracks
// what will actually be submitted, and the Advanced raw-JSON escape hatch
// round-trips both ways without silently dropping the analyst's edits.

import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import CheckParamsForm, { paramSpecsFor, validateParams } from "./CheckParamsForm";
import type { ParamSpec, ParamValues } from "./CheckParamsForm";

const spec = (name: string, type: string, over: Partial<ParamSpec> = {}): ParamSpec => ({
  name,
  type,
  required: false,
  default: null,
  description: "",
  ...over,
});

// Mirrors of the real registry entries (backend app/core/check_types.py).
const ACCEPTED_VALUES: ParamSpec[] = [
  spec("values", "list", { required: true, default: [], description: "Allowed values" }),
  spec("case_sensitive", "boolean", { default: true, description: "Compare case-sensitively" }),
];
const RANGE: ParamSpec[] = [
  spec("min", "number", { description: "Lower bound" }),
  spec("max", "number", { description: "Upper bound" }),
];
const CUSTOM_SQL: ParamSpec[] = [
  spec("sql", "sql", { required: true, description: "Violation query" }),
];

/** The component is controlled; this is the state its real parents own. */
function Harness({ specs, initial = {} }: { specs: ParamSpec[]; initial?: ParamValues }) {
  const [params, setParams] = useState<ParamValues>(initial);
  return (
    <CheckParamsForm
      specs={specs}
      params={params}
      onChange={setParams}
      errors={validateParams(specs, params)}
    />
  );
}

function preview(): string {
  const pre = document.querySelector(".check-params-preview pre");
  return pre?.textContent ?? "";
}

function openAdvanced() {
  fireEvent.click(screen.getByRole("button", { name: /advanced \(raw json\)/i }));
}

const rawJson = () => screen.getByLabelText("Raw params JSON") as HTMLTextAreaElement;

// ---------------------------------------------------------------------------
// fields per check type
// ---------------------------------------------------------------------------

describe("fields rendered per check type", () => {
  it("renders a list textarea + boolean checkbox for accepted_values", () => {
    render(<Harness specs={ACCEPTED_VALUES} />);
    expect(screen.getByLabelText(/^values/)).toHaveProperty("tagName", "TEXTAREA");
    expect(screen.getByLabelText(/^case_sensitive/)).toHaveProperty("type", "checkbox");
    expect(screen.queryByLabelText(/^min/)).not.toBeInTheDocument(); // no other type's fields
  });

  it("renders numeric bounds for range", () => {
    render(<Harness specs={RANGE} />);
    expect(screen.getByLabelText(/^min/)).toHaveProperty("type", "number");
    expect(screen.getByLabelText(/^max/)).toHaveProperty("type", "number");
    expect(screen.queryByLabelText(/^values/)).not.toBeInTheDocument();
  });

  it("renders a monospace textarea for a sql param", () => {
    render(<Harness specs={CUSTOM_SQL} />);
    const sql = screen.getByLabelText(/^sql/);
    expect(sql).toHaveProperty("tagName", "TEXTAREA");
    expect(sql).toHaveStyle({ fontFamily: "var(--mono)" });
  });

  it("renders a select when the schema enumerates options", () => {
    const withOptions = [
      { ...spec("strategy", "string"), options: ["static", "adaptive"] } as ParamSpec,
    ];
    render(<Harness specs={withOptions} />);
    const select = screen.getByLabelText(/^strategy/) as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    // optional -> an explicit "none" escape, so a value can be unset again
    expect([...select.options].map((o) => o.value)).toEqual(["", "static", "adaptive"]);
  });

  it("always offers the universal tolerance field, and never twice", () => {
    render(<Harness specs={ACCEPTED_VALUES} />);
    expect(screen.getByLabelText(/^tolerance/)).toBeInTheDocument();

    expect(paramSpecsFor(ACCEPTED_VALUES).map((p) => p.name)).toEqual([
      "values",
      "case_sensitive",
      "tolerance",
    ]);
    const already = [spec("tolerance", "number")];
    expect(paramSpecsFor(already).map((p) => p.name)).toEqual(["tolerance"]);
    expect(paramSpecsFor(undefined).map((p) => p.name)).toEqual(["tolerance"]);
  });

  it("marks required params and defaults a boolean to its schema default", () => {
    render(<Harness specs={ACCEPTED_VALUES} />);
    expect(screen.getByLabelText(/^values \*/)).toBeInTheDocument();
    // case_sensitive defaults to true server-side: the control must show the
    // effective behaviour, not an unchecked box that lies about it.
    expect(screen.getByLabelText(/^case_sensitive/)).toBeChecked();
  });
});

// ---------------------------------------------------------------------------
// accessible names (#258 / #260)
// ---------------------------------------------------------------------------

describe("accessible names", () => {
  // The <label> wraps the hint and the error as well as the control, so relying
  // on the implicit association folded both into the accessible name — "min"
  // was announced as "min Lower bound", and once the value went bad it became
  // "min Lower bound Must be a number or date". Each control must be named by
  // its param and nothing else.
  it("names each control by its param alone, not by its hint", () => {
    render(<Harness specs={RANGE} />);
    expect(screen.getByRole("spinbutton", { name: "min" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "max" })).toBeInTheDocument();
    // the hint is still reachable — as a description, which is what it is
    expect(screen.getByRole("spinbutton", { name: "min" })).toHaveAccessibleDescription("Lower bound");
  });

  it("keeps the name stable when the value goes invalid", () => {
    render(<Harness specs={RANGE} initial={{ min: "yesterday" }} />);
    const min = screen.getByRole("textbox", { name: "min" }); // falls back to text
    expect(min).toHaveAttribute("aria-invalid", "true");
    expect(min).toHaveAccessibleDescription(/Must be a number or date/);
  });

  it("names every control shape — textarea, checkbox, select", () => {
    render(<Harness specs={ACCEPTED_VALUES} />);
    expect(screen.getByRole("textbox", { name: "values *" })).toHaveProperty("tagName", "TEXTAREA");
    expect(screen.getByRole("checkbox", { name: "case_sensitive" })).toBeChecked();
    expect(screen.getByRole("spinbutton", { name: "tolerance" })).toBeInTheDocument();

    render(<Harness specs={CUSTOM_SQL} />);
    expect(screen.getByRole("textbox", { name: "sql *" })).toBeInTheDocument();

    const withOptions = [
      { ...spec("strategy", "string"), options: ["static", "adaptive"] } as ParamSpec,
    ];
    render(<Harness specs={withOptions} />);
    expect(screen.getByRole("combobox", { name: "strategy" })).toBeInTheDocument();
  });

  it("exposes required-ness programmatically, not just with a red asterisk", () => {
    render(<Harness specs={ACCEPTED_VALUES} />);
    expect(screen.getByRole("textbox", { name: "values *" })).toHaveAttribute("aria-required", "true");
    // optional params must not claim to be required
    expect(screen.getByRole("checkbox", { name: "case_sensitive" })).not.toHaveAttribute("aria-required");
    expect(screen.getByRole("spinbutton", { name: "tolerance" })).not.toHaveAttribute("aria-required");
  });

  it("announces the fields as one named group, so a bare `min` has context", () => {
    render(<Harness specs={RANGE} />);
    const group = screen.getByRole("group", { name: "Check parameters" });
    expect(group).toContainElement(screen.getByRole("spinbutton", { name: "min" }));
  });

  it("gives every instance its own ids when the form renders twice on a page", () => {
    // The edit modal and the contract tab can both be mounted; duplicate ids
    // would make a label point at the wrong instance's input.
    render(
      <>
        <Harness specs={RANGE} />
        <Harness specs={RANGE} />
      </>,
    );
    const mins = screen.getAllByRole("spinbutton", { name: "min" });
    expect(mins).toHaveLength(2);
    expect(mins[0].id).not.toBe(mins[1].id);
    expect(mins[0].getAttribute("aria-labelledby")).not.toBe(mins[1].getAttribute("aria-labelledby"));

    // and typing in the second one only moves the second one
    fireEvent.change(mins[1], { target: { value: "7" } });
    expect(mins[0]).toHaveValue(null);
    expect(mins[1]).toHaveValue(7);
  });
});

// ---------------------------------------------------------------------------
// inline validation
// ---------------------------------------------------------------------------

describe("validateParams", () => {
  it("flags a missing required param", () => {
    expect(validateParams(ACCEPTED_VALUES, {})).toEqual({ values: "Required" });
    expect(validateParams(ACCEPTED_VALUES, { values: [] })).toEqual({ values: "Required" });
    expect(validateParams(ACCEPTED_VALUES, { values: ["a"] })).toEqual({});
  });

  it("flags a non-numeric value in a number field", () => {
    expect(validateParams(RANGE, { min: "abc" })).toEqual({ min: "Must be a number or date" });
    expect(validateParams(RANGE, { min: 0 })).toEqual({});
    expect(validateParams(RANGE, { min: "12.5" })).toEqual({});
  });

  it("accepts date bounds on a number field (range documents numeric/date)", () => {
    // Editing an existing date-bounded range check must not report it as broken.
    expect(validateParams(RANGE, { min: "2024-01-01", max: "2024-12-31T23:59:59Z" })).toEqual({});
  });

  it("does not invent errors for unset optional params", () => {
    expect(validateParams(RANGE, {})).toEqual({});
  });
});

describe("inline error rendering", () => {
  // A native number input already refuses letters, so a bad numeric value can
  // only arrive from the stored check (or the raw-JSON hatch) — which is exactly
  // when the analyst most needs to be told which field is wrong.
  it("surfaces a bad stored number as an announced, programmatically-linked error", () => {
    render(<Harness specs={RANGE} initial={{ min: "yesterday" }} />);
    const min = screen.getByLabelText(/^min/);

    const error = screen.getByRole("alert");
    expect(error).toHaveTextContent("Must be a number or date");
    expect(min).toHaveAttribute("aria-invalid", "true");
    // the field points at both its hint and its error, so a screen reader
    // reads the requirement and the failure together
    expect(min.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
    // ...and the offending value stays visible/editable: a number input cannot
    // display it, so the control falls back to text rather than blanking it.
    expect(min).toHaveProperty("type", "text");
    expect(min).toHaveValue("yesterday");
  });

  it("clears the error once the value becomes valid", () => {
    render(<Harness specs={RANGE} initial={{ min: "yesterday" }} />);
    const min = screen.getByLabelText(/^min/);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.change(min, { target: { value: "10" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^min/)).not.toHaveAttribute("aria-invalid");
    expect(JSON.parse(preview())).toEqual({ min: 10 }); // coerced to a real number
  });

  it("keeps a date bound editable without flagging it", () => {
    render(<Harness specs={RANGE} initial={{ min: "2024-01-01" }} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^min/)).toHaveValue("2024-01-01");
  });

  it("shows Required until a required list gets a value", () => {
    render(<Harness specs={ACCEPTED_VALUES} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Required");
    fireEvent.change(screen.getByLabelText(/^values/), { target: { value: "active, inactive" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// effective params preview
// ---------------------------------------------------------------------------

describe("Effective params preview", () => {
  it("starts empty and reflects each field as it is filled", () => {
    render(<Harness specs={ACCEPTED_VALUES} />);
    expect(preview()).toBe("{}");

    fireEvent.change(screen.getByLabelText(/^values/), { target: { value: "active, inactive" } });
    expect(JSON.parse(preview())).toEqual({ values: ["active", "inactive"] });

    fireEvent.click(screen.getByLabelText(/^case_sensitive/)); // true -> false
    fireEvent.change(screen.getByLabelText(/^tolerance/), { target: { value: "3" } });
    expect(JSON.parse(preview())).toEqual({
      values: ["active", "inactive"],
      case_sensitive: false,
      tolerance: 3,
    });
  });

  it("drops emptied fields instead of submitting nulls", () => {
    render(<Harness specs={RANGE} initial={{ min: 1, max: 10 }} />);
    expect(JSON.parse(preview())).toEqual({ min: 1, max: 10 });

    fireEvent.change(screen.getByLabelText(/^max/), { target: { value: "" } });
    expect(JSON.parse(preview())).toEqual({ min: 1 });
  });

  it("keeps extra keys the schema does not know about", () => {
    // e.g. a param added server-side before the UI catches up — losing it on
    // an unrelated edit would silently rewrite the analyst's check.
    render(<Harness specs={RANGE} initial={{ min: 1, exclusive: true }} />);
    fireEvent.change(screen.getByLabelText(/^min/), { target: { value: "2" } });
    expect(JSON.parse(preview())).toEqual({ min: 2, exclusive: true });
  });
});

// ---------------------------------------------------------------------------
// advanced raw-JSON escape hatch
// ---------------------------------------------------------------------------

describe("Advanced (raw JSON)", () => {
  it("is collapsed by default and reports its state to assistive tech", () => {
    render(<Harness specs={ACCEPTED_VALUES} initial={{ values: ["a"] }} />);
    const toggle = screen.getByRole("button", { name: /advanced \(raw json\)/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Raw params JSON")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAttribute("aria-controls", rawJson().closest("div")?.id ?? "");
  });

  it("opens pre-filled with the effective params", () => {
    render(<Harness specs={ACCEPTED_VALUES} initial={{ values: ["a", "b"] }} />);
    openAdvanced();
    expect(JSON.parse(rawJson().value)).toEqual({ values: ["a", "b"] });
  });

  it("round-trips: JSON edits drive the fields, field edits drive the JSON", () => {
    render(<Harness specs={ACCEPTED_VALUES} initial={{ values: ["a", "b"] }} />);
    openAdvanced();

    // JSON -> fields
    fireEvent.change(rawJson(), {
      target: { value: '{"values": ["x", "y"], "case_sensitive": false, "tolerance": 2}' },
    });
    expect(screen.getByLabelText(/^values/)).toHaveValue("x, y");
    expect(screen.getByLabelText(/^case_sensitive/)).not.toBeChecked();
    expect(screen.getByLabelText(/^tolerance/)).toHaveValue(2);
    expect(JSON.parse(preview())).toEqual({
      values: ["x", "y"],
      case_sensitive: false,
      tolerance: 2,
    });

    // fields -> JSON (re-mirrored on the next open, so the panel never reopens stale)
    fireEvent.change(screen.getByLabelText(/^values/), { target: { value: "z" } });
    openAdvanced(); // close
    openAdvanced(); // reopen
    expect(JSON.parse(rawJson().value)).toEqual({
      values: ["z"],
      case_sensitive: false,
      tolerance: 2,
    });
  });

  it("reports malformed JSON without discarding what the fields already hold", () => {
    render(<Harness specs={ACCEPTED_VALUES} initial={{ values: ["a"] }} />);
    openAdvanced();
    fireEvent.change(rawJson(), { target: { value: '{"values": [' } });

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(rawJson()).toHaveAttribute("aria-invalid", "true");
    // the typed text stays put (nothing is silently reverted under the cursor)
    expect(rawJson()).toHaveValue('{"values": [');
    // ...and the committed params are untouched
    expect(screen.getByLabelText(/^values/)).toHaveValue("a");
    expect(JSON.parse(preview())).toEqual({ values: ["a"] });
  });

  it("rejects a JSON array or scalar — params must be an object", () => {
    render(<Harness specs={ACCEPTED_VALUES} initial={{ values: ["a"] }} />);
    openAdvanced();

    fireEvent.change(rawJson(), { target: { value: '["a", "b"]' } });
    expect(screen.getByRole("alert")).toHaveTextContent("Params must be a JSON object");
    fireEvent.change(rawJson(), { target: { value: "null" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Params must be a JSON object");
    expect(JSON.parse(preview())).toEqual({ values: ["a"] });
  });

  it("recovers when the JSON is fixed", () => {
    render(<Harness specs={ACCEPTED_VALUES} initial={{ values: ["a"] }} />);
    openAdvanced();
    fireEvent.change(rawJson(), { target: { value: "{" } });
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.change(rawJson(), { target: { value: '{"values": ["b"]}' } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^values/)).toHaveValue("b");
  });

  it("clearing the box clears the params", () => {
    render(<Harness specs={ACCEPTED_VALUES} initial={{ values: ["a"], tolerance: 5 }} />);
    openAdvanced();
    fireEvent.change(rawJson(), { target: { value: "" } });

    expect(preview()).toBe("{}");
    expect(screen.getByLabelText(/^values/)).toHaveValue("");
    // and the now-missing required param is reported inline, not swallowed
    expect(screen.getAllByRole("alert").some((n) => n.textContent === "Required")).toBe(true);
  });
});
