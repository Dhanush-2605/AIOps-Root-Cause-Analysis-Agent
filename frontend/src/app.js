import React, { useState, useEffect, useRef, useMemo } from "react";
import {
  Activity,
  Terminal,
  MessageSquare,
  Play,
  XOctagon,
  Search,
  ShieldCheck,
  ChevronDown,
  Zap,
  Lock,
  Gauge,
  Droplets,
  Bell,
  Workflow,
  Archive,
  CheckCircle2,
  ArrowUpRight,
  FileText,
  GitPullRequest,
} from "lucide-react";

// The Node.js backend under test — NOT the Python agent (that's :8080,
// used only for /alert, /alerts, /incidents, /approve below).
// Both backends are reached through nginx as relative paths, not absolute
// localhost URLs — the browser loads this dashboard from wherever it's
// deployed (e.g. http://20.204.243.24), and "localhost" there means the
// visitor's own machine, not the cluster. nginx forwards /api/* to the
// Node.js backend as-is (routes already start with /api/...), and /agent/*
// to the Python agent with that prefix stripped (see nginx.conf).
const NODE_BASE_URL = ""; // scenario run() calls already include the /api/... prefix
const AGENT_BASE_URL = "/agent";

// Fires `count` concurrent POSTs without waiting for each one sequentially —
// needed to actually push memory/cache metrics far enough to cross a
// Prometheus threshold within a normal scrape interval.
async function fireBurst(url, count, bodyFn) {
  const requests = Array.from({ length: count }, (_, i) =>
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyFn ? bodyFn(i) : {}),
    }).catch(() => {})
  );
  await Promise.allSettled(requests);
}

// ==========================================
// Scenario definitions — each `run()` calls the REAL Node.js chaos endpoint
// so the actual condition happens (request really hangs, row is really
// locked, cache/leak really grows). Nothing here talks to the Python agent
// directly — Prometheus discovers the problem on its own, same as prod.
// `expectedAlertName` is only used to color-match once the real alert
// shows up in the Alerts tab, not to fire anything itself.
// ==========================================
const SCENARIOS = [
  {
    id: "hanging-request",
    label: "Hanging 3rd-Party Request",
    description: "Payment call exceeds 30s, never returns",
    icon: Zap,
    accent: "amber",
    expectedAlertName: "HangingRequestsDetected",
    run: async () => {
      // Fire-and-forget: this call genuinely takes ~60s to resolve. We
      // don't await it so the UI isn't blocked, but the request really is
      // sitting there in Node's activeRequests map for Prometheus to see.
      fetch(`${NODE_BASE_URL}/api/payments/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }).catch(() => {});
    },
  },
  {
    id: "blocking-transaction",
    label: "Blocking DB Transaction",
    description: "Row lock held open, checkout stalls behind it",
    icon: Lock,
    accent: "violet",
    expectedAlertName: "DatabaseLockContentionDetected",
    run: async () => {
      // Must complete first — this is the call that actually acquires and
      // holds the lock (its transaction is left open on purpose).
      await fetch(`${NODE_BASE_URL}/api/orders/lock-row`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: 1 }),
      }).catch(() => {});
      // This one will genuinely block behind the lock above — don't await it.
      fetch(`${NODE_BASE_URL}/api/orders/checkout-slow`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: 1 }),
      }).catch(() => {});
    },
  },
  {
    id: "memory-genuine",
    label: "Genuine Memory Demand",
    description: "Cache growing with load, expected to plateau",
    icon: Gauge,
    accent: "teal",
    expectedAlertName: "PodMemoryNearLimit",
    run: async () => {
      // Real burst of traffic against the bounded cache — memory actually
      // rises here, and will actually plateau/drain, unlike a faked metric.
      await fireBurst(`${NODE_BASE_URL}/api/catalog/lookup`, 400, (i) => ({
        itemId: i % 4000,
      }));
    },
  },
  {
    id: "memory-leak",
    label: "Memory Leak",
    description: "Unbounded growth, load-independent, no plateau",
    icon: Droplets,
    accent: "rose",
    expectedAlertName: "PodMemoryNearLimit",
    run: async () => {
      await fireBurst(`${NODE_BASE_URL}/api/reports/export`, 400, (i) => ({
        reportId: i,
      }));
    },
  },
];

const ACCENTS = {
  amber: { text: "text-amber-400", bg: "bg-amber-500/10", dot: "bg-amber-400" },
  violet: { text: "text-violet-400", bg: "bg-violet-500/10", dot: "bg-violet-400" },
  teal: { text: "text-teal-400", bg: "bg-teal-500/10", dot: "bg-teal-400" },
  rose: { text: "text-rose-400", bg: "bg-rose-500/10", dot: "bg-rose-400" },
  slate: { text: "text-gray-400", bg: "bg-gray-700/40", dot: "bg-gray-500" },
};

function accentForAlert(alertName) {
  const match = SCENARIOS.find((s) => s.expectedAlertName === alertName);
  return match ? ACCENTS[match.accent] : ACCENTS.slate;
}

function relativeTime(iso) {
  if (!iso) return "";
  const diffSec = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

const RESOLVED_STATUSES = new Set(["resolved", "aborted", "failed"]);

const TABS = [
  { id: "alerts", label: "Alerts", icon: Bell },
  { id: "investigations", label: "Investigations", icon: Workflow },
  { id: "history", label: "History", icon: Archive },
];

export default function AIOpsDashboard() {
  const [incidents, setIncidents] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [selectedThread, setSelectedThread] = useState(null);
  const [feedbackInput, setFeedbackInput] = useState("");
  const [pendingScenarioId, setPendingScenarioId] = useState(null);
  // Tracks the most recently fired scenario so the Alerts tab can show a
  // "waiting for Prometheus to notice" banner until the real alert lands.
  const [pendingScenario, setPendingScenario] = useState(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [activeTab, setActiveTab] = useState("alerts");
  const pickerRef = useRef(null);

  useEffect(() => {
    const fetchIncidents = async () => {
      try {
        const response = await fetch(`${AGENT_BASE_URL}/incidents`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        setIncidents(data.active_threads || []);
      } catch (error) {
        console.warn("Incidents endpoint not reachable yet. Retrying in 5s...", error.message);
      }
    };
    // Raw Alertmanager feed — separate from agent thread state, so an alert
    // shows up here the instant it fires, even before (or without) an
    // investigation being spun up for it.
    const fetchAlerts = async () => {
      try {
        const response = await fetch(`${AGENT_BASE_URL}/alerts`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        setAlerts(data.alerts || []);
      } catch (error) {
        console.warn("Alerts endpoint not reachable yet. Retrying in 5s...", error.message);
      }
    };
    fetchIncidents();
    fetchAlerts();
    const interval = setInterval(() => {
      fetchIncidents();
      fetchAlerts();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Once the real alert we were waiting on actually shows up in the feed,
  // the "waiting for Prometheus" banner has done its job — drop it.
  useEffect(() => {
    if (!pendingScenario) return;
    const matched = alerts.some(
      (a) => a.alertname === pendingScenario.expectedAlertName && new Date(a.startsAt).getTime() >= pendingScenario.firedAt - 5000
    );
    if (matched) setPendingScenario(null);
  }, [alerts, pendingScenario]);

  useEffect(() => {
    function handleClickOutside(e) {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) setPickerOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const activeInvestigations = useMemo(
    () => incidents.filter((i) => !RESOLVED_STATUSES.has(i.status)),
    [incidents]
  );
  const historyIncidents = useMemo(
    () => incidents.filter((i) => RESOLVED_STATUSES.has(i.status)),
    [incidents]
  );
  const firingAlerts = useMemo(() => alerts.filter((a) => a.status === "firing"), [alerts]);

  const threadForAlert = (alert) =>
    incidents.find((i) => i.alert_name === alert.alertname && i.target_resource === alert.pod);

  const goToThread = (thread) => {
    setSelectedThread(thread);
    setActiveTab(RESOLVED_STATUSES.has(thread.status) ? "history" : "investigations");
  };

  const triggerScenario = async (scenario) => {
    setPickerOpen(false);
    setPendingScenarioId(scenario.id);
    setActiveTab("alerts");
    try {
      // Hits the real Node.js endpoint(s) — the condition actually happens
      // (request hangs, lock is held, memory grows). No alert is posted
      // from here; Prometheus scrapes /metrics on its own schedule and
      // Alertmanager fires HangingRequestsDetected / DatabaseLockContentionDetected /
      // PodMemoryNearLimit for real once the threshold is genuinely crossed.
      await scenario.run();
      const fired = { label: scenario.label, expectedAlertName: scenario.expectedAlertName, firedAt: Date.now() };
      setPendingScenario(fired);
      // Clear the "waiting" banner after a while regardless — by then either
      // the real alert has shown up in the feed, or the scrape/`for:` window
      // has simply passed without crossing threshold.
      setTimeout(() => {
        setPendingScenario((p) => (p && p.firedAt === fired.firedAt ? null : p));
      }, 60000);
    } catch (error) {
      console.error("Failed to trigger scenario against backend", error);
    } finally {
      setPendingScenarioId(null);
    }
  };

  const submitHITLDecision = async (decision) => {
    if (!selectedThread) return;
    const payload = {
      thread_id: selectedThread.thread_id,
      decision,
      feedback: decision === "feedback" ? feedbackInput : "",
    };
    try {
      await fetch(`${AGENT_BASE_URL}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setFeedbackInput("");
      setSelectedThread({ ...selectedThread, status: "executing" });
    } catch (error) {
      console.error("Failed to submit HITL decision", error);
    }
  };

  return (
    <div className="flex h-screen bg-gray-900 text-gray-100 font-sans">
      {/* LEFT PANE */}
      <div className="w-1/3 border-r border-gray-700 bg-gray-800 flex flex-col">
        <div className="p-4 border-b border-gray-700">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-bold flex items-center">
              <Activity className="mr-2 h-5 w-5 text-blue-400" /> AIOps Command Center
            </h2>

            <div className="relative" ref={pickerRef}>
              <button
                onClick={() => setPickerOpen((v) => !v)}
                className="bg-gray-700 hover:bg-gray-600 text-xs px-3 py-1.5 rounded flex items-center gap-1.5 transition-colors"
                title="Simulate a chaos scenario"
              >
                Simulate Incident
                <ChevronDown className={`h-3 w-3 transition-transform ${pickerOpen ? "rotate-180" : ""}`} />
              </button>

              {pickerOpen && (
                <div className="absolute right-0 mt-2 w-72 bg-gray-800 border border-gray-700 rounded-lg shadow-2xl z-20 overflow-hidden">
                  {SCENARIOS.map((scenario) => {
                    const accent = ACCENTS[scenario.accent];
                    const Icon = scenario.icon;
                    const isPending = pendingScenarioId === scenario.id;
                    return (
                      <button
                        key={scenario.id}
                        onClick={() => triggerScenario(scenario)}
                        disabled={pendingScenarioId !== null}
                        className="w-full text-left px-3 py-2.5 flex items-start gap-3 hover:bg-gray-700/60 transition-colors disabled:opacity-50 border-b border-gray-700/60 last:border-b-0"
                      >
                        <span className={`mt-0.5 p-1.5 rounded ${accent.bg}`}>
                          <Icon className={`h-3.5 w-3.5 ${accent.text}`} />
                        </span>
                        <span className="flex-1 min-w-0">
                          <span className="block text-sm font-medium text-gray-100">
                            {isPending ? "Firing..." : scenario.label}
                          </span>
                          <span className="block text-xs text-gray-400 mt-0.5">{scenario.description}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Segmented tab control — Alerts / Investigations / History */}
          <div className="flex gap-1 mt-4 bg-gray-900/60 p-1 rounded-lg">
            {TABS.map((tab) => {
              const Icon = tab.icon;
              const count =
                tab.id === "alerts"
                  ? firingAlerts.length
                  : tab.id === "investigations"
                  ? activeInvestigations.length
                  : historyIncidents.length;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex-1 flex items-center justify-center gap-1.5 text-xs font-medium py-1.5 rounded-md transition-colors ${
                    isActive ? "bg-gray-700 text-white shadow" : "text-gray-400 hover:text-gray-200"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {tab.label}
                  {count > 0 && (
                    <span
                      className={`ml-0.5 px-1.5 rounded-full text-[10px] leading-4 ${
                        tab.id === "alerts" ? "bg-rose-500/80 text-white" : "bg-gray-600 text-gray-200"
                      }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        <div className="overflow-y-auto flex-1 p-2 space-y-2">
          {/* ALERTS TAB — raw feed, independent of agent state */}
          {activeTab === "alerts" && pendingScenario && (
            <div className="p-3 rounded border border-dashed border-gray-600 bg-gray-900/60 flex items-center gap-2.5">
              <span className="h-2 w-2 rounded-full bg-gray-400 animate-ping shrink-0" />
              <p className="text-xs text-gray-400">
                <span className="text-gray-200 font-medium">{pendingScenario.label}</span> is running against the
                backend — waiting for Prometheus to scrape and Alertmanager to fire{" "}
                <span className="font-mono text-gray-300">{pendingScenario.expectedAlertName}</span>.
              </p>
            </div>
          )}
          {activeTab === "alerts" &&
            (alerts.length === 0 ? (
              <EmptyState text="No alerts received yet. Simulate one above to see it land here." />
            ) : (
              alerts.map((alert) => {
                const accent = accentForAlert(alert.alertname);
                const thread = threadForAlert(alert);
                return (
                  <div
                    key={alert.fingerprint || `${alert.alertname}-${alert.startsAt}`}
                    className="p-3 rounded border border-gray-600 bg-gray-900/40"
                  >
                    <div className="flex justify-between items-start gap-2">
                      <span className="flex items-center gap-2 min-w-0">
                        <span
                          className={`h-1.5 w-1.5 rounded-full shrink-0 ${accent.dot} ${
                            alert.status === "firing" ? "animate-pulse" : ""
                          }`}
                        />
                        <span className="font-semibold text-gray-100 truncate text-sm">{alert.alertname}</span>
                      </span>
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded-full shrink-0 uppercase tracking-wide ${
                          alert.status === "firing" ? "bg-rose-500/20 text-rose-300" : "bg-gray-700 text-gray-400"
                        }`}
                      >
                        {alert.status}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 pl-3.5 mt-1">{alert.pod || alert.namespace}</p>
                    <div className="flex justify-between items-center mt-2 pl-3.5">
                      <span className="text-[11px] text-gray-500">{relativeTime(alert.startsAt)}</span>
                      {thread ? (
                        <button
                          onClick={() => goToThread(thread)}
                          className="text-[11px] text-blue-400 hover:text-blue-300 flex items-center gap-1"
                        >
                          View investigation <ArrowUpRight className="h-3 w-3" />
                        </button>
                      ) : (
                        <span className="text-[11px] text-gray-600 italic">Awaiting pickup</span>
                      )}
                    </div>
                  </div>
                );
              })
            ))}

          {/* INVESTIGATIONS TAB — the agentic flow, in progress only */}
          {activeTab === "investigations" &&
            (activeInvestigations.length === 0 ? (
              <EmptyState text="No investigations in progress." />
            ) : (
              activeInvestigations.map((incident) => {
                const accent = accentForAlert(incident.alert_name);
                const isSelected = selectedThread?.thread_id === incident.thread_id;
                return (
                  <div
                    key={incident.thread_id}
                    onClick={() => setSelectedThread(incident)}
                    className={`p-3 rounded cursor-pointer border transition-colors ${
                      isSelected ? "border-blue-500 bg-gray-700" : "border-gray-600 hover:bg-gray-750"
                    }`}
                  >
                    <div className="flex justify-between items-center mb-1 gap-2">
                      <span className="flex items-center gap-2 min-w-0">
                        <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${accent.dot}`} />
                        <span className="font-semibold text-gray-100 truncate">{incident.alert_name}</span>
                      </span>
                      {incident.status === "paused" && (
                        <span className="px-2 py-1 text-xs bg-yellow-600 text-white rounded animate-pulse shrink-0">
                          Action Required
                        </span>
                      )}
                      {incident.status === "executing" && (
                        <span className="px-2 py-1 text-xs bg-blue-600 text-white rounded shrink-0">Executing</span>
                      )}
                    </div>
                    <p className="text-sm text-gray-400 pl-3.5">Target: {incident.target_resource}</p>
                  </div>
                );
              })
            ))}

          {/* HISTORY TAB — closed incidents with post-mortem/PR links */}
          {activeTab === "history" &&
            (historyIncidents.length === 0 ? (
              <EmptyState text="No resolved incidents yet." />
            ) : (
              historyIncidents.map((incident) => {
                const accent = accentForAlert(incident.alert_name);
                const isSelected = selectedThread?.thread_id === incident.thread_id;
                const wasResolved = incident.status === "resolved" && incident.verification_passed;
                return (
                  <div
                    key={incident.thread_id}
                    onClick={() => setSelectedThread(incident)}
                    className={`p-3 rounded cursor-pointer border transition-colors ${
                      isSelected ? "border-blue-500 bg-gray-700" : "border-gray-600 hover:bg-gray-750"
                    }`}
                  >
                    <div className="flex justify-between items-center mb-1 gap-2">
                      <span className="flex items-center gap-2 min-w-0">
                        <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${accent.dot}`} />
                        <span className="font-semibold text-gray-100 truncate">{incident.alert_name}</span>
                      </span>
                      {wasResolved ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                      ) : (
                        <XOctagon className="h-3.5 w-3.5 text-gray-500 shrink-0" />
                      )}
                    </div>
                    <p className="text-sm text-gray-400 pl-3.5">Target: {incident.target_resource}</p>
                    <div className="flex items-center gap-3 mt-2 pl-3.5">
                      {incident.post_mortem_md && (
                        <span className="text-[11px] text-gray-500 flex items-center gap-1">
                          <FileText className="h-3 w-3" /> Post-mortem
                        </span>
                      )}
                      {incident.github_pr_url && (
                        <a
                          href={incident.github_pr_url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="text-[11px] text-blue-400 hover:text-blue-300 flex items-center gap-1"
                        >
                          <GitPullRequest className="h-3 w-3" /> PR
                        </a>
                      )}
                    </div>
                  </div>
                );
              })
            ))}
        </div>
      </div>

      {/* RIGHT PANE: Agent Trace & HITL Controls */}
      <div className="flex-1 flex flex-col bg-gray-900">
        {selectedThread ? (
          <>
            <div className="p-4 border-b border-gray-700 bg-gray-800 flex justify-between items-center">
              <h3 className="font-bold text-xl text-white">Incident Trace: {selectedThread.alert_name}</h3>
              <span className="text-xs text-gray-500 font-mono">Thread: {selectedThread.thread_id}</span>
            </div>

            <div className="flex-1 p-4 overflow-y-auto font-mono text-sm space-y-4 bg-black">
              {selectedThread.context_gathered && (
                <div className="border border-gray-700 bg-gray-900 rounded p-3 shadow-md">
                  <h4 className="text-blue-400 font-bold mb-2 flex items-center text-xs">
                    <Search className="mr-2 h-4 w-4" /> Agent Investigation Findings
                  </h4>
                  <pre className="whitespace-pre-wrap text-gray-300 text-xs border-l-2 border-blue-500 pl-3">
                    {selectedThread.context_gathered}
                  </pre>
                </div>
              )}

              {selectedThread.detailed_plan && selectedThread.detailed_plan.length > 0 && (
                <div className="border border-indigo-700 bg-indigo-900/20 rounded p-3 mt-4 shadow-md">
                  <h4 className="text-indigo-400 font-bold mb-3 flex items-center text-xs">
                    <ShieldCheck className="mr-2 h-4 w-4" /> Proposed Remediation Plan
                  </h4>
                  <div className="space-y-3">
                    {selectedThread.detailed_plan.map((action, idx) => (
                      <div key={idx} className="bg-gray-800 p-3 rounded border border-gray-700 shadow-sm">
                        <div className="font-bold text-red-400 text-sm">
                          <span className="text-gray-400">Action:</span> {action.action}{" "}
                          <span className="text-gray-400 font-normal">on</span> {action.target}
                        </div>
                        <div className="text-gray-300 text-xs mt-2 italic bg-gray-900 p-2 rounded">
                          "{action.reasoning}"
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selectedThread.post_mortem_md && (
                <div className="border border-emerald-700 bg-emerald-900/10 rounded p-3 mt-4 shadow-md">
                  <h4 className="text-emerald-400 font-bold mb-2 flex items-center text-xs">
                    <FileText className="mr-2 h-4 w-4" /> Post-Mortem
                  </h4>
                  <pre className="whitespace-pre-wrap text-gray-300 text-xs">{selectedThread.post_mortem_md}</pre>
                </div>
              )}

              <div className="space-y-2 mt-6 p-2 rounded">
                {selectedThread.logs?.map((log, index) => (
                  <div key={index} className="text-gray-400">
                    <span className="text-green-500 mr-2">[{log.timestamp}]</span>
                    <span className="text-blue-400 mr-2">[{log.node_name}]</span>
                    <span className="text-gray-300">{log.message}</span>
                  </div>
                ))}
              </div>
            </div>

            {selectedThread.status === "paused" && (
              <div className="p-4 border-t border-gray-700 bg-gray-800 shadow-xl z-10">
                <p className="text-sm text-gray-300 mb-3 font-semibold">Do you approve this remediation plan?</p>
                <div className="flex gap-3 mb-3">
                  <button
                    onClick={() => submitHITLDecision("approve")}
                    className="flex-1 bg-green-600 hover:bg-green-700 text-white py-2 px-4 rounded flex justify-center items-center font-bold transition-colors shadow-lg"
                  >
                    <Play className="mr-2 h-4 w-4" /> Approve Execution
                  </button>
                  <button
                    onClick={() => submitHITLDecision("abort")}
                    className="flex-1 bg-red-600 hover:bg-red-700 text-white py-2 px-4 rounded flex justify-center items-center font-bold transition-colors shadow-lg"
                  >
                    <XOctagon className="mr-2 h-4 w-4" /> Abort Plan
                  </button>
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={feedbackInput}
                    onChange={(e) => setFeedbackInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && feedbackInput.trim()) submitHITLDecision("feedback");
                    }}
                    placeholder="Or instruct the agent to change the plan (e.g., 'Don't delete pod, just increase memory')"
                    className="flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500 text-sm"
                  />
                  <button
                    onClick={() => submitHITLDecision("feedback")}
                    disabled={!feedbackInput.trim()}
                    className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white py-2 px-4 rounded flex justify-center items-center font-semibold transition-colors shadow-lg"
                  >
                    <MessageSquare className="mr-2 h-4 w-4" /> Request Re-plan
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="flex-1 flex flex-col justify-center items-center text-gray-500 bg-gray-900">
            <Terminal className="h-16 w-16 mb-4 opacity-30" />
            <p>Select an alert or investigation from the left to view its trace.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState({ text }) {
  return <div className="text-center text-gray-500 text-sm mt-12 px-4">{text}</div>;
}
