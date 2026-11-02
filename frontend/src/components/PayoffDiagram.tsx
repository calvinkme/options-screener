import { useId, useState, type MouseEvent } from "react";
import type { Row } from "../types";

const PROFIT = "#047857";
const LOSS = "#be123c";
const BE = "#b45309";
const MUTED = "#64748b";

const usd = (n: number) =>
  n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/**
 * Payoff at expiry, computed from the legs rather than from the summary metrics, so the
 * picture and the numbers cannot disagree. Stock legs are not modelled; the covered-call
 * row shows the option leg alone and says so.
 *
 * Max profit, max loss and every breakeven are drawn on the chart itself, labelled with
 * their values. Hovering reads off the P&L at any price.
 */
export function PayoffDiagram({ row }: { row: Row }) {
  const [hover, setHover] = useState<{ price: number; pnl: number } | null>(null);
  const uid = useId().replace(/:/g, "");
  const strikes = row.legs.map((l) => l.strike);
  if (!strikes.length) return null;

  // A synthetic runs to zero on one side, so a window hugging the strikes hides the shape
  // that matters. Widen it enough to show where the line actually crosses.
  const wide = row.strategy.startsWith("synthetic");
  const lo = Math.min(...strikes) * (wide ? 0.6 : 0.85);
  const hi = Math.max(...strikes) * (wide ? 1.4 : 1.15);
  const steps = 240;

  const pnlAt = (price: number) => {
    let pnl = 0;
    for (const leg of row.legs) {
      const intrinsic =
        leg.right === "C" ? Math.max(price - leg.strike, 0) : Math.max(leg.strike - price, 0);
      pnl += (intrinsic - (leg.mark ?? 0)) * leg.qty;
    }
    return pnl;
  };

  const points: { price: number; pnl: number }[] = [];
  for (let i = 0; i <= steps; i++) {
    const price = lo + ((hi - lo) * i) / steps;
    points.push({ price, pnl: pnlAt(price) });
  }

  const maxPnl = Math.max(...points.map((p) => p.pnl));
  const minPnl = Math.min(...points.map((p) => p.pnl));
  // Headroom so the max/min labels never sit on the frame.
  const top = Math.max(maxPnl, 0) * 1.18 || 1;
  const bottom = Math.min(minPnl, 0) * 1.18 || -1;

  const W = 640;
  const H = 280;
  const pad = { l: 64, r: 16, t: 22, b: 34 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const x = (price: number) => pad.l + ((price - lo) / (hi - lo)) * iw;
  const y = (pnl: number) => pad.t + ((top - pnl) / (top - bottom)) * ih;
  const zeroY = y(0);

  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.price).toFixed(1)} ${y(p.pnl).toFixed(1)}`)
    .join(" ");
  // Closed to the zero line, then clipped above/below it to shade profit and loss zones.
  const area = `${path} L ${x(hi).toFixed(1)} ${zeroY} L ${x(lo).toFixed(1)} ${zeroY} Z`;

  const spot = row.underlying.price;
  const breakevens = [row.metrics["breakeven"], row.metrics["secondary_breakeven"]]
    .map(Number)
    .filter((v) => Number.isFinite(v) && v > lo && v < hi);

  // An unbounded side has no true max; the curve's edge is just where the window stops.
  const profitUnbounded = row.strategy === "synthetic_long" || row.strategy === "long_call";
  const lossUnbounded = row.strategy === "synthetic_short";
  const maxProfit = Number(row.metrics["max_profit"]);
  const maxLoss = Number(row.metrics["max_loss"]);
  const profitLabel = Number.isFinite(maxProfit) ? maxProfit : maxPnl;
  const lossLabel = Number.isFinite(maxLoss) ? -Math.abs(maxLoss) : minPnl;

  const priceTicks = Array.from({ length: 6 }, (_, i) => lo + ((hi - lo) * i) / 5);
  const uniqueStrikes = [...new Set(strikes)].filter((s) => s > lo && s < hi);

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    if (px < pad.l || px > W - pad.r) return setHover(null);
    const price = lo + ((px - pad.l) / iw) * (hi - lo);
    setHover({ price, pnl: pnlAt(price) });
  };

  return (
    <div className="flex flex-wrap items-center gap-4">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width={W}
        height={H}
        className="max-w-full rounded border border-slate-200 bg-white"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <clipPath id={`${uid}-above`}>
            <rect x={pad.l} y={pad.t} width={iw} height={Math.max(zeroY - pad.t, 0)} />
          </clipPath>
          <clipPath id={`${uid}-below`}>
            <rect x={pad.l} y={zeroY} width={iw} height={Math.max(pad.t + ih - zeroY, 0)} />
          </clipPath>
        </defs>

        <path d={area} fill={PROFIT} fillOpacity={0.12} clipPath={`url(#${uid}-above)`} />
        <path d={area} fill={LOSS} fillOpacity={0.12} clipPath={`url(#${uid}-below)`} />

        {/* Zero line and price axis. */}
        <line x1={pad.l} y1={zeroY} x2={W - pad.r} y2={zeroY} stroke="#94a3b8" />
        <text x={pad.l - 6} y={zeroY + 3} fontSize={10} fill={MUTED} textAnchor="end">
          $0
        </text>
        {priceTicks.map((p, i) => (
          <text key={i} x={x(p)} y={H - 8} fontSize={10} fill={MUTED} textAnchor="middle">
            {p.toFixed(0)}
          </text>
        ))}

        {/* Strikes: a tick on the zero line and the strike price under the axis. */}
        {uniqueStrikes.map((s) => (
          <g key={s}>
            <line x1={x(s)} y1={zeroY - 5} x2={x(s)} y2={zeroY + 5} stroke={MUTED} />
            <text x={x(s)} y={H - 20} fontSize={9} fill="#334155" textAnchor="middle">
              K {s}
            </text>
          </g>
        ))}

        {/* Max profit / max loss: dashed level lines labelled with the dollar amount. */}
        {maxPnl > 0 && (
          <g>
            <line
              x1={pad.l}
              y1={y(maxPnl)}
              x2={W - pad.r}
              y2={y(maxPnl)}
              stroke={PROFIT}
              strokeDasharray="4 3"
            />
            <text x={pad.l - 6} y={y(maxPnl) + 3} fontSize={10} fill={PROFIT} textAnchor="end">
              {usd(maxPnl)}
            </text>
            <text x={W - pad.r - 4} y={y(maxPnl) - 5} fontSize={11} fill={PROFIT} textAnchor="end">
              {profitUnbounded ? "Profit unlimited ↗" : `Max profit ${usd(profitLabel)}`}
            </text>
          </g>
        )}
        {minPnl < 0 && (
          <g>
            <line
              x1={pad.l}
              y1={y(minPnl)}
              x2={W - pad.r}
              y2={y(minPnl)}
              stroke={LOSS}
              strokeDasharray="4 3"
            />
            <text x={pad.l - 6} y={y(minPnl) + 3} fontSize={10} fill={LOSS} textAnchor="end">
              {usd(minPnl)}
            </text>
            <text x={pad.l + 4} y={y(minPnl) + 13} fontSize={11} fill={LOSS}>
              {lossUnbounded ? "Loss unlimited ↘" : `Max loss ${usd(lossLabel)}`}
            </text>
          </g>
        )}

        <path d={path} fill="none" stroke="#0f766e" strokeWidth={2} />

        {/* Breakevens: vertical marker, a dot where the payoff crosses zero, and the price. */}
        {breakevens.map((be, i) => (
          <g key={i}>
            <line
              x1={x(be)}
              y1={pad.t}
              x2={x(be)}
              y2={pad.t + ih}
              stroke={BE}
              strokeDasharray="2 3"
            />
            <circle cx={x(be)} cy={zeroY} r={3.5} fill={BE} />
            <text
              x={x(be) + 5}
              y={zeroY + (i % 2 === 0 ? -7 : 15)}
              fontSize={11}
              fontWeight={600}
              fill={BE}
            >
              Breakeven {be.toFixed(2)}
            </text>
          </g>
        ))}

        {typeof spot === "number" && spot > lo && spot < hi && (
          <g>
            <line x1={x(spot)} y1={pad.t} x2={x(spot)} y2={pad.t + ih} stroke={MUTED} strokeDasharray="5 4" />
            <text x={x(spot)} y={pad.t - 8} fontSize={10} fill="#334155" textAnchor="middle">
              Spot {spot.toFixed(2)}
            </text>
          </g>
        )}

        {hover && (
          <g pointerEvents="none">
            <line x1={x(hover.price)} y1={pad.t} x2={x(hover.price)} y2={pad.t + ih} stroke="#0f172a" strokeOpacity={0.25} />
            <circle cx={x(hover.price)} cy={y(hover.pnl)} r={3.5} fill="#0f172a" />
            <rect
              x={Math.min(x(hover.price) + 8, W - pad.r - 150)}
              y={pad.t + 4}
              width={142}
              height={34}
              rx={4}
              fill="#0f172a"
              fillOpacity={0.88}
            />
            <text x={Math.min(x(hover.price) + 16, W - pad.r - 142)} y={pad.t + 18} fontSize={10} fill="#e2e8f0">
              At expiry, price {hover.price.toFixed(2)}
            </text>
            <text
              x={Math.min(x(hover.price) + 16, W - pad.r - 142)}
              y={pad.t + 32}
              fontSize={11}
              fontWeight={600}
              fill={hover.pnl >= 0 ? "#6ee7b7" : "#fda4af"}
            >
              P&amp;L {hover.pnl >= 0 ? "+" : ""}
              {usd(hover.pnl)}
            </text>
          </g>
        )}
      </svg>

      <div className="space-y-0.5 text-[11px] text-slate-600">
        <Line
          label="Max profit"
          value={row.metrics["max_profit"]}
          unbounded={row.strategy === "synthetic_long" || row.strategy === "long_call"}
        />
        <Line
          label="Max loss"
          value={row.metrics["max_loss"]}
          unbounded={row.strategy === "synthetic_short"}
        />
        <Line label="Breakeven" value={row.metrics["breakeven"]} />
        <Line label="Capital" value={row.metrics["capital"]} />
        <StrategyNote strategy={row.strategy} row={row} />
      </div>
    </div>
  );
}

/**
 * The line at the bottom of the panel that the numbers alone do not carry. Each one is a
 * structural fact about the shape above it, not a disclaimer.
 */
function StrategyNote({ strategy, row }: { strategy: string; row: Row }) {
  if (strategy === "covered_call") {
    return (
      <div className="mt-1 max-w-[240px] text-amber-700">
        Option leg only — the 100 shares are not drawn.
      </div>
    );
  }

  if (strategy === "jade_lizard") {
    const upside = Number(row.metrics["upside_risk"]);
    return (
      <div className="mt-1 max-w-[240px] text-[11px]">
        {Number.isFinite(upside) && upside <= 0 ? (
          <span className="text-emerald-700">
            Credit covers the call width — the right-hand step never crosses zero, so there
            is no upside risk.
          </span>
        ) : (
          <span className="text-rose-700">
            Credit is short of the call width by{" "}
            {Number.isFinite(upside) ? `$${upside.toFixed(2)}` : "—"} — the upside does lose.
          </span>
        )}
      </div>
    );
  }

  if (strategy === "synthetic_long") {
    return (
      <div className="mt-1 max-w-[240px] text-amber-700">
        Profit unlimited; loss runs to the short put strike, as if you owned the shares —
        on roughly a fifth of the capital.
      </div>
    );
  }

  if (strategy === "synthetic_short") {
    return (
      <div className="mt-1 max-w-[240px] text-amber-700">
        The short call has no ceiling — loss is unbounded above the strike.
      </div>
    );
  }

  return null;
}

function Line({
  label,
  value,
  unbounded,
}: {
  label: string;
  value: unknown;
  unbounded?: boolean;
}) {
  const n = Number(value);
  const empty = value === null || value === undefined || !Number.isFinite(n);

  return (
    <div className="flex gap-2">
      <span className="w-20 text-slate-500">{label}</span>
      <span className={`tnum ${empty && unbounded ? "text-amber-700" : ""}`}>
        {empty
          ? unbounded
            ? "unlimited"
            : "—"
          : n.toLocaleString(undefined, { style: "currency", currency: "USD" })}
      </span>
    </div>
  );
}
