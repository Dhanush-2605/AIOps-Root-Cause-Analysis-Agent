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
const circuitBreakers = { external_partner: { tripped: false } };

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


// SCENARIO B: Database Connection Leak (Early Return Bug)
app.post('/api/orders/checkout', async (req, res) => {
    const { userId } = req.body;
    logger.info("Processing checkout cart", { endpoint: "/api/orders/checkout", user_id: userId });
    
    let client;
    try {
        client = await pgPool.connect();
        await client.query('BEGIN');
        
        const isValid = false; // Business logic check fails
        
        if (!isValid) {
           logger.info("Checkout rejected: User failed fraud verification", { 
               endpoint: "/api/orders/checkout", 
               user_id: userId, 
               reason: "fraud_verification_failed" 
           });
           
           // BUG: Missing ROLLBACK and release. The connection is now leaked.
           return res.status(400).json({ error: "Account verification failed" });
        }

        await client.query('COMMIT');
        client.release();
        
        logger.info("Checkout completed successfully", { endpoint: "/api/orders/checkout", user_id: userId, status: "success" });
        res.json({ status: "success" });
    } catch (err) {
        logger.error("Database or processing error during checkout", { 
            endpoint: "/api/orders/checkout", 
            error_message: err.message,
            stack: err.stack
        });
        
        if (client) {
            await client.query('ROLLBACK').catch(() => {});
            client.release();
        }
        res.status(500).json({ error: "Checkout failed" });
    }
});

// ==========================================
// SCENARIO 3: The Badly Optimized Query
// ==========================================
app.post('/api/reports/generate', async (req, res) => {
    const { reportType } = req.body;
    logger.info("Generating report", { endpoint: "/api/reports/generate", report_type: reportType });
    
    let client;
    try {
        client = await pgPool.connect();
        await client.query('SELECT pg_sleep(90)'); 
        client.release();
        
        logger.info("Report generated successfully", { endpoint: "/api/reports/generate", report_type: reportType, status: "success" });
        res.json({ status: "success" });
    } catch (err) {
        logger.error("Report generation failed", { endpoint: "/api/reports/generate", report_type: reportType, error_message: err.message });
        if (client) client.release();
        res.status(500).json({ error: "Report generation failed" });
    }
});

// ==========================================
// 5. START SERVER
// ==========================================
app.listen(8080, () => logger.info("Backend API started", { port: 8080, environment: "production" }));