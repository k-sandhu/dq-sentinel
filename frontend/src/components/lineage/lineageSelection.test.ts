import { describe, expect, it } from "vitest";

import type { Connection } from "../../api/types";
import { resolveLineageConnection } from "./lineageSelection";

const conns = [{ id: 5 }, { id: 9 }] as unknown as Connection[];

describe("resolveLineageConnection", () => {
  it("uses a valid numeric ?connection= param", () => {
    expect(resolveLineageConnection("9", conns)).toEqual({ fromParam: 9, connectionId: 9 });
  });

  it("ignores a non-numeric param and falls back to the first connection", () => {
    expect(resolveLineageConnection("abc", conns)).toEqual({ fromParam: null, connectionId: 5 });
  });

  it("falls back to the first connection when no param is set", () => {
    expect(resolveLineageConnection(null, conns)).toEqual({ fromParam: null, connectionId: 5 });
  });

  it("is null/null before connections load and when the list is empty", () => {
    expect(resolveLineageConnection(null, undefined)).toEqual({ fromParam: null, connectionId: null });
    expect(resolveLineageConnection(null, [])).toEqual({ fromParam: null, connectionId: null });
  });
});
