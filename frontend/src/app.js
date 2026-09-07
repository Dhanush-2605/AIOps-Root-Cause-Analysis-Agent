import React, { useState, useEffect } from "react";
import { AlertTriangle, Activity, CheckCircle, Terminal, MessageSquare, Play, XOctagon, Bug } from 'lucide-react';

export default function AIOpsDashboard() {
  const [incidents, setIncidents] = useState([]);
  const [selectedThread, setSelectedThread] = useState(null);
  const [feedbackInput, setFeedbackInput] = useState("");
const [isSimulating, setIsSimulating] = useState(false);
  // Polling backend for active incidents and their LangGraph state
useEffect(() => {
    const fetchIncidents = async () => {
      try {
        // Tip: If localhost fails, try 'http://127.0.0.1:8000/incidents'
        const response = await fetch('http://localhost:8080/incidents');
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        setIncidents(data.active_threads || []);
      } catch (error) {
        console.warn("Backend not reachable yet. Retrying in 5s...", error.message);
        // We don't crash the app, we just wait for the next interval
      }
    };
    
    fetchIncidents();
    const interval = setInterval(fetchIncidents, 5000);
    return () => clearInterval(interval);
  }, []);

  // Handle the HITL Decision

  const triggerMockAlert = async () => {
    setIsSimulating(true);

    // The exact JSON payload you provided
    const mockPayload = {
      receiver: "ai-remediation-agent",
      status: "firing",
      alerts: [
        {
          status: "firing",
          labels: {
            alertname: "JavaHeapSpaceOOMError",
            severity: "critical",
            namespace: "default",
            deployment: "logger-error",
          },
          annotations: {
            summary: "Logger deployment is crashing or restarting",
            description:
              "The deployment default/logger-error is experiencing container restarts due to OutOfMemory Java heap space errors.",
          },
          startsAt: new Date().toISOString(), // Dynamic timestamp so it looks fresh
        },
      ],
    };

    try {
      await fetch("http://localhost:8080/alert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(mockPayload),
      });
      console.log("Mock alert fired successfully!");
    } catch (error) {
      console.error("Failed to fire mock alert", error);
    } finally {
      setIsSimulating(false);
    }
  };

  const submitHITLDecision = async (decision) => {
    if (!selectedThread) return;

    const payload = {
      thread_id: selectedThread.thread_id,
      decision: decision,
      feedback: decision === "feedback" ? feedbackInput : "",
    };

    try {
      await fetch("http://localhost:8000/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      setFeedbackInput("");
      // Optimistically update UI to show it's executing
      setSelectedThread({ ...selectedThread, status: "executing" });
    } catch (error) {
      console.error("Failed to submit HITL decision", error);
    }
  };

  return (
    <div className="flex h-screen bg-gray-900 text-gray-100 font-sans">
      {/* LEFT PANE: Incident Queue */}
      <div className="w-1/3 border-r border-gray-700 bg-gray-800 flex flex-col">
        <div className="p-4 border-b border-gray-700">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-bold flex items-center">
              <Activity className="mr-2 text-blue-400" /> AIOps Command Center
            </h2>

            {/* 2. The Simulate Alert Button */}
            <button
              onClick={triggerMockAlert}
              disabled={isSimulating}
              className="bg-gray-700 hover:bg-gray-600 text-xs px-2 py-1 rounded flex items-center transition-colors disabled:opacity-50"
              title="Fire mock Prometheus payload"
            >
              <Bug className="h-3 w-3 mr-1 text-purple-400" />
              {isSimulating ? "Sending..." : "Test Alert"}
            </button>
          </div>

          <div className="flex gap-4 mt-4 text-sm">
            <span className="text-yellow-400">
              ● Paused (HITL):{" "}
              {incidents.filter((i) => i.status === "paused").length}
            </span>
            <span className="text-blue-400">
              ● Executing:{" "}
              {incidents.filter((i) => i.status === "executing").length}
            </span>
          </div>
        </div>

        <div className="overflow-y-auto flex-1 p-2 space-y-2">
          {incidents.map((incident) => (
            <div
              key={incident.thread_id}
              onClick={() => setSelectedThread(incident)}
              className={`p-3 rounded cursor-pointer border ${selectedThread?.thread_id === incident.thread_id ? "border-blue-500 bg-gray-700" : "border-gray-600 hover:bg-gray-750"}`}
            >
              <div className="flex justify-between items-center mb-1">
                <span className="font-semibold text-red-400">
                  {incident.alert_name}
                </span>
                {incident.status === "paused" && (
                  <span className="px-2 py-1 text-xs bg-yellow-600 text-white rounded animate-pulse">
                    Action Required
                  </span>
                )}
              </div>
              <p className="text-sm text-gray-400">
                Target: {incident.target_resource}
              </p>
            </div>
          ))}
        </div>
      </div>

      {/* RIGHT PANE: Agent Trace & HITL Controls */}
      <div className="flex-1 flex flex-col">
        {selectedThread ? (
          <>
            {/* Header */}
            <div className="p-4 border-b border-gray-700 bg-gray-800 flex justify-between items-center">
              <h3 className="font-bold text-xl">
                Incident Trace: {selectedThread.alert_name}
              </h3>
              <span className="text-xs text-gray-500">
                Thread: {selectedThread.thread_id}
              </span>
            </div>

            {/* Step-by-Step Logs (The Agent's "Brain") */}
            <div className="flex-1 p-4 overflow-y-auto bg-black font-mono text-sm space-y-3">
              {selectedThread.logs.map((log, index) => (
                <div key={index} className="text-gray-300">
                  <span className="text-green-400 mr-2">[{log.timestamp}]</span>
                  <span className="text-blue-300">[{log.node_name}]</span>{" "}
                  {log.message}
                </div>
              ))}

              {/* HITL Interruption Banner */}
              {selectedThread.status === "paused" && (
                <div className="mt-4 p-4 border border-yellow-500 bg-yellow-900/30 rounded">
                  <div className="text-yellow-400 font-bold mb-2 flex items-center">
                    <AlertTriangle className="mr-2 h-5 w-5" />
                    AGENT PAUSED: HUMAN APPROVAL REQUIRED
                  </div>
                  <pre className="whitespace-pre-wrap text-gray-300 mt-2">
                    {selectedThread.proposed_plan_summary}
                  </pre>
                </div>
              )}
            </div>

            {/* HITL Control Panel */}
            {selectedThread.status === "paused" && (
              <div className="p-4 border-t border-gray-700 bg-gray-800">
                <p className="text-sm text-gray-400 mb-3">
                  How do you want to proceed with this remediation plan?
                </p>

                <div className="flex gap-3 mb-3">
                  <button
                    onClick={() => submitHITLDecision("approve")}
                    className="flex-1 bg-green-600 hover:bg-green-700 text-white py-2 px-4 rounded flex justify-center items-center font-semibold transition-colors"
                  >
                    <Play className="mr-2 h-4 w-4" /> Approve & Execute
                  </button>
                  <button
                    onClick={() => submitHITLDecision("abort")}
                    className="flex-1 bg-red-600 hover:bg-red-700 text-white py-2 px-4 rounded flex justify-center items-center font-semibold transition-colors"
                  >
                    <XOctagon className="mr-2 h-4 w-4" /> Abort Plan
                  </button>
                </div>

                <div className="flex gap-2">
                  <input
                    type="text"
                    value={feedbackInput}
                    onChange={(e) => setFeedbackInput(e.target.value)}
                    placeholder="Provide feedback to regenerate plan (e.g., 'Do not restart, scale instead')"
                    className="flex-1 bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white focus:outline-none focus:border-blue-500"
                  />
                  <button
                    onClick={() => submitHITLDecision("feedback")}
                    disabled={!feedbackInput.trim()}
                    className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white py-2 px-4 rounded flex justify-center items-center font-semibold transition-colors"
                  >
                    <MessageSquare className="mr-2 h-4 w-4" /> Send Feedback
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="flex-1 flex flex-col justify-center items-center text-gray-500">
            <Terminal className="h-16 w-16 mb-4 opacity-50" />
            <p>Select an incident from the queue to view execution trace.</p>
          </div>
        )}
      </div>
    </div>
  );
}
