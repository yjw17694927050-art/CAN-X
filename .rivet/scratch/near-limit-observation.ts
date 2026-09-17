/**
 * One-shot near-limit transport observation for V0.3-08. NOT a product file.
 *
 * Runs the real `importDbcAsset` serialization path with a payload that is exactly
 * `MAX_DBC_IMPORT_BYTES` of content once Base64-encoded, and measures four things:
 *
 *   * `wallMs` — the whole client call, with the network replaced by a stub;
 *   * `synchronousStringifyMs` — the JSON body build, which is undeniably main-thread
 *     synchronous work;
 *   * `timerLagMs` — how long a `setTimeout(…, 0)` posted *before* the call had to
 *     wait for its turn. This is the starvation judgement: it is the delay a renderer
 *     would impose on its own next frame or input event;
 *   * `ticksDuringCall` — how many `setTimeout(…, 4)` samples the loop managed while
 *     the call was in flight.
 *
 * The verdict rule is relative, never an absolute millisecond budget: starvation is
 * demonstrated when `timerLagMs` tracks `wallMs` (the loop was unavailable for the
 * whole call) and `ticksDuringCall` stays at zero. A machine-independent reading
 * comes from comparing the near-limit call against a baseline call with a tiny
 * payload measured the same way.
 *
 * Run:  node .rivet/scratch/near-limit-observation.ts
 */

import { performance } from "node:perf_hooks";

import { importDbcAsset } from "../../apps/desktop/src/runtime/dbc-client.ts";
import type { ImportDbcAssetInput } from "../../apps/desktop/src/runtime/dbc-client.ts";

const MAX_DBC_IMPORT_BYTES = 16 * 1024 * 1024;
const SAMPLE_MS = 4;

/** The longest Base64 text this endpoint accepts: 4 * ceil(bytes / 3) characters. */
function nearLimitBase64(): string {
  const wholeQuanta = Math.floor(MAX_DBC_IMPORT_BYTES / 3);
  const remainder = MAX_DBC_IMPORT_BYTES % 3;
  const tail = remainder === 0 ? "" : remainder === 1 ? "AA==" : "AAA=";
  return "AAAA".repeat(wholeQuanta) + tail;
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/** Time one JSON body build of the given payload, in isolation. */
function measureJsonStringify(
  projectPath: string,
  sourceName: string,
  contentBase64: string,
): number {
  const started = performance.now();
  JSON.stringify({
    project_path: projectPath,
    source_name: sourceName,
    content_base64: contentBase64,
  });
  return performance.now() - started;
}

/**
 * A stand-in for the network that still pays the encoding cost the real one does:
 * a browser serialises a string body to UTF-8 bytes before it leaves the renderer,
 * so the stub encodes the body too. Skipping that would understate the work.
 */
function installStubFetch(): { encodedBytes: number; encodeMs: number } {
  const state = { encodedBytes: 0, encodeMs: 0 };
  globalThis.fetch = ((_url: string, init: RequestInit) => {
    const body = init.body;
    if (typeof body === "string") {
      const encodeStarted = performance.now();
      state.encodedBytes = new TextEncoder().encode(body).byteLength;
      state.encodeMs = round(state.encodeMs + (performance.now() - encodeStarted));
    }
    return Promise.resolve(
      new Response(JSON.stringify(RUNTIME_ASSET), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }) as typeof fetch;
  return state;
}

interface Measurement {
  readonly wallMs: number;
  readonly timerLagMs: number;
  readonly ticksDuringCall: number;
}

/**
 * Run one import and observe the event loop around it.
 *
 * The zero-delay timer and the sampling timer are both posted *before* the call, so
 * neither needs the loop for itself: what they observe is purely how long the loop
 * was unavailable to them.
 */
async function measure(input: ImportDbcAssetInput): Promise<Measurement> {
  const started = performance.now();
  let ticks = 0;

  const firstTick = new Promise<number>((resolve) => {
    setTimeout(() => resolve(performance.now() - started), 0);
  });

  let sampling = true;
  const sample = (): void => {
    ticks += 1;
    if (sampling) setTimeout(sample, SAMPLE_MS);
  };
  setTimeout(sample, SAMPLE_MS);

  const callStarted = performance.now();
  await importDbcAsset(input);
  const wallMs = performance.now() - callStarted;

  sampling = false;
  const timerLagMs = await firstTick;
  return { wallMs: round(wallMs), timerLagMs: round(timerLagMs), ticksDuringCall: ticks };
}

const RUNTIME_ASSET: Record<string, unknown> = {
  asset_id: "dbc-asset-0001",
  source_name: "near_limit.dbc",
  sha256: "a".repeat(64),
  size_bytes: MAX_DBC_IMPORT_BYTES,
  encoding: "utf-8",
  imported_at: "2026-09-17T08:30:00+00:00",
};

const content = nearLimitBase64();
const decodedBytes = Buffer.from(content, "base64").length;
const projectPath = "C:\\canx\\projects\\near-limit-fixture";
const stub = installStubFetch();

// ---- Warm-up (discarded): the first call in a process pays module/JIT costs ------
await measure({ projectPath, sourceName: "warmup.dbc", contentBase64: "AAECAwQ=" });

// ---- Baseline ------------------------------------------------------------------
const baseline = await measure({
  projectPath,
  sourceName: "tiny.dbc",
  contentBase64: "AAECAwQ=",
});
const baselineStringifyMs = round(
  measureJsonStringify(projectPath, "tiny.dbc", "AAECAwQ="),
);

// ---- Near limit ----------------------------------------------------------------
const synchronousStringifyMs = round(
  measureJsonStringify(projectPath, "near_limit.dbc", content),
);

const nearLimit = await measure({
  projectPath,
  sourceName: "near_limit.dbc",
  contentBase64: content,
});
const nearLimitAgain = await measure({
  projectPath,
  sourceName: "near_limit.dbc",
  contentBase64: content,
});

// ---- After the big payloads, to show the loop recovers ---------------------------
const recovery = await measure({
  projectPath,
  sourceName: "tiny.dbc",
  contentBase64: "AAECAwQ=",
});

process.stdout.write(
  `${JSON.stringify(
    {
      environment: { node: process.version, platform: process.platform },
      payload: {
        base64Chars: content.length,
        decodedBytes,
        maxImportBytes: MAX_DBC_IMPORT_BYTES,
        wireBodyBytes: Buffer.byteLength(
          JSON.stringify({
            project_path: projectPath,
            source_name: "near_limit.dbc",
            content_base64: content,
          }),
          "utf8",
        ),
        wireBodyEncodedBytes: stub.encodedBytes,
      },
      synchronousStringifyMs,
      bodyEncodeMs: stub.encodeMs,
      baselineStringifyMs,
      baseline,
      nearLimit,
      nearLimitAgain,
      recovery,
    },
    null,
    2,
  )}\n`,
);
