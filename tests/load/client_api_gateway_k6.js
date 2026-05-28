import http from 'k6/http';
import crypto from 'k6/crypto';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '20s', target: 2 },
    { duration: '20s', target: 2 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<1000'],
  },
};

const BASE_URL = __ENV.CLIENT_GATEWAY_URL || 'http://localhost:8005';
const PROJECT_ID = __ENV.PROJECT_ID || 'load-test-project';
const HMAC_SECRET = __ENV.HMAC_SECRET || 'load-test-secret';
const ENTITY_ID = __ENV.ENTITY_ID || 'load-test-entity';

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
    entity_id: ENTITY_ID,
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
    'status is 2xx': (res) => res.status >= 200 && res.status < 300,
  });

  sleep(1);
}
