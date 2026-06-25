import { describe, expect, it } from "vitest";

import type { SavedQuery, User } from "../../api/types";
import { canManageQuery, makeTab, nextLimitAfter } from "./shared";

const user = (over: Partial<User>): User => ({ id: 1, role: "viewer", ...over }) as unknown as User;
const savedQuery = (over: Partial<SavedQuery>): SavedQuery =>
  ({ id: 10, created_by_id: 1, ...over }) as unknown as SavedQuery;

describe("nextLimitAfter", () => {
  it("returns the next rung above the current limit", () => {
    expect(nextLimitAfter(50)).toBe(200);
    expect(nextLimitAfter(200)).toBe(500);
    expect(nextLimitAfter(500)).toBe(1000);
    expect(nextLimitAfter(1000)).toBe(2000);
  });

  it("jumps to the first rung above an in-between value", () => {
    expect(nextLimitAfter(120)).toBe(200);
  });

  it("clamps at the 2000 max", () => {
    expect(nextLimitAfter(2000)).toBe(2000);
    expect(nextLimitAfter(5000)).toBe(2000);
  });
});

describe("makeTab", () => {
  it("creates a clean tab with sane defaults", () => {
    const t = makeTab("SELECT 1");
    expect(t.sql).toBe("SELECT 1");
    expect(t.dirty).toBe(false);
    expect(t.result).toBeNull();
    expect(t.error).toBeNull();
    expect(t.resultLimit).toBe(200);
    expect(t.view).toBe("table");
    expect(t.showFilters).toBe(false);
    expect(t.id).toBeTruthy();
  });

  it("gives each tab a distinct id", () => {
    expect(makeTab().id).not.toBe(makeTab().id);
  });
});

describe("canManageQuery", () => {
  it("lets an admin manage any query", () => {
    expect(canManageQuery(user({ role: "admin", id: 9 }), savedQuery({ created_by_id: 1 }))).toBe(true);
  });

  it("lets the creator manage their own query", () => {
    expect(canManageQuery(user({ id: 5, role: "editor" }), savedQuery({ created_by_id: 5 }))).toBe(true);
  });

  it("denies a non-creator, non-admin", () => {
    expect(canManageQuery(user({ id: 5, role: "editor" }), savedQuery({ created_by_id: 6 }))).toBe(false);
  });

  it("denies when there is no user", () => {
    expect(canManageQuery(null, savedQuery({ created_by_id: 5 }))).toBe(false);
  });
});
