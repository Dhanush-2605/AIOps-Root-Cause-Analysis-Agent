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
    console.log(`[INFO] Initiating payment processing...`);
    
    try {
        if (circuitBreakers.external_partner.tripped) {
            console.warn(`[CIRCUIT BREAKER] Rejecting payment request, upstream API is marked as DOWN.`);
            return res.status(503).json({ error: "Service Unavailable" });
        }
        
        // This timer writes to the logs so the Agent can see the request is stuck!
        const logTimer = setTimeout(() => {
            console.error(`[FATAL WARNING] External payment API is hanging! Request pending for > 30s. Thread blocked.`);
        }, 30000);

        await new Promise((resolve, reject) => {
            const requestTimer = setTimeout(() => resolve(), 60000); 
            
            req.abortSignal.addEventListener('abort', () => {
                clearTimeout(requestTimer);
                clearTimeout(logTimer);
                reject(new Error('Aborted by SRE Agent'));
            });
        });

        res.json({ status: "success" });
    } catch (err) {
        if (err.message.includes('Aborted')) {
            console.log(`[RECOVERY] Hanging request successfully aborted. Event loop freed.`);
            return res.status(504).json({ error: "Gateway Timeout" });
        }
        res.status(500).json({ error: "Internal Server Error" });
    }
});



// SCENARIO B: Database Connection Leak (Early Return Bug)
app.post('/api/orders/checkout', async (req, res) => {
    let client;
    try {
        // Log the current pool stats so the AI can see if it's maxed out
        console.log(`[DB POOL STATS] Total: ${pgPool.totalCount}, Idle: ${pgPool.idleCount}, Waiting: ${pgPool.waitingCount}`);
        
        // If the pool is exhausted, it throws an error here
        client = await pgPool.connect(); 
        
        await client.query('BEGIN');
        await client.query('UPDATE employee_data SET salary = salary + 1 WHERE id = 1');
        
        const isValidUser = false; 
        
        if (!isValidUser) {
           console.warn(`[WARN] Checkout failed validation. Returning 400.`);
           // THE BUG: We return 400 but NEVER release the client!
           return res.status(400).json({ error: "Invalid user account status" });
        }

        await client.query('COMMIT');
        client.release();
        res.json({ status: "success" });
        
    } catch (err) {
        // THIS is the log the AI will see when the pool is fully leaked
        console.error(`[DB ERROR] Failed to acquire connection from pool: ${err.message}`);
        
        if (client) {
            await client.query('ROLLBACK');
            client.release();
        }
        res.status(500).json({ error: "Database Connection Error" });
    }
});

// SCENARIO C: Long-Running Database Lock (Expensive Query)
app.post('/api/reports/generate', async (req, res) => {
    console.log(`[INFO] Generating massive financial report...`);
    let client;
    try {
        client = await pgPool.connect();
        
        // Log that we are starting a potentially dangerous operation
        console.log(`[DB WARNING] Executing expensive aggregation query (Expected duration: ~200s).`);
        
        await client.query('BEGIN');
        
        // Simulating a terrible query that locks tables and takes 200 seconds to run.
        await client.query('SELECT pg_sleep(200)'); 
        
        await client.query('COMMIT');
        console.log(`[INFO] Financial report generated successfully.`);
        res.json({ status: "success", reportUrl: "/reports/download/123" });
        
    } catch (err) {
        // If the query times out or fails
        console.error(`[DB ERROR] Report generation failed: ${err.message}`);
        res.status(500).json({ error: "Report Generation Failed" });
    } finally {
        if (client) client.release();
    }
});

// ==========================================
// 5. START SERVER
// ==========================================
const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
    console.log(`Backend API listening on port ${PORT}`);
});