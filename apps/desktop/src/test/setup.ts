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

// jsdom implements <canvas> but not its 2D drawing context: getContext("2d") returns null.
// ECharts (via zrender) then fails inside the frame it schedules, and because that failure is
// asynchronous, whether vitest attributes it to a test file — and therefore whether the run
// exits 0 or 1 — depends on machine speed. On a 2-vCPU-constrained host it reproduces as:
//
//   TypeError: Cannot set properties of null (setting 'dpr')          (LivePlotPanel setOption)
//   TypeError: Cannot read properties of null (reading 'clearRect')   (LivePlotPanel dispose)
//   Test Files 12 passed · Tests 162 passed · Errors 2 → exit 1
//
// A real browser always provides a canvas context, so the gap is jsdom's, not the component's
// — the same class of gap the ResizeObserver stub above already fills. This stub makes the run
// deterministic. It changes no assertion: every test still renders the real components and
// every expected value in the suite is untouched.
function createCanvasContextStub(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const state: Record<string, unknown> = { canvas };
  const noop = () => undefined;

  return new Proxy(state, {
    get(target, property, receiver) {
      if (property in target) return Reflect.get(target, property, receiver) as unknown;
      switch (property) {
        case "measureText":
          return (text: string) => ({ width: text.length * 6 });
        case "createLinearGradient":
        case "createRadialGradient":
          return () => ({ addColorStop: noop });
        case "createPattern":
          return () => null;
        case "getLineDash":
          return () => [];
        case "getImageData":
          return (x: number, y: number, width: number, height: number) => ({
            data: new Uint8ClampedArray(Math.max(width, 0) * Math.max(height, 0) * 4),
            height,
            width,
          });
        case "isPointInPath":
        case "isPointInStroke":
          return () => false;
        default:
          // Every other CanvasRenderingContext2D member zrender touches becomes a no-op.
          return noop;
      }
    },
    set(target, property, value) {
      target[property as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

const originalGetContext = HTMLCanvasElement.prototype.getContext as unknown as (
  contextId: string,
  ...rest: unknown[]
) => unknown;

HTMLCanvasElement.prototype.getContext = function (
  this: HTMLCanvasElement,
  contextId: string,
  ...rest: unknown[]
) {
  if (contextId === "2d") return createCanvasContextStub(this);
  return originalGetContext.call(this, contextId, ...rest);
} as unknown as typeof HTMLCanvasElement.prototype.getContext;
