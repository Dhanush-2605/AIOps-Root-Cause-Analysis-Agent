import http from 'k6/http';
import { check, sleep } from 'k6';
import { scenario } from 'k6/execution';

export const options = {
  scenarios: {
    memory_spike: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 10,
      maxVUs: 50,
      stages: [
        { duration: '2m', target: 3 },  // ramp
        { duration: '6m', target: 3 },  // hold: plateau ~400MB
        { duration: '3m', target: 0 },  // wind down: TTL sweep drops memory
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const url = 'http://20.219.215.58:3000/api/catalog/lookup';
  const itemId = scenario.iterationInTest + 1; // unique every time

  const res = http.post(url, JSON.stringify({ itemId }), {
    headers: { 'Content-Type': 'application/json' },
  });

  check(res, { 'status is 200': (r) => r.status === 200 });
  sleep(0.1);
}