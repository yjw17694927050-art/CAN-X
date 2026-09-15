import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

class TestResizeObserver implements ResizeObserver {
  readonly #callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.#callback = callback;
  }

  disconnect(): void {}

  observe(target: Element): void {
    const contentRect = new DOMRect(0, 0, 800, 320);
    const borderBoxSize = [{ blockSize: 320, inlineSize: 800 }] as ResizeObserverSize[];
    const entry: ResizeObserverEntry = {
      borderBoxSize,
      contentBoxSize: borderBoxSize,
      contentRect,
      devicePixelContentBoxSize: borderBoxSize,
      target,
    };
    this.#callback([entry], this);
  }

  unobserve(): void {}
}

globalThis.ResizeObserver = TestResizeObserver;
