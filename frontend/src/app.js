import React, { useState, useEffect, useRef } from 'react';
import { 
  Database, Server, Activity, Bot, Zap, 
  ArrowRight, GitMerge, Terminal, ShieldCheck, 
  AlertTriangle, CheckCircle2 ,Layers
} from 'lucide-react';

export default function App() {
  const [queueDepth, setQueueDepth] = useState(0);
  const [spikeCount, setSpikeCount] = useState(5000);
  const [isSpiking, setIsSpiking] = useState(false);
  const [logs, setLogs] = useState([]);
  const [dbCpu, setDbCpu] = useState(12);
  const logsEndRef = useRef(null);

  // Auto-scroll logs
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  useEffect(() => {
    let lastDepth = 0;

    const fetchMetrics = () => {
      fetch('/api/metrics')
        .then((res) => res.json())
        .then((data) => {
          const currentDepth = data.queueDepth || 0;
          setQueueDepth(currentDepth);

          // Simulate Telemetry Logs based on queue movement
          if (currentDepth > 0 && currentDepth < lastDepth) {
            // Queue is draining - simulate backend worker activity
            const processed = lastDepth - currentDepth;
            const newLogs = [];
            
            newLogs.push(`[Worker] Pulled batch of ${processed} records from Redis`);
            newLogs.push(`[Pgpool-II] Parsing SQL: Detected INSERT operation`);
            newLogs.push(`[Pgpool-II] Routing WRITE -> postgres-0 (Master)`);
            
            // Simulate random read traffic happening at the same time
            if (Math.random() > 0.5) {
              const readNode = Math.random() > 0.5 ? 'postgres-0' : 'postgres-1';
              newLogs.push(`[Pgpool-II] Parsing SQL: Detected SELECT operation`);
              newLogs.push(`[Pgpool-II] Load Balancing READ -> ${readNode}`);
            }

            setLogs(prev => [...prev, ...newLogs].slice(-50)); // Keep last 50 logs
            setDbCpu(Math.min(98, dbCpu + Math.random() * 15)); // CPU spikes during writes
          } else if (currentDepth === 0 && lastDepth > 0) {
            setLogs(prev => [...prev, `[System] Queue empty. System returning to idle state.`].slice(-50));
          }

          // Cool down CPU when idle
          if (currentDepth === 0) {
            setDbCpu(prev => Math.max(12, prev - Math.random() * 5));
          }

          lastDepth = currentDepth;
        })
        .catch((err) => console.error('Error fetching metrics:', err));
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 1000);
    return () => clearInterval(interval);
  }, []);

  const handleSpike = (e) => {
    e.preventDefault();
    if (spikeCount <= 0) return;

    setIsSpiking(true);
    setLogs(prev => [...prev, `[API] INGRESS SPIKE DETECTED: ${spikeCount} requests received!`]);
    setLogs(prev => [...prev, `[Redis] Buffering ${spikeCount} payloads to prevent database crash.`]);
    
    fetch('/api/load-test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count: Number(spikeCount) }),
    })
      .then((res) => res.json())
      .then((data) => {
        setIsSpiking(false);
      })
      .catch((err) => {
        console.error('Error triggering spike:', err);
        setIsSpiking(false);
      });
  };

  const isWarning = queueDepth > 2000;
  const isCritical = queueDepth > 4000;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 p-4 md:p-8 font-sans selection:bg-blue-500/30">
      
      {/* Header */}
      <header className="mb-8 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <Activity className="w-8 h-8 text-blue-400" />
            <h1 className="text-3xl font-bold text-white tracking-tight">AIOps Command Center</h1>
          </div>
          <p className="text-slate-400">Kubernetes High-Availability Data Tier & Telemetry Dashboard</p>
        </div>
        <div className="hidden md:flex items-center gap-4 bg-slate-900 border border-slate-800 rounded-lg p-3">
          <div className="flex flex-col">
            <span className="text-xs text-slate-500 uppercase tracking-wider">Cluster Status</span>
            <span className="flex items-center gap-2 text-sm font-medium text-emerald-400">
              <CheckCircle2 className="w-4 h-4" /> Healthy
            </span>
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Left Column: Controls & AI Bay */}
        <div className="space-y-6 lg:col-span-1">
          
          {/* Load Generator Card */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl relative overflow-hidden">
            <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-blue-500 to-indigo-500"></div>
            <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Zap className="w-5 h-5 text-amber-400" /> Traffic Generator
            </h2>
            
            <form onSubmit={handleSpike} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">
                  Simulate API Requests
                </label>
                <input
                  type="number"
                  value={spikeCount}
                  onChange={(e) => setSpikeCount(e.target.value)}
                  min="100"
                  max="10000"
                  disabled={isSpiking}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all disabled:opacity-50"
                />
              </div>
              <button 
                type="submit" 
                disabled={isSpiking || queueDepth > 0}
                className="w-full bg-blue-600 hover:bg-blue-500 text-white font-semibold py-3 px-4 rounded-lg transition-colors flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {queueDepth > 0 ? 'Processing Queue...' : 'FIRE TRAFFIC SPIKE'}
              </button>
            </form>
          </div>

          {/* AI Agent Reserved Bay */}
          <div className="bg-slate-900/50 border border-indigo-500/30 rounded-xl p-6 shadow-xl relative">
            <div className="absolute top-0 left-0 w-full h-1 bg-indigo-500/50"></div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-indigo-300 flex items-center gap-2">
                <Bot className="w-5 h-5" /> Autonomous SRE Agent
              </h2>
              <span className="text-xs bg-indigo-500/20 text-indigo-300 px-2 py-1 rounded-full border border-indigo-500/30">
                Phase 3 Feature
              </span>
            </div>
            
            <div className="space-y-4">
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-400">Agent Status:</span>
                <span className="text-slate-500 flex items-center gap-1">
                  <div className="w-2 h-2 rounded-full bg-slate-500 animate-pulse"></div>
                  Sleeping (Awaiting thresholds)
                </span>
              </div>
              
              <div className="bg-slate-950 rounded-lg p-3 border border-slate-800">
                <h3 className="text-xs text-slate-500 uppercase tracking-wider mb-2">Monitored Telemetry</h3>
                <ul className="text-xs space-y-2 text-slate-400">
                  <li className="flex items-center gap-2"><CheckCircle2 className="w-3 h-3 text-emerald-500" /> Redis Queue Depth</li>
                  <li className="flex items-center gap-2"><CheckCircle2 className="w-3 h-3 text-emerald-500" /> PostgreSQL CPU Load</li>
                  <li className="flex items-center gap-2"><CheckCircle2 className="w-3 h-3 text-emerald-500" /> Pgpool Read/Write Ratio</li>
                </ul>
              </div>

              <div className="text-xs text-slate-500 italic text-center mt-2">
                Agent will wake up and inject diagnostic reports here when queue depth exceeds threshold.
              </div>
            </div>
          </div>

        </div>

        {/* Right Column: Topology & Logs */}
        <div className="space-y-6 lg:col-span-2">
          
          {/* Architecture Topology */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-white mb-6 flex items-center gap-2">
              <Layers className="w-5 h-5 text-emerald-400" /> Live Cluster Topology
            </h2>

            {/* Visual Flow Map */}
            <div className="relative">
              
              {/* Redis Shock Absorber */}
              <div className="flex flex-col items-center mb-8">
                <div className={`w-full max-w-md p-4 rounded-xl border-2 transition-colors duration-500 ${isCritical ? 'bg-red-950/40 border-red-500' : isWarning ? 'bg-amber-950/40 border-amber-500' : 'bg-slate-800/50 border-slate-700'}`}>
                  <div className="flex justify-between items-center mb-2">
                    <div className="flex items-center gap-2">
                      <Database className={`w-5 h-5 ${isCritical ? 'text-red-400' : 'text-blue-400'}`} />
                      <span className="font-semibold text-slate-200">Redis Shock Absorber</span>
                    </div>
                    <span className={`font-mono text-lg font-bold ${isCritical ? 'text-red-400' : 'text-slate-300'}`}>
                      {queueDepth.toLocaleString()} <span className="text-xs text-slate-500">pending</span>
                    </span>
                  </div>
                  {/* Progress Bar */}
                  <div className="w-full bg-slate-950 rounded-full h-2 mt-3 overflow-hidden">
                    <div 
                      className={`h-2 rounded-full transition-all duration-300 ${isCritical ? 'bg-red-500' : isWarning ? 'bg-amber-500' : 'bg-blue-500'}`}
                      style={{ width: `${Math.min((queueDepth / 10000) * 100, 100)}%` }}
                    ></div>
                  </div>
                </div>
                <ArrowRight className="w-6 h-6 text-slate-600 rotate-90 my-2" />
              </div>

              {/* Pgpool & Postgres */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-3xl mx-auto">
                
                {/* Traffic Cop */}
                <div className="flex flex-col items-center justify-center">
                  <div className="bg-slate-800 border border-slate-600 rounded-lg p-4 w-full text-center relative z-10">
                    <ShieldCheck className="w-6 h-6 text-emerald-400 mx-auto mb-2" />
                    <h3 className="font-semibold text-sm">Pgpool-II Proxy</h3>
                    <p className="text-[10px] text-slate-400 mt-1">Load Balancing: ON</p>
                    <p className="text-[10px] text-slate-400">R/W Splitting: ON</p>
                  </div>
                </div>

                {/* Arrows */}
                <div className="hidden md:flex flex-col justify-center relative">
                  {/* Top line to Master */}
                  <div className="absolute top-[25%] left-0 w-full h-[2px] bg-slate-700">
                    <div className="absolute top-[-8px] right-2 text-[10px] text-amber-400 font-mono">WRITE</div>
                  </div>
                  {/* Bottom line to Replica */}
                  <div className="absolute bottom-[25%] left-0 w-full h-[2px] bg-slate-700">
                    <div className="absolute top-[-8px] right-2 text-[10px] text-blue-400 font-mono">READ</div>
                  </div>
                </div>

                {/* Databases */}
                <div className="flex flex-col gap-4">
                  {/* Master */}
                  <div className="bg-slate-950 border border-amber-500/30 rounded-lg p-3 relative">
                    <div className="absolute -left-2 top-1/2 w-4 h-4 bg-amber-500/20 border border-amber-500/50 rounded-full animate-pulse"></div>
                    <div className="flex items-center gap-2 mb-1">
                      <Server className="w-4 h-4 text-amber-400" />
                      <span className="font-semibold text-sm text-slate-200">postgres-0</span>
                    </div>
                    <div className="flex justify-between items-end">
                      <div>
                        <span className="text-[10px] bg-amber-500/20 text-amber-300 px-2 py-0.5 rounded border border-amber-500/30">MASTER</span>
                      </div>
                      <div className="text-right">
                        <span className="text-[10px] text-slate-500 block">CPU Load</span>
                        <span className={`text-xs font-mono ${dbCpu > 80 ? 'text-red-400' : 'text-emerald-400'}`}>
                          {Math.round(dbCpu)}%
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Replication Link */}
                  <div className="flex items-center gap-2 pl-4">
                    <div className="w-1 h-8 bg-slate-700 rounded-full"></div>
                    <div className="text-[10px] text-slate-500 flex items-center gap-1">
                      <GitMerge className="w-3 h-3" /> Async Streaming Replication
                    </div>
                  </div>

                  {/* Replica */}
                  <div className="bg-slate-950 border border-blue-500/30 rounded-lg p-3 relative">
                    <div className="flex items-center gap-2 mb-1">
                      <Server className="w-4 h-4 text-blue-400" />
                      <span className="font-semibold text-sm text-slate-200">postgres-1</span>
                    </div>
                    <div>
                      <span className="text-[10px] bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded border border-blue-500/30">REPLICA (Read-Only)</span>
                    </div>
                  </div>
                </div>

              </div>
            </div>
          </div>

          {/* Live Telemetry Console */}
          <div className="bg-[#0A0A0A] border border-slate-800 rounded-xl overflow-hidden shadow-xl flex flex-col h-64">
            <div className="bg-slate-900 border-b border-slate-800 px-4 py-2 flex items-center gap-2">
              <Terminal className="w-4 h-4 text-slate-400" />
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Live Routing Telemetry</span>
            </div>
            <div className="p-4 overflow-y-auto font-mono text-xs flex-1 space-y-1">
              {logs.length === 0 ? (
                <div className="text-slate-600">Awaiting traffic to trace routing patterns...</div>
              ) : (
                logs.map((log, idx) => (
                  <div key={idx} className={`${
                    log.includes('SPIKE') ? 'text-red-400 font-bold' :
                    log.includes('INSERT') ? 'text-amber-300' :
                    log.includes('SELECT') ? 'text-blue-300' :
                    log.includes('Worker') ? 'text-emerald-400' :
                    'text-slate-400'
                  }`}>
                    <span className="text-slate-600 mr-2">{new Date().toISOString().split('T')[1].slice(0, -1)}</span>
                    {log}
                  </div>
                ))
              )}
              <div ref={logsEndRef} />
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}