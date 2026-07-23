import { AgentControls } from "../components/AgentControls";
import { ControlPanel } from "../components/ControlPanel";
import { GeminiTraderPanel } from "../components/GeminiTraderPanel";
import { useDashboard } from "../layout/DashboardContext";

export function SettingsPage() {
  const { status, available, account } = useDashboard();
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>
            Setting pribadi per-mode · setting server bersama (1× untuk dry/test/live)
            · agent — hot-reload tiap siklus bot.
          </p>
        </div>
      </div>
      <ControlPanel status={status} available={available} account={account} />
      <AgentControls compact />
      <GeminiTraderPanel />
    </div>
  );
}
