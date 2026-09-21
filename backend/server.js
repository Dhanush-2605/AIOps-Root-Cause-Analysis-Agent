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
let dbPassword = process.env.DB_PASSWORD || "supersecretpassword";
try {
    if (fs.existsSync('/mnt/secrets/db-password.txt')) {
        dbPassword = fs.readFileSync('/mnt/secrets/db-password.txt', 'utf8').trim();
    }
} catch (err) {
    console.warn("Secret file not found, falling back to env var.");
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

// ---- Metric: request duration histogram (also lets you alert on p95/p99, not just count) ----
const httpRequestDuration = new promClient.Histogram({
    name: 'http_request_duration_seconds',
    help: 'HTTP request duration in seconds',
    labelNames: ['method', 'route', 'status_code'],
    buckets: [0.1, 0.5, 1, 2, 5, 10, 20, 30, 45, 60, 90],
    registers: [register]
});

app.get('/metrics', async (req, res) => {
    res.set('Content-Type', register.contentType);
    res.end(await register.metrics());
});

// ---- Duration tracking middleware — add alongside your existing activeRequests middleware ----
app.use((req, res, next) => {
    const start = process.hrtime.bigint();
    res.on('finish', () => {
        const durationSec = Number(process.hrtime.bigint() - start) / 1e9;
        httpRequestDuration.observe(
            { method: req.method, route: req.route?.path || req.path, status_code: res.statusCode },
            durationSec
        );
    });
    next();
});

// ---- Poll activeRequests every 5s and update the gauge ----
// This is what turns your existing tracking Map into something Prometheus can alert on
const HANG_THRESHOLD_SEC = 30;
setInterval(() => {
    const now = Date.now();
    let count = 0;
    for (const record of activeRequests.values()) {
        if ((now - record.startTime) / 1000 > HANG_THRESHOLD_SEC) count++;
    }
    longRunningRequests.set({ threshold_sec: String(HANG_THRESHOLD_SEC) }, count);
}, 5000);



// Middleware to track in-flight requests so the AI Agent can abort them
app.use((req, res, next) => {
    if (req.path.startsWith('/admin') || req.path.startsWith('/api/employees')) return next();

    const requestId = `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
    const controller = new AbortController();
    req.abortSignal = controller.signal;

    activeRequests.set(requestId, {
        id: requestId,
        startTime: Date.now(),
        controller,
        res
    });

    res.on('finish', () => activeRequests.delete(requestId));
    res.on('close', () => activeRequests.delete(requestId));
    next();
});

// Admin Routes (Called by executor_node via kubectl exec)
app.post('/admin/requests/abort', (req, res) => {
    const thresholdSec = parseInt(req.query.threshold || '30', 10);
    const now = Date.now();
    let abortedCount = 0;

    for (const [id, record] of activeRequests.entries()) {
        if ((now - record.startTime) / 1000 > thresholdSec) {
            record.controller.abort();
            if (!record.res.headersSent) {
                record.res.status(504).json({ error: 'Request aborted by SRE AIOps agent' });
            }
            activeRequests.delete(id);
            abortedCount++;
        }
    }
    res.json({ status: 'success', abortedRequests: abortedCount });
});

app.post('/admin/circuit-breaker/trip', (req, res) => {
    circuitBreakers.external_partner.tripped = true;
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
app.post('/api/payments/process', async (req, res) => {
    const txId = `tx_${Math.floor(Math.random() * 10000)}`;
    logger.info("Initiating payment transaction", { endpoint: "/api/payments/process", transaction_id: txId });
    
    try {
        if (circuitBreakers.stripe_api.tripped) {
            logger.warn("Payment rejected due to tripped circuit breaker", { endpoint: "/api/payments/process", upstream: "stripe_api" });
            return res.status(503).json({ error: "Payment gateway unavailable." });
        }
        
        await new Promise((resolve, reject) => {
            const timer = setTimeout(() => resolve(), 60000); 
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
            return res.status(504).json({ error: "Gateway Timeout" });
        }
        
        logger.error("Internal Server Error during payment processing", { endpoint: "/api/payments/process", error_message: err.message });
        res.status(500).json({ error: "Internal Server Error" });
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



// ==========================================
// ADMIN: data-gather / execution
// ==========================================
app.get('/admin/requests/list', (req, res) => {
    const thresholdSec = parseInt(req.query.threshold || '30', 10);
    const now = Date.now();
    const hanging = [];
    for (const [id, record] of activeRequests.entries()) {
        const ageSec = (now - record.startTime) / 1000;
        if (ageSec > thresholdSec) hanging.push({ id, ageSec: Math.round(ageSec) });
    }
    res.json({ count: hanging.length, requests: hanging });
});



app.post('/admin/requests/abort', (req, res) => {
    const thresholdSec = parseInt(req.query.threshold || '30', 10);
    const now = Date.now();
    let abortedCount = 0;

    for (const [id, record] of activeRequests.entries()) {
        if ((now - record.startTime) / 1000 > thresholdSec) {
            record.controller.abort();
            if (!record.res.headersSent) {
                record.res.status(504).json({ error: 'Request aborted by SRE AIOps agent' });
            }
            activeRequests.delete(id);
            abortedCount++;
        }
    }
    logger.warn("Requests aborted by agent action", { threshold_sec: thresholdSec, aborted_count: abortedCount });
    res.json({ status: 'success', abortedRequests: abortedCount });
});



// ==========================================
// 5. START SERVER
// ==========================================
app.listen(8080, () => logger.info("Backend API started", { port: 8080, environment: "production" }));