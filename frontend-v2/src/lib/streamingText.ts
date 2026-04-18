/**
 * streamingText — zero-cost text accumulation during streaming.
 *
 * A plain mutable object that the WebSocket handler writes to and
 * AnimatedText reads from. No Immer, no React, no proxies, no cost.
 *
 * Zustand gets ONE write per block boundary (text→tool, text→done).
 * Between boundaries, this is the source of truth for live text.
 *
 * Each entry is keyed by chatId:messageId. AnimatedText polls this
 * on every rAF frame to get the current target text.
 */

export interface StreamingEntry {
  /** Accumulated text for this part. Grows as deltas arrive. */
  text: string;
  /** Accumulated thinking for this part. */
  thinking: string;
}

/** Active streaming entries, keyed by "chatId:messageId". */
const entries = new Map<string, StreamingEntry>();

/** Get or create an entry. */
export function getStreamingEntry(chatId: string, messageId: string): StreamingEntry {
  const key = `${chatId}:${messageId}`;
  let entry = entries.get(key);
  if (!entry) {
    entry = { text: "", thinking: "" };
    entries.set(key, entry);
  }
  return entry;
}

/** Append text delta. Zero cost — just string concatenation. */
export function pushTextDelta(chatId: string, messageId: string, delta: string): void {
  getStreamingEntry(chatId, messageId).text += delta;
}

/** Append thinking delta. */
export function pushThinkingDelta(chatId: string, messageId: string, delta: string): void {
  getStreamingEntry(chatId, messageId).thinking += delta;
}

/** Read the current text for a streaming message. */
export function readStreamingText(chatId: string, messageId: string): string {
  return entries.get(`${chatId}:${messageId}`)?.text ?? "";
}

/** Read the current thinking for a streaming message. */
export function readStreamingThinking(chatId: string, messageId: string): string {
  return entries.get(`${chatId}:${messageId}`)?.thinking ?? "";
}

/** Check if a streaming entry exists (not yet cleared). */
export function hasStreamingEntry(chatId: string, messageId: string): boolean {
  return entries.has(`${chatId}:${messageId}`);
}

/** Clear an entry (called on finalization — assistant-message seals it). */
export function clearStreamingEntry(chatId: string, messageId: string): void {
  entries.delete(`${chatId}:${messageId}`);
}
