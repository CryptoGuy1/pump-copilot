import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { type Schema, call, client } from "../api/client";
import { Chart, type Shade, toSeconds } from "../components/Chart";
import { A8Notice, Loading, Provenance, Synthetic } from "../components/common";

const RUNNING = "rgba(60, 140, 60, 0.08)";

export function AssetDay() {
  const { asset = "", day = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const session = params.get("session") ? Number(params.get("session")) : undefined;
  const path = { asset_id: asset, source_day: day };
  const info = useQuery({ queryKey: ["asset-day", asset, day], queryFn: () => call(
    client.GET("/api/assets/{asset_id}/days/{source_day}", { params: { path } })) });
  const segs = useQuery({ queryKey: ["segments", asset, day], queryFn: () =>
    call(client.GET("/api/assets/{asset_id}/days/{source_day}/segments",
                                             { params: { path } })) });
  const oneMin = useQuery({ queryKey: ["signals-1m", asset, day], queryFn: () =>
    call(client.GET(
      "/api/assets/{asset_id}/days/{source_day}/signals",
      { params: { path, query: { resolution: "1m" } } })) });
  const hasSessions = !!info.data?.sessions.length;
  const scores = useQuery({ queryKey: ["scores", asset, day, session], enabled: hasSessions,
    queryFn: () => call(client.GET(
      "/api/assets/{asset_id}/days/{source_day}/scores",
      { params: { path, query: { session_id: session } } })) });
  const bands = useQuery({ queryKey: ["bands", asset, day, session], enabled: hasSessions,
    queryFn: () => call(client.GET(
      "/api/assets/{asset_id}/days/{source_day}/bands",
      { params: { path, query: { session_id: session } } })) });

  const shade: Shade[] = useMemo(() => (segs.data?.segments ?? [])
    .filter((s) => s.state === "running")
    .map((s) => ({ from: Date.parse(s.start_at) / 1000, to: Date.parse(s.end_at) / 1000,
                   color: RUNNING })), [segs.data]);

  const scored = useMemo(() => {
    const by: Record<string, Schema<"ScoreRow">[]> = {};
    for (const r of scores.data?.scores ?? []) (by[r.signal_name] ??= []).push(r);
    return by;
  }, [scores.data]);
  const current = info.data?.sessions.find((s) => s.session_id === scores.data?.session_id);

  return (
    <section>
      <h1>{asset} · {day}</h1>
      <Loading q={info}>
        <p>
          replay session:{" "}
          <select value={scores.data?.session_id ?? ""} onChange={(e) =>
            setParams({ session: e.target.value })}>
            {info.data?.sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                #{s.session_id} {s.status}{s.synthetic ? " (SYNTHETIC)" : ""}</option>))}
          </select>{" "}
          <Synthetic show={current?.synthetic} />
          {!hasSessions && " none yet: start one on the Replay page"}
        </p>
        {hasSessions && <A8Notice />}
        {scores.data && <Provenance model_version={scores.data.model_version}
                                    assumptions={scores.data.assumptions} />}
        <p className="muted">Green background: running segments. Scored signals show the window
          median against its 3a-3 baseline band; red points are review_suggested windows.</p>
        {Object.entries(scored).map(([sig, rows]) => {
          const band = bands.data?.bands?.runs[0]?.signals[sig]?.band;
          const x = toSeconds(rows.map((r) => r.window_end));
          return <Chart key={sig} title={`${sig}${band ? ` (${band.unit})` : ""}`} x={x}
            shade={shade} series={[
              { label: "window median", values: rows.map((r) => r.median), color: "#2060c0" },
              { label: "band low", values: rows.map((r) => r.band_low), color: "#999",
                dash: [4, 4] },
              { label: "band high", values: rows.map((r) => r.band_high), color: "#999",
                dash: [4, 4] },
              { label: "review", points: true, color: "#c02020", values: rows.map((r) =>
                r.state === "review_suggested" ? r.median : null) },
            ]} />;
        })}
        <details open={!hasSessions}>
          <summary>Raw signals, 1-minute mean</summary>
          <Loading q={oneMin}>
            {Object.entries(oneMin.data?.signals ?? {}).map(([sig, points]) => {
              const rows = points as Schema<"MinutePoint">[]; // resolution=1m
              return (
              <Chart key={sig} title={sig} height={140} shade={shade}
                x={toSeconds(rows.map((r) => r.bucket))}
                series={[{ label: "1-min mean", values: rows.map((r) => r.mean),
                           color: "#555" }]} />);
            })}
          </Loading>
        </details>
      </Loading>
    </section>
  );
}
