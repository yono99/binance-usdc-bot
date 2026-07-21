import { GeminiUsage } from "../components/GeminiUsage";
import { HistoryPanels } from "../components/HistoryPanels";
import { TradeHistory } from "../components/TradeHistory";
import { useDashboard } from "../layout/DashboardContext";

export function HistoryPage() {
  const { tick } = useDashboard();
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>History</h1>
          <p>Trade log, screening, news veto, token Gemini.</p>
        </div>
      </div>
      <TradeHistory tick={tick} />
      <HistoryPanels />
      <GeminiUsage />
    </div>
  );
}
