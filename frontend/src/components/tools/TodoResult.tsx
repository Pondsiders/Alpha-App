/**
 * TodoResult — Inline todo list for TodoWrite tool calls.
 *
 * Renders the todo list with status indicators:
 * - pending: empty circle
 * - in_progress: amber pulsing dot
 * - completed: avocado checkmark
 */

import { Check, Circle } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

interface Todo {
  content: string;
  status: "pending" | "in_progress" | "completed";
  activeForm: string;
}

export const TodoResult: ToolCallMessagePartComponent = ({
  argsText,
  result,
}) => {
  // Parse todos from args
  let todos: Todo[] = [];
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    todos = args.todos || [];
  } catch {
    // Partial JSON while streaming — can't render yet
  }

  const hasResult = result !== undefined && result !== null;
  const isRunning = !hasResult;

  if (todos.length === 0 && !isRunning) return null;

  const completed = todos.filter((t) => t.status === "completed").length;
  const total = todos.length;

  return (
    <div
      data-testid="todo-result"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-surface">
        <span className="text-[13px] text-muted">
          {completed}/{total}
        </span>
        {/* Mini progress bar */}
        <div className="flex-1 h-1 bg-border rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{
              width: total > 0 ? `${(completed / total) * 100}%` : "0%",
              backgroundColor: "var(--success)",
            }}
          />
        </div>
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? "animate-pulse-dot" : ""}`}
          style={{
            backgroundColor: isRunning
              ? "var(--primary)"
              : completed === total
              ? "var(--success)"
              : "var(--primary)",
          }}
        />
      </div>

      {/* Todo items */}
      {todos.length > 0 && (
        <div className="border-t border-border">
          {todos.map((todo, i) => (
            <div
              key={i}
              className={`flex items-start gap-2 px-3 py-1.5 ${
                i > 0 ? "border-t border-border/50" : ""
              }`}
            >
              {/* Status icon */}
              {todo.status === "completed" ? (
                <Check
                  size={14}
                  className="mt-[2px] shrink-0"
                  style={{ color: "var(--success)" }}
                />
              ) : todo.status === "in_progress" ? (
                <Circle
                  size={14}
                  className="mt-[2px] shrink-0 animate-pulse-dot"
                  style={{ color: "var(--primary)" }}
                  fill="currentColor"
                />
              ) : (
                <Circle
                  size={14}
                  className="mt-[2px] shrink-0 text-muted/30"
                />
              )}

              {/* Content — show activeForm for in_progress, content otherwise */}
              <span
                className={`text-[13px] leading-snug ${
                  todo.status === "completed"
                    ? "text-muted/50 line-through"
                    : todo.status === "in_progress"
                    ? "text-text"
                    : "text-muted/70"
                }`}
              >
                {todo.status === "in_progress" ? todo.activeForm : todo.content}
              </span>
            </div>
          ))}
        </div>
      )}

      {isRunning && todos.length === 0 && (
        <div className="px-3 py-2 border-t border-border">
          <span className="text-muted/40 text-xs italic">
            Updating...
          </span>
        </div>
      )}
    </div>
  );
};
