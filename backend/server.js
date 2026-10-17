const express = require('express');
const cors = require('cors');
const { Pool } = require('pg');
const fs = require('fs');

const app = express();
app.use(cors());
app.use(express.json());

// ==========================================
// 1. CREDENTIALS & CONNECTIONS
// ==========================================
const SECRET_PATH = '/mnt/secrets/app-env';
let dbPassword = process.env.DB_PASSWORD;

if (fs.existsSync(SECRET_PATH)) {
    const raw = fs.readFileSync(SECRET_PATH, 'utf8');
    const match = raw.match(/^DB_PASSWORD=(.*)$/m);
    dbPassword = (match ? match[1] : raw).replace(/^"|"$/g, '').trim();
}

if (!dbPassword) {
    console.error('No DB password available, exiting');
    process.exit(1);
}

const logger = {
    info: (msg, meta = {}) => {
        console.log(JSON.stringify({ level: 'info', timestamp: new Date().toISOString(), message: msg, ...meta }));
    },
    warn: (msg, meta = {}) => {
        console.log(JSON.stringify({ level: 'warn', timestamp: new Date().toISOString(), message: msg, ...meta }));
    },
    error: (msg, meta = {}) => {
        console.error(JSON.stringify({ level: 'error', timestamp: new Date().toISOString(), message: msg, ...meta }));
    }
};

const pgPool = new Pool({
    host: process.env.DB_HOST || 'pgpool-service',
    user: process.env.DB_USER || 'postgres',
    password: dbPassword,
    database: process.env.DB_NAME || 'mytierdb',
    port: 5432,
    max: 20
});

// ==========================================
// 2. SRE CONTROL PLANE (For the AI Agent)
// ==========================================
const activeRequests = new Map();
const circuitBreakers = {
    external_partner: { tripped: false },
    stripe_api: { tripped: false }
};

const promClient = require('prom-client');
const register = new promClient.Registry();
promClient.collectDefaultMetrics({ register });

const longRunningRequests = new promClient.Gauge({
    name: 'http_requests_long_running_total',
    help: 'Count of in-flight requests currently exceeding the hang threshold',
    labelNames: ['threshold_sec'],
    registers: [register]
});

// ---- Metric: request duration histogram ----
const httpRequestDuration = new promClient.Histogram({
    name: 'http_request_duration_seconds',
    help: 'HTTP request duration in seconds',
    labelNames: ['method', 'route', 'status_code'],
    buckets: [0.1, 0.5, 1, 2, 5, 10, 20, 30, 45, 60, 90],
    registers: [register]
});

// ---- Metric: request counter (used for request rate and 5xx error rate) ----
const httpRequestsTotal = new promClient.Counter({
    name: 'http_requests_total',
    help: 'Total HTTP requests',
    labelNames: ['method', 'route', 'status'],
    registers: [register]
});

// Registered before the tracking middlewares, so Prometheus scrapes are not counted as traffic
app.get('/metrics', async (req, res) => {
    res.set('Content-Type', register.contentType);
    res.end(await register.metrics());
});

// ---- Duration + request counter middleware ----
app.use((req, res, next) => {
    const start = process.hrtime.bigint();
    res.on('finish', () => {
        const durationSec = Number(process.hrtime.bigint() - start) / 1e9;
        const route = req.route?.path || 'unmatched';   // avoids a new time series per random URL

        httpRequestDuration.observe(
            { method: req.method, route, status_code: res.statusCode },
            durationSec
        );
        httpRequestsTotal.inc({ method: req.method, route, status: res.statusCode });
    });
    next();
});

// ---- Hang detection: poll in-flight requests every 5s, update the gauge, log each hang once ----
const HANG_THRESHOLD_SEC = 10;
setInterval(() => {
    const now = Date.now();
    let count = 0;
    for (const record of activeRequests.values()) {
        const ageSec = (now - record.startTime) / 1000;
        if (ageSec > HANG_THRESHOLD_SEC) {
            count++;
            if (!record.warned) {
                record.warned = true;
                // Contains "hang" and "timeout" so the agent's log filter picks it up
                logger.warn("Request exceeded hang threshold - possible timeout", {
                    endpoint: record.path,
                    method: record.method,
                    request_id: record.id,
                    age_sec: Math.round(ageSec),
                    threshold_sec: HANG_THRESHOLD_SEC
                });
            }
        }
    }
    longRunningRequests.set({ threshold_sec: String(HANG_THRESHOLD_SEC) }, count);
}, 5000);

// ---- Middleware to track in-flight requests so the AI Agent can list and abort them ----
app.use((req, res, next) => {
    if (req.path.startsWith('/admin') || req.path.startsWith('/api/employees')) return next();

    const requestId = `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
    const controller = new AbortController();
    req.abortSignal = controller.signal;

    activeRequests.set(requestId, {
        id: requestId,
        method: req.method,
        path: req.path,
        startTime: Date.now(),
        warned: false,
        controller,
        res
    });

    res.on('finish', () => activeRequests.delete(requestId));
    res.on('close', () => {
        if (!res.writableFinished) controller.abort();   // client gave up: stop the handler too
        activeRequests.delete(requestId);
    });
    next();
});

// ==========================================
// ADMIN ROUTES (called by executor_node via kubectl exec)
// ==========================================

// List in-flight requests older than the threshold (data-gather step)
app.get('/admin/requests/list', (req, res) => {
    const thresholdSec = parseInt(req.query.threshold || String(HANG_THRESHOLD_SEC), 10);
    const now = Date.now();
    const hanging = [];
    for (const record of activeRequests.values()) {
        const ageSec = (now - record.startTime) / 1000;
        if (ageSec > thresholdSec) {
            hanging.push({
                id: record.id,
                method: record.method,
                endpoint: record.path,
                ageSec: Math.round(ageSec)
            });
        }
    }
    res.json({ count: hanging.length, requests: hanging });
});

// Abort in-flight requests older than the threshold (execution step)
app.post('/admin/requests/abort', (req, res) => {
    const thresholdSec = parseInt(req.query.threshold || String(HANG_THRESHOLD_SEC), 10);
    const now = Date.now();
    const aborted = [];

    for (const [id, record] of activeRequests.entries()) {
        if ((now - record.startTime) / 1000 > thresholdSec) {
            record.controller.abort();
            if (!record.res.headersSent) {
                record.res.status(504).json({ error: 'Request aborted by SRE AIOps agent' });
            }
            activeRequests.delete(id);
            aborted.push(record.path);
        }
    }
    logger.warn("Requests aborted by agent action", {
        threshold_sec: thresholdSec,
        aborted_count: aborted.length,
        endpoints: aborted
    });
    res.json({ status: 'success', abortedRequests: aborted.length, endpoints: aborted });
});

// Trips every breaker, including stripe_api which /api/payments/process actually checks.
// Note: stays tripped until the pod restarts.
app.post('/admin/circuit-breaker/trip', (req, res) => {
    Object.values(circuitBreakers).forEach(b => { b.tripped = true; });
    logger.warn("Circuit breakers tripped by agent action");
    res.json({ status: 'tripped', state: circuitBreakers });
});

// ==========================================
// 3. SEED & VERIFY DATA (Frontend Helpers)
// ==========================================

// Seed dummy data directly into Postgres
app.post('/api/seed', async (req, res) => {
    const { count = 50 } = req.body;
    const departments = ['Engineering', 'HR', 'Sales', 'Marketing'];

    try {
        // Ensure table exists
        await pgPool.query(`
            CREATE TABLE IF NOT EXISTS employee_data (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100),
                department VARCHAR(50),
                salary INT,
                join_date DATE
            )
        `);

        // Insert records
        for (let i = 0; i < count; i++) {
            const name = `Emp_${Math.floor(Math.random() * 100000)}`;
            const dept = departments[Math.floor(Math.random() * departments.length)];
            const salary = Math.floor(Math.random() * 100000) + 50000;
            const date = new Date().toISOString().split('T')[0];

            await pgPool.query(
                'INSERT INTO employee_data (name, department, salary, join_date) VALUES ($1, $2, $3, $4)',
                [name, dept, salary, date]
            );
        }
        res.status(201).json({ message: `Successfully injected ${count} records into Postgres.` });
    } catch (err) {
        console.error(err);
        res.status(500).json({ error: "Database ingestion failed" });
    }
});

// Fetch top 5 rows for Frontend confirmation
app.get('/api/employees', async (req, res) => {
    try {
        const result = await pgPool.query('SELECT * FROM employee_data ORDER BY id DESC LIMIT 5');
        res.json({ data: result.rows });
    } catch (err) {
        console.error(err);
        res.status(500).json({ error: "Failed to fetch data" });
    }
});

// ==========================================
// 4. PRODUCTION BUGS (Chaos Scenarios)
// ==========================================

// SCENARIO A: Network Delay (Hanging 3rd Party API)
// Hang length is configurable so the agent has time to investigate before it ends on its own.
const PAYMENT_HANG_MS = parseInt(process.env.PAYMENT_HANG_MS || '300000', 10); // 5 min default

app.post('/api/payments/process', async (req, res) => {
    const txId = `tx_${Math.floor(Math.random() * 10000)}`;
    logger.info("Initiating payment transaction", { endpoint: "/api/payments/process", transaction_id: txId });

    try {
        if (circuitBreakers.stripe_api.tripped) {
            logger.warn("Payment rejected due to tripped circuit breaker", { endpoint: "/api/payments/process", upstream: "stripe_api" });
            return res.status(503).json({ error: "Payment gateway unavailable." });
        }

        await new Promise((resolve, reject) => {
            const timer = setTimeout(() => resolve(), PAYMENT_HANG_MS);
            req.abortSignal.addEventListener('abort', () => {
                clearTimeout(timer);
                reject(new Error('AbortError'));
            });
        });

        logger.info("Transaction completed successfully", { endpoint: "/api/payments/process", transaction_id: txId, status: "success" });
        res.json({ status: "success", transaction_id: txId });
    } catch (err) {
        if (err.message === 'AbortError') {
            logger.error("Transaction failed: Client disconnected or request aborted.", {
                endpoint: "/api/payments/process",
                transaction_id: txId,
                error_type: "AbortError"
            });
            // The abort route may already have sent the 504, so guard against a double send
            if (!res.headersSent) res.status(504).json({ error: "Gateway Timeout" });
            return;
        }

        logger.error("Internal Server Error during payment processing", { endpoint: "/api/payments/process", error_message: err.message });
        if (!res.headersSent) res.status(500).json({ error: "Internal Server Error" });
    }
});

// SCENARIO B: Blocking Transaction
app.post('/api/orders/lock-row', async (req, res) => {
    const { orderId = 1 } = req.body;
    let client;
    try {
        client = await pgPool.connect();
        await client.query('BEGIN');
        await client.query('SELECT * FROM employee_data WHERE id = $1 FOR UPDATE', [orderId]);

        logger.info("Order lock acquired", { endpoint: "/api/orders/lock-row", order_id: orderId });
        res.json({ status: "locked", order_id: orderId });
        // no commit/rollback here — connection stays open holding the lock,
        // same as any real code path that forgets to release on an early branch
    } catch (err) {
        logger.error("Order lock failed", { endpoint: "/api/orders/lock-row", order_id: orderId, error_message: err.message });
        if (client) { await client.query('ROLLBACK').catch(() => {}); client.release(); }
        res.status(500).json({ error: "Lock acquisition failed" });
    }
});

app.post('/api/orders/checkout-slow', async (req, res) => {
    const { orderId = 1 } = req.body;
    const start = Date.now();
    logger.info("Checkout started", { endpoint: "/api/orders/checkout-slow", order_id: orderId });

    let client;
    try {
        client = await pgPool.connect();
        await client.query('BEGIN');
        await client.query('SELECT * FROM employee_data WHERE id = $1 FOR UPDATE', [orderId]);
        await client.query('COMMIT');
        client.release();

        const durationMs = Date.now() - start;
        logger.info("Checkout completed", { endpoint: "/api/orders/checkout-slow", order_id: orderId, status: "success", duration_ms: durationMs });
        res.json({ status: "success", order_id: orderId });
    } catch (err) {
        logger.error("Checkout failed", { endpoint: "/api/orders/checkout-slow", order_id: orderId, error_message: err.message, duration_ms: Date.now() - start });
        if (client) { await client.query('ROLLBACK').catch(() => {}); client.release(); }
        res.status(500).json({ error: "Checkout failed" });
    }
});

// SCENARIO C: Genuine Memory Demand (bounded cache, scales with load, plateaus)
const MAX_CACHE_ENTRIES = 2000;
const CACHE_ENTRY_SIZE_BYTES = 1024 * 1024; // 1MB per entry
const requestCache = new Map();

function evictOldest(maxEntries) {
    while (requestCache.size > maxEntries) {
        const oldestKey = requestCache.keys().next().value;
        requestCache.delete(oldestKey);
    }
}

// Periodic TTL sweep — entries older than the TTL are evicted regardless of cache size,
// so memory comes back down once load drops (this is what makes it "genuine
// demand" rather than a leak — it's load-correlated in both directions)
const CACHE_TTL_MS = 120000;

setInterval(() => {
    const now = Date.now();
    let evicted = 0;
    for (const [key, entry] of requestCache.entries()) {
        if (now - entry.createdAt > CACHE_TTL_MS) {
            requestCache.delete(key);
            evicted++;
        }
    }
    if (evicted > 0) {
        logger.info("Cache TTL sweep completed", { evicted_count: evicted, cache_size: requestCache.size });
    }
}, 10000);

app.post('/api/catalog/lookup', (req, res) => {
    const { itemId = Math.floor(Math.random() * 100000) } = req.body;
    const cacheKey = `item_${itemId}`;

    if (!requestCache.has(cacheKey)) {
        // simulate building/caching a real response payload
        const payload = Buffer.alloc(CACHE_ENTRY_SIZE_BYTES, 'x');
        requestCache.set(cacheKey, { payload, createdAt: Date.now() });
        evictOldest(MAX_CACHE_ENTRIES);
    }

    logger.info("Catalog lookup served", { endpoint: "/api/catalog/lookup", item_id: itemId, cache_size: requestCache.size });
    res.json({ status: "success", item_id: itemId, cache_size: requestCache.size });
});

// SCENARIO D: Memory Leak (unbounded growth, independent of load, never released)
const leakedBuffers = []; // intentionally never cleared — this IS the leak

app.post('/api/reports/export', (req, res) => {
    const { reportId = Math.floor(Math.random() * 100000) } = req.body;

    // simulate holding onto a generated report "for caching" but never releasing it —
    // classic leak pattern: looks like normal caching code, just missing any eviction
    const reportBuffer = Buffer.alloc(100 * 1024, 'r'); // 100KB per call
    leakedBuffers.push({ reportId, reportBuffer, createdAt: Date.now() });

    logger.info("Report export generated", { endpoint: "/api/reports/export", report_id: reportId, total_exports: leakedBuffers.length });
    res.json({ status: "success", report_id: reportId });
});

// ---- App-level gauges used by the agent to tell leak from genuine load ----
const memoryUsageGauge = new promClient.Gauge({
    name: 'nodejs_process_memory_bytes',
    help: 'Current Node.js process memory usage (RSS)',
    registers: [register]
});

const cacheEntriesGauge = new promClient.Gauge({
    name: 'app_bounded_cache_entries',
    help: 'Current entries in the bounded request cache (Scenario C)',
    registers: [register]
});

const leakedBuffersGauge = new promClient.Gauge({
    name: 'app_unbounded_buffers_total',
    help: 'Total buffers accumulated with no eviction (Scenario D)',
    registers: [register]
});

setInterval(() => {
    memoryUsageGauge.set(process.memoryUsage().rss);
    cacheEntriesGauge.set(requestCache.size);
    leakedBuffersGauge.set(leakedBuffers.length);
}, 5000);

// ==========================================
// 5. START SERVER
// ==========================================
app.listen(3000, () => logger.info("Backend API started", { port: 3000, environment: "production" }));