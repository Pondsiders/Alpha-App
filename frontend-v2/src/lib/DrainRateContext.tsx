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
  useRef,
  useState,
  type FC,
  type ReactNode,
} from "react";

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface DrainRateContextValue {
  /** Current drain rate in chars/sec. */
  rate: number;
  /** Register a part's remaining character count. Call on every rAF frame. */
  reportRemaining: (partIndex: number, remaining: number) => void;
}

const DrainRateCtx = createContext<DrainRateContextValue>({
  rate: 60,
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
  baseRate = 60,
  chaseFactor = 0.3,
  children,
}) => {
  const [rate, setRate] = useState(baseRate);
  const remainingMap = useRef(new Map<number, number>());

  const reportRemaining = useCallback(
    (partIndex: number, remaining: number) => {
      remainingMap.current.set(partIndex, remaining);

      // Sum all remaining chars across all parts
      let total = 0;
      for (const v of remainingMap.current.values()) {
        total += v;
      }

      setRate(baseRate + total * chaseFactor);
    },
    [baseRate, chaseFactor],
  );

  return (
    <DrainRateCtx.Provider value={{ rate, reportRemaining }}>
      {children}
    </DrainRateCtx.Provider>
  );
};
