const express = require('express');
const cors = require('cors');
const { Pool } = require('pg');
const { createClient } = require('redis');
const fs = require('fs');

const app = express();
app.use(cors());
app.use(express.json());

// ==========================================
// 1. CREDENTIALS & CONNECTIONS
// ==========================================

// Safely read the database password from the mounted GCP Secret volume
let dbPassword = process.env.DB_PASSWORD || "supersecretpassword";
try {
    if (fs.existsSync('/mnt/secrets/db-password.txt')) {
        dbPassword = fs.readFileSync('/mnt/secrets/db-password.txt', 'utf8').trim();
    }
} catch (err) {
    console.warn("Secret file not found, falling back to env var.");
}

// Connect to the Pgpool Proxy (NOT directly to the DB pods)
const pgPool = new Pool({
    host: process.env.DB_HOST || 'pgpool-service', // Maps to ConfigMap
    user: process.env.DB_USER || 'postgres',
    password: dbPassword,
    database: process.env.DB_NAME || 'mytierdb',
    port: 5432,
    max: 20 // Connection pool size
});

// Connect to the Redis Shock Absorber
const redisClient = createClient({
    url: `redis://${process.env.REDIS_HOST || 'redis-service'}:6379`
});

redisClient.on('error', (err) => console.error('Redis Client Error', err));

// ==========================================
// 2. THE API ROUTES (FAST INGESTION)
// ==========================================

// A special route to simulate a massive traffic spike
app.post('/api/load-test', async (req, res) => {
    const { count = 1000 } = req.body; // Default to 1000 records
    const departments = ['Engineering', 'HR', 'Sales', 'Marketing'];

    try {
        // Generate dummy records and slam them into Redis as fast as possible
        for (let i = 0; i < count; i++) {
            const employee = {
                name: `LoadTest_Emp_${Math.floor(Math.random() * 100000)}`,
                department: departments[Math.floor(Math.random() * departments.length)],
                salary: Math.floor(Math.random() * 100000) + 50000,
                join_date: new Date().toISOString().split('T')[0]
            };
            
            // LPUSH adds the record to the left side of the queue
            await redisClient.lPush('employee_queue', JSON.stringify(employee));
        }

        res.status(202).json({ 
            message: `Successfully buffered ${count} records into Redis.`,
            status: "Processing in background" 
        });
    } catch (err) {
        console.error(err);
        res.status(500).json({ error: "Ingestion failed" });
    }
});

// Metrics route for the Frontend (and future AI Agent) to monitor backpressure
app.get('/api/metrics', async (req, res) => {
    try {
        const queueDepth = await redisClient.lLen('employee_queue');
        res.json({ queueDepth });
    } catch (err) {
        res.status(500).json({ error: "Failed to read metrics" });
    }
});

// ==========================================
// 3. THE BACKGROUND WORKER (SLOW DRAIN)
// ==========================================

async function processQueue() {
    try {
        // Pop up to 50 items off the right side of the queue
        const batchSize = 50;
        const items = [];
        
        for (let i = 0; i < batchSize; i++) {
            const item = await redisClient.rPop('employee_queue');
            if (!item) break; // Queue is empty
            items.push(JSON.parse(item));
        }

        if (items.length > 0) {
            console.log(`Worker pulling ${items.length} records from Redis... writing to Postgres via Pgpool.`);
            
            // We use a transaction to safely write the batch
            const client = await pgPool.connect();
            try {
                await client.query('BEGIN');
                for (let emp of items) {
                    await client.query(
                        'INSERT INTO employee_data (name, department, salary, join_date) VALUES ($1, $2, $3, $4)',
                        [emp.name, emp.department, emp.salary, emp.join_date]
                    );
                }
                await client.query('COMMIT');
            } catch (err) {
                await client.query('ROLLBACK');
                console.error("Database write failed, data lost:", err);
                // In a production system, we would put the items back into Redis here!
            } finally {
                client.release();
            }
        }
    } catch (err) {
        console.error("Worker encountered an error:", err);
    }

    // Deliberately delay the loop by 1 second. 
    // This creates an artificial bottleneck so we can actually watch the queue fill up during a load test!
    setTimeout(processQueue, 1000); 
}

// ==========================================
// 4. START THE SERVER
// ==========================================
const PORT = process.env.PORT || 8080;

async function start() {
    await redisClient.connect();
    console.log("Connected to Redis Shock Absorber");
    
    app.listen(PORT, () => {
        console.log(`Backend API listening on port ${PORT}`);
        
        // Start the background worker loop
        processQueue();
    });
}

start();