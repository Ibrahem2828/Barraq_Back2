// k6 load test for the two highest-risk endpoints under concurrency:
// authentication (login) and AI job creation (reserves subscription usage
// with select_for_update, then dispatches to the AI service).
//
// Usage (against a local docker-compose stack, never production):
//   k6 run -e BASE_URL=http://localhost:8000 -e EMAIL=... -e PASSWORD=... scripts/load_test_k6.js
//
// Install k6: https://k6.io/docs/get-started/installation/
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const EMAIL = __ENV.EMAIL;
const PASSWORD = __ENV.PASSWORD;

export const options = {
  scenarios: {
    login: {
      executor: 'ramping-vus',
      exec: 'loginScenario',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 20 },
        { duration: '1m', target: 20 },
        { duration: '15s', target: 0 },
      ],
    },
    ai_jobs: {
      executor: 'ramping-vus',
      exec: 'aiJobScenario',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },
        { duration: '1m', target: 10 },
        { duration: '15s', target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<800'],
    http_req_failed: ['rate<0.01'],
  },
};

export function loginScenario() {
  const response = http.post(
    `${BASE_URL}/api/v1/auth/login/`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  check(response, {
    'login status is 200': (res) => res.status === 200,
    'login returns access token': (res) => !!res.json('data.access'),
  });
  sleep(1);
}

export function aiJobScenario() {
  const loginResponse = http.post(
    `${BASE_URL}/api/v1/auth/login/`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  const access = loginResponse.json('data.access');
  if (!access) {
    sleep(1);
    return;
  }
  const response = http.post(
    `${BASE_URL}/api/v1/ai/jobs/`,
    JSON.stringify({
      client_job_id: `${__VU}-${__ITER}-${Date.now()}`,
      character: 'fahes',
      task_type: 'fahes_generate_quiz',
      input: {},
      parameters: {},
    }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${access}` } },
  );
  check(response, {
    'ai job accepted or ok': (res) => [200, 202, 409].includes(res.status),
  });
  sleep(1);
}
