import React, { useState, useEffect } from 'react';


function App() {
  const [queueDepth, setQueueDepth] = useState(0);
  const [spikeCount, setSpikeCount] = useState(5000);
  const [isSpiking, setIsSpiking] = useState(false);

  // Poll the backend metrics every 1 second to watch the queue drain
  useEffect(() => {
    const fetchMetrics = () => {
      fetch('/api/metrics')
        .then((res) => res.json())
        .then((data) => {
          setQueueDepth(data.queueDepth || 0);
        })
        .catch((err) => console.error('Error fetching metrics:', err));
    };

    // Fetch immediately, then loop
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 1000);
    
    return () => clearInterval(interval); // Cleanup on unmount
  }, []);

  // Trigger the massive traffic spike
  const handleSpike = (e) => {
    e.preventDefault();
    if (spikeCount <= 0) return;

    setIsSpiking(true);
    
    fetch('/api/load-test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count: Number(spikeCount) }),
    })
      .then((res) => res.json())
      .then((data) => {
        console.log("Spike triggered:", data);
        setIsSpiking(false);
      })
      .catch((err) => {
        console.error('Error triggering spike:', err);
        setIsSpiking(false);
      });
  };

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        {/* Header Segment */}
        <div style={styles.header}>
          <div style={styles.badge}>AIOps Architecture Lab</div>
          <h1 style={styles.title}>Traffic Ingestion & Backpressure</h1>
          <p style={styles.subtitle}>
            Simulate massive traffic spikes. Requests are buffered in a Redis shock absorber and slowly drained into the PostgreSQL replica cluster.
          </p>
        </div>

        <hr style={styles.divider} />

        {/* Load Test Form */}
        <form onSubmit={handleSpike} style={styles.form}>
          <div style={styles.inputWrapper}>
            <label style={styles.label}>Simulated Requests</label>
            <input
              type="number"
              value={spikeCount}
              onChange={(e) => setSpikeCount(e.target.value)}
              min="100"
              max="50000"
              style={styles.input}
              disabled={isSpiking}
            />
          </div>
          <button 
            type="submit" 
            style={{
              ...styles.button,
              opacity: isSpiking ? 0.7 : 1,
              cursor: isSpiking ? 'not-allowed' : 'pointer',
              backgroundColor: isSpiking ? '#94a3b8' : '#ef4444' // Red for danger/spike
            }}
            disabled={isSpiking}
          >
            {isSpiking ? 'Firing...' : '🔥 FIRE TRAFFIC SPIKE'}
          </button>
        </form>

        {/* Live Metrics Segment */}
        <div style={styles.metricsSection}>
          <h3 style={styles.sectionTitle}>Live Redis Queue Depth</h3>
          
          <div style={styles.metricCard}>
            <div style={styles.metricValue}>
              {queueDepth.toLocaleString()}
            </div>
            <div style={styles.metricLabel}>
              {queueDepth === 0 
                ? 'Queue empty. System idle.' 
                : 'Messages pending DB ingestion...'}
            </div>
            
            {/* Visual Progress Bar */}
            <div style={styles.progressBarBg}>
               <div style={{
                 ...styles.progressBarFill,
                 width: `${Math.min((queueDepth / 10000) * 100, 100)}%`,
                 backgroundColor: queueDepth > 2000 ? '#ef4444' : '#3b82f6'
               }}></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Clean, modern style configurations
const styles = {
  container: {
    minHeight: '100vh',
    backgroundColor: '#f8fafc',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    padding: '24px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  },
  card: {
    backgroundColor: '#ffffff',
    borderRadius: '16px',
    boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 10px 15px -3px rgba(0, 0, 0, 0.1)',
    width: '100%',
    maxWidth: '560px',
    padding: '32px',
    border: '1px solid #e2e8f0',
  },
  header: {
    marginBottom: '24px',
  },
  badge: {
    display: 'inline-block',
    backgroundColor: '#fef2f2',
    color: '#991b1b',
    fontSize: '12px',
    fontWeight: '600',
    padding: '4px 10px',
    borderRadius: '9999px',
    marginBottom: '12px',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  title: {
    fontSize: '24px',
    fontWeight: '700',
    color: '#0f172a',
    margin: '0 0 8px 0',
    letterSpacing: '-0.02em',
  },
  subtitle: {
    fontSize: '14px',
    color: '#64748b',
    lineHeight: '1.5',
    margin: '0',
  },
  divider: {
    border: '0',
    borderTop: '1px solid #f1f5f9',
    margin: '24px 0',
  },
  form: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: '12px',
    marginBottom: '32px',
  },
  inputWrapper: {
    flex: '1',
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  label: {
    fontSize: '12px',
    fontWeight: '600',
    color: '#475569',
    textTransform: 'uppercase',
  },
  input: {
    width: '100%',
    padding: '12px 16px',
    fontSize: '16px',
    fontWeight: '600',
    borderRadius: '8px',
    border: '1px solid #cbd5e1',
    backgroundColor: '#f8fafc',
    color: '#0f172a',
    outline: 'none',
    boxSizing: 'border-box',
  },
  button: {
    padding: '14px 20px',
    fontSize: '14px',
    fontWeight: '700',
    color: '#ffffff',
    border: 'none',
    borderRadius: '8px',
    transition: 'background-color 0.2s',
    height: '46px',
  },
  metricsSection: {
    marginTop: '16px',
  },
  sectionTitle: {
    fontSize: '14px',
    fontWeight: '600',
    color: '#475569',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    margin: '0 0 12px 0',
  },
  metricCard: {
    backgroundColor: '#f8fafc',
    border: '1px solid #e2e8f0',
    borderRadius: '12px',
    padding: '32px 24px',
    textAlign: 'center',
  },
  metricValue: {
    fontSize: '48px',
    fontWeight: '800',
    color: '#0f172a',
    fontFamily: 'monospace',
    lineHeight: '1',
    marginBottom: '8px',
  },
  metricLabel: {
    fontSize: '14px',
    color: '#64748b',
    marginBottom: '24px',
  },
  progressBarBg: {
    height: '8px',
    backgroundColor: '#e2e8f0',
    borderRadius: '9999px',
    overflow: 'hidden',
  },
  progressBarFill: {
    height: '100%',
    transition: 'width 0.5s ease-out, background-color 0.3s',
  }
};

export default App;