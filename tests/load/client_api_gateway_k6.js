import http from 'k6/http';
import crypto from 'k6/crypto';
import encoding from 'k6/encoding';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '1m', target: 25 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<1000'],
  },
};

const BASE_URL = __ENV.CLIENT_GATEWAY_URL || 'http://localhost:8005';
const PROJECT_ID = __ENV.PROJECT_ID || 'load-test-project';
const HMAC_SECRET = __ENV.HMAC_SECRET || 'load-test-secret';

function sign(method, path, body) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const bodyHash = crypto.sha256(body, 'hex');
  const message = `${timestamp}${method.toUpperCase()}${path}${bodyHash}`;
  const signature = crypto.hmac('sha256', HMAC_SECRET, message, 'hex');

  return {
    'Content-Type': 'application/json',
    'X-Project-ID': PROJECT_ID,
    'X-Timestamp': timestamp,
    'X-Signature': signature,
  };
}

export default function () {
  const path = '/api/client/meta';
  const payload = {
    project_id: PROJECT_ID,
    task_id: `load-${__VU}-${__ITER}`,
    entity_id: 'load-test-entity',
    entity_type: 'wordpress_post',
    changes: [
      {
        op: 'replace',
        path: '/meta_description',
        value: `Load test description ${__VU}-${__ITER}`,
      },
    ],
    metadata: { source: 'k6' },
  };

  const body = JSON.stringify(payload);
  const response = http.patch(`${BASE_URL}${path}`, body, { headers: sign('PATCH', path, body) });

  check(response, {
    'status is 2xx or expected auth/config failure': (res) =>
      (res.status >= 200 && res.status < 300) || [401, 403, 429, 502, 503].includes(res.status),
  });

  sleep(1);
}

