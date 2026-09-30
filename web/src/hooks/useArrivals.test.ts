import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useArrivals } from "./useArrivals";

describe("useArrivals", () => {
  it("marks only items that arrive after the first load", () => {
    const { result, rerender } = renderHook(({ k }) => useArrivals(k),
                                            { initialProps: { k: undefined as number[] | undefined } });
    expect([...result.current]).toEqual([]);
    rerender({ k: [3, 2] });            // first load: nothing slides in
    expect([...result.current]).toEqual([]);
    rerender({ k: [4, 3, 2] });         // a new live case
    expect([...result.current]).toEqual([4]);
    rerender({ k: [4, 3, 2] });
    expect([...result.current]).toEqual([4]);
  });
});
