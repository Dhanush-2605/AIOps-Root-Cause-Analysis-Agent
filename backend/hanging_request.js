import http from 'k6/http';

import { check } from 'k6';



export const options = {

  scenarios: {

    hanging: {

      executor: 'per-vu-iterations',

      vus: 3,

      iterations: 1,

      maxDuration: '10m',

    },

  },

};



export default function () {

  const res = http.post(

    'http://20.244.79.163:3000/api/payments/process',

    '{}',

    { headers: { 'Content-Type': 'application/json' }, timeout: '400s' }

  );

  check(res, { 'aborted by agent (504)': (r) => r.status === 504 });

}