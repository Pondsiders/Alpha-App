/**
 * DrainRateContext — global adaptive drain rate for text animation.
 *
 * One provider per assistant message. Tracks total unreviewed characters
 * across all AnimatedText components, computes a shared drain rate.
 *
 * Rate formula: charsPerSec = BASE_RATE + totalUnreviewed * CHASE_FACTOR
 *
 * AnimatedText components register/unregister their remaining char count.
 * The provider sums them and publishes the rate via context.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type FC,
  type ReactNode,
} from "react";
import { useStore } from "@/store";

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface DrainRateContextValue {
  /** Current drain rate in chars/sec. */
  rate: number;
  /** True when any text part is still animating (has unreviewed chars). */
  isAnimating: boolean;
  /** Register a part's remaining character count. Call on every rAF frame. */
  reportRemaining: (partIndex: number, remaining: number) => void;
}

const DrainRateCtx = createContext<DrainRateContextValue>({
  rate: 60,
  isAnimating: false,
  reportRemaining: () => {},
});

export const useDrainRate = () => useContext(DrainRateCtx);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

interface DrainRateProviderProps {
  baseRate?: number;
  chaseFactor?: number;
  children: ReactNode;
}

export const DrainRateProvider: FC<DrainRateProviderProps> = ({
  baseRate = 0,
  chaseFactor = 1.0,
  children,
}) => {
  const [rate, setRate] = useState(baseRate);
  const [isAnimating, setIsAnimating] = useState(false);
  const remainingMap = useRef(new Map<number, number>());
  const smoothedRate = useRef(baseRate);
  const lastReportTime = useRef(performance.now());
  const BASE_ALPHA = 0.05; // smoothing factor normalized to 60fps

  const reportRemaining = useCallback(
    (partIndex: number, remaining: number) => {
      remainingMap.current.set(partIndex, remaining);

      // Sum all remaining chars across all parts
      let total = 0;
      for (const v of remainingMap.current.values()) {
        total += v;
      }

      const targetRate = baseRate + total * chaseFactor;
      // Frame-rate-independent exponential moving average.
      // Smooths the rate to prevent jerky oscillation as the buffer
      // fills and drains with Claude's bursty token delivery.
      const now = performance.now();
      const dt = (now - lastReportTime.current) / 1000; // seconds
      lastReportTime.current = now;
      const correctedAlpha = 1 - Math.pow(1 - BASE_ALPHA, dt * 60);
      smoothedRate.current += (targetRate - smoothedRate.current) * correctedAlpha;
      setRate(smoothedRate.current);
      setIsAnimating(total > 0);
    },
    [baseRate, chaseFactor],
  );

  // Sync isAnimating to Zustand so the WS handler can check it
  const setStoreAnimating = useStore((s) => s.setIsAssistantAnimating);
  useEffect(() => {
    setStoreAnimating(isAnimating);
  }, [isAnimating, setStoreAnimating]);

  // Total remaining chars for the debug widget
  const totalRemaining = useRef(0);

  // Debug widget — shows drain rate and buffer depth
  useEffect(() => {
    let div = document.getElementById("drain-rate-debug") as HTMLDivElement;
    if (!div) {
      div = document.createElement("div");
      div.id = "drain-rate-debug";
      div.style.cssText =
        "position:fixed; top:8px; right:80px; background:#1a1a1a; color:#f0c040; " +
        "font-family:monospace; font-size:11px; padding:4px 8px; border-radius:4px; " +
        "z-index:9999; opacity:0.85; pointer-events:none; border:1px solid #333;";
      document.body.appendChild(div);
    }
    div.textContent = `⚡ ${Math.round(rate)} c/s | 📦 ${totalRemaining.current} chars`;
  }, [rate]);

  const reportRemainingWrapped = useCallback(
    (partIndex: number, remaining: number) => {
      reportRemaining(partIndex, remaining);
      // Update total for debug display
      let t = 0;
      for (const v of remainingMap.current.values()) t += v;
      totalRemaining.current = t;
    },
    [reportRemaining],
  );

  return (
    <DrainRateCtx.Provider value={{ rate, isAnimating, reportRemaining: reportRemainingWrapped }}>
      {children}
    </DrainRateCtx.Provider>
  );
};
