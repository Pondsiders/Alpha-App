import { TopicBar } from "../components/TopicBar";

/**
 * DevTopics — Visual mockup of topic pills in all three states.
 * Route: /dev/topics
 * No wiring, no WebSocket, just the visual.
 */
export default function DevTopics() {
  const mockTopics = [
    { name: "alpha-app", state: "on" as const },
    { name: "intake", state: "armed" as const },
    { name: "cortex", state: "off" as const },
    { name: "rosemary", state: "off" as const },
    { name: "solitude", state: "off" as const },
  ];

  return (
    <div className="min-h-screen bg-background text-text p-8">
      <h1 className="text-2xl mb-1">Topic Pills</h1>
      <p className="text-muted mb-8 text-sm">
        Three states: off (gray), armed (amber), on (green)
      </p>

      <div className="max-w-3xl mx-auto space-y-12">
        {/* In-context: mock composer */}
        <div>
          <h2 className="text-lg mb-4 text-muted">In composer</h2>
          <div className="flex flex-col gap-3 p-4 bg-composer rounded-2xl shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.4),0_0_0_0.5px_rgba(108,106,96,0.15)]">
            <TopicBar topics={mockTopics} onToggle={() => {}} />
            <div className="w-full py-2 text-muted/60 italic text-[18px]">
              Message Alpha...
            </div>
            <div className="flex justify-end items-center gap-3">
              <div className="w-9 h-9 flex items-center justify-center bg-transparent border border-border rounded-lg text-muted/50">
                📎
              </div>
              <div className="w-9 h-9 flex items-center justify-center bg-primary border-none rounded-lg text-white">
                ↑
              </div>
            </div>
          </div>
          <p className="text-right text-muted mt-2 text-[11px]">
            Alpha remembers everything. Except when she doesn't. 🦆
          </p>
        </div>

        {/* Isolated: just the pills */}
        <div>
          <h2 className="text-lg mb-4 text-muted">Isolated — all states</h2>
          <div className="space-y-4">
            <div>
              <p className="text-xs text-muted mb-2">Off (available, not active)</p>
              <TopicBar
                topics={[
                  { name: "alpha-app", state: "off" },
                  { name: "intake", state: "off" },
                  { name: "cortex", state: "off" },
                ]}
                onToggle={() => {}}
              />
            </div>
            <div>
              <p className="text-xs text-muted mb-2">Armed (will inject on next send)</p>
              <TopicBar
                topics={[
                  { name: "alpha-app", state: "armed" },
                  { name: "intake", state: "armed" },
                  { name: "cortex", state: "armed" },
                ]}
                onToggle={() => {}}
              />
            </div>
            <div>
              <p className="text-xs text-muted mb-2">On (context injected, active)</p>
              <TopicBar
                topics={[
                  { name: "alpha-app", state: "on" },
                  { name: "intake", state: "on" },
                  { name: "cortex", state: "on" },
                ]}
                onToggle={() => {}}
              />
            </div>
            <div>
              <p className="text-xs text-muted mb-2">Mixed (realistic scenario)</p>
              <TopicBar topics={mockTopics} onToggle={() => {}} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
