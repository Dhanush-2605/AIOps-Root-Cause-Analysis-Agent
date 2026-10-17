import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    buffer_leak: {
      executor: 'constant-arrival-rate',
      rate: 12,             // 12 req/s = ~1.2MB/s = ~72MB/min
      timeUnit: '1s',
      duration: '5m30s',    // ~3960 calls = ~396MB leaked
      preAllocatedVUs: 20,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const res = http.post(
    'http://20.244.79.163:3000/api/reports/export',
    JSON.stringify({ reportId: __ITER }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  check(res, { 'status is 200': (r) => r.status === 200 });
}